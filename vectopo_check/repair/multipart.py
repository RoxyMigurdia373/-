"""Lossless split before hole and small-area repair, preserving source lineage."""
import geopandas as gpd
import pandas as pd
from ..checks.base import polygons, safe_geometry
from .base import result


def split_layer(original):
    geometry = original.geometry.name
    working = original.copy()
    children, child_ids, records, proposals = [], [], [], []
    split_into, sources = {}, {}
    used = set(original.index)
    for fid, geom in original.geometry.items():
        if geom is None or geom.geom_type not in {"MultiPolygon", "GeometryCollection"}:
            continue
        parts = list(polygons(geom))
        if not parts or safe_geometry(geom) is None or not geom.is_valid or any(not p.is_valid for p in parts):
            continue
        # Do not silently discard non-polygon members of a collection.
        if geom.geom_type == "GeometryCollection" and any(g.geom_type not in {"Polygon", "MultiPolygon"} for g in geom.geoms):
            continue
        ids = []
        for number, part in enumerate(parts, 1):
            child = f"{fid}#part{number}"
            while child in used:
                child += "_"
            used.add(child)
            row = original.loc[fid].copy()
            row[geometry] = part
            children.append(row)
            child_ids.append(child)
            ids.append(child)
            sources[child] = fid
        split_into[fid] = ids
        event = result(fid, geom, geom, "split_multipart", "auto_accepted", "先拆分为独立单部件面，继承源属性并保留源 ID")
        record = event.model_dump(exclude={"repaired_geom"})
        record.update(event_id=str(len(records)+1), issue_type="MULTIPART", child_ids=ids)
        record["diff"].update(part_count_before=len(parts), feature_count_after=len(parts), output_type="Polygon", area_change=0.0)
        record["diff"]["after"]["type"] = "Polygon"
        record["diff"]["type_changed"] = True
        records.append(record)
        proposals.append({"event_id":record["event_id"], "feature_id":fid, "target_id":None, "action":event.action,
                          "status":event.status, "reason":event.reason, "geom_original":geom.wkt,
                          "source_original":geom.wkt, "geom_repaired":geom})
    if children:
        child_frame = gpd.GeoDataFrame(children, index=child_ids, geometry=geometry, crs=original.crs)
        working = gpd.GeoDataFrame(pd.concat([original.drop(index=list(split_into)),child_frame]), geometry=geometry, crs=original.crs)
        stage = gpd.GeoDataFrame(pd.concat([original,child_frame]), geometry=geometry, crs=original.crs)
    else:
        stage = original.copy()
    return working, stage, split_into, sources, records, proposals
