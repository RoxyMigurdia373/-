"""Launcher (desktop EXE front end) smoke tests.

These cover the code that only the packaged executable exercises: the small Tk
window that fronts the local web service, the port-conflict fallback, and the
``--selftest`` bundle probe. They are skipped when Tk cannot open a window.
"""

from __future__ import annotations

import json

import pytest

from vectopo_check.launcher import build_window, selftest, start_server


@pytest.fixture
def root():
    tkinter = pytest.importorskip("tkinter")
    try:
        widget = tkinter.Tk()
    except Exception as exc:  # no display / restricted session
        pytest.skip(f"Tk 不可用：{exc}")
    widget.withdraw()
    try:
        yield widget
    finally:
        widget.destroy()


def test_window_displays_service_address(root):
    ui = build_window(root, port=0, open_browser=False, autostart=False)
    root.update()

    assert ui["url"].get() == "正在启动本地服务…"
    assert str(ui["buttons"]["打开检查界面"].cget("state")) == "disabled"
    assert "启动中" in ui["log"].get("1.0", "end")

    handle = start_server(0)
    try:
        ui["attach"](handle)
        root.update()
        assert ui["url"].get() == handle.url
        for name in ("打开检查界面", "复制地址", "打开成果目录"):
            assert str(ui["buttons"][name].cget("state")) == "normal"
        text = ui["log"].get("1.0", "end")
        assert f"服务已就绪：{handle.url}" in text
        assert handle.output_dir.is_dir()
    finally:
        handle.close()


def test_busy_port_falls_back_to_a_free_one():
    first = start_server(0)
    second = None
    try:
        second = start_server(first.server.server_port)
        assert second.server.server_port != first.server.server_port
        assert second.url != first.url
    finally:
        if second is not None:
            second.close()
        first.close()


def test_selftest_reports_every_probe(tmp_path):
    report_path = tmp_path / "selftest.json"
    assert selftest(report_path) is True

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["version"]
    names = [item["name"] for item in report["checks"]]
    assert names == ["versions", "proj database", "shapely overlay", "proj transform",
                     "GPKG write/read", "overlap repair", "web assets", "web server"]
    assert all(item["ok"] for item in report["checks"])
