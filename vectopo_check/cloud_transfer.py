"""Bounded messages for cloud uploads; never send a complete file over a widget."""
import base64
from pathlib import Path
import shutil
import uuid

from .web import UPLOAD_SUFFIXES

CHUNK_SIZE = 1024 * 1024
MAX_UPLOAD = 180 * 1024 * 1024


def upload_request(workspace, action, data):
    if not hasattr(workspace, "incoming"):
        workspace.incoming = {}
    if action == "begin":
        files = data.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("请选择文件")
        seen, total = set(), 0
        for file in files:
            name, size = file.get("name"), file.get("size")
            if not isinstance(name, str) or not name or any(c in name for c in '/\\:') or name in {".", ".."}:
                raise ValueError("文件名不合法")
            if (Path(name).suffix.lower() not in UPLOAD_SUFFIXES and not name.lower().endswith('.shp.xml')) or name.lower() in seen:
                raise ValueError("文件类型不支持或文件名重复")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ValueError("文件大小不合法")
            seen.add(name.lower())
            total += size
        if total > MAX_UPLOAD:
            raise ValueError("文件总大小超过 180 MB")
        # Each page uploads a single dataset at a time. Discard abandoned chunks.
        for token in list(workspace.incoming):
            shutil.rmtree(workspace.root / token, ignore_errors=True)
            del workspace.incoming[token]
        token = uuid.uuid4().hex
        folder = workspace.root / token
        folder.mkdir()
        entries = [{"name":f["name"], "size":f["size"], "received":0} for f in files]
        for file in entries:
            (folder / file["name"]).touch()
        workspace.incoming[token] = entries
        return {"upload":token, "chunk_size":CHUNK_SIZE}
    token = data.get("upload")
    entries = workspace.incoming.get(token)
    if entries is None:
        raise ValueError("上传会话已失效，请重新选择文件")
    folder = workspace.root / token
    if action == "abort":
        workspace.incoming.pop(token)
        shutil.rmtree(folder, ignore_errors=True)
        return {"aborted":True}
    if action == "chunk":
        index, offset = data.get("index"), data.get("offset")
        if type(index) is not int or not 0 <= index < len(entries):
            raise ValueError("分块文件序号不合法")
        file = entries[index]
        if type(offset) is not int or offset != file["received"]:
            raise ValueError("分块顺序不正确，请重新上传")
        encoded = data.get("base64", "")
        if not isinstance(encoded, str) or len(encoded) > 4*((CHUNK_SIZE+2)//3):
            raise ValueError("分块过大")
        chunk = base64.b64decode(encoded, validate=True)
        if not chunk or len(chunk)>CHUNK_SIZE or offset+len(chunk)>file["size"]:
            raise ValueError("分块大小与文件大小不匹配")
        with (folder / file["name"]).open("ab") as stream:
            stream.write(chunk)
        file["received"] += len(chunk)
        return {"received":file["received"]}
    if action == "finish":
        if any(f["size"] != f["received"] for f in entries):
            raise ValueError("文件尚未上传完整")
        workspace.incoming.pop(token)
        paths = [folder/f["name"] for f in entries if not f["name"].lower().endswith('.shp.xml')]
        try:
            return workspace.register_upload(token, folder, paths)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    raise ValueError("未知上传操作")
