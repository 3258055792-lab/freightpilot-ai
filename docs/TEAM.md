# 三人协作

GitHub 保存代码、任务和评审；Pachat 用于提醒、讨论与进度同步。群聊里的“已完成”须附分支/提交、测试结果和未完成项。每台电脑使用独立账号、独立本地克隆。

## 邀请与加入

1. 两位队友各自注册 GitHub 账号并提供用户名。
2. 仓库所有者打开 https://github.com/3258055792-lab/freightpilot-ai ，选择 Settings → Collaborators → Add people，分别邀请用户名；组织仓库页面可能显示 Collaborators & teams。
3. 队友接受邮件或 GitHub 邀请，在自己的 GitHub Desktop 登录自己的账号。
4. File → Clone repository，选择同一仓库并克隆。按照 README 安装、生成合成数据并运行测试。
5. 所有者发布迁移分支并创建 Pull Request，经人工检查合并 main 后，队友再 Fetch origin / Pull origin 获取完整原型。

官方步骤：https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository

## 开发规则

- 每项任务先记录目标、负责人、涉及目录、验收条件及依赖，再开 `codex/任务名` 分支。
- 每次开始先同步 main；不能几个人直接改 main。避免同时修改同一个核心文件。
- 完成后提交代码、推送任务分支、创建 Pull Request。附测试与限制，另一位成员评审，人确认后合并。
- 自动测试通过不代表业务验收通过；关键设计、合并及发布交给人决定。
- 拒绝审批的业务流程不能靠重试、模型文字或群聊确认绕过。
- 不提交账号密码、Pachat token、模型密钥、客户资料、真实报价文件及本机运行日志。

## 建议分工（待成员确认）

- A：核心报价工具、审批与订单状态接口。
- B：工作流调度、模拟订舱/追踪事件、异常恢复。
- C：前端操作台、端到端演示和测试材料。

当前先用两个 Agent 联调一个小任务：一方提出审批接口契约，另一方审查拒绝、重复提交、版本变更、重启恢复用例；确认接口后再并行编码。

后续按实际实现新增 backend/、frontend/、tests/e2e/ 等目录，本次不放空壳服务冒充完成。
