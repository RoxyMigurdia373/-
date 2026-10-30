import importlib
import pytest
from shapely.geometry import box, Polygon
from . import fixtures as f


@pytest.mark.parametrize("module,geom,expected", [
    ("empty_geometry", f.empty, "EMPTY_GEOMETRY"),
    ("empty_geometry", None, "EMPTY_GEOMETRY"),
    ("invalid_coordinate", f.nan_coord, "INVALID_COORDINATE"),
    ("multipart", f.multipart_two, "MULTIPART"),
    ("holes", f.poly_with_hole, "HAS_HOLE"),
    ("holes", Polygon(f.square.exterior, [box(20, 20, 21, 21).exterior]), "INVALID_HOLE"),
    ("self_intersection", f.bowtie, "SELF_INTERSECTION"),
    ("touch_island", f.hole_touching_exterior, "TOUCH_ISLAND"),
    ("touch_island", f.two_holes_touch, "TOUCH_ISLAND"),
    ("sharp_angle", f.sharp_triangle, "SHARP_ANGLE"),
    ("duplicate_vertex", f.square_with_dup_vertex, "DUPLICATE_VERTEX"),
    ("short_segment", f.tiny_edge, "SHORT_SEGMENT"),
    ("small_area", f.tiny_area, "SMALL_AREA"),
])
def test_detect(module, geom, expected, cfg):
    cfg.rules.sharp_angle_degrees = 30
    cfg.tolerance.distance = .0001
    check = importlib.import_module(f"vectopo_check.checks.single.{module}").check
    assert expected in {i.issue_type for i in check("a", geom, {}, cfg)}


@pytest.mark.parametrize("module", ["empty_geometry", "invalid_coordinate", "multipart", "holes", "self_intersection", "touch_island", "sharp_angle", "duplicate_vertex", "short_segment", "small_area"])
def test_square_clean(module, cfg):
    assert not importlib.import_module(f"vectopo_check.checks.single.{module}").check("a", f.square, {}, cfg)


def test_valid_hole(cfg):
    from vectopo_check.checks.single import holes, touch_island
    assert [i.issue_type for i in holes.check("a", f.poly_with_hole, {}, cfg)] == ["HAS_HOLE"]
    assert touch_island.check("a", f.poly_with_hole, {}, cfg) == []


def test_concave_and_reverse(cfg):
    from vectopo_check.checks.single import sharp_angle
    cfg.rules.sharp_angle_degrees = 30
    a = sharp_angle.check("a", f.sharp_triangle, {}, cfg)
    b = sharp_angle.check("a", Polygon(list(f.sharp_triangle.exterior.coords)[::-1]), {}, cfg)
    assert {tuple(i.location) for i in a} == {tuple(i.location) for i in b}
    concave = Polygon([(0, 0), (10, 0), (10, 10), (5, 1), (0, 10)])
    assert not any(i.location == [5, 1] for i in sharp_angle.check("a", concave, {}, cfg))
