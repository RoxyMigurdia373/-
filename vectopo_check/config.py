from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
from .models import IssueType

COMMON_RULES = ["HAS_HOLE", "TOUCH_ISLAND", "MULTIPART", "SMALL_AREA", "OVERLAP"]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Input(Settings):
    path: str = "input.gpkg"
    layer: str | None = None
    id_field: str | None = None


class Tolerance(Settings):
    distance: float = Field(default=0.001, ge=0)
    min_segment_length: float = Field(default=0.01, ge=0)
    min_area: float = Field(default=1.0, ge=0)
    overlap_area: float = Field(default=0.01, ge=0)
    sliver_width: float = Field(default=0.5, ge=0)
    sliver_ratio: float = Field(default=50.0, gt=0)
    projected_coordinate_limit: float = Field(default=1e7, gt=0)
    longitude_limit: float = Field(default=180, gt=0)
    latitude_limit: float = Field(default=90, gt=0)


class Rules(Settings):
    multipart_policy: Literal["info", "warn", "error"] = "warn"
    hole_policy: Literal["info", "warn", "error"] = "info"
    sharp_angle_degrees: float = Field(default=15, gt=0, le=180)
    max_issues_per_feature: int = Field(default=50, gt=0)
    max_pairs: int = Field(default=2000000, gt=0)
    segment_index_threshold: int = Field(default=2000, gt=0)
    multipart_direct_threshold: int = Field(default=20, gt=0)
    location_precision: int = Field(default=6, ge=0)
    enable: list[IssueType] = Field(default_factory=lambda: list(COMMON_RULES))


HOLE_ACTIONS = ("legacy", "cut_open", "slit", "split_open", "fill")


class RepairSettings(Settings):
    # legacy: keep holes, cut_open: 1 cm channel, slit: hairline channel,
    # split_open: zero-width cut that splits the parcel, fill: absorb the hole.
    hole_action: Literal["legacy", "cut_open", "slit", "split_open", "fill"] = "legacy"
    cut_width: float = Field(default=0.01, gt=0)
    slit_width: float = Field(default=0.0001, gt=0)
    fill_all_max_area: float | None = Field(default=None, ge=0)
    merge_without_class: bool = False
    delete_isolated_small_area: bool = False
    small_area_strategy: Literal["longest_shared_edge", "largest_area"] = "longest_shared_edge"
    auto_fill_max_area: float | None = Field(default=None, ge=0)
    fill_if_same_class: bool = False
    class_field: str | None = None
    touch_max_area_change: float = Field(default=1.0, ge=0)
    touch_max_area_ratio: float = Field(default=0.01, ge=0, le=1)


class Config(Settings):
    simple_mode: bool = False
    input: Input = Field(default_factory=Input)
    output_dir: str = "report"
    tolerance: Tolerance = Field(default_factory=Tolerance)
    rules: Rules = Field(default_factory=Rules)
    repair: RepairSettings = Field(default_factory=RepairSettings)
    _repair_context: object = PrivateAttr(default=None)
    _active_rules: set | None = PrivateAttr(default=None)
    # Runtime CRS context, populated by the engine; useful for standalone checks too.
    is_geographic: bool = False

    @property
    def id_field(self):
        return self.input.id_field


def load_config(path: str | Path | None = None) -> Config:
    if path is None:
        return Config()
    with Path(path).open(encoding="utf-8") as stream:
        return Config.model_validate(yaml.safe_load(stream) or {})
