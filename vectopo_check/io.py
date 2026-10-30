from pathlib import Path
import geopandas as gpd


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
