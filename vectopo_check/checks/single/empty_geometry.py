from ..base import make_issue
ISSUE_TYPE = "EMPTY_GEOMETRY"


def check(feature_id, geom, attrs, cfg):
    if geom is None or geom.is_empty:
        return [make_issue(feature_id, ISSUE_TYPE, suggested_fix="remove_or_rebuild")]
    return []
