# Subagents 参考

当前支持异步、并行、只读子任务分派，并可在同一父 Run 内让已结束的子任务续用已保存上下文续轮。Server（Web/Desktop、默认 CLI）与 `--direct` CLI 装配同一套能力。可选预算配置见 [配置参考](configuration.md#subagents-预算)；更新源码后重启 Server，新 Run 使用新工具。已打包应用需要重新构建。

## 模型工具

| 工具 | 参数 | 行为 |
|---|---|---|
| `spawn_subagents` | `tasks`：1–8 个 `{task, max_steps}` | 立即返回子 Run ID 和状态，任务并行运行或排队，父 Agent 可以继续工作 |
| `get_subagents` | `run_ids=[]`、`output_chars=1000` | 查询本父 Run 的状态及结果；空 ID 列表查询全部 |
| `wait_subagents` | `run_ids`：1–8 个 ID、`mode=any`、`timeout_seconds=30`、`output_chars=1000` | 等待任意一个或全部目标完成；返回当前结果与 `pending`，超时不取消任务 |
| `read_subagent_report` | `run_id`、可选 `round`、`offset=0`、`limit=4000` | 分页读取已结束子任务的报告；偏移按字符计数，跟随 `next_offset` 读取全文；省略 `round` 读当前轮，指定则回读历史轮 |
| `resume_subagent` | `run_id`、`prompt`、可选 `max_steps` | 让本 Run 内已结束的子任务带着已保存上下文再跑一轮，立即返回排队状态；共享预算不重置 |
| `cancel_subagent` | `run_id` | 取消指定任务，等待其清理，再返回状态；已结束任务保持原结果 |
| `delegate_task` | `task`、可选 `max_steps` | 兼容旧调用，创建单个子任务并同步等待；同样占用本 Run 资源预算 |

`task` 必填，1–16000 字符；应包含独立任务与必要背景。`max_steps` 默认 15，范围 1–40，具体默认值和上限以本 Run 的配置与工具 schema 为准；严格拒绝布尔值。ID 必须属于当前父 Run，查询拒绝重复或未知 ID。`output_chars` 范围 0–4000，按字符计数；状态/结果查询遵守聚合输出限额，可减少 ID 或输出长度。

批量分派在启动前检查排队状态与任务 ID 是否能放入当前输出限额。若无法返回 ID，整批拒绝，不创建子任务、不产生排队事件，也不占用累计任务名额或模型调用预算；可缩小批次重试。

异步工具结果包含 `tasks`（每项为 `{run_id,status,steps,round,max_steps,output,report_chars,output_truncated,error}`）、`model_calls`（已使用次数与上限）、`limits`（并发与累计任务上限）。`wait_subagents` 额外返回 `pending`。`round` 为该任务当前轮次，`max_steps` 为该轮步数上限；`steps` 为跨轮累计值。运行或排队任务尚无结果；失败不会包装成完成。父 Agent 需检查状态和证据，再整合结果；limit_reached 可以携带部分发现，应说明未完成项，不应把整项调查标记完成。

`mode=any` 在任意所选任务结束时返回，已结束目标也会使等待立即返回；继续等待时传入剩余 `pending`。`mode=all` 等待全部目标。等待最长 60 秒，并受 ExecutionContext 总超时上限约束；`timeout_seconds=0` 只获取快照。等待超时不消耗子任务结果，也不停止它们。

## 资源与生命周期

每个父 Run 默认：

- 最多同时运行 3 个子任务，其余排队。
- 累计最多创建 8 个子任务，已结束任务仍计入，不允许无限循环分派。
- 父任务、所有子任务及双方的上下文压缩共享 160 次模型调用预算。调用前预留，失败与取消的调用也计数；达到上限不再发出请求。
- 每个子任务默认最多 15 步，可指定到 40 步；预算内最后一步预留为无工具的汇总调用，要求返回已确认发现、证据和未检查项。达到该步骤仍标记 limit_reached，报告作为部分结果；若模型仍请求工具则拒绝执行，并提供明确的未完成说明。提早给出最终答复才标记 completed。排队和运行共同受 `ExecutionContext.max_timeout_seconds` 限制，默认 300 秒。

步数默认值、上限和共享调用预算可在 [配置参考](configuration.md#subagents-预算) 中调整；并发与累计任务限制仍为装配值。调用次数不是 token 或费用预算，尚无精确 token/费用硬上限。并发上限指子任务，父任务可同时发起自己的模型调用。

子任务属于本次父 Run，不能跨父 Run 或跨父对话轮次续用；在同一父 Run 内可通过 `resume_subagent` 续轮，见下节。父任务完成、失败、步数耗尽、取消或 Server 关闭时，拒绝新分派和续轮，取消仍运行或排队的任务，并等待清理。清理完成后才发出父终态事件、关闭共享模型客户端。父 Agent 应在最终答复前收集所需结果，不依赖未完成的后台工作。超时清理依赖协程和工具配合取消，不是操作系统强制终止。

重叠的取消请求只向子 worker 发起一次取消，随后共同等待资源清理。取消控制调用或在父 Run 清理期间重复取消，也会等待清理结束后再传播取消；父终态事件在清理后发出一次。尚未启动的任务同样归结为 cancelled，不遗留 queued 状态。

预留汇总不追加预算外调用，也不绕过共享模型预算、超时或取消；如果这些边界提前终止，可能仍无报告。`max_steps=1` 没有调查工具步骤，只能返回当前信息下的报告。

管理器在本父 Run 内完整保存脱敏后的最终报告，不按工具输出限额截断存储。查询与同步兼容入口只返回短预览，`report_chars` 给出全文字符数，`output_truncated` 标识预览未包含全文。模型应使用 `read_subagent_report` 取回所需全文，而不是反复扩大预览。

分页返回 `{run_id,status,round,offset,text,total_chars,next_offset}`，`limit` 范围 1–8000，`offset` 为非负整数，严格拒绝布尔值；超出全文长度报错，等于长度返回空页。`next_offset=null` 表示已读完。JSON 转义及元数据也计入 ExecutionContext 输出限额，必要时自动缩小页长，无法返回至少一个字符则明确失败；不要自行用 limit 推算下一页偏移。运行/排队任务没有最终报告，读取返回未就绪；取消/失败任务可读取已有报告，通常为空。指定 `round` 读取历史轮，省略读取当前轮，不存在的轮次返回未就绪。读取只允许本父 Run 的子 ID，不产生模型调用。跨父 Run 及 Server 重启后不能通过工具读取，历史全文通过报告事件回放。

同步兼容工具返回 `{run_id,status,steps,output,report_chars,output_truncated,error}`；超时返回工具 `execution_error`。

## 续轮

`resume_subagent` 让本次父 Run 内已结束的子任务带着已保存的消息历史再跑一轮，父 Agent 可在拿到部分报告后追加指令，而不必新建任务。

- 只接受本 Run 中状态为 completed 或 limit_reached 且已保存上下文的子任务；运行中、排队中、已取消或失败的任务一律拒绝，未知或跨父 Run 的 ID 同样拒绝。
- `prompt` 是追加指令，1–16000 字符，省略时使用默认续做提示；`max_steps` 与分派共用同一配置的默认值和上限，严格拒绝布尔值。
- 每轮独立授予步数预算，`steps` 报告跨轮累计值，不随续轮清零。
- 复用同一子 Runtime 及其上下文压缩 checkpoint，不新建子 Run、子 Session 或数据库记录。
- 共享模型调用预算跨轮累计，不因续轮重置；预算耗尽时拒绝续轮，且准入失败不改变任务当前轮次、输出或事件序列。
- 排队与运行同样受 `ExecutionContext.max_timeout_seconds` 限制，每轮自排队起重新计时。
- 父 Run 结束时同样取消续轮任务并等待清理。

每次续轮使 `round` 加一，以 `subagent.resumed` 事件开始，携带该轮 `prompt` 与 `max_steps`。报告按轮分别保存：`read_subagent_report` 省略 `round` 读当前轮，指定 `round` 可回读历史轮，同一次父 Run 内旧轮报告始终可读。

## 上下文与能力

- 独立 Run ID、消息状态和压缩引擎；不继承父对话、压缩 checkpoint 或本轮记忆索引消息。父 Agent 必须显式传递任务背景。
- 使用同一模型客户端、模型窗口、workspace 和审批 Policy；共享客户端不意味着共享对话。
- 继承本轮冻结的用户/项目说明和 Skills 目录。通过只读工具按需检索文件、技能、记忆、历史及网络资料，不创建子 Session。
- 强制移除写入工具、`run_shell` 及所有子任务控制工具，不允许递归分派。子工具调用仍经过继承的 Policy。
- 只读和 workspace 约束是工具能力约束，不是操作系统沙箱。

## 事件、历史与客户端

子事件包装为父 Run 的 `subagent.event`，payload：

`{tool_call_id,child_run_id,child_seq,child_step,child_type,child_payload}`。

`tool_call_id` 始终关联最初创建任务的调用，即使父 Agent 后来调用其他工具。每个子事件的 payload 附带 `round`，标识所属轮次。每个子任务从 `subagent.queued` 开始，续轮时先发出 `subagent.resumed`，随后转发子 Runtime 事件，再以 `subagent.finished` 报告最终状态；超时可能先出现子 Runtime 的取消事件，最终状态以 finished 为准。父 seq 和各子 seq 连续递增，续轮不重置子 seq。子终态不会结束父 Run 或 SSE，子模型增量不会混入父答复。

Server 沿用现有事件持久化和脱敏路径；Trace/SSE 可重放嵌套事件。Activities 分别显示各子任务的状态和步数，不逐条展示子模型增量；CLI 显示子事件。Trace 顶层 `steps`、`model_calls`、`tool_calls` 仍只统计父 Run，子调用和 usage 在嵌套事件中查看。

不新增子 Run 数据库记录、子 Session 或 HTTP 控制端点，`GET /runs/{child_run_id}` 不可用。主历史保存分派/查询/等待/取消调用及有界结果，不追加子对话；直连 CLI 保存结果到 transcript，子事件仅在终端显示。Server 重启后不能恢复子任务，父 Run 沿用现有 interrupted 恢复机制。

## 子任务详情

点击 Activities 的子任务条目打开独立详情，显示任务、状态、逐步文字输出、可折叠推理、工具请求/结果摘要、结束报告和事件时间线。运行中持续更新；历史 Run 通过既有父 Trace 重放。关闭详情返回父对话，阅读位置保留。详情向上滚动时暂停跟随，回到底部或 Jump to latest 恢复。

新子任务的嵌套事件追加有界数据：queued 的 task 最多 2000 字符；model.completed 的正文与推理各最多 8000 字符，前 20 个工具请求的参数各最多 2000 字符；tool.completed/tool.failed 的 result 为最多 6000 字符的结果 JSON 摘要；最终报告通过 `subagent.report` 分块记录，每块 `{offset,text,total_chars}` 的 text 最多 4000 字符；finished 附上前 1000 字符的预览及截断标记、report_chars 与 error。详情按 `round` 分组、按 offset 拼接全文，续轮时展示该轮追加指令，旧 Run 回退到原有预览。摘要截断有标记。实时增量仍保留，子任务详情优先显示已接收的流式正文；非流式模型使用 completed 中的正文。

明细在 Core 中做凭据模式脱敏，Server 继续使用现有已知密钥脱敏与持久化路径。只有子 Runtime 启用明细，父事件默认字段不变，不向父模型上下文自动追加完整子对话。旧 Run 没有记录的任务、参数和结果明确显示缺失，不推断或补造。CLI 仍显示简短子事件，不输出明细字段。

尚不支持写入角色、子模型选择、跨父 Run 续用和后台任务。真实模型首轮复验已覆盖并行分派、父任务继续工作、等待、分页工具使用和汇总；续轮目前仅由离线测试验证，真实模型效果待评估；部分任务仍会耗尽步数，任务规划与整合质量需持续评估。
