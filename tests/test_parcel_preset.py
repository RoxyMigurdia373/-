import geopandas as gpd
import pytest
from shapely.geometry import box, Polygon, Point
from vectopo_check.preset import prepare_parcels, SMALL_AREA_M2
from vectopo_check.config import Config
from vectopo_check.repair import holes, touch_island
from vectopo_check.repair.workflow import repair_layer, write_repair_outputs
from vectopo_check.engine import check_layer


def test_mu_threshold_and_merge_without_settings(tmp_path):
    gdf = gpd.GeoDataFrame({'name': ['small', 'large']}, geometry=[box(0,0,5,10), box(5,0,20,10)], crs=32650)
    projected, cfg, metadata = prepare_parcels(gdf)
    assert cfg.tolerance.min_area == pytest.approx(66.6666666667)
    checked = check_layer(projected, cfg)
    assert checked.summary == {'SMALL_AREA': 1}
    bundle = repair_layer(projected, cfg)
    assert len(bundle['accepted']) == 1
    assert bundle['accepted'].geometry.iloc[0].area == 200
    assert bundle['accepted'].iloc[0]['name'] == 'large'
    assert bundle['records'][0]['status'] == 'auto_accepted'
    write_repair_outputs(bundle,tmp_path)
    processed=gpd.read_file(tmp_path/'repair_staging.gpkg',layer='processed')
    assert len(processed)==1 and processed.geometry.iloc[0].area==200


@pytest.mark.parametrize('touch', [False, True])
def test_cut_instead_of_fill(touch):
    ring = [(0,5),(2,4),(2,6),(0,5)] if touch else list(box(2,2,4,4).exterior.coords)
    geom = Polygon(box(0,0,20,20).exterior, [ring])
    cfg = Config()
    cfg.repair.hole_action = 'cut_open'
    cfg.simple_mode = True
    result = (touch_island if touch else holes).repair('0', geom, {}, cfg)
    assert result.status == 'auto_accepted', result.reason
    assert result.repaired_geom.is_valid
    assert len(result.repaired_geom.interiors) == 0
    assert result.repaired_geom.area < geom.area
    assert not result.repaired_geom.covers(Polygon(ring).representative_point())
    assert result.diff['removed_area'] < 1


def test_preset_slits_open_and_preserves_occupied_hole():
    inner = box(4,4,6,6)
    outer = Polygon(box(0,0,20,20).exterior, [inner.exterior])
    gdf = gpd.GeoDataFrame(geometry=[outer,inner], crs=32650)
    gdf, cfg, _ = prepare_parcels(gdf)
    assert cfg.repair.hole_action == 'slit'
    # Only inspect hole action here: no small-area merge for this fixture.
    cfg.tolerance.min_area = 0
    bundle = repair_layer(gdf,cfg)
    event = next(r for r in bundle['records'] if r['action']=='slit_open_holes')
    assert event['status']=='auto_accepted', event
    assert bundle['accepted'].geometry.iloc[0].intersection(inner).area == 0


def test_geographic_projection_and_missing_crs():
    gdf = gpd.GeoDataFrame(geometry=[box(116,30,116.00005,30.00005)], crs=4326)
    projected, cfg, info = prepare_parcels(gdf)
    assert projected.crs.is_projected
    assert projected.geometry.iloc[0].area < SMALL_AREA_M2
    assert info['source_crs'] == 'EPSG:4326'
    with pytest.raises(ValueError,match='坐标系'):
        prepare_parcels(gpd.GeoDataFrame(geometry=[box(0,0,1,1)]))


def test_threshold_is_strict():
    gdf=gpd.GeoDataFrame(geometry=[box(0,0,SMALL_AREA_M2,1),box(100,0,166,1)],crs=32650)
    projected,cfg,_=prepare_parcels(gdf)
    report=check_layer(projected,cfg)
    assert [(i.feature_id,i.issue_type) for i in report.issues]==[('1','SMALL_AREA')]


def test_no_adjacent_target_does_not_bridge_gap():
    gdf=gpd.GeoDataFrame(geometry=[box(0,0,5,10),box(6,0,20,10)],crs=32650)
    projected,cfg,_=prepare_parcels(gdf)
    bundle=repair_layer(projected,cfg)
    assert len(bundle['accepted'])==1
    assert bundle['records'][0]['action']=='delete_isolated_small_area'
    assert bundle['records'][0]['status']=='auto_accepted'


@pytest.mark.parametrize("neighbor", [None, box(5,10,20,20)])
def test_isolated_deletion_export(tmp_path, neighbor):
    geometries = [box(0,0,5,10)] + ([] if neighbor is None else [neighbor])
    source = gpd.GeoDataFrame({'name': ['small'] + ([] if neighbor is None else ['large'])}, geometry=geometries, crs=32650)
    frame, cfg, _ = prepare_parcels(source)
    bundle = repair_layer(frame, cfg)
    assert '0' not in bundle['accepted'].index
    assert bundle['records'][0]['diff']['removed_area'] == 50
    assert len(source) == len(geometries)
    write_repair_outputs(bundle, tmp_path)
    stage = gpd.read_file(tmp_path/'repair_staging.gpkg', layer='staging')
    assert stage.iloc[0]['repair_status'] == 'deleted'
    assert stage.iloc[0]['geom_original'] == source.geometry.iloc[0].wkt
    assert stage.geometry.iloc[0] is None
    processed = gpd.read_file(tmp_path/'repair_staging.gpkg', layer='processed')
    assert len(processed) == len(geometries)-1


def test_short_shared_edge_is_not_deleted():
    frame = gpd.GeoDataFrame(geometry=[box(0,0,5,10), box(5,9.9999,100,100)], crs=32650)
    frame, cfg, _ = prepare_parcels(frame)
    bundle = repair_layer(frame, cfg)
    assert len(bundle['accepted']) == 2
    assert bundle['records'][0]['status'] == 'rejected'
