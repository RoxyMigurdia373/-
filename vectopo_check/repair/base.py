from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from shapely import STRtree
from shapely.geometry.base import BaseGeometry

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
