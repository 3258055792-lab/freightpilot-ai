# FP-02 审批状态草案

状态：供负责人审阅，未实现。初稿：2026-11-16；本节增补：2026-11-17。
基线：本地 HEAD `6be5371454c4a8054cb1d9ad9e802cdb6b87666a`。原始实现边界依据该基线的本地源码与文档；协作方报告见第 10.4 节，尚未在本仓库独立验证。
负责人：业务审批负责人待指定；接口实现建议 A、恢复与并发建议 B、审批展示与验收建议 C，均待成员确认（现有建议见 TEAM.md）。涉及范围：本文件；后续业务服务及存储方案尚未确定。

## 一、当前已实现的边界

下列路径相对仓库根目录，行号对应上述基线，均为源码静态核对，并非本次测试结果。

| 已实现行为 | 证据 |
| --- | --- |
| Harness 每次调用先复制输入、核对分类候选，再请求人工确认；将解析出的来源 SHA 固定到本次输入 | [freight-plugin.mjs](../prototype/harness/freight-plugin.mjs#L78)，78–104 行 |
| 缺失审批服务、执行上下文或取消信号，审批抛错，返回值非 `allowed-once`，或者批准后已取消，均阻止进入试算（fail-closed） | [freight-plugin.mjs](../prototype/harness/freight-plugin.mjs#L85)，85–89、121–127 行 |
| 模型传入额外确认字段被拒绝；导出辅助入口禁止直接计算；有效批准后仅在该次调用中执行 Python | [freight-plugin.mjs](../prototype/harness/freight-plugin.mjs#L28)，28–31、72–77、124–129 行 |
| 结果携带本次分类确认及审阅输入，范围为 `this_call_only`；确认不等于试算业务成功 | [freight-plugin.mjs](../prototype/harness/freight-plugin.mjs#L127)，127–129 行；[freight_tools.py](../prototype/freight_tools.py#L195)，195–200 行仍可能返回 blocked |
| Python 直接接口不是身份授权边界；来源选择必须唯一，读取来源版本的规则记录；输出仍不可用于正式报价 | [freight_tools.py](../prototype/freight_tools.py#L70)，70–76、165–194 行；[README](../README.md#L14) |

现实现状不是下文状态机：适配器中只有内存局部变量，没有持久化审批记录、跨调用消费台账、原子幂等锁或跨重启恢复协议。Python 数据库中的 `user_rule_confirmations` 是来源规则记录，不是某次业务调用的授权凭据（freight_tools.py 179–182 行）。README 末段及 MIGRATION.md 的“当前限制”明确将持久化审批等列为未来能力。已有测试源码包含重复审批、快照与取消场景（freight-plugin.test.mjs 183、203、217 行），本次没有运行测试，也不能据模拟服务测试声称真实审批 UI 已联调。

## 二、建议审批记录和职责

以下均为建议设计，不能用于声称当前系统已具备这些能力。

由未来业务服务保存审批聚合，Python 仅执行确定性计算，Harness 作为人工交互适配器。最小记录：`approval_id`、业务主体/租户、`scope`、创建人、指定审批人或角色、可信通道及请求关联号、状态、单调递增 `revision`、规范化输入及哈希、来源 SHA、分类证据、规则快照哈希、计算器版本、创建/到期/决定时间、决定人、理由、消费操作 ID、结果引用和审计事件。

服务从认证上下文核验实际操作人及权限，不信任模型提供的 approver、confirmed 或群聊文字。创建人不默认拥有审批权限。审批通道必须展示商品、候选、所选类别、来源、体积重量、服务项和授权范围，并把人工决定绑定记录及 revision。通道不可用、身份不可验证、存储不可用或快照不完整时均不执行。审批负责人及代理/回避规则由负责人决定；Pachat 用于讨论和任务线索，不作为默认审批通道。

## 三、建议状态与合法迁移

| 当前状态 | 合法下一状态 | 条件及责任 |
| --- | --- | --- |
| DRAFT | PENDING、CANCELLED | 创建人提交完整快照及指定审批人，或撤销 |
| PENDING | APPROVED、REJECTED、CANCELLED、EXPIRED、INVALIDATED | 授权审批人批准/拒绝；授权发起人取消；服务按到期或版本变化失效 |
| APPROVED | CONSUMING、CANCELLED、EXPIRED、INVALIDATED | 服务原子检查未消费、身份、有效期、快照及 revision 后认领；认领前可取消/失效 |
| CONSUMING | CONSUMED、FAILED、IN_DOUBT | 服务提交可追溯结果；确认执行失败；无法判断是否完成时进入待核对状态 |
| IN_DOUBT | CONSUMED、FAILED | 恢复协调器依据同一操作 ID 的持久化证据核对，不能人工猜测或重新消费 |
| CONSUMED、FAILED、REJECTED、CANCELLED、EXPIRED、INVALIDATED | 无 | 终态；重办必须创建新审批 ID 并引用旧记录，不恢复旧批准 |

DRAFT 与 PENDING 不得直接执行；REJECTED/CANCELLED 不得靠重试、换调用 ID 或群聊确认复活。CONSUMING 后取消请求记录为审计事件并返回“执行中，无法保证取消”，不伪造 CANCELLED；认领和取消并发时以数据库原子提交的先后决定结果。IN_DOUBT 冻结授权，不允许新执行。FAILED 表示该授权已耗尽，业务重试需新审批；同操作结果查询不算新执行。

## 四、一次性批准、幂等与并发建议

1. 批准只授权指定 scope 下的一次执行尝试，不授权整场会话、其他商品或后续订单。APPROVED 不等于 CONSUMED，更不等于正式报价获批。
2. 决定请求携带幂等键和预期 revision：同键同载荷返回原结果，同键异载荷拒绝；不同决定竞争仅一个条件更新成功。禁止批准覆盖拒绝或取消。
3. 消费操作具备稳定 `operation_id`，数据库对 approval_id 的消费关系设唯一约束；在同一事务内比较状态/revision、检查快照和有效期、绑定操作并进入 CONSUMING。两个进程争用仅一方取得执行权；失败方查询既有操作，不再启动计算。
4. 相同 operation_id 的重复提交仅返回持久化状态/结果，不能再次执行；不同 operation_id 使用同一批准必须拒绝。不同业务请求不得借更换幂等键复用批准。
5. 若执行器支持操作幂等，结果按 operation_id 持久化并原子结束审批。若执行器不支持，不能宣称数据库事务提供端到端 exactly-once；超时或失联进入 IN_DOUBT。未来订舱等外部副作用还需要外部幂等键、状态查询及补偿协议，超出当前分类试算范围。

## 五、输入和数据版本绑定建议

规范化必须有版本规范：十进制使用确定性字符串，明确默认值、单位及服务项；商品限定词不得丢弃。审批前验证数值与业务边界，生成包含全部有效输入、分类证据、来源 SHA、规则快照和计算器版本的不可变快照及哈希。不得只绑定文件名、类别或调用 ID。

任一有效输入、来源内容/规则、分类证据或计算语义变化，都不能沿用旧批准；旧快照不原地修改，新建记录重新审阅。建议采用保守策略：批准后来源被撤销、规则或计算器版本更新时使未消费记录 INVALIDATED；是否允许继续使用仍有效的历史版本待负责人决定。当前固定来源 SHA 不等于已固定规则记录、计算器版本或历史数据的完整不可变性。到期与撤销检查由服务端在消费认领事务中实施，不能仅在审批展示时检查。

## 六、重启和异常恢复建议

数据库是状态真源，不能把内存缓存、聊天历史或 Harness 回调当恢复依据。启动后读取未终结记录：PENDING 保持等待并核验通道关联；APPROVED 重验到期、来源与权限；CONSUMING 检查稳定 operation_id 的执行/结果记录。存在可验证结果则完成 CONSUMED，确认未执行且协议允许恢复同一操作时才恢复执行，否则置 IN_DOUBT。不得重启后自动创建新调用并重复使用批准。

持久化审计事件与状态变更应同事务；向通道发送请求/通知可用 outbox 和关联 ID 去重，不能靠通知发送成功证明批准成立。审批通道丢失后不能自动批准；原请求无法安全关联时失效并重新申请。具体数据库、事务隔离、故障注入与恢复运维责任仍待选定。

## 七、分类确认与正式报价审批

当前 `allowed-once` 仅确认本次商品分类使用和待审核试算（freight-plugin.mjs 118、129 行）。Python 返回 `usable_for_quote=false`、`total_payable=null`（freight_tools.py 190–194 行）；分类已确认仍可能试算 blocked，不能将其转换成可发送客户的总价。

建议将 `classification_trial`、`formal_quote`、客户确认和订单操作拆为不同 scope/独立记录。正式报价必须另有有效运价、完整附加费、币种处理、报价版本与审批人审核；客户确认还需独立证据。分类批准不能自动迁移为正式报价批准，报价批准也不能自动代表客户接受或订舱授权。正式报价流程尚未实现，本草案不补造其完成状态。

## 八、后续实现验收条件（本次未执行）

- 所有合法迁移成功，所有未列迁移拒绝并保留原状态；拒绝、取消及失效终态不因重试或重启复活。
- 缺审批通道/身份/存储、拒绝、异常结果、审批中取消及批准后执行前取消均不能启动计算；通过真实 Harness UI 验证展示与关联，另列模拟测试结果。
- 同请求重复提交、同键异载荷、两审批人竞争、两消费者并发及批准与取消并发均有确定结果；最多一个操作获得执行权。
- 商品、体积重量、服务项、来源 SHA、规则和计算器版本任一变化导致拒用旧批准；多来源不得隐式选择“最新”。非法数值不得先批准后绕过校验。
- 在批准落库前后、认领后执行前、执行后结果落库前、结果提交后响应前分别模拟崩溃：恢复到可证明状态或 IN_DOUBT，不能重复消费或静默丢失决定。
- 相同操作重试返回相同已保存结果；不同操作复用授权被拒绝；无幂等执行器时明确未知状态，不宣称 exactly-once。
- 审批人越权、跨主体复用和伪造模型确认字段均拒绝；审计可关联快照、决定、消费和恢复证据。
- 试算结果无论分类是否确认均保持非正式报价边界；现有回归能力不退化。真实运价、客户和船公司链路另行验收，不由合成数据证明。

## 九、待负责人决定

1. 指定业务负责人、实现与评审人，以及谁有分类审批、正式报价审批、取消和恢复核对权限；是否允许自审？
2. 选择可信审批通道、身份认证及回调关联方案；真实 Harness UI 的可验收版本与环境是什么？
3. 批准有效期、历史来源继续使用政策、规则/计算器升级失效策略及是否要求最新有效版本。
4. 选择持久化存储、事务/幂等实现、结果留存及 IN_DOUBT 处置责任与时限；FAILED 后重新审批是否接受。
5. 明确正式报价完整性要求、客户确认凭据和与后续订单授权的分界，再确定实现范围。

本草案尚待负责人评审；本次仅交付设计文档，未修改业务代码、未运行测试。上述验收条件是未来实现的目标，不是本次已通过的结论。

## 十、对接契约补充（建议，未实现）

本节将现有状态机草案细化为 FP-01/FP-03 可讨论的字段绑定、请求重放与证据约定。路径、字段名、状态响应和错误码均是建议，不代表仓库已有对应 API。范围仍限于合成数据上的分类试算和本地模拟，不包括真实报价、订舱、付款或其他外部副作用。

### 10.1 记录与快照绑定

| 字段 | 建议含义及校验 |
| --- | --- |
| `case.id`、`case.version` | 由业务记录服务确定。版本单调递增；审批创建及消费认领时均核对权威当前版本。 |
| `approval_id`、`revision` | 由审批服务生成。`approval_id` 标识审批，`revision` 标识其状态/决定版本；它与 `case.version` 是不同版本轴。人工决定和消费都需带预期 revision 做条件更新。 |
| `source.bytes_sha256` | 服务端对实际读取并参与处理的来源字节计算；不可采信请求方自报哈希。对 JSON 源应明确哈希覆盖的原始字节范围。 |
| `source.declared_sha256` | 来源内容中自带的声明值，单独保存为来源数据；不能当作实际字节哈希或身份凭据。只有在格式规范定义其覆盖范围时才可验证，不能默认它与 `bytes_sha256` 相等。 |
| `snapshot_hash` | 服务端对有版本的规范化输入快照计算，覆盖 case ID/版本、有效输入、来源字节哈希、分类证据、规则快照及计算器版本。哈希算法和规范化版本写入协议。 |
| `operation_id`、`request_digest` | 调用方为一次逻辑执行生成稳定 operation ID；服务端计算摘要，覆盖 case ID/版本、approval ID/预期 revision、scope、snapshot hash、action 和所有执行参数。重试、查询、恢复沿用同一 operation ID。 |

快照创建时应从权威来源读取 case 当前版本；消费认领事务中再次核对。即使版本失效通知尚未处理，版本已变化也必须拒绝旧快照。对已消费记录保留当时事实，不回写成失效。原草案关于输入或来源变化后重新审批的规则继续适用。

### 10.2 建议请求与响应

下面的路由和字段是讨论用示例。占位哈希不是有效摘要，也不构成已实现接口承诺。

创建审批：`POST /approvals`

```json
{
  "idempotency_key": "create-case-17-v3",
  "case": { "id": "case-17", "version": 3 },
  "scope": "classification_trial",
  "source_ref": "synthetic-source-1",
  "input": { "product_name": "合成样例", "volume_cbm": "2", "weight_kg": "100" }
}
```

服务端校验 case 版本和来源后生成 `approval_id`、`revision`、不可变快照与哈希；请求体不得指定批准人、决定状态或服务端校验结果。读取审批应返回同一权威记录、当前 revision、有效期及权限允许查看的审阅快照。

建议响应：

```json
{
  "approval_id": "approval-17",
  "revision": 1,
  "state": "PENDING",
  "case": { "id": "case-17", "version": 3 },
  "source": {
    "bytes_sha256": "sha256:<source-bytes>",
    "declared_sha256": null
  },
  "snapshot_hash": "sha256:<snapshot>"
}
```

人工决定：`POST /approvals/{approval_id}/decisions`

```json
{
  "idempotency_key": "decision-17-1",
  "expected_revision": 1,
  "snapshot_hash": "sha256:<snapshot>",
  "decision": "APPROVE",
  "reason": "已核对合成样例"
}
```

服务端从独立认证上下文取得决定人、权限和可信通道记录，并持久化这些证据；不得接受模型或请求体自报的 `actor_id`、`confirmed` 或审批状态。拒绝、撤销及批准都绑定指定 approval revision 和 snapshot hash。

建议响应：

```json
{
  "approval_id": "approval-17",
  "revision": 2,
  "state": "APPROVED",
  "decision_record": {
    "id": "decision-17",
    "actor_type": "human",
    "actor_id": "authenticated-principal",
    "channel_event_ref": "event-17",
    "snapshot_hash": "sha256:<snapshot>"
  }
}
```

执行模拟：`POST /operations`

```json
{
  "operation_id": "op-17",
  "approval_id": "approval-17",
  "expected_approval_revision": 2,
  "case": { "id": "case-17", "version": 3 },
  "snapshot_hash": "sha256:<snapshot>",
  "action": "simulate_classification_trial"
}
```

执行器只使用已批准快照，不接受额外未审批参数。操作记录至少绑定 `operation_id`、服务端 `request_digest`、`approval_id`/revision、case ID/version、snapshot hash、执行者、终态及结果引用/哈希。操作状态（如 `SUCCEEDED`）与审批状态（如 `CONSUMED`）分开表达；模拟结果仍须保持 `usable_for_quote=false`，不得被描述成正式报价。

建议成功响应：

```json
{
  "operation_id": "op-17",
  "request_digest": "sha256:<request>",
  "state": "SUCCEEDED",
  "approval_state": "CONSUMED",
  "result": {
    "record_id": "result-17",
    "result_hash": "sha256:<result>",
    "usable_for_quote": false,
    "total_payable": null
  }
}
```

重放约定：

- 同一 `operation_id`、同一服务端摘要：返回原操作记录和已保存结果，不再次执行；客户端也可查询 `GET /operations/{operation_id}`。已存在操作的重放先解析该操作，不能因其首次执行改变了 approval revision 而误创建第二次操作。
- 同一 `operation_id` 但摘要不同（包括换 approval ID/revision、case 版本、snapshot 或 action）：拒绝并返回明确冲突；原记录不变。
- 不同 `operation_id` 试图消费同一一次性审批：拒绝。拒绝、取消、过期或失效的审批保持终态；重办需新 `approval_id`。
- case 当前版本与快照不一致：拒绝旧审批；未消费记录按既定规则失效。不得用新的 operation ID 绕过版本检查。
- `IN_DOUBT` 时只查询或恢复同一 operation ID；不能把未知结果当成失败并新建操作。

### 10.3 权威决定与执行证据

| 要证明的事实 | 权威证据 | 不足以证明的内容 |
| --- | --- | --- |
| 人工批准/拒绝 | 服务端认证授权后的持久决定记录，关联审批人/主体、approval ID、revision、snapshot hash、决定时间及可信通道事件引用 | 模型自述、群聊确认、前端按钮状态或未认证回调 |
| 模拟执行成功 | 执行器持久结果记录，关联 execution/result ID、operation ID、request digest、approval ID/revision、case ID/version、来源与 snapshot hash、执行者、终态及结果引用/哈希 | HTTP 200、前端日志、模型“已完成”文字、仅有 CONSUMING 状态 |

审计链应能从 `operation_id` 找到获准快照、已认证的人类决定以及执行器结果。若效果可能已经发生但没有可验证的持久结果，状态为 `IN_DOUBT`；不得报告成功或释放审批供另一操作使用。知晓 ID 不等于有权读取记录，读取、重放和恢复仍需主体/租户授权。

### 10.4 原子边界与文案校正

若本地模拟效果、一次性授权消费、结果记录和审计事件位于同一数据库，可在一个事务里完成版本校验、唯一消费绑定、效果写入及成功结果提交；事务提交后才报告模拟成功。效果和结果不在同一原子边界时，超时或提交结果不明则保留同一 operation ID 并进入/呈现 `IN_DOUBT`，通过权威记录查询；不得承诺跨系统 exactly-once。

关于金额和审批的产品文案，应准确区分计算与状态决定。FP-01 在群内回复 #171 报告：固定候选 `FP01-UI-FIX-20261117` 的 `core.js` 在 `compare()`（第 30–31 行）预计算金额，`requestApproval()`（第 37 行）将金额和规则锁入 snapshot，`decide()`（第 38 行）只改变状态并写本地日志。按该候选的报告，文案应为“申请时已预计算金额并锁入 snapshot，审批通过后展示试算结果”，不得写成“批准后才计算金额”。这是协作方对该固定前端候选的说明，本仓库未独立核验；它不证明后端、真实模型接入或恰好一次执行已经实现。后续版本须按实际代码重新核验。

本节是接口讨论稿；上述字段绑定、端点、持久化证据和事务边界均未实现、未联调、未测试。原第八节验收条件仍需在实现后逐项验证。
