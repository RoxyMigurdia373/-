import geopandas as gpd
import pytest
from typer.testing import CliRunner
from vectopo_check.cli import app
from vectopo_check.io import read_layer
from . import fixtures as f


@pytest.mark.parametrize("extension,driver", [("geojson", "GeoJSON"), ("shp", "ESRI Shapefile"), ("gpkg", "GPKG")])
def test_read_formats(tmp_path, extension, driver):
    path = tmp_path / ("input." + extension)
    gpd.GeoDataFrame(geometry=[f.square], crs="EPSG:3857").to_file(path, driver=driver, engine="pyogrio")
    assert len(read_layer(path)) == 1


@pytest.mark.parametrize("mode,expected", [("never", 0), ("warn", 1), ("error", 0)])
def test_warn_exit(tmp_path, mode, expected):
    path = tmp_path / "tiny.gpkg"
    gpd.GeoDataFrame(geometry=[f.tiny_area], crs="EPSG:3857").to_file(path, driver="GPKG", engine="pyogrio")
    result = CliRunner().invoke(app, ["check", str(path), "--out", str(tmp_path / "report"), "--fail-on", mode])
    assert result.exit_code == expected, result.output


def test_no_writeback(sample_data, tmp_path):
    before = sample_data.read_bytes()
    result = CliRunner().invoke(app, ["check", str(sample_data), "--layer", "sample", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert sample_data.read_bytes() == before
