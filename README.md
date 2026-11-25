# FreightPilot AI

国际货代智能体参赛项目。当前交付是**可本地运行的报价工具原型**，不是完整货代业务系统。

## 已迁入

- Excel 原文存档、来源 SHA 校验及待审核报价导入。
- 商品完整列举项匹配、保留限定词和歧义候选。
- Decimal 基础运费试算、边界/密度检查、币种分离。
- Harness 单次人工确认适配器：拒绝、取消、缺少审批通道时阻止试算。
- 34 项 Python 回归测试、18 项 Node 插件测试；全部使用合成数据。

返回 `usable_for_quote=false`、`total_payable=null`。插件确认仅授权一次分类试算，不是正式报价批准或客户确认。Python 工具本身不是权限边界，未来业务服务必须独立实现授权。

## 结构

```text
prototype/
  freight_tools.py         # 查询、分类、确定性试算
  extract_quote.py        # 固定格式 Excel 提取
  import_quotes.py         # 仅待审核报价导入
  import_workbook.py       # 原始工作簿存档与来源校验
  create_demo.py           # 从零生成合成测试数据
  tests/                  # Python 回归测试
  harness/                # Harness 适配器及 Node 测试
  source/, output/        # 本机生成，不提交 Git
frontend/workbench/       # 独立浏览器模拟工作台及 15 项测试
scripts/                  # 跨平台测试入口
docs/                    # 项目边界、迁移与协作说明
.github/workflows/        # Mac / Windows 自动测试配置
```

## 本机快速开始

安装 Python 3.9+（建议 3.11）及 Node.js 22+。在仓库根目录执行。

Mac：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci --ignore-scripts
.venv/bin/python prototype/create_demo.py
.venv/bin/python scripts/check.py
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci --ignore-scripts
.\.venv\Scripts\python.exe prototype\create_demo.py
.\.venv\Scripts\python.exe scripts\check.py
```

生成器只在首次运行；检测到已有 source/output 就停止，不覆盖数据。测试不需要模型、API Key 或 Pachat。其他 Python 安装位置可用环境变量 `FREIGHT_PYTHON` 指定。

查询示例（Mac）：

```sh
printf '%s' '{"product_name":"塑料收纳盒"}' | .venv/bin/python prototype/freight_tools.py freight_classify
```

运价、商品和模拟规则仅用于回归测试，无商业有效性；17 个合成工作表主要验证分页与多表读取，不表示实现 17 条业务渠道。合成规则记录不是人的真实审批。真实数据只能在仓库外管理，不能将生成器用于真实数据授权。

## 后续 MVP

询价 → 报价草稿 → 独立人工审批和客户确认 → 订单 → 模拟订舱/运输 → 延误 → 通知草稿 → 归档。
浏览器模拟前端已加入，但尚未连接 Python 原型；真实订单、工作流调度、服务端持久化审批与追踪尚未实现。FP-02 另有一个只用于本机合成数据的持久审批 API 骨架（见 [本地审批后端说明](docs/FP-02-backend-minimum.md)），它不是生产审批服务；下方工作台章节描述的联调同样仅限本机合成数据。计划详见 [协作说明](docs/TEAM.md) 与 [迁移记录](docs/MIGRATION.md)。

## 浏览器模拟工作台（FP-01 修复候选）

源码位于 `frontend/workbench`，与 Python 原型是两套独立合成数据。此候选修复 UI-01 未保存输入被旧值覆盖及 UI-02 小数箱数误提取。进入该目录运行 `npm test`；Mac 用 `python3 -m http.server 4173 --bind 127.0.0.1 --directory dist`，Windows 用 `py -m http.server 4173 --bind 127.0.0.1 --directory dist`，浏览器打开 http://127.0.0.1:4173 。无需安装 npm 依赖。

使用独立浏览器配置、单标签、合成数据。德国 3CBM/400kg 示例为 1260 CNY，不是 Python 示例的 2460 USD。审批是浏览器本地模拟，没有服务端鉴权或持久化幂等；CSS 使用 Google Fonts 外部字体。

固定版本、哈希与独立复测证据见 [候选说明](docs/FP01-UI-FIX.md)。尚未合并或发布，验收通过仅限已测模拟路径。

## 本地真实模型工具调用

新增命令行适配器见 [运行与权限边界](prototype/agent/README.md)。已支持本机模型调用Python分类/查询并回传证据。计算在缺少独立人类确认通道时停止；前端与后端审批尚未接通。

### 本机千问客户询价

按上方步骤准备 Python 环境、合成样例和 Node 依赖。在 LM Studio 启动本机服务并加载 qwen3.5-9b-mlx，然后运行 `npm run inquiry`，在终端粘贴客户的自然语言需求，例如“中国海运到巴西，30箱塑料收纳盒，总体积3CBM，总重400kg，请估基础运费”。操作员对照客户原文核对工具提出的输入，输入“确认试算”仅允许本次计算，其他回答停止。终端显示 Python 工具产生的基础运费、来源及待核费用。目前支持合成巴西海运数据。此入口供本机操作员使用。

在工作台页面运行同一流程时，执行 `npm run workbench`，打开 http://127.0.0.1:4178，选择“本地千问询价”。试算请求及决定由本机服务处理，模型由 LM Studio 提供。页面展示本次试算回执；该入口当前不写入订单或持久审批库。

### 工作台草稿改重联调

运行 `npm run workbench` 后从概览选「新建询价」。填写商品名、总体积和总重，在「后端分类试算审批（合成）」中选择「保存为后端草稿」→「申请本版审批」→ 核对后决定。保存时，服务端为该案例分配独立的版本记录。在同一案例把重量从 400 改为 800 kg，点击已有按钮「保存人工修改」，页面会显示旧授权失效。重新申请后由操作员重新决定。「读取服务端状态」可查询 SQLite 中最近一次审批。其他页面或标签页修改后请重新读取、核对字段再保存。

本轮接入范围是案例详情的商品、体积、重量及合成分类审批。使用独立的本机测试角色；千问弹窗、浏览器演示订单与此审批流程仍各自独立。SQLite 默认保存在忽略目录 prototype/output/workbench-approvals.db，可用 FP_WORKBENCH_DB 指定隔离测试文件。前述巴西海运合成 Excel 和审批后端种子来源仍是两份数据。

### 同票 A/B 运价与内部草稿审核

在本机工作台打开「新建询价」，输入 **塑料收纳盒、3 m³、400 kg**。在后端审批卡选择「运价表 A · v1」，保存为后端草稿，申请并批准本版试算许可，点击「生成内部报价草稿」。核对金额及引用后点「申请内部草稿审核」，再决定批准或拒绝。案例页显示的审批用途区分试算和审核。

将选择改为 B，点击「保存人工修改」。旧审批失效，当前版金额须重新试算。用相同流程切回 A；第三版仍需独立许可和草稿审核。预期基础运费依次为 2,460、2,280、2,460 USD。拒绝后的同版重试保留原决定。适用范围是固定巴西海运合成样例，使用本机模拟操作员；独立千问弹窗仍有自己的试算流程。表内资料与 SOP 留在 data/sample/fp01。

在同一案例识别客户原文时，先在「01 / 客户询价」粘贴中国至巴西海运需求，点击审批卡中的「用本地千问识别客户原文」。程序从 LM Studio 获取品名、目的国与运输方式的原文摘录，用现有数量校验提取重量和体积；页面显示 SOP-01 §1 的依据与缺项。补充原文后再次识别，再保存为后端草稿。此入口使用固定合成 SOP，适用于本演示场景。
