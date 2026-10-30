import json
from zipfile import ZipFile
import geopandas as gpd
from shapely.geometry import box
from vectopo_check.export import write_shp_zip


def test_shp_export_fields_unicode_and_geometry(tmp_path):
    source = tmp_path / 'source.gpkg'
    gdf = gpd.GeoDataFrame({'very_long_name_a':['中文'*200], 'very_long_name_b':['正常'],
                           '中文字段':['岛洞；触岛'], 'topo_count':[2], 'shape':['保留']},
                          geometry=[box(0,0,10,10)], crs=32650)
    gdf.to_file(source, layer='processed', engine='pyogrio')
    target = tmp_path/'processed_shp.zip'
    write_shp_zip(source, 'processed', target)
    with ZipFile(target) as archive:
        assert {'parcels.shp','parcels.shx','parcels.dbf','parcels.prj','parcels.cpg','fields.json','attributes_full.json'} <= set(archive.namelist())
        fields = json.loads(archive.read('fields.json'))
        names = fields['field_mapping']
        assert len(set(names.values())) == len(names)
        assert all(len(name)<=10 and name.isascii() for name in names.values())
        assert fields['truncated_text_fields'] == ['very_long_name_a']
        assert json.loads(archive.read('attributes_full.json'))[0]['very_long_name_a'] == '中文'*200
    loaded = gpd.read_file('zip://' + str(target), engine='pyogrio')
    assert loaded.crs.to_epsg()==32650
    assert loaded.geometry.iloc[0].equals(gdf.geometry.iloc[0])
    assert loaded[names['中文字段']].iloc[0]=='岛洞；触岛'
    assert loaded[names['shape']].iloc[0]=='保留'
    assert loaded.topo_count.iloc[0]==2


def test_shp_empty_layer(tmp_path):
    source=tmp_path/'empty.gpkg'
    gpd.GeoDataFrame({'id':[]}, geometry=[], crs=32650).to_file(source,layer='processed',engine='pyogrio')
    write_shp_zip(source,'processed',tmp_path/'empty.zip')
    assert len(gpd.read_file('zip://'+str(tmp_path/'empty.zip'),engine='pyogrio'))==0
