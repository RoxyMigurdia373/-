"""One-click local parcel workflow, in metric coordinates."""
import math
from .config import Config, COMMON_RULES

SMALL_AREA_M2 = 200 / 3  # 0.1 mu, 1 mu = 2000/3 square metres.


def prepare_parcels(gdf, cfg=None):
    if gdf.crs is None:
        raise ValueError("文件缺少坐标系，无法可靠换算亩；请提供 .prj 或带 CRS 的文件")
    cfg = (cfg or Config()).model_copy(deep=True)
    original_crs = str(gdf.crs)
    metric = gdf.crs.is_projected and all(abs(a.unit_conversion_factor - 1) < 1e-12 for a in gdf.crs.axis_info[:2])
    frame = gdf
    if not metric or gdf.crs.to_epsg() == 3857:
        if len(gdf) == 0 or gdf.geometry.is_empty.all():
            raise ValueError("空图层无法自动选择面积计算投影")
        geographic = gdf.to_crs(4326)
        left, bottom, right, top = geographic.total_bounds
        if not all(math.isfinite(v) for v in (left, bottom, right, top)) or right-left > 6 or bottom < -80 or top > 84:
            raise ValueError("数据跨度过大或超出自动 UTM 范围，请提供适合当地面积计算的米制投影数据")
        target = geographic.estimate_utm_crs()
        if target is None:
            raise ValueError("无法自动选择米制投影，请核对数据坐标系")
        frame = gdf.to_crs(target)
    cfg.simple_mode = True
    cfg.rules.enable = list(COMMON_RULES)
    cfg.tolerance.min_area = SMALL_AREA_M2
    cfg.tolerance.min_segment_length = cfg.tolerance.distance
    cfg.repair.hole_action = "cut_open"
    cfg.repair.small_area_strategy = "longest_shared_edge"
    cfg.repair.merge_without_class = True
    cfg.repair.delete_isolated_small_area = True
    cfg.repair.auto_fill_max_area = None
    cfg.repair.fill_if_same_class = False
    cfg.repair.class_field = None
    return frame, cfg, {"source_crs": original_crs, "analysis_crs": str(frame.crs),
                        "min_area_mu": 0.1, "min_area_m2": SMALL_AREA_M2,
                        "cut_width_m": cfg.repair.cut_width}
