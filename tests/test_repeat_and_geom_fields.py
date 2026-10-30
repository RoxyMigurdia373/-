import base64
import json
import time
import geopandas as gpd
from shapely.geometry import box
from .test_web import web_server, request
from vectopo_check.io import write_gpkg_layer


def test_user_geom_attributes_and_repeat_requests(web_server, tmp_path):
    base, workspace = web_server
    source = tmp_path / 'fields.gpkg'
    gdf = gpd.GeoDataFrame({'geom':['original text','second text'], 'fid':['parcel-a','parcel-b'],
                           'vt_geometry':['keep','keep'], 'vt_fid':[11,12]},
                          geometry=[box(0,0,5,10),box(5,0,20,10)],crs=32650)
    write_gpkg_layer(gdf, source, 'input')
    uploaded = json.load(request(base, '/api/upload', {'files':[{'name':source.name,'data':base64.b64encode(source.read_bytes()).decode()}]}))
    payload = {'dataset':uploaded['dataset'],'layer':'input','repair':True,'simple_mode':True}
    first = json.load(request(base, '/api/check', payload))
    # A repeated click during a run returns the existing task, not a busy error.
    assert json.load(request(base,'/api/check',payload))['job']==first['job']
    for _ in range(200):
        result=json.load(request(base,'/api/jobs/'+first['job']))
        if result['status'] not in {'queued','running'}:
            break
        time.sleep(.02)
    assert result['status']=='done',result
    assert result['vector_export_error'] is None, result
    assert result['repair_error'] is None, result
    again=json.load(request(base,'/api/check',payload))
    assert again=={'job':first['job'],'reused':True}
    for name in ['checked_features_shp.zip','processed_shp.zip']:
        assert request(base,f"/api/jobs/{first['job']}/{name}").read().startswith(b'PK')
    processed=gpd.read_file(workspace.root/first['job']/'repair_staging.gpkg',layer='processed')
    assert len(processed)==1
    assert processed.geom.tolist()==['second text']
    assert processed.fid.tolist()==['parcel-b']
    assert processed.vt_fid.tolist()==[12]
    # Processing an exported result is a new dataset, not a cache hit.
    shp=request(base,f"/api/jobs/{first['job']}/processed_shp.zip").read()
    reuploaded=json.load(request(base,'/api/upload',{'files':[{'name':'processed.zip','data':base64.b64encode(shp).decode()}]}))
    changed=json.load(request(base,'/api/check',{**payload,'dataset':reuploaded['dataset'],'layer':reuploaded['layers'][0]}))
    assert changed['job']!=first['job']
    for _ in range(200):
        rerun=json.load(request(base,'/api/jobs/'+changed['job']))
        if rerun['status'] not in {'queued','running'}:
            break
        time.sleep(.02)
    assert rerun['status']=='done',rerun
    assert rerun['repair_error'] is None and rerun['vector_export_error'] is None
    assert rerun['report']['feature_count']==1
