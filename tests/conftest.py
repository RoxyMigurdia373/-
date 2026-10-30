from pathlib import Path
from typing import get_args
import geopandas as gpd
import pytest
from vectopo_check.config import Config
from vectopo_check.models import IssueType
from . import fixtures as f


@pytest.fixture
def cfg():
    # Legacy regression tests deliberately exercise the full optional rule set.
    cfg = Config()
    cfg.rules.enable = list(get_args(IssueType))
    return cfg


@pytest.fixture(scope="session", autouse=True)
def sample_data():
    path = Path(__file__).parent / "data" / "sample.gpkg"
    path.parent.mkdir(exist_ok=True)
    gpd.GeoDataFrame({"fid_text": ["a", "b", "c", "d"]}, geometry=[f.square, f.square, f.bowtie, f.poly_with_hole], crs="EPSG:3857").to_file(path, layer="sample", driver="GPKG", engine="pyogrio")
    return path
