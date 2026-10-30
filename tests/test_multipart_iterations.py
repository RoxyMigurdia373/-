import geopandas as gpd
from shapely.geometry import MultiPolygon, box

from vectopo_check.preset import prepare_parcels, SMALL_AREA_M2
from vectopo_check.repair.workflow import repair_layer, write_repair_outputs
from vectopo_check.repair import small_area
from vectopo_check.repair.base import reject


def run(geometries, **attrs):
    frame = gpd.GeoDataFrame(attrs, geometry=geometries, crs=32649)
    frame, cfg, _ = prepare_parcels(frame)
    return frame, repair_layer(frame, cfg)


def test_split_before_small_area_and_preserve_lineage(tmp_path):
    source, bundle = run([MultiPolygon([box(0,0,5,5),box(50,0,70,10)]),box(5,0,25,10)],name=['source','neighbor'])
    result = bundle['accepted']
    assert set(result.geom_type) == {'Polygon'}
    assert len(result) == 2
    assert result.area.min() >= SMALL_AREA_M2
    assert result.area.sum() == source.area.sum()
    assert bundle['records'][0]['action'] == 'split_multipart'
    assert bundle['verification']['passed']
    write_repair_outputs(bundle,tmp_path)
    stage = gpd.read_file(tmp_path/'repair_staging.gpkg',layer='staging')
    assert stage.loc[stage.repair_id=='0','repair_status'].iloc[0]=='split'
    assert set(stage.loc[stage.repair_id.str.contains('#part'),'source_id']) == {'0'}
    assert source.geometry.iloc[0].geom_type == 'MultiPolygon'


def test_chain_of_small_merges_reaches_threshold():
    source,bundle=run([box(i*2,0,(i+1)*2,10) for i in range(5)])
    assert bundle['verification']['passed']
    assert len(bundle['accepted'])==1
    assert bundle['accepted'].area.iloc[0]==100
    assert len([r for r in bundle['records'] if r['action']=='merge_small_area' and r['status']=='auto_accepted'])==4


def test_small_cluster_deleted_if_merged_total_still_too_small():
    _,bundle=run([box(0,0,2,10),box(2,0,4,10)])
    assert bundle['accepted'].empty
    assert bundle['verification']['passed']
    assert bundle['records'][-1]['action']=='delete_isolated_small_area'


def test_rescan_previously_visited_target(monkeypatch):
    actual=small_area.repair
    calls=[]
    def delayed(fid,geom,attrs,cfg):
        calls.append(fid)
        if calls==['0']:
            return reject(fid,geom,'第一轮候选暂不可用','merge_small_area')
        return actual(fid,geom,attrs,cfg)
    monkeypatch.setattr(small_area,'repair',delayed)
    _,bundle=run([box(0,0,2,10),box(2,0,4,10)])
    assert calls==['0','1','0']
    assert bundle['verification']['small_area_rounds']==2
    assert bundle['verification']['passed']
    assert bundle['accepted'].empty


def test_overlapping_faces_are_merged_before_small_area_cleanup():
    # 50 ㎡ face overlaps 160 ㎡: trimming would leave 40 ㎡, so it is absorbed
    # whole and the layer ends clean instead of dead-ending on the overlap.
    _,bundle=run([box(0,0,5,10),box(4,0,20,10)])
    assert bundle['verification']['passed']
    assert bundle['verification']['remaining_overlap']==0
    assert bundle['verification']['remaining_small_area']==0
    assert len(bundle['accepted'])==1
    assert bundle['accepted'].area.iloc[0]==200
    event=next(r for r in bundle['records'] if r['issue_type']=='OVERLAP')
    assert event['action']=='merge_overlap' and event['status']=='auto_accepted'
    assert event['target_feature_id']=='1'


def test_exact_threshold_is_retained_and_split_ids_do_not_collide():
    _,bundle=run([MultiPolygon([box(0,0,SMALL_AREA_M2,1)]),box(100,0,200,1)],parcel=['p','p#part1'])
    assert bundle['verification']['passed']
    assert len(bundle['accepted'])==2


def test_merge_new_hole_is_cut_in_same_transaction():
    target=box(0,0,10,10).difference(box(4,4,6,10))
    source,bundle=run([box(4,6,6,10),target])
    assert bundle['verification']['passed']
    assert len(bundle['accepted'])==1
    geom=bundle['accepted'].geometry.iloc[0]
    assert geom.is_valid and not geom.interiors
    event=next(r for r in bundle['records'] if r['action']=='merge_small_area')
    assert 0 < event['diff']['post_merge_cut_area'] < 1
    assert geom.area < source.area.sum()


def test_microscopic_overlap_does_not_prevent_shared_edge_merge():
    from shapely.geometry import Polygon
    neighbor=Polygon([(5,0),(20,0),(20,10),(5,10),(5,5),(5-1e-10,4),(5,3),(5,0)])
    _,bundle=run([box(0,0,5,10),neighbor])
    assert bundle['verification']['passed']
    assert len(bundle['accepted'])==1
    event=next(r for r in bundle['records'] if r['action']=='merge_small_area')
    assert 0 < event['diff']['tolerated_overlap_area'] < 1e-6


def test_split_id_field_collision_and_attribute_preservation():
    frame=gpd.GeoDataFrame({'id':['p','p#part1'],'source_id':['original','neighbor']},
        geometry=[MultiPolygon([box(0,0,100,1)]),box(200,0,300,1)],crs=32649)
    frame,cfg,_=prepare_parcels(frame)
    cfg.input.id_field='id'
    bundle=repair_layer(frame,cfg)
    assert len(set(bundle['accepted'].index))==2
    assert 'p#part1_' in bundle['accepted'].index
    assert bundle['accepted'].loc['p#part1_','source_id']=='original'
