from typing import Literal
from pydantic import BaseModel, Field

IssueType = Literal[
    "EMPTY_GEOMETRY", "INVALID_COORDINATE", "MULTIPART", "HAS_HOLE",
    "INVALID_HOLE", "SELF_INTERSECTION", "TOUCH_ISLAND", "SHARP_ANGLE",
    "DUPLICATE_VERTEX", "SHORT_SEGMENT", "SMALL_AREA", "DUPLICATE_GEOMETRY",
    "OVERLAP", "SLIVER", "GAP",
]


class Issue(BaseModel):
    issue_id: str = ""
    feature_id: str
    issue_type: IssueType
    severity: Literal["INFO", "WARN", "ERROR"]
    message: str
    location: list[float] | None = None
    geometry: dict | None = None
    context: dict = Field(default_factory=dict)
    suggested_fix: str | None = None
    related_feature_id: str | None = None


class Report(BaseModel):
    tool: str = "vectopo-check"
    version: str
    input_path: str
    layer: str | None
    crs: str
    is_geographic: bool
    feature_count: int
    issue_count: int
    summary: dict[str, int]
    issues: list[Issue]
    sampled: bool = False
    candidate_count: int = 0
    checked_pair_count: int = 0
