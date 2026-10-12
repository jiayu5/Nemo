# M11 · Subagents

## 1. 目标与范围

推进 Phase 3 的子 Agent 能力。2026-10-10 交付同步只读分派首版，M11 整体仍进行中；同日按用户确认方案追加异步并行只读分派，真实模型验收当时尚未完成。

## 2. 交付物

- Core 同步兼容入口与 Run 所属 SubagentManager，复用现有 AgentRuntime；追加异步创建、查询、等待和取消工具。
- Server 与直连 CLI 装配，主 Agent 接收有界子任务结果。
- 父 Trace/SSE 中的嵌套事件，Activities 与 CLI 的子任务显示。
- [现行参考](../reference/subagents.md) 与 [设计](../design/subagents.md)。

## 3. 关键决策

首版同步只读，不支持递归；独立上下文与压缩引擎，继承模型、workspace、说明、Skills 和审批策略。采用现有父事件流保存子事件，无数据库迁移或新增依赖。随后确认的并行方案支持父 Agent 继续工作、Run 所属任务清理、并发/累计任务上限与父子共享调用预算。参数和限制以参考文档为准。

## 4. 实施摘要

ToolRegistry 增加可选事件执行入口，父 Runtime 包装子事件。取消与超时等待子 Runtime 清理；工具结果保留子状态，主历史不追加子对话。新增隔离、工具过滤、策略、步数、失败、取消、超时、输出限制及 Server/CLI 集成测试。原有压缩测试调整输入长度以容纳新增工具 schema，仍验证压缩与 checkpoint 恢复。

并行追加先建立任务管理器、共享模型预算和 Runtime 生命周期接口，再接入 Server/CLI 及 Activities 状态摘要。通过模型屏障验证真正并发和父任务进展；固定捕获创建调用 ID 以保持后台事件归属。压缩集成测试缩小首轮输入以容纳新增工具 schema，保留触发压缩、checkpoint 恢复及不重复摘要的断言。

2026-10-11 修复批量分派的输出失败边界：创建 worker 前预检 ID 快照，超出限额时整批拒绝，不留下任务、排队事件或资源消耗。回归测试先复现问题，再验证无副作用及缩小批次后的成功重试。

2026-10-12 修复重叠取消打断资源清理的问题：对同一 worker 只发起一次取消，取消控制调用和父 Runtime 均等待清理后再传播取消。补充受控异步清理测试，验证重叠取消、父清理期间重复取消、未启动任务的状态归结，以及父终态只在清理后发出一次。

真实试用中三个子任务耗尽步数且未生成报告。追加修复在预算内预留无工具汇总步骤，保留 limit_reached 状态和已确认/未检查部分，禁止最后一步继续工具调用；不扩大调用预算或改变父 Runtime 默认行为。

执行详情追加：Activities 子任务条目打开实时详情，展示有界任务、模型输出、工具请求/结果摘要与报告，并复用父 Trace 重放。父对话与子详情的滚动在用户上滑后暂停跟随，回到底部或点击按钮恢复。

追加可配置预算：用户配置提供子任务默认步数、步数上限和共享调用预算，分派工具 schema 与参数校验使用同一配置。父模型按任务输出 max_steps，超限整批拒绝；Server 每个新 Run 重新装配，直连 CLI 启动时读取，配置编辑保留该节。

完整报告追加：管理器取消 8000 字符存储截断；查询和同步入口保留预览并标记长度与截断。新增 read_subagent_report，按字符分页且缩小到当前 JSON 输出限额，校验 Run 所有权、就绪状态与偏移。最终报告通过有界分块事件持久化，详情回放拼接全文，不新增数据库 schema 或依赖。

子任务续轮追加：新增 resume_subagent，在本次父 Run 内让 completed 或 limit_reached 的子任务带已保存上下文再跑一轮，复用同一 Runtime 与压缩 checkpoint。任务增加 round 与按轮报告表，read_subagent_report 支持按 round 回读历史轮，所有子事件 payload 附带 round，UI 按轮分段展示续轮指令、步骤与报告。准入沿用批量分派顺序，先预检输出限额再改动轮次与状态；每轮独立授予步数预算但累计 steps 与共享模型调用预算不清零。

## 5. 验证

- `conda run -n nemo python -m unittest discover -s tests -q`：316 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `conda run -n nemo python -m nemo.cli --help`：通过。
- `npm --prefix apps/ui test -- --run`：28 项通过。
- `npm --prefix apps/ui run build`：类型检查与构建通过。
- `git diff --check`：通过。

异步并行追加验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：328 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `conda run -n nemo python -m nemo.cli --help`：通过。
- `npm --prefix apps/ui test -- --run`：29 项通过。
- `npm --prefix apps/ui run typecheck`、`npm --prefix apps/ui run build`：通过。
- `git diff --check`：通过。

2026-10-11 分派失败边界验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：330 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `git diff --check`：通过。

2026-10-12 取消清理边界验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：333 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `git diff --check`：通过。

步数耗尽报告修复验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：336 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `git diff --check`：通过。

子任务详情与阅读位置验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：337 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `npm --prefix apps/ui test -- --run`：35 项通过。
- `npm --prefix apps/ui run typecheck`、`npm --prefix apps/ui run build`：通过。
- `git diff --check`：通过。

可配置预算验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：342 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `git diff --check`：通过。

完整报告验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：345 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `npm --prefix apps/ui test -- --run`：36 项通过。
- `npm --prefix apps/ui run build`：类型检查与构建通过。
- `git diff --check`：通过。

子任务续轮验证（离线测试，未调用真实模型）：

- `conda run -n nemo python -m unittest discover -s tests -q`：354 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `npm --prefix apps/ui test -- --run`：38 项通过。
- `npm --prefix apps/ui run typecheck`、`npm --prefix apps/ui run build`：通过。
- `git diff --check`：通过。

真实模型复验：使用当前配置 Provider 执行一父三子只读检查，父任务 completed、26 步，父子共享计数 78 次；子任务分别 completed（17 步、6395 字符）、completed（15 步、10370 字符）、limit_reached（20 步、9905 字符）。父任务实际调用 5 次 read_subagent_report 并生成汇总。此次验证覆盖真实分派、父任务继续工作、等待、最终报告及分页工具使用；静态审查中的未验证项仍保留，不将部分报告视为任务全部完成。取消清理、历史回放和边界断言由上述离线测试验证，不声称在真实模型复验中逐项触发。

## 6. 遗留与下一步

用户真实模型试用发现步数耗尽报告缺失；汇总、可配置预算和完整报告分页已完成上述真实模型首轮复验，仍有任务耗尽步数并返回部分结果。未构建发布 Nemo.app。异步并行只读分派、模型调用次数预算和 Run 内子任务续轮已实现；续轮仅由离线测试验证，尚未在真实模型会话中使用。精确 token/费用预算、写入角色、子模型选择、跨父 Run 续用与后台恢复尚未实现，后续范围需确定。
