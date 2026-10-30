"""Streamlit Community Cloud entry point; isolated workspace per browser session."""
import base64
import hashlib
import uuid

import streamlit as st

from vectopo_check.config import Config
from vectopo_check.export import write_shp_zip
from vectopo_check.web import Workspace


class SessionWorkspace(Workspace):
    def __del__(self):
        self.close()


st.set_page_config(page_title="图斑拓扑检查", page_icon="🌾", layout="wide")
st.title("图斑拓扑检查与处理")
st.caption("岛洞 · 触岛 · 多部件 · 小面积 · 重叠面")
st.info("岛洞和触岛以 1 厘米窄通道切开；小于 0.1 亩的图斑并入共边最长的邻地块，无共边邻地块则删除。原始几何与属性保存在修复对照文件中。")
st.caption("文件上传至云端处理，请仅上传允许在云端处理的数据。SHP 请上传 ZIP 或完整配套文件。")
files = st.file_uploader("上传矢量数据", type=["zip", "gpkg", "geojson", "json", "shp", "shx", "dbf", "prj", "cpg"], accept_multiple_files=True)
if not files:
    st.stop()
if sum(f.size for f in files) > 180 * 1024 * 1024:
    st.error("文件总量不能超过 180 MB。")
    st.stop()
fingerprint = hashlib.sha256()
for file in files:
    fingerprint.update(file.name.encode())
    fingerprint.update(file.getbuffer())
key = fingerprint.hexdigest()
if st.session_state.get("upload_key") != key:
    old = st.session_state.pop("workspace", None)
    if old is not None:
        old.close()
    for item in ("upload_key", "dataset", "result", "job", "result_layer"):
        st.session_state.pop(item, None)
    workspace = SessionWorkspace()
    try:
        with st.spinner("正在读取图层…"):
            dataset = workspace.upload([{"name": f.name, "data": base64.b64encode(f.getbuffer()).decode()} for f in files])
    except Exception as exc:
        workspace.close()
        st.error(str(exc))
        st.stop()
    st.session_state.update(workspace=workspace, dataset=dataset, upload_key=key)
workspace = st.session_state.workspace
layer = st.selectbox("图层", st.session_state.dataset["layers"])
left, right = st.columns(2)
process = left.button("检查并处理", type="primary", use_container_width=True)
check = right.button("仅检查，不处理", use_container_width=True)
if process or check:
    cfg = Config(simple_mode=True)
    job = uuid.uuid4().hex
    st.session_state.pop("result", None)
    with st.spinner("正在检查并生成成果，请勿关闭页面…"):
        workspace._run(job, workspace.datasets[st.session_state.dataset["dataset"]], layer, cfg, repair=process)
    st.session_state.update(job=job, result=workspace.jobs[job], result_layer=layer)
result = st.session_state.get("result")
if result is None or st.session_state.get("result_layer") != layer:
    st.stop()
if result["status"] != "done":
    st.error(result.get("error", "处理失败，请重试。"))
    st.stop()
st.success("处理完成，可继续处理或下载成果。")
for field in ("repair_error", "vector_export_error"):
    if result.get(field):
        st.error(result[field])
summary = result["report"].get("summary", {})
st.write("检查统计", summary or "未发现问题")
if result.get("repair_summary"):
    st.write("修复统计", result["repair_summary"])
issues = result["report"].get("issues", [])
if issues:
    st.dataframe(issues, use_container_width=True)
if result.get("preview_limited"):
    st.caption("网页仅显示前 2,000 条问题，完整问题请下载检查报告。")
folder = workspace.root / st.session_state.job
st.subheader("下载成果")
exports = [("检查报告 ZIP", "check_reports.zip"), ("带问题字段的 GPKG", "checked_features.gpkg")]
if result.get("repair_enabled") and not result.get("repair_error"):
    exports += [("修复报告 JSON", "repair_report.json"), ("处理成果及原始对照 GPKG", "repair_staging.gpkg")]
for label, name in exports:
    path = folder / name
    if path.exists():
        st.download_button(label, path.read_bytes(), file_name=name, key=name)
for label, name, source, source_layer, enabled in [
    ("带问题字段的 SHP", "checked_features_shp.zip", "checked_features.gpkg", "checked_features", not result.get("vector_export_error")),
    ("处理后的 SHP", "processed_shp.zip", "repair_staging.gpkg", "processed", result.get("repair_enabled") and not result.get("repair_error")),
]:
    if not enabled:
        continue
    path = folder / name
    if not path.exists() and st.button("生成" + label, key="make_" + name):
        try:
            with st.spinner("正在生成 SHP 压缩包…"):
                write_shp_zip(folder / source, source_layer, path)
        except Exception as exc:
            st.error(f"SHP 导出失败：{exc}；仍可下载 GPKG。")
    if path.exists():
        st.download_button("下载" + label + " ZIP", path.read_bytes(), file_name=name, mime="application/zip", key=name)
