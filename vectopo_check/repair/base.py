from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from shapely import STRtree, make_valid
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from ..checks.base import polygons, safe_geometry


class RepairResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    feature_id: str
    repaired_geom: BaseGeometry | None
    action: str
    status: Literal["auto_accepted", "pending_review", "rejected"]
    diff: dict = Field(default_factory=dict)
    reason: str = ""
    target_feature_id: str | None = None


class RepairContext:
    """Current working layer with a base index and changed-feature overlay."""
    def __init__(self, gdf, ids):
        attributes = gdf.drop(columns=gdf.geometry.name).to_dict("records") or [{} for _ in range(len(gdf))]
        self.records = {str(fid): (geom, attrs) for fid, geom, attrs in zip(ids, gdf.geometry, attributes)}
        # Stable layer position, used to break ties deterministically (equal areas).
        self.order = {fid: position for position, fid in enumerate(self.records)}
        # Ranking pinned for the duration of a priority based phase (overlap claims).
        self.priority = {}
        self.index_ids = [fid for fid, (g, _) in self.records.items() if safe_geometry(g) is not None]
        self.invalid_ids = {fid for fid in self.index_ids if not self.records[fid][0].is_valid}
        self.tree = STRtree([self.records[fid][0] for fid in self.index_ids])
        self.changed = set()
        self.blocked = set()

    def update(self, fid, geom, attrs):
        self.records[str(fid)] = (geom, attrs)
        self.changed.add(str(fid))
        if geom is not None and not geom.is_valid:
            self.invalid_ids.add(str(fid))
        else:
            self.invalid_ids.discard(str(fid))

    def remove(self, fid):
        self.records.pop(str(fid), None)
        self.changed.discard(str(fid))
        self.invalid_ids.discard(str(fid))

    def nearby(self, geom):
        candidates = {self.index_ids[int(i)] for i in self.tree.query(geom)} | self.changed
        return {fid for fid in candidates if fid in self.records and self.records[fid][0].envelope.intersects(geom.envelope)}

    def neighbors(self, geom, exclude=None, include_blocked=False):
        candidates = self.nearby(geom)
        return [(fid, *self.records[fid]) for fid in sorted(candidates)
                if fid != exclude and (include_blocked or fid not in self.blocked) and fid in self.records
                and safe_geometry(self.records[fid][0]) is not None and self.records[fid][0].is_valid
                and self.records[fid][0].envelope.intersects(geom.envelope)]


def shared_edge_length(geom, other, tolerance):
    """Boundary length shared with ``other``, tolerant of hairline coordinate gaps.

    Cadastral layers are often digitised twice, so two parcels that look adjacent
    on screen can be a fraction of a millimetre apart. Exact
    ``boundary ∩ boundary`` then reports zero, which used to make a real
    neighbour look isolated (and the parcel eligible for deletion) or hide the
    right merge target. Any boundary running inside ``tolerance`` of the other
    boundary counts as shared.
    """
    edge = geom.boundary.intersection(other.boundary).length
    if edge > 0 or tolerance <= 0:
        return float(edge)
    return float(geom.boundary.intersection(other.buffer(tolerance)).length)


def contact_band(geom, other, tolerance):
    """Area the two tolerance buffers share: a band for neighbours, a dot for corners."""
    half = tolerance / 2
    if half <= 0:
        return float(geom.intersection(other).area)
    return float(geom.buffer(half).intersection(other.buffer(half)).area)


def bridge_gap(merged, tolerance):
    """Join near-touching parts across a hairline seam into one polygon.

    Two parcels digitised separately can leave a sub-millimetre gap, so their
    union stays a MultiPolygon and a merge would be refused. Bridging the seam
    with a corridor no wider than ``tolerance`` costs no measurable area and
    lets the merge go through.
    """
    if merged is None or merged.geom_type not in {"MultiPolygon", "GeometryCollection"} or tolerance <= 0:
        return None
    parts = [part for part in merged.geoms if part.geom_type == "Polygon" and not part.is_empty]
    if len(parts) < 2:
        return None
    changed = True
    while len(parts) > 1 and changed:
        changed = False
        for i in range(len(parts)):
            for j in range(i + 1, len(parts)):
                first, second = parts[i], parts[j]
                if first.distance(second) > tolerance:
                    continue
                a, b = nearest_points(first, second)
                if a.distance(b) > 0:
                    bridge = LineString([a, b]).buffer(tolerance / 2, cap_style="flat")
                else:
                    # A zero-width pinch: a dot of tolerance width opens the seam.
                    bridge = a.buffer(tolerance / 2)
                joined = unary_union([first, second, bridge])
                if joined.geom_type == "Polygon" and joined.is_valid:
                    parts = [part for index, part in enumerate(parts) if index not in (i, j)] + [joined]
                    changed = True
                    break
            if changed:
                break
    return parts[0] if len(parts) == 1 else None


def heal_self_touch(geom, cfg):
    """Rebuild a self-touching ring into a valid single polygon.

    A ring that touches itself is what a hole cut to zero width looks like:
    ``make_valid`` turns it back into a polygon whose interior ring is a real
    island. Only a single valid polygon counts as healed, so a bow tie (which
    ``make_valid`` splits into two lobes) is refused. GEOS cannot be trusted with
    the area of the invalid ring, so the caller compares the result against its
    own protection thresholds instead of the raw shoelace value.
    """
    if geom is None or geom.is_empty or geom.is_valid:
        return geom, False
    try:
        fixed = make_valid(geom)
    except Exception:
        return None, False
    parts = [part for part in getattr(fixed, "geoms", [fixed])
             if getattr(part, "geom_type", "") == "Polygon" and not part.is_empty]
    if len(parts) != 1 or not parts[0].is_valid:
        return None, False
    return parts[0], True


def heal_status(original, healed, cfg):
    """Area moved by a rebuild must stay inside the touch protection band."""
    if original is None or healed is None or cfg.is_geographic:
        return "pending_review"
    change = abs(original.area - healed.area)
    ratio = change / max(abs(original.area), cfg.tolerance.distance)
    if change > cfg.repair.touch_max_area_change or ratio > cfg.repair.touch_max_area_ratio:
        return "pending_review"
    return "auto_accepted"


def geometry_diff(original, candidate):
    def stats(g):
        return {"area": float(g.area) if g is not None else 0.0,
                "hole_count": sum(len(p.interiors) for p in polygons(g)),
                "type": g.geom_type if g is not None else None}
    before, after = stats(original), stats(candidate)
    return {"before": before, "after": after, "area_change": after["area"] - before["area"],
            "hole_count_change": after["hole_count"] - before["hole_count"],
            "type_changed": before["type"] != after["type"]}


def result(fid, original, candidate, action, status, reason="", target=None):
    return RepairResult(feature_id=str(fid), repaired_geom=candidate, action=action,
                        status=status, diff=geometry_diff(original, candidate), reason=reason,
                        target_feature_id=target)


def reject(fid, geom, reason, action="none"):
    return result(fid, geom, geom, action, "rejected", reason)


def validate_candidate(fid, original, candidate, attrs, cfg, action, status="auto_accepted", reason=""):
    """Independent modules reject new single-feature findings; engine also checks the layer."""
    if safe_geometry(candidate) is None or not candidate.is_valid:
        return reject(fid, original, "候选几何为空、非有限或不合法，已回退", action)
    if candidate.geom_type != original.geom_type:
        return reject(fid, original, "几何类型发生变化，已回退", action)
    from ..engine import SINGLE_CHECKERS
    def findings(g):
        return Counter((i.issue_type, i.severity) for checker in SINGLE_CHECKERS
                       if not cfg.simple_mode or checker.ISSUE_TYPE in cfg.rules.enable
                       for i in checker.check(str(fid), g, attrs, cfg))
    try:
        introduced = findings(candidate) - findings(original)
    except Exception as exc:
        return reject(fid, original, f"复检异常，已回退：{exc}", action)
    if introduced:
        return reject(fid, original, f"修复引入新问题，已回退：{dict(introduced)}", action)
    if cfg.is_geographic:
        status, reason = "pending_review", reason + "；地理坐标系下强制人工复核"
    return result(fid, original, candidate, action, status, reason)
