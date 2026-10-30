# vectopo-check

Python 3.11+ 的纯本地面要素拓扑检查 CLI。支持 GeoJSON、GPKG、SHP，默认启用岛洞、触岛、多部件、小面积、重叠面 5 项常用检查；其他 10 项保留为 CLI 配置可选规则，网页不再显示。运行时不联网、不调用模型、不修改原数据。默认仅检查；显式启用修复时，另外输出修复候选、审核报告和已接受变化的副本。

## 网页操作

### 当前默认：免参数图斑处理

上传后直接点击“检查并处理”，或选择“仅检查，不处理”。网页不再要求填写容差、分类字段或修复策略，固定执行五项常用检查。以下预设是当前网页行为，后文 YAML/旧修复策略仅适用于显式 CLI 高级配置。

* 小面积严格按 **小于 0.1 亩 = 200/3 平方米 ≈ 66.67 平方米** 判断。自动选用当地 UTM 米制投影；已有米制投影保留（Web Mercator 自动转当地 UTM）。这使用投影平面面积，不是大地测量意义的法定面积认证。
* 岛洞、触岛采用 `cut_open_holes`：在洞与外边界的最近位置建立 1 厘米宽窄通道，把内环变成开放边界。不是填洞，也不是零面积切线，切口会产生少量面积损失；切口影响的面积记录在审核报告中。洞内已有地块不被吞并。
* 小图斑通过空间索引寻找与自身有真实共边、无面积交叠的邻面，优先合入共边最长者，同长按 ID 稳定选择。不要求再指定分类字段；按用户授权保留目标面属性，源属性留在对照图层。仅共点、隔着空隙或没有共边邻面的图斑直接删除；报告记录删除面积，staging 保留原始几何与属性并标记 deleted，处理成果不包含已删除图斑。有共边但邻面无法安全合并（待审核、类型不符、共边过短等）时保留，不按孤立面删除。
* 切开后仍有洞、产生意外多部件、几何非法或复检新增问题时回退。面积变化超过 1 平方米或原面积的 1% 时转人工复核。缺失 CRS、数据跨度超过自动 UTM 的 6°经度范围或超出其纬度适用范围时提示提供适当坐标信息，不能直接把经纬度当作平方米。
* 简洁模式按五项选定规则复核改动图斑及空间邻面，不再逐笔全图层执行耗时的自相交、GAP 等已移除规则；额外检查候选几何合法性。高级模式的旧完整复检仍保留。

处理成果下载 `repair_staging.gpkg`：**`processed` 为已接受处理后的地块图层**，合并源行已移除；`staging` 保留原始行及前后几何对照；`candidates` 保留待审核/拒绝/接受候选记录。待审核和拒绝的修改不会进入 processed。导出的坐标系为面积计算使用的米制投影，网页显示输入和输出 CRS，原文件不修改。

CLI 可使用同一预设：`vectopo-check check input.gpkg --simple --repair --out report`；不带 `--repair` 时仅按预设检查。

双击项目中的 `启动网页版.cmd`，或在已安装环境执行：

```powershell
python -m vectopo_check.cli web
# 自定义端口或不自动打开浏览器
python -m vectopo_check.cli web --port 8766 --no-open
```

浏览器访问 `http://127.0.0.1:8765`。选择或拖入文件 → 选择图层 → 调整参数和规则 → 开始检查。也可点击“试用示例”体验。SHP 请同时选择同名 `.shp/.shx/.dbf`，建议附带 `.prj/.cpg`；支持多个图层的 GPKG。可导入 YAML 配置、导出当前配置，参数经服务端 Pydantic 校验。

也可直接上传包含单个 SHP 图层及配套文件的 ZIP（支持子目录），无需手动解压；`.shp.xml` 元数据自动忽略。ZIP 解压后总大小上限为 512 MB；若包含多个 SHP 主文件，请拆分后上传。压缩包内文件在临时目录使用统一名称，避免中文文件名编码差异影响配套文件识别。

检查后可查看原生坐标空间预览、缩放平移、点击问题定位，按级别、类型或要素 ID 筛选明细，并下载全部四份报告。无底图，不使用在线地图资源，预览不自动重投影。无位置的问题仍显示在明细中。

网页服务仅绑定 `127.0.0.1`，用 Python 标准库提供 HTTP，不引入 Web 框架。文件只传给本机进程，在临时目录检查，不修改原文件。停止服务（终端 Ctrl+C）会等待当前任务结束并清理临时文件；请在停止前下载需要保留的报告。上传文件总量限 180 MB（HTTP 编码请求上限 256 MB）；服务一次执行一个检查任务。空间预览最多抽样显示 2,000 个图斑，网页明细及筛选仅覆盖前 2,000 条问题；统计、报告及矢量导出仍基于全部检查结果，不因预览限额减少检查。关闭网页不会取消已开始的检查；页面刷新后需重新上传。

## 安装与使用

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
pytest -q
vectopo-check check tests/data/sample.gpkg --layer sample --out report
vectopo-check check input.gpkg --layer parcels --id-field parcel_id --config config.yaml --out report --fail-on error
```

`tests/data/sample.gpkg` 由 pytest 自动生成，不提交二进制。开发环境已有 `.venv`，可直接使用 `.\.venv\Scripts\vectopo-check.exe`。

退出码：正常完成为 0；`--fail-on error` 遇到 ERROR 或 `--fail-on warn` 遇到 WARN/ERROR 为 1；输入、配置、输出失败为 2。默认 `--fail-on never`。CLI 参数覆盖配置，未传参数使用配置默认值。多图层 GPKG 必须指定图层。

## 配置

参见 [config.yaml](config.yaml)。缺字段用默认值，未知字段、负容差及无效规则会被拒绝。距离、长度和宽度均为输入 CRS 单位，面积为其平方；不会自动重投影。

`rules.enable` 精确控制输出类型，包括同一模块产生的多个类型。`multipart_policy`、`hole_policy` 支持 info/warn/error。可覆盖坐标范围、所有检查阈值、索引切换阈值、位置去重精度、候选对上限和每要素报告上限。

候选对按行号稳定排序，超过 `max_pairs` 时使用固定种子的单遍蓄水池抽样；报告记录 `sampled`、总候选数和实际检查数。流式索引查询不会一次性分配全部候选对，但计数仍需遍历全部候选。每要素截断后保留项带 `truncated=true`。问题按要素 ID、类型、位置排序，ID 由此顺序确定，不含时间戳。

## 输出

网页额外提供 **带问题字段 SHP（ZIP）** 和 **处理后地块 SHP（ZIP）**：前者导出检查标记图层，后者仅导出 processed 中已接受的处理结果，不包含 staging/candidates。点击时按需生成并缓存，不增加日常检查耗时。ZIP 包含 `.shp/.shx/.dbf/.prj/.cpg`（未知 CRS 时无 `.prj`），编码 UTF-8。SHP 字段名改为唯一 ASCII 短名（最长 10 字符），对照保存在 `fields.json`；超过 254 字节的文本按完整 UTF-8 字符截断，完整属性按同行顺序保存在 `attributes_full.json`。需要精确属性类型、多图层和不截断文本时仍使用 GPKG。

相同文件会话、图层、配置和处理模式的重复点击会恢复同一任务或复用已完成结果，不重复计算。重新上传文件（包括上次的处理后 SHP）会创建新任务。输入中的 geom、fid 等属性会原样保留，GPKG 内部几何和主键使用不冲突的名称；若部分导出失败，页面会明确显示错误，而不只显示“完成”。

网页版另外提供“完整检查报告 ZIP”和“带问题字段的矢量 GPKG”下载。GPKG 保留原几何、属性和 CRS，在 `checked_features` 图层新增：`topo_issue`（中文问题类型，多个用分号分隔）、`topo_types`（类型代码）、`topo_count`（关联问题数）、`topo_level`（最高级别，无问题为 NONE）、`topo_state`（检查范围提示）。字段已存在时追加数字后缀，不覆盖原字段。重叠、重复等面间问题会标记双方图斑；空隙是无对应图斑的区域，仍见问题 GeoJSON。字段反映已启用规则和报告中保留的问题，未发现问题不代表全部拓扑合格。ID 字段重复时仍可下载检查报告，但需选择唯一字段或留空重新检查后才能导出带字段矢量；导出失败不会影响报告下载。输出格式采用 GPKG 以避免 SHP 字段名和文本长度限制。

* `report.json`：完整模型、问题、计数、CRS 和抽样信息。
* `issues.geojson`：有位置的问题；context 为 JSON 字符串。坐标保留输入 CRS，使用旧式 GeoJSON `crs` 声明，非严格 RFC 7946，经纬度专用查看器可能无法正确显示投影坐标。
* `report.csv`：要素 ID、类型、级别、说明、x/y、建议及关联 ID；UTF-8 BOM。
* `summary.txt`：计数、CRS 和抽样提示。

无几何/位置的问题仅保留在 JSON/CSV 中。NaN/Infinity 在 JSON context 中写为 null，具体原因保留于 `reason`，输出严格合法 JSON。

## 算法及已知限制

仅支持面要素和仅包含面的 GeometryCollection。不做跨图层、线点、NESTED、共边长度检查。检查器统一提供独立 `check()` 接口，注册表为后续扩展入口。

重叠必须超过交集面积阈值，共边不会报 OVERLAP；尖角统一 CCW 后计算内角，凹角不按锐角误报。Shapely 读取时会自动闭合环，因此原文件未闭合环未必还能被识别。

地理坐标系的量纲相关结果仅供参考，建议先投影；报告 `is_geographic=true`，SMALL_AREA/SLIVER/SHARP_ANGLE 为 WARN 并附复核提示。坐标合理范围采用配置中的粗略边界，可能不适合特定投影；未知 CRS 使用投影默认值并提示。`id_field` 建议唯一且非空，否则相同 ID 的问题会按同一要素去重、截断；字段不存在时按规格回退行号。

GAP 基于凸包近似，需人工复核：有意保留的洞、覆盖范围边缘凹陷及相离面之间区域都可能被报告。面积超过阈值的细长空隙分类为 SLIVER，feature_id 为 `__gap__`；其他空隙为 GAP。独立 SLIVER 规则也会检查空隙，enable 按最终类型过滤。

原规格对 `make_valid` 存在冲突，默认检查流程不调用几何修复或 make_valid；追加的显式修复流程按下节规则生成副本。无效面仍执行单要素检查和重复几何检测；无法安全相交的无效面不参与 OVERLAP/SLIVER 和 GAP union。因此这类数据的面间结果可能不完整，应先依据单要素问题人工复核。单检查器异常会成为对应类型 ERROR；逐对执行以隔离面间异常。

同类型同要素同位置（默认六位小数）按规格去重并保留最高级别，可能合并同位置不同关联要素的问题。不同类型可共存；同位置 INVALID_HOLE 优先于 TOUCH_ISLAND。拓扑错误兜底可能将无效洞同时报告为 SELF_INTERSECTION，保留 explain_validity 原文。

## 修复策略与人工复核

CLI 增加可选 `--repair`；网页中展开“可选修复与人工复核”，勾选“生成修复候选”。不启用时保持原检查行为。检查报告和 `checked_features.gpkg` 始终描述原始输入；修复结果仅在独立修复文件中。

```powershell
vectopo-check check input.gpkg --layer parcels --config config.yaml --repair --out report
```

```yaml
repair:
  small_area_strategy: longest_shared_edge  # 或 largest_area
  auto_fill_max_area: null  # null 关闭自动填洞；数值为允许填充的单洞面积上限
  fill_if_same_class: false # true 时额外要求同类邻面，不能跳过面积上限
  class_field: null        # 例如 land_type；碎面自动接受需要该字段存在且同类
  touch_max_area_change: 1.0
  touch_max_area_ratio: 0.01
```

| 问题 | 候选动作 | 自动 / 人工边界 |
| --- | --- | --- |
| INVALID_HOLE | 外环减去洞区域的并集，越界部分裁至外环 | 结构重建一律 pending_review；外环/洞环自身无效、重建出现意外 MultiPolygon 或仍不合法时 rejected，保留原面 |
| HAS_HOLE | 删除满足条件的洞环 | 默认关闭；配置 `auto_fill_max_area` 后允许小洞填充；`fill_if_same_class=true` 额外要求 `class_field` 非空且所有与洞有正长度共边的其他面同类，必须至少有一个同类邻面。若填洞覆盖其他要素，图层复检仍会回退，分类一致不豁免重叠规则 |
| TOUCH_ISLAND | remove_touching_hole；移除与外环相交的洞，洞洞相交时选择面积较小者 | 面积绝对变化和变化比例均不超阈值才可自动接受；任一超过时强制 pending_review。洞洞等面积时按洞序号选择。仅“距离接近”不自动处理；候选仍不合法时 rejected |
| SMALL_AREA | 将有效单部件碎面与一个共边邻面 union | STRtree 查询邻面，要求交叠面积为零、共边长度大于 distance；策略分别按最长共边或最大邻面面积选择，平局按 ID 排序。配置的分类字段非空且源/目标一致才可自动接受，否则 pending_review。目标原属性保留，源属性保留在 staging 原始行，其他属性差异需要业务确认 |

**执行与回退**：按整个图层的 INVALID_HOLE（同模块包含 HAS_HOLE 规则填充）→ TOUCH_ISLAND → SMALL_AREA 顺序执行。修复独立于 `rules.enable`，复检不受禁用规则、报告去重或每要素截断影响。已接受变化会更新工作图层和空间索引；待审核的源/目标锁定，不参加后续自动合并。

每次候选执行后比较修改前后所有规则的问题类型/级别/涉及要素计数，对自相交、接触、尖角、重复点、短线段额外比较问题位置，并检查已有重叠面积是否增长。任何新类型/级别/关联对、新增同类问题数量或重叠增长都会令该笔修改 rejected 并原子回退。复检异常或候选对超过 `max_pairs` 时无法确认全量结果，强制 pending_review，不以抽样结果自动批准。此复检不是数学完备的拓扑等价证明：聚合类问题的组成变化但总数不变不一定被识别，因此候选仍保留供人工核对。

**其他强制复核条件**：未知 CRS 或地理坐标系、非法洞结构重建、接触洞面积超保护阈值、碎面分类属性不能确认一致。空几何、非有限坐标、GeometryCollection、碎面中的 MultiPolygon 不自动修复。非唯一 ID 或以 `__` 开头的 ID 不进入修复，需改用唯一字段或默认行号。

**输出及几何列**：

* `repair_report.json`：动作、状态、原因、源/目标 ID、面积/洞数/类型变化、候选差异及回退原因。状态为 `auto_accepted` / `pending_review` / `rejected`；rejected 的有效 diff 为零，候选差异另列。合并 diff 附源和目标面积、共边长度与面积守恒误差。
* `repair_staging.gpkg` 的 `staging` 图层保留所有输入属性和源行，只应用 auto_accepted。`geom_original` 为原几何 WKT 文本，`geom_repaired` 为原生 GPKG 几何列（GIS 客户端读入时常显示为 geometry）；这是 GPKG 单图层仅注册一个几何列的兼容实现。原有同名属性追加 `_source`，不会覆盖。已合并源行的修复几何为 NULL，`repair_status=merged`，`merged_into` 指向本次合并目标；可沿后续合并记录追踪最终目标。NULL 表示合并消耗，不能当作新空几何错误。
* 同文件的 `candidates` 图层按事务保存候选几何、原几何 WKT、源原几何 WKT、动作及状态。合并候选的 `geom_original` 是目标面，`source_original` 是碎面。pending_review 绝不自动进入 staging 成果；rejected 不应用。没有候选时不创建该图层。

建议在 QGIS/ArcGIS 中同时查看 staging 和 candidates，按 status 筛选，核对面积变化、分类、相邻要素和保留的源属性，再人工决定是否采用。当前版本提供审核数据，不提供网页“批准并写回”功能。`--fail-on` 仍依据修复前检查报告；自动接受不等于全部问题已修好。全图层逐事务复检偏保守，复杂或大型图层会明显慢于纯检查。

独立调用三个模块的统一接口：`repair(feature_id, geom, attrs, cfg) -> RepairResult`。碎面合并和按分类填洞需要调用方设置 `cfg._repair_context = RepairContext(gdf, ids)`；引擎会自动设置并刷新。`RepairResult.repaired_geom` 在碎面合并时代表合并后的目标面，`target_feature_id` 指明目标，调用方必须按原子事务同时消耗源面。

## 测试命令

```powershell
pytest -q
vectopo-check check tests/data/sample.gpkg --layer sample --out report
```

测试覆盖 15 种类型、合法共边、内角方向、空/非法坐标、规则过滤、异常隔离、抽样截断、稳定输出、四份报告和 CLI 退出码。

## 五项常用检查与性能

网页仅保留岛洞（HAS_HOLE：识别面内环）、触岛（TOUCH_ISLAND：洞与外环或洞洞接触/近邻）、多部件（MULTIPART）、小面积（SMALL_AREA）、重叠面（OVERLAP）。共享边不当作重叠。无洞面直接跳过洞相关处理，单 Polygon 直接跳过多部件检查。默认不运行自相交、尖角、重复点、短边、非法洞结构校验、细长面、凸包空隙等检查。若从旧 YAML 导入网页，仅保留五项中的已启用项；CLI 仍按 YAML 明确启用的全部类型运行。

大图层建议先不勾选修复：修复为保障回退，仍需逐事务全规则复检，不随精简检查缩减。EPSG:4326 的面积阈值单位为平方度，默认 min_area=1 可能将大量图斑报为小面积；如需按平方米判断，请使用投影坐标数据，或自行设置与数据量纲一致的阈值。程序不会为了提速偷偷改变阈值或跳过选中的图斑。

岛洞、多部件、小面积问题的 GeoJSON 几何使用代表点定位，完整面保留在带字段的矢量导出中；重叠问题继续保留交叠面。候选配对采用单遍查询，逐对检查复用几何数组；导出字段的完整性标记只统计一次。网页显示当前阶段和处理数量，避免长任务无进度反馈。

## Streamlit 云端部署

仓库根目录提供 `streamlit_app.py` 和 `requirements.txt`。在 Streamlit Community Cloud 选择 GitHub 仓库、分支和入口 `streamlit_app.py`，Python 选择 3.11 或 3.12。无需配置密钥。

本地运行：`python -m pip install -r requirements.txt`，然后 `python -m streamlit run streamlit_app.py`。

各浏览器会话使用独立临时目录。云端版本会将上传文件传输至部署服务器；服务重启或会话结束后成果不保证保留，请及时下载。云端内存和 CPU 有限，大图层建议使用本地网页版。SHP 点击原版下载区的链接后按需打包；检查报告、问题字段、修复对照与孤立小图斑删除规则与本地版共用同一引擎。

云端与本地共用原版 HTML 工作台，保留左右分栏、统计卡片、地图缩放平移、问题筛选和全部下载入口。处理进度条按读取、检查、修复和导出阶段加权，阶段内按已处理图斑/候选对更新，并非剩余时间预测；完成后显示 100%，失败时保留最后进度并提示错误。

云端上传与下载使用 1 MiB 分块传输，避免约 20 MB 文件一次性编码进入组件消息导致无响应。上传区显示已传输 MB 和百分比；单次请求超过 120 秒显示网络/重试提示。每个会话的分块目录隔离，拒绝乱序、超限和不完整文件。
