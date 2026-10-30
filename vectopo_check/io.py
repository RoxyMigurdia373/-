from pathlib import Path
import shutil
from zipfile import ZipFile

import geopandas as gpd

SHP_SUFFIXES = {".gpkg", ".geojson", ".json", ".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx", ".zip"}


def write_gpkg_layer(gdf, path, layer, geometry_name="vt_geometry"):
    """Keep user attributes named geom/fid; reserve distinct physical columns."""
    used = {str(c).lower() for c in gdf.columns if c != gdf.geometry.name}
    while geometry_name.lower() in used:
        geometry_name += "_"
    fid_name = "vt_fid"
    while fid_name.lower() in used:
        fid_name += "_"
    gdf.to_file(path, layer=layer, driver="GPKG", engine="pyogrio", index=False,
                geometry_name=geometry_name, fid=fid_name)


def unpack_shapefile(archive_path, folder):
    """Select one SHP bundle and normalize names without trusting ZIP paths."""
    with ZipFile(archive_path) as archive:
        entries = [i for i in archive.infolist() if not i.is_dir() and not i.filename.startswith('__MACOSX/')]
        if sum(i.file_size for i in entries) > 512 * 1024 * 1024:
            raise ValueError("ZIP 解压后超过 512 MB，请拆分图层")
        mains = [i for i in entries if i.filename.lower().endswith('.shp')]
        if len(mains) != 1:
            raise ValueError("ZIP 内必须包含且仅包含一个 SHP 图层")
        main = mains[0]
        stem = main.filename[:-4].lower()
        companions = {}
        for item in entries:
            name = item.filename.lower()
            suffix = Path(name).suffix
            if name[:-len(suffix)] == stem and suffix in SHP_SUFFIXES - {'.zip'}:
                if suffix in companions:
                    raise ValueError("ZIP 内同名配套文件重复")
                companions[suffix] = item
        if not {'.shp', '.shx', '.dbf'}.issubset(companions):
            raise ValueError("ZIP 中缺少同名 .shp/.shx/.dbf 文件")
        target = Path(folder) / 'extracted'
        target.mkdir(parents=True, exist_ok=True)
        for suffix, item in companions.items():
            # A fixed basename avoids ZIP filename encoding differences on Windows.
            with archive.open(item) as source, (target / ('dataset' + suffix)).open('wb') as dest:
                shutil.copyfileobj(source, dest)
        return target / 'dataset.shp'


def list_layer_names(path):
    """Layer names for a dataset; empty when the format has no layer concept."""
    path = Path(path)
    if path.suffix.lower() not in {".gpkg", ".geojson", ".json", ".shp"}:
        return []
    try:
        return [str(row["name"]) for _, row in gpd.list_layers(path).iterrows()]
    except Exception:
        return []


def read_layer(input_path, layer=None):
    path = Path(input_path)
    if path.suffix.lower() not in {".geojson", ".json", ".gpkg", ".shp"}:
        raise ValueError("仅支持 GeoJSON / GPKG / SHP 面图层")
    if path.suffix.lower() == ".gpkg" and layer is None:
        layers = gpd.list_layers(path)
        if len(layers) != 1:
            raise ValueError("多图层 GPKG 必须通过 --layer 指定单图层")
        layer = str(layers.iloc[0]["name"])
    gdf = gpd.read_file(path, layer=layer, engine="pyogrio")
    validate_layer(gdf)
    return gdf


def validate_layer(gdf):
    def polygon_only(geom):
        if geom is None or geom.is_empty:
            return True
        if geom.geom_type in {"Polygon", "MultiPolygon"}:
            return True
        return geom.geom_type == "GeometryCollection" and all(polygon_only(g) for g in geom.geoms)
    if not all(polygon_only(g) for g in gdf.geometry):
        raise ValueError("仅支持面要素；图层包含非面几何")
