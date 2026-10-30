"""Zero-width hole opening: cut a hole-bearing parcel into touching faces.

A hole-free single polygon can only be produced by shaving off a channel, which
loses a bit of area. This pass keeps the area instead: a chord through the hole
splits the parcel into valid faces without holes that share the chord segments,
so their areas add up to the original exactly. The price is that one parcel
becomes several features, which is why the mode is opt-in.
"""
from __future__ import annotations

import math

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Polygon
from shapely.ops import split

from ..checks.base import polygons, safe_geometry
from .base import result

# Chords are tried in this order so the first cut usually runs along a
# predictable direction; a failed chord (it would shave a neck) falls back.
ANGLES = (0, 45, 90, 135, 30, 60, 120, 150)
MAX_FACES = 16


def _chord(polygon, ring, angle):
    centre = Polygon(ring).centroid
    left, bottom, right, top = polygon.bounds
    span = max(right - left, top - bottom) * 2 + 1.0
    dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return LineString([(centre.x - dx * span, centre.y - dy * span),
                       (centre.x + dx * span, centre.y + dy * span)])


def open_faces(polygon):
    """Split ``polygon`` until no hole is left, or return None when impossible."""
    if not polygon.interiors:
        return [polygon]
    faces, done = [polygon], []
    budget = MAX_FACES + 4 * len(polygon.interiors)
    while faces:
        face = faces.pop()
        if not face.interiors:
            done.append(face)
            continue
        budget -= 1
        if budget < 0:
            return None
        opened = None
        for angle in ANGLES:
            pieces = [p for p in split(face, _chord(face, face.interiors[0], angle)).geoms
                      if p.geom_type == "Polygon"]
            if len(pieces) < 2 or not all(p.is_valid for p in pieces):
                continue
            if abs(sum(p.area for p in pieces) - face.area) > max(face.area * 1e-9, 1e-9):
                continue
            opened = pieces
            break
        if opened is None:
            return None
        faces.extend(opened)
    if not done or any(f.is_empty or f.interiors or not f.is_valid for f in done):
        return None
    return done


def split_layer(frame, start_event=0):
    """Cut every hole open with a zero-width chord; returns the usual stage tuple."""
    geometry = frame.geometry.name
    children, child_ids = [], []
    split_into, sources = {}, {}
    records, proposals = [], []
    used = set(frame.index)
    for fid, geom in frame.geometry.items():
        if geom is None or geom.is_empty or not geom.is_valid or safe_geometry(geom) is None:
            continue
        parts = list(polygons(geom))
        if not parts or not any(part.interiors for part in parts):
            continue
        faces = []
        for part in parts:
            opened = open_faces(part)
            if opened is None:
                faces = []
                break
            faces.extend(opened)
        if len(faces) <= 1:
            continue
        area_before = geom.area
        if abs(sum(face.area for face in faces) - area_before) > max(area_before * 1e-9, 1e-9):
            continue
        ids = []
        for number, face in enumerate(faces, 1):
            child = f"{fid}#hole{number}"
            while child in used:
                child += "_"
            used.add(child)
            row = frame.loc[fid].copy()
            row[geometry] = face
            children.append(row)
            child_ids.append(child)
            ids.append(child)
            sources[child] = fid
        split_into[fid] = ids
        event = result(fid, geom, geom, "split_open_holes", "auto_accepted",
                       "零宽切线开洞：拆成共用切线且不含洞的面，面积零损失")
        record = event.model_dump(exclude={"repaired_geom"})
        record.update(event_id=str(start_event + len(records) + 1), issue_type="HAS_HOLE", child_ids=ids)
        record["diff"].update(hole_count_before=sum(len(part.interiors) for part in parts), hole_count_after=0,
                              feature_count_after=len(faces), area_change=0.0,
                              area_before=area_before, area_after=sum(face.area for face in faces),
                              removed_area=0.0, repair_choice="split")
        record["diff"]["after"]["type"] = "Polygon"
        record["diff"]["type_changed"] = geom.geom_type != "Polygon" or len(faces) > 1
        records.append(record)
        proposals.append({"event_id": record["event_id"], "feature_id": fid, "target_id": None,
                          "action": event.action, "status": event.status, "reason": event.reason,
                          "geom_original": geom.wkt, "source_original": geom.wkt, "geom_repaired": geom})
    if not children:
        return frame, frame, split_into, sources, records, proposals
    child_frame = gpd.GeoDataFrame(children, index=child_ids, geometry=geometry, crs=frame.crs)
    working = gpd.GeoDataFrame(pd.concat([frame.drop(index=list(split_into)), child_frame]),
                               geometry=geometry, crs=frame.crs)
    stage = gpd.GeoDataFrame(pd.concat([frame, child_frame]), geometry=geometry, crs=frame.crs)
    return working, stage, split_into, sources, records, proposals
