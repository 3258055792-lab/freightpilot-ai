# FP-02 本地合成审批后端

这个小服务把 FP-02 契约的一段落到可运行 SQLite API：创建审批、由独立测试人类身份作决定、用一次性授权写入合成效果/结果，并查询持久记录。它只处理 `classification_trial`，不做货运计算或正式报价。

## 安全边界

- 服务只绑定 `127.0.0.1`，没有 TLS、生产身份提供方、浏览器 UI 或远程访问配置。不要把它暴露到局域网或公网。
- 人类、模型、执行器和只读查看者身份由服务启动时的环境变量映射。它们只是本机测试身份，不证明真实人的身份；请求体中的 `actor_id`、`confirmed` 或状态字段会被拒绝。
- 种子 case 和来源都是内置合成字节。创建审批时服务对实际来源字节计算 SHA-256，绑定不可变 source ref/version、case version、输入和模拟器版本到 snapshot。
- 拒绝、终态和快照绑定不能靠重试复活。幂等键按 tenant、请求主体和创建操作作用域；同键同请求返回原审批及当前状态，同键异请求返回 409。首次保存来源快照和审批在一个 SQLite 事务内完成。
- 本地模拟效果、结果、审计和审批消费在一个 SQLite 事务内提交。同一个 operation ID 的重试只返回持久结果；另一 operation 不能消费同一审批。此版本没有任何非事务外部副作用，所以不会伪造 `IN_DOUBT` 或声称跨系统 exactly-once；未来接入外部执行器前必须另做核对/恢复协议。
- 模拟结果永远带 `usable_for_quote=false`、`total_payable=null`；效果记录只是“合成分类试算授权已记账”，没有生成报价、订舱、付款或其他真实业务操作。

## 启动与停止（Windows PowerShell）

在仓库根目录打开 Windows PowerShell，使用 Python 3.9+。在这一个窗口生成四个随机测试凭据，再把服务启动为子进程。后续请求也在同一窗口运行，这样客户端和服务可使用同一组凭据。凭据只保留在进程环境中。

```powershell
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $bytes = New-Object 'byte[]' 32
    foreach ($role in @('MODEL', 'HUMAN', 'EXECUTOR', 'VIEWER')) {
        $rng.GetBytes($bytes)
        [Environment]::SetEnvironmentVariable("FP_APPROVAL_" + $role + "_TOKEN", [Convert]::ToBase64String($bytes), 'Process')
    }
    [Array]::Clear($bytes, 0, $bytes.Length)
} finally {
    $rng.Dispose()
}

$python = & py -3 -c 'import sys; print(sys.executable)'
$serviceArgs = @('prototype\approval_service.py', 'serve', '--db', 'prototype\output\approval-demo.db')
$approvalProcess = Start-Process -FilePath $python -ArgumentList $serviceArgs -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -PassThru
```

服务使用 `127.0.0.1:8765`，请先确保端口空闲。启动时会创建 `prototype/output/approval-demo.db` 和一个合成来源与案例。数据库在 Git 忽略目录中。准备好后，可用 `Invoke-RestMethod 'http://127.0.0.1:8765/health'` 确认服务返回 `environment: synthetic-local`。

使用 `Stop-Process -InputObject $approvalProcess` 停止。要核对重启后记录仍可查询，在同一窗口重复运行 `$approvalProcess = Start-Process ...` 那一行，继续用原数据库和原测试凭据，再运行下面的 GET 请求。若只要初始化数据库：

```powershell
py -3 prototype\approval_service.py init-demo --db prototype\output\approval-demo.db
```

## 最小流程

在启动服务的同一窗口执行以下命令。模型身份请求审批，人类测试身份作决定，执行器身份执行，查看者身份读取记录。每次从头演示使用新的操作编号。

```powershell
$base = 'http://127.0.0.1:8765'
$model = @{ Authorization = "Bearer $env:FP_APPROVAL_MODEL_TOKEN" }
$human = @{ Authorization = "Bearer $env:FP_APPROVAL_HUMAN_TOKEN" }
$executor = @{ Authorization = "Bearer $env:FP_APPROVAL_EXECUTOR_TOKEN" }
$viewer = @{ Authorization = "Bearer $env:FP_APPROVAL_VIEWER_TOKEN" }
$demoId = [Guid]::NewGuid().ToString('N')
$jsonContentType = 'application/json; charset=utf-8'

$create = @{
  idempotency_key = "create-$demoId"
  case = @{ id = 'case-synthetic-001'; version = 1 }
  scope = 'classification_trial'
  source_ref = 'synthetic-source-1'
  source_version = 'v1'
  input = @{ product_name = '合成塑料收纳盒' }
} | ConvertTo-Json -Depth 5
$created = Invoke-RestMethod -Method Post -Uri "$base/approvals" -Headers $model -ContentType $jsonContentType -Body ([Text.Encoding]::UTF8.GetBytes($create))
$approval = $created.approval

$decision = @{
  idempotency_key = "decision-$demoId"
  expected_revision = $approval.revision
  snapshot_hash = $approval.snapshot_hash
  decision = 'APPROVE'
  reason = '本地合成演示'
} | ConvertTo-Json
$decided = Invoke-RestMethod -Method Post -Uri "$base/approvals/$($approval.approval_id)/decisions" -Headers $human -ContentType $jsonContentType -Body ([Text.Encoding]::UTF8.GetBytes($decision))

$operationId = "operation-$demoId"
$operation = @{
  operation_id = $operationId
  approval_id = $approval.approval_id
  expected_approval_revision = $decided.approval.revision
  case = $approval.case
  snapshot_hash = $approval.snapshot_hash
  action = 'simulate_classification_trial'
} | ConvertTo-Json -Depth 5
$null = Invoke-RestMethod -Method Post -Uri "$base/operations" -Headers $executor -ContentType $jsonContentType -Body ([Text.Encoding]::UTF8.GetBytes($operation))
```

通过下面两个请求读取服务端持久记录。审批应为 `CONSUMED`，并含 `decision_record`。操作应关联单一效果和合成结果，结果包含 `result_hash`。重启服务后使用相同的 ID 查询，记录应一致。

```powershell
Invoke-RestMethod -Uri "$base/approvals/$($approval.approval_id)" -Headers $viewer | ConvertTo-Json -Depth 10
Invoke-RestMethod -Uri "$base/operations/$operationId" -Headers $viewer | ConvertTo-Json -Depth 10
```

Use the same create key and body after a lost response to get the same approval; changing that body with the same key returns `409 IDEMPOTENCY_KEY_REUSED`. Repeating the same operation ID and body returns the saved effect/result; a changed body returns `409 OPERATION_ID_REUSED`; a new operation cannot reuse the consumed approval. After restarting the process, query the same approval/operation IDs to read the same database records.

## Routes and roles

| Route | Required role | Purpose |
| --- | --- | --- |
| `GET /health` | none | Liveness only; identifies itself as synthetic local, not production authority |
| `POST /approvals` | model or human | Create one immutable, pending approval snapshot |
| `GET /approvals/{approval_id}` | any authenticated same-tenant identity | Read state, revision, snapshot, and decision record |
| `POST /approvals/{approval_id}/decisions` | human | Approve or reject using expected revision and snapshot hash; self-approval is denied |
| `POST /operations` | executor | Atomically consume one approved snapshot and record a synthetic effect/result |
| `GET /operations/{operation_id}` | any authenticated same-tenant identity | Read operation plus linked effect and result |
| `GET /effects/{effect_id}` / `GET /results/{result_id}` | any authenticated same-tenant identity | Read authoritative effect/result rows |
| `GET /audit/{entity_type}/{entity_id}` | any authenticated same-tenant identity | Read persisted audit events for the record |

The viewer test identity has read-only access. Unknown bearer tokens receive 401; recognized identities with insufficient roles receive 403. Reads are tenant-scoped and cross-tenant record IDs return 404.

## Verification and current limits

Run this focused acceptance suite:

```powershell
py -3 -m unittest discover -s prototype/tests -p 'test_approval_service.py' -v
```

It covers loopback HTTP flow, role checks and forged identity fields, self-approval denial, create/decision/operation idempotency, concurrent same-key creation and operation replay, case/source staleness, terminal state after restart, and durable authoritative queries. It does not test a real human-auth provider, UI integration, external executor, network failure outside the local process, the second source/UI selector (FP-01), or formal quotation/booking. Those remain separate work; the peer-reported Edge/LM Studio results are not this service's test evidence.
