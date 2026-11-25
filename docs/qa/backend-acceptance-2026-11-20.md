# FP-03 本地合成审批后端独立验收

2026-11-20，北京时间。固定候选 `3e5b3416d8576dd30bcaba557c1a95cd2432ece6` 的专项测试 **10/10 通过**，独立 HTTP 验证 **5/5 组通过**。可复现脚本整理后，在新的隔离输出目录复跑五组，退出码 0，取得 **32 次 HTTP 观测**。

本轮核对既有原型的模拟授权和持久结果。结果始终为 `usable_for_quote=false`、`total_payable=null`。业务工作台与后端联调、真实身份、货运试算及外部业务接入仍需分别验收。

## 固定证据

- 候选由固定提交的 `git archive` 解包，保持产品源码原字节。`prototype/approval_service.py` SHA-256 为 `ba47305156ffdcac79713709249c8d8bad70d8b067b4e0b29cb7f54f577d04b6`。
- 上游命令为 `python -B -m unittest discover -s prototype/tests -p test_approval_service.py -v`，在候选根目录执行。实际结果 `Ran 10 tests in 13.386s`、`OK`、退出码 0。输出含四条 HTTPError 隐式清理的 ResourceWarning，断言均通过。
- 独立脚本为 [approval-http-regression.py](../../scripts/qa/approval-http-regression.py)。它只接受固定候选目录和独立证据输出目录，先核对服务文件 SHA，再启动真实 CLI 子进程。
- [结构化验收摘要](evidence/acceptance-20261120/backend-summary.json) 保留 32 次 HTTP 的业务请求字段、响应状态、记录 ID/hash、角色标签、持久计数和五组结果。上传范围仅包含此摘要与本报告及 QA 脚本。

## 独立验证结果

| 路径 | 实际结果和权威证据 |
|---|---|
| 身份与伪造字段 | 未认证读取 401；模型和查看者批准各 403；人类身份夹带 `actor_id`、`confirmed` 返回 400。原审批维持 PENDING，决定、操作、效果、结果均为 0 行。 |
| 创建重放 | 同键同体返回原审批 ID 和 `replayed=true`；同键换商品为 `409 IDEMPOTENCY_KEY_REUSED`。审批仅 1 行。 |
| 不同操作并发与持久重放 | 两个不同 operation ID 同时竞争一个批准，分别返回 200、409。终止真实进程并重启后，同操作返回原 result ID/hash，效果和结果查询成功。更换 snapshot 返回 `409 OPERATION_ID_REUSED`。操作、效果、结果各 1 行。 |
| 拒绝后重启 | 拒绝后终止进程并重启，同创建键仍返回原 ID 和 REJECTED。两个不同 operation ID 均返回 `409 APPROVAL_NOT_APPROVED`，再次批准冲突；无新增效果。 |
| case 与来源变化 | 服务停止时向隔离种子库插入合成来源 B、把 case 更新至 v2 后重启。原批准执行返回 `409 APPROVAL_INVALIDATED`，换 operation ID 返回 `409 APPROVAL_NOT_APPROVED`。审批查询为 INVALIDATED，无新增效果。 |

累计持久记录为 3 条审批、3 条决定、1 条操作、1 条效果、1 条结果。成功、拒绝和失效案例分别保留。四次真实服务启动、三次终止后重新启动，测试结束时服务已停止。独立 HTTP 身份是随机生成的本机模拟身份，令牌仅在进程内存和子进程环境中使用。

## 最小复现

先将固定提交解包到 D 盘独立候选目录。将 `$candidate` 指向该目录，`$evidenceOutput` 指向 D 盘另一个新的输出目录，在 FP-03 仓库根目录执行。

```powershell
python -B scripts/qa/approval-http-regression.py $candidate $evidenceOutput
```

脚本使用标准库、loopback 和固定合成输入，禁用代理、重定向和产品字节码写入。输出目录必须位于候选目录之外；发现已有 `independent.sqlite3` 时退出码为 2，拒绝覆盖。本轮实际验证了该保护，并核对拒绝重跑前后数据库和摘要哈希相同。脚本固定校验当前实现 SHA，未来候选应先核对代码再更新验收脚本。

本地输出保存 SQLite 库、服务诊断输出和结构化摘要，供本机复查。仅将经过筛选的业务摘要纳入仓库。上游原始日志、运行环境路径/PID、数据库和凭据均未纳入交付文件。

## 适用范围与待测

- 来源 B 是独立脚本按已知 schema 注入隔离库的合成夹具，证明旧审批失效检查；工作台第二来源文件与选择 UI 尚待交接。本轮同时改变 case 版本和来源引用，同版本来源字节被篡改未覆盖。
- 新增路径覆盖提交成功或拒绝后真实进程终止与重启，未注入事务提交中的崩溃、响应丢失或磁盘故障。
- 候选把消费、模拟效果、结果和审计放在同一 SQLite 事务，没有 IN_DOUBT 或外部执行器。本次结果仅适用于该原子边界，跨系统恢复仍待实现与验收。
- 上游专项覆盖跨租户访问。独立 CLI 路径采用候选的固定单租户测试身份映射，真实身份提供方、浏览器和前后端联调未测。
- 新脚本与结构化摘要是本次独立证据；开发此前自报的结果没有替代本轮实际输出。没有生成报价、订舱或付款业务结论。
