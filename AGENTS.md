# 协作约定（对 AI 代理与维护者）

## 每次功能改动都要同步三处

任何代码或行为改动都必须同时完成下面三件事，缺一不可：

1. **更新源码与测试**：改 `vectopo_check/`、补 `tests/`，`& .\.venv\Scripts\python.exe -m pytest -q` 必须全绿。
2. **推送到 GitHub**：`git add -A` → 提交（仓库未配置身份时用 `git -c user.name=Codex -c user.email=codex@users.noreply.github.com commit -F 消息文件`）→ `git -c http.proxy=http://127.0.0.1:7897 push origin main`。本机直连 github.com:443 会被阻断，必须走本地代理（`127.0.0.1:7897`，Clash 之类的端口，变了就按实际改）；`git ls-remote` 同样要带这个参数。
3. **重新打包 EXE**：`powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_exe.ps1 -SelfTest`，产物是 `dist\地类图斑拓扑检查与修复工具_v<版本>.exe`，自检 8 项必须全部 OK。改了 `vectopo_check/**` 或 `packaging/**` 就必须重打包，`dist/` 不入库，用文件或发布页分发。

版本号变化时：改 `vectopo_check/__init__.py` 的 `__version__` 与 `packaging/version_info.txt`，两边保持一致，EXE 文件名随之更新。

## 环境与命令

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test,build]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\vectopo-check.exe check tests/data/sample.gpkg --layer sample --out report
```

## 平台注意事项

* 本机只有 Windows PowerShell 5.1（没有 `pwsh`）。`.ps1` 必须是纯 ASCII：无 BOM 的脚本会按 GBK 解码，中文会破坏引号配对。中文提示放到 `.py` 里。
* `git diff > 文件` 会写成 UTF-16LE，`git apply` 报 "No valid patches in input"；导出补丁用 `cmd /c "git diff --binary > 文件"`。
* 启动带窗口的 EXE 做截图验证要用 `cmd /c start "" "<exe>"`，否则继承隐藏启动信息，窗口 `IsWindowVisible` 为假。
