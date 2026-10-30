"""Cloud host for the original polygon workbench."""
import hashlib
from pathlib import Path
import tempfile

import streamlit as st
import streamlit.components.v1 as components

from vectopo_check.cloud import CloudWorkspace, component_html, dispatch

st.set_page_config(page_title="图斑拓扑检查", page_icon="🌾", layout="wide")
st.markdown("""<style>
header[data-testid="stHeader"]{display:none}
.block-container{padding:0!important;max-width:none!important}
[data-testid="stAppViewContainer"]{background:#f4f7f6}
iframe{border:0}
</style>""", unsafe_allow_html=True)

@st.cache_resource
def workbench_component(version):
    folder = Path(tempfile.mkdtemp(prefix="vectopo-component-"))
    (folder / "index.html").write_text(component_html(), encoding="utf-8")
    return components.declare_component("vectopo_workbench", path=str(folder))

if "cloud_workspace" not in st.session_state:
    st.session_state.cloud_workspace = CloudWorkspace()
ui = workbench_component(hashlib.sha256(component_html().encode()).hexdigest())
response = st.session_state.get("cloud_response")
request = ui(response=response, key="workbench", default=None)
if request and request.get("id") != (response or {}).get("id"):
    response = {"id": request.get("id")}
    try:
        response["value"] = dispatch(st.session_state.cloud_workspace, request)
    except Exception as exc:
        response["error"] = str(exc)
    st.session_state.cloud_response = response
    st.rerun()
