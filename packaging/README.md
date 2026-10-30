# 打包为单个 EXE（分享用）

把工具封装成一个双击即用的 Windows 可执行文件，接收方无需安装 Python、无需联网。

## 构建

```powershell
# 一次性准备（虚拟环境内）
.\.venv\Scripts\python.exe -m pip install -e ".[build]"

# 打包并自检
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_exe.ps1 -SelfTest
```

产物：`dist\地类图斑拓扑检查与修复工具_v<版本>.exe`（单文件，约 200 MB，内含 Python 运行时、
GDAL/PROJ、tkinter 与网页资源）。构建中间文件在 `build\pyinstaller\`，两者都已被 `.gitignore` 忽略。

只打包不跑自检时去掉 `-SelfTest`；`-SkipClean` 可复用上次的构建缓存（更快，但改动资源文件后建议清理）。

> 改完代码就要重打包：EXE 里固化的是打包那一刻的源码，`vectopo_check/**` 一改，旧 EXE 就过期了。
> 打包前请先关掉正在运行的旧 EXE，否则同名文件被占用、复制会失败（脚本会提示）。

## 发布清单

1. `& .\.venv\Scripts\python.exe -m pytest -q` 全绿。
2. `git add -A` 并提交、`git push origin main`（源码与文档必须先进仓库）。
3. `powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_exe.ps1 -SelfTest`，8 项自检全 OK。
4. 用 `dist\` 里的 EXE 走一遍真实数据（上传 → 勾选“自动修复” → 下载成果），确认重叠面、小面积、多部件都按预期处理。
5. 发布：把 `dist\地类图斑拓扑检查与修复工具_v<版本>.exe` 作为附件/Release 资源分发；`dist/` 不入库。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `vectopo_check/desktop.py` | 原生桌面窗口：选文件、图层、简易模式/自动修复、进度条、汇总与问题明细、日志、导出 SHP；检查在后台线程跑 `pipeline.run_pipeline`，不经过 HTTP |
| `vectopo_check/launcher.py` | EXE 入口：默认开原生窗口，`--input` 直接载入数据；`--web` 才走本地网页模式；`--selftest` 打包自检、`--serve` 无窗口启动服务、`--port` 自定义端口 |
| `vectopo_check/pipeline.py` | 桌面窗口、网页与 CLI 共用的检查+修复流水线，含取消检查 |
| `packaging/launcher_entry.py` | PyInstaller 入口脚本（绝对导入，保证包资源被收集） |
| `packaging/vectopo_check.spec` | onefile + 窗口模式（`console=False`）；整体收集 geopandas/shapely/pyproj/pyogrio 的二进制与 CRS 数据；排除 matplotlib/streamlit/PIL 等未用依赖 |
| `packaging/rth_env.py` | 运行时钩子，在导入前把 `PROJ_DATA`/`PROJ_LIB`/`GDAL_DATA` 指向 EXE 内解包目录，避免目标机器没装 PROJ 时报坐标转换错误 |
| `packaging/make_icon.py` | 生成 `packaging/app.ico`（绿色圆角底 + 两个叠放地块 + 琥珀色重叠带）；图标已提交，改样式后重新执行即可 |
| `packaging/version_info.txt` | EXE 属性里的产品名/版本（0.3.0，中文描述） |

EXE 内的启动流程：双击 → 解包到临时目录（首次 3–6 秒）→ 直接打开原生桌面窗口 → 选数据 → 处理，全程本进程内完成，没有本地服务、没有浏览器、没有数据拷贝。需要网页时加 `--web`，才会起 `127.0.0.1:8765`（被占用自动换空闲端口）并打开浏览器。

## 自检覆盖范围

`--selftest` 写 JSON 报告，覆盖：依赖版本、`proj.db` 存在、shapely 叠加运算、EPSG:4326→32650 坐标转换、
GPKG 读写（几何 + CRS）、重叠面裁剪修复、桌面窗口组件（Notebook/缩放）、`static/index.html` 资源、本地 HTTP 服务与 `/api/config`。
打包漏掉数据文件时这一步会失败，是发布前唯一的必过门槛。

## 分发注意事项

* 目标机器：Windows 10/11 64 位，无需 Python、无需管理员权限；首次运行会解包，请预留临时目录空间。
* 未做代码签名，SmartScreen 会提示"Windows 已保护你的电脑"，可点"更多信息 → 仍要运行"；企业环境可加入白名单。
* 病毒扫描偶发误报（PyInstaller 引导器常见），可对 `%LOCALAPPDATA%\vectopo-check\` 与 EXE 目录加白。
* 桌面窗口不联网、不起端口；只有 `--web` 模式会监听 `127.0.0.1:8765`（被占用自动换端口），数据不出本机。
* 成果默认写到 `<数据所在目录>\<文件名>_成果`，窗口里可“打开成果目录”。临时解包目录在关闭窗口时清理。
* 排错日志：`%LOCALAPPDATA%\vectopo-check\launcher.log`（窗口内也会显示最后几行）。
* 命令行用法：`"地类图斑拓扑检查与修复工具_v0.3.0.exe" --input 数据.gpkg`（直接载入数据）、
  `--web --serve --no-browser --port 8765`（网页模式）、`--selftest 报告.json`。
  岛洞方式在窗口里用“岛洞处理”单选框选择（默认 0.1 毫米细缝），CLI 对应 `check ... --hole-action slit|cut_open|split_open|fill|legacy`。
  需要控制台输出的调试版，把 spec 里的 `console=False` 改为 `True` 后重新打包。
