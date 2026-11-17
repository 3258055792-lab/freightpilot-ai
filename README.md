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
浏览器模拟前端已加入，但尚未连接 Python 原型；真实订单、工作流调度、服务端持久化审批与追踪尚未实现。计划详见 [协作说明](docs/TEAM.md) 与 [迁移记录](docs/MIGRATION.md)。

## 浏览器模拟工作台（FP-01 修复候选）

源码位于 `frontend/workbench`，与 Python 原型是两套独立合成数据。此候选修复 UI-01 未保存输入被旧值覆盖及 UI-02 小数箱数误提取。进入该目录运行 `npm test`；Mac 用 `python3 -m http.server 4173 --bind 127.0.0.1 --directory dist`，Windows 用 `py -m http.server 4173 --bind 127.0.0.1 --directory dist`，浏览器打开 http://127.0.0.1:4173 。无需安装 npm 依赖。

使用独立浏览器配置、单标签、合成数据。德国 3CBM/400kg 示例为 1260 CNY，不是 Python 示例的 2460 USD。审批是浏览器本地模拟，没有服务端鉴权或持久化幂等；CSS 使用 Google Fonts 外部字体。

固定版本、哈希与独立复测证据见 [候选说明](docs/FP01-UI-FIX.md)。尚未合并或发布，验收通过仅限已测模拟路径。
