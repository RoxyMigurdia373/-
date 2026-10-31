import geopandas as gpd
from shapely.geometry import Polygon, box, MultiPolygon
from vectopo_check.config import Config, COMMON_RULES
from vectopo_check.engine import check_layer, generate_candidates


def test_default_rules():
    assert "INVALID_HOLE" in COMMON_RULES
    assert Config().rules.enable == COMMON_RULES
    outer = box(0, 0, 10, 10)
    hole = Polygon(outer.exterior, [box(2, 2, 4, 4).exterior])
    touch = Polygon(outer.exterior, [[(0, 5), (2, 4), (2, 6), (0, 5)]])
    multi = MultiPolygon([box(30, 0, 31, 1), box(32, 0, 33, 1)])
    # A ring that pinches its island to zero width is invalid and has no interior.
    self_touch = Polygon([(200, 0), (210, 0), (210, 10), (200, 10), (200, 5),
                          (205, 5), (205, 2), (207, 2), (207, 8), (205, 8), (205, 5)])
    gdf = gpd.GeoDataFrame(geometry=[hole, touch, multi, self_touch, box(40, 0, 40.5, .5),
                                     box(9, 0, 19, 10)], crs='EPSG:3857')
    report = check_layer(gdf)
    assert set(report.summary) == set(COMMON_RULES)


def test_disabled_hole_validation_not_executed(monkeypatch):
    from vectopo_check.checks.single import holes
    def fail(*args):
        raise AssertionError('disabled hole validity check ran')
    monkeypatch.setattr(holes, 'explain_validity', fail)
    geom = Polygon(box(0, 0, 10, 10).exterior, [box(2, 2, 4, 4).exterior])
    cfg = Config()
    cfg.rules.enable = [rule for rule in cfg.rules.enable if rule != "INVALID_HOLE"]
    result = check_layer(gpd.GeoDataFrame(geometry=[geom]), cfg)
    assert result.summary == {'HAS_HOLE': 1}
    assert result.issues[0].severity == 'INFO'


def test_candidate_sampling_repeatable():
    cfg = Config()
    cfg.rules.max_pairs = 3
    gdf = gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)] * 10)
    a, sampled, count = generate_candidates(gdf, cfg)
    assert sampled and count == 45 and len(a) == 3
    assert a == generate_candidates(gdf, cfg)[0]
