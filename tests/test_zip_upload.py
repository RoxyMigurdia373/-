import base64
from zipfile import ZipFile

import geopandas as gpd
from shapely.geometry import box

from vectopo_check.web import Workspace
from vectopo_check.io import read_layer


def test_zip_with_metadata_and_folder(tmp_path):
    path = tmp_path / '样本.shp'
    gpd.GeoDataFrame(geometry=[box(0, 0, 1, 1)], crs='EPSG:4326').to_file(path, engine='pyogrio')
    (tmp_path / '样本.shp.xml').write_text('<metadata/>')
    archive_path = tmp_path / 'sample.zip'
    with ZipFile(archive_path, 'w') as archive:
        for item in tmp_path.glob('样本.*'):
            archive.write(item, 'nested/' + item.name)
    workspace = Workspace()
    try:
        result = workspace.upload([{'name': 'sample.zip', 'data': base64.b64encode(archive_path.read_bytes()).decode()}])
        assert result['name'] == 'sample.zip'
        frame = read_layer(workspace.datasets[result['dataset']])
        assert len(frame) == 1
        assert frame.crs.to_epsg() == 4326
        files = [{'name': item.name, 'data': base64.b64encode(item.read_bytes()).decode()} for item in tmp_path.glob('样本.*')]
        unpacked = workspace.upload(files)
        assert len(read_layer(workspace.datasets[unpacked['dataset']])) == 1
    finally:
        workspace.close()
