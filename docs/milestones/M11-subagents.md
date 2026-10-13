# M11 · Subagents

## 1. 目标与范围

已完成约定的同一父 Run 内异步并行只读子任务闭环，包括分派、等待、取消、完整报告和保留上下文续轮。2026-10-10 首版同步分派交付时里程碑仍进行中，随后追加并行与边界修复，2026-10-12 完成续轮实现；2026-10-13 按用户确认整理交付状态。

## 2. 交付物

- Run 所属 SubagentManager 与同步兼容入口，复用 AgentRuntime。
- 并行创建、状态查询、等待、取消、完整报告分页与 Run 内续轮工具。
- Server、CLI 装配与共享模型调用预算；可配置步数默认值和上限。
- 父 Trace/SSE 的子事件、Activities 状态与按轮执行详情；阅读时暂停自动滚动。
- [现行参考](../reference/subagents.md)、[设计](../design/subagents.md) 与 [预算配置](../reference/configuration.md#subagents-预算)。

## 3. 关键决策

子任务保持只读、不递归，共用模型和 workspace，独立保存消息与压缩状态。生命周期由父 Run 管理，父终态与客户端关闭前先排空子任务。父、子及压缩共享调用预算；每轮预留预算内最后一步汇总，limit_reached 明确表示部分完成。

使用既有父事件持久化，不新增数据库 schema。最终报告完整保存、按字符分页，并以有界分块事件回放。续轮沿用子任务 ID、上下文与压缩 checkpoint，累计步数和调用预算不清零，每轮报告分别保存。接口与限额以现行参考为准。

## 4. 实施摘要

先交付同步兼容，再增加异步任务管理、并发队列和共享预算。修复批量准入失败留下后台任务、重叠取消打断清理、耗尽步数后无报告及报告存储截断等边界。追加可配置预算、报告分页和详情回放，最后实现 Run 内续轮与按轮报告。

通过受控 Fake Model 验证并发、父任务进展、隔离、审批、取消、超时、终态顺序和资源清理。续轮覆盖重复请求拒绝、上下文保留、累计预算、压缩计数及 checkpoint 复用；Server 验证事件归属、持久化、SSE 断线与关闭，UI 验证阅读位置和轮次显示。

## 5. 验证

- `conda run -n nemo python -m unittest discover -s tests -q`：首版 316 项、并行 328 项，最终续轮交付 354 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `conda run -n nemo python -m nemo.cli --help`：通过。
- `npm --prefix apps/ui test -- --run`：首版 28 项，最终 38 项通过。
- `npm --prefix apps/ui run typecheck`、`npm --prefix apps/ui run build`：通过。
- `git diff --check`：通过。

真实模型首轮复验通过分派、父任务继续工作、等待、报告分页与汇总；三个子任务中两个 completed，一个 limit_reached 且保留部分报告。续轮交付使用离线测试验证，未记录真实模型续轮验收结论，不将两种验证混同。

## 6. 遗留

约定的 Run 内只读协作范围已交付。真实模型续轮使用质量仍需持续观察；未重新构建发布 Nemo.app。跨父 Run 续用、重启后后台恢复、写入角色、独立模型与精确 token/费用预算不属于本次交付范围，未决定新增里程碑。下一项为 M12 MCP 接入。
