import importlib
import geopandas as gpd
import pytest
from . import fixtures as f


@pytest.mark.parametrize("module,geoms,expected", [
    ("duplicate_geometry", f.identical_pair, "DUPLICATE_GEOMETRY"),
    ("overlap", f.overlap_pair, "OVERLAP"),
    ("sliver", f.sliver_pair, "SLIVER"),
    ("gap", f.gap_three, "GAP"),
])
def test_pair(module, geoms, expected, cfg):
    cfg.tolerance.min_area = .5
    gdf = gpd.GeoDataFrame(geometry=geoms, crs="EPSG:3857")
    issues = importlib.import_module(f"vectopo_check.checks.pair.{module}").check(gdf, [(0, 1)], cfg)
    assert expected in {i.issue_type for i in issues}


def test_shared_edge(cfg):
    from vectopo_check.checks.pair.overlap import check
    assert check(gpd.GeoDataFrame(geometry=f.shared_edge_pair), [(0, 1)], cfg) == []
