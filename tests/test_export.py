import geopandas as gpd
import pytest
from vectopo_check.config import Config
from vectopo_check.engine import check_layer
from vectopo_check.export import annotate_layer, write_vector_export
from .fixtures import square, shared_edge_pair


def test_annotated_both_sides_and_preserve(tmp_path):
    cfg = Config()
    cfg.rules.enable = ["DUPLICATE_GEOMETRY"]
    gdf = gpd.GeoDataFrame({"name": ["a", "b", "c"], "topo_issue": ["original"]*3}, geometry=[square, square, shared_edge_pair[1]], crs="EPSG:3857")
    report = check_layer(gdf, cfg)
    annotated, fields = annotate_layer(gdf, report, cfg)
    assert fields["topo_issue"] == "topo_issue_1"
    assert list(annotated.topo_count) == [1, 1, 0]
    assert list(annotated.topo_level) == ["ERROR", "ERROR", "NONE"]
    assert annotated[fields["topo_issue"]].tolist() == ["重复几何", "重复几何", "未发现问题"]
    write_vector_export(gdf, report, cfg, tmp_path)
    loaded = gpd.read_file(tmp_path / "checked_features.gpkg", engine="pyogrio")
    assert loaded.geometry.equals(gdf.geometry)
    assert loaded.topo_issue.tolist() == ["original"]*3
    assert "topo_count" not in gdf.columns


def test_duplicate_ids_rejected():
    cfg = Config()
    cfg.input.id_field = "id"
    gdf = gpd.GeoDataFrame({"id": ["same", "same"]}, geometry=[square, square])
    with pytest.raises(ValueError, match="不唯一"):
        annotate_layer(gdf, check_layer(gdf, cfg), cfg)
