import math
from shapely.geometry import mapping
from ..models import Issue


def polygons(geom):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield geom
    elif geom.geom_type in {"MultiPolygon", "GeometryCollection"}:
        for part in geom.geoms:
            yield from polygons(part)


def rings(geom):
    for part_index, poly in enumerate(polygons(geom)):
        yield part_index, "exterior", 0, poly.exterior
        for index, ring in enumerate(poly.interiors):
            yield part_index, "interior", index, ring


def ring_coords(ring):
    coords = list(ring.coords)
    if coords and coords[0] == coords[-1]:
        coords.pop()
    return coords


def is_geographic(crs):
    return bool(crs is not None and crs.is_geographic)


def finite_geometry(geom):
    return all(all(math.isfinite(v) for v in coord[:2]) for *_, ring in rings(geom) for coord in ring.coords)


def safe_geometry(geom):
    """Guard unsafe coordinates without repairing or altering input geometry."""
    return geom if geom is not None and not geom.is_empty and finite_geometry(geom) else None


def json_finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: json_finite(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_finite(v) for v in value]
    return value


def make_issue(feature_id, issue_type, severity="ERROR", message=None, geom=None,
               location=None, context=None, suggested_fix=None, related_feature_id=None):
    geometry = None
    if geom is not None and not geom.is_empty:
        geometry = json_finite(mapping(geom))
        if location is None:
            p = geom if geom.geom_type == "Point" else geom.representative_point()
            location = [p.x, p.y]
    if location is not None and not all(math.isfinite(x) for x in location):
        location = None
        geometry = None
    return Issue(feature_id=str(feature_id), issue_type=issue_type, severity=severity,
                 message=message or issue_type, location=location, geometry=geometry,
                 context=json_finite(context or {}), suggested_fix=suggested_fix,
                 related_feature_id=None if related_feature_id is None else str(related_feature_id))


def ring_context(part, kind, index):
    return {"part_index": part, "ring_type": kind, "ring_index": index}


def pair_ids(gdf, i, j):
    ids = gdf.attrs.get("vectopo_ids")
    if ids is None:
        ids = [str(x) for x in gdf.index]
    return ids[i], ids[j]


def intersections(gdf, candidates):
    cached = gdf.attrs.get("vectopo_pair_safe")
    geoms = getattr(gdf, "_vectopo_geometries", None)
    if geoms is None:
        geoms = gdf.geometry.array
    for i, j in candidates:
        a, b = geoms[i], geoms[j]
        if cached is not None:
            if not cached[i] or not cached[j]:
                continue
        else:
            if safe_geometry(a) is None or safe_geometry(b) is None:
                continue
            # Invalid inputs are never repaired implicitly for pair rules.
            if not a.is_valid or not b.is_valid:
                continue
        yield i, j, a, b, a.intersection(b)


def geographic_message(message, cfg):
    return message + ("（地理坐标系，建议投影后复核）" if cfg.is_geographic else "")
