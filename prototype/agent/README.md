# 本地真实模型工具调用（第一阶段）

这是命令行适配器，不是浏览器接口或持久审批后端。使用 LM Studio 的本机 OpenAI-compatible chat/completions 接口，模型生成标准 tool_calls，执行现有 Harness 插件及 Python 只读工具，再将原始工具结果交回模型。没有 shell 工具、远程模型地址或模型提供的批准入口。

先按根README安装依赖并生成合成数据，启动本机LM Studio服务且加载支持工具调用的模型：

```sh
node prototype/agent/run.mjs "合成测试：塑料收纳盒发巴西，查询候选分类与来源证据，不做试算"
node --test prototype/agent/run.test.mjs
```

默认模型 `qwen3.5-9b-mlx`，地址 `http://127.0.0.1:1234/v1`；可用 `FREIGHT_MODEL` 与 `FREIGHT_MODEL_URL` 覆盖，URL只接受HTTP本机地址。未配置云端API密钥。stdout为结果JSON，stderr为过程事件；不要把真实客户询价或秘密日志提交Git。

## 边界

- `freight_lookup` / `freight_classify` 真正调用 Python 和本地数据库，不靠模型编造来源。
- `freight_calculate` 继续通过原插件。该适配器没有注入人类审批提供者，因此必须返回 blocked 并停止，不会产生金额。下一阶段需要对接FP-02身份校验和持久审批，不能通过自动allowed-once或模型confirmed标志替代。
- 工具blocked/error立即停止；最多6轮（上限8轮）、每轮最多4工具调用，模型请求90秒超时。拒绝未知工具/无效参数/重复调用ID。
- `completed`仅表示工具调用后模型返回了文本，不代表业务完成、批准成立或模型文本已被独立核验。`no_tool_call`明确表示没有工具执行。
- 单元回归使用模型HTTP替身，但业务分类子进程是真实执行；实机Qwen记录另放docs/evidence/real-tools-20261117。
- 德国前端JSON与巴西Python数据库仍是独立样例。尚无第二来源选择界面、前端到本适配器的服务接口，也未接后端审批，不把它们报告为完成。
