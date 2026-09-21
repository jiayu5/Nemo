# M4 · Context、CLI 与 Server

| 项 | 内容 |
|---|---|
| 编号 | M4 |
| 状态 | 进行中（M4a、M4b、M4d 已完成；M4c 未开始） |
| 完成日期 | M4a：2026-09-17；M4b：2026-09-18；M4d：2026-09-21 |
| 对应阶段 | Phase 1 · Core |
| 代码范围 | `src/nemo/core/context/`、`src/nemo/core/session.py`、`src/nemo/core/tools/`、`src/nemo/core/runtime/agent.py`、`src/nemo/prompts/`、`src/nemo/cli/`、`src/nemo/bootstrap.py` |

M4 是 Phase 1 里最大的一步，按交付顺序拆成三条子线，共用这一个文件：

| 子线 | 内容 | 状态 |
|---|---|---|
| **M4a · 上下文组装** | 稳定前缀、每轮注入、`AGENTS.md` 快照 | 已完成 2026-09-17 |
| **M4b · CLI 与三档审批** | 会话、三档审批、transcript、脱敏 | 已完成 2026-09-18 |
| **M4c · Server 与 SQLite** | Sessions/Runs API、SSE、持久化 | 未开始 |
| **M4d · 指令分层** | 用户级 `~/.nemo/AGENTS.md` + 项目级 `<workspace>/AGENTS.md` | 已完成 2026-09-21（晚于 M4c 交付，见下） |

M4d 在 M4c 之前交付：它来自一次复盘——「Nemo 传给模型的 `AGENTS.md` 是从哪来的？」查下来发现只有项目级一层，Nemo 自己的 Agent 指令硬编码在源码里、用户改不了。这是概念缺口而不是排期问题，所以先补。

**为什么合在一个文件**：架构文档 §11 的 M4 是「Server 与 CLI」，而「基础 Context」在 Phase 1 的交付能力里没有自己的编号。三条子线交付的其实是同一件事——**把 Core 变成能天天用的东西**——所以共用一个里程碑编号、共用一份记录，而不是让两个文件都叫 M4。子线的编号只在本文内部使用，不新增文件。

## 1. 目标与范围

### M4a · 上下文组装

**目标**：把 [上下文组装设计](../design/context-assembly.md) 从纸面变成可运行的代码——稳定前缀逐字不变（吃满前缀缓存），易变信息只作为每轮注入存在（不污染前缀、不累积），项目说明（`AGENTS.md`）按会话快照进入稳定层。

**做**：

- 稳定前缀：`ContextBuilder` 在构造时组装一次并冻结，`build()` 不重新拼装。
- 每轮注入：`Reminder` 契约 + 注入规则 + `step_budget` 这一条实际提醒。
- 项目说明：从 workspace 读取 `AGENTS.md`（读取在 core 之外），带大小上限与截断标记。
- 运行时接线：`AgentRuntime` 每轮决定是否注入，并把注入结果写进 `model.started` 事件。
- 模型侧可见性：系统提示的环境块声明 `<reminder>` 标签的含义。
- 25 个新测试，覆盖设计文档 §9 列出的每一条验收标准。

**不做**（刻意排除）：

- Server 与 CLI：当时的下一步，已由 M4b 与 M4c 承担。
- 历史压缩、检索、长期记忆：Phase 2 的 Context Engine。
- 按需加载的大块参考内容（对应 Claude Code 的 Skill / Data 层）：单 agent 无 skill 体系，暂不需要。
- 更多 reminder：只有 `step_budget` 通过了「运行时才知道 / 每轮会变 / 影响本轮决策」三条判据。
- 按 token 的精确预算：先用字符近似。

### M4b · CLI 与三档审批

**目标**：让 Nemo 从「一条命令跑一次」变成能天天用的会话式 CLI，同时补上 M3 遗留的最大安全缺口——策略只能整体允许或拒绝，做不到「这一次问一下用户」。

**做**：

- 三档审批：`ask`（默认，改动前确认）、`auto`（只确认识别得出的危险）、`full`（不确认）。
- shell 命令形态分类：`read_only` / `destructive` / `unclassified`，规则表人工维护。
- 会话：`Session` 跨 run 累积历史；`AgentRuntime.run()` 新增 `history`，run 之间互不影响。
- CLI：`python -m nemo.cli`，支持一次性任务与 REPL、`:mode` 切换、`--session` 恢复。
- transcript：JSONL，600 权限，写入前脱敏并打 `redacted` 标记。
- 输出层脱敏：事件、模型输出、存档统一过一遍遮蔽。

**不做**：

- Server、FastAPI、SSE、SQLite：M4c，等 CLI 定了会话手感再定数据模型（schema 变更在规范里是红线）。
- 流式输出：等 M5 的 UI 有真实消费者。
- 沙箱：Phase 3。因此 `auto` 只能是启发式。
- workspace 之外的文件读写：仍然硬拒绝，本步不给审批开口子。
- 多会话并发、TUI、命令补全、历史检索。

### M4c · Server 与 SQLite（未开始）

**目标**（照架构文档 §11）：Sessions/Runs API、SSE、SQLite 持久化，与 UI 共用同一套 Session/Run 生命周期。

进入这一步前必须先决定一件事：**CLI 是否改为经由 Server 访问 Core**。架构文档 §3 的工程建议是「Phase 1 CLI 默认走 Server，与 UI 共用 Session/Run 生命周期」，而 M4b 交付的 CLI 是进程内直连 Runtime + JSONL 存档。维持现状会得到两套任务状态实现——正是那条建议要避免的情况。

### M4d · 指令分层

**目标**：把「喂给模型的说明书」分成两个来源、两个作用域，并让用户能够自定义属于 Nemo 自己的那一层。

**做**：

- 用户级 `~/.nemo/AGENTS.md`：跨项目复用的 Agent 行为偏好，用户自己写。
- 项目级 `<workspace>/AGENTS.md`：沿用 M4a 已有的能力，语义明确为「这个项目的约定」。
- 顺序固定为 内置系统提示 → 用户级 → 项目级；越具体越靠后。
- 两层各自 ≤ 8000 字符、各自独立截断。
- CLI 启动行报告实际加载了哪些文件、各多少字符，缺失写 `none`。

**不做**：

- 说明文件的合并、继承、`@import` 之类的机制：两层线性叠加已经够用。
- 会话内热重载：快照语义不破。
- 让说明文件影响权限：路径检查与审批是代码强制的，任何说明都改不动。

## 2. 交付物

### M4a · 上下文组装

| 文件 | 作用 |
|---|---|
| `src/nemo/core/context/reminder.py` | `Reminder` 契约、`step_budget()`、`slot_open()`、`fit_reminders()`、`describe()` |
| `src/nemo/core/context/builder.py` | 三层组装：冻结的稳定前缀 + 深拷贝历史 + 追加式 reminder |
| `src/nemo/prompts/project.py` | `load_project_instructions()`：读取 workspace 的 `AGENTS.md`，缺失返回 `None` |
| `src/nemo/prompts/local_agent.py` | 环境块增加 `<reminder>` 标签语义说明 |
| `src/nemo/core/runtime/agent.py` | 每轮判定注入、`model.started` 事件携带注入结果 |
| `examples/local_agent.py` | 示例接入项目说明加载 |
| `tests/test_context.py` | 25 个用例 |

### M4b · CLI 与三档审批

| 文件 | 作用 |
|---|---|
| `src/nemo/core/tools/command_rules.py` | `classify(command, workspace=…)`：切段、取动词、查表、匹配结构信号；纯函数，无 I/O |
| `src/nemo/core/tools/approval.py` | `ApprovalMode`、`ApprovalOutcome`、`ApprovalRequest`、`ApprovalPolicy`：唯一决定「要不要问人」的地方 |
| `src/nemo/core/contracts/tools.py` | 新增 `ToolCallFacts`；`ToolPolicy.decide()` 改为收结构体 |
| `src/nemo/core/tools/registry.py` | 组装 facts（含工具自报的 `shell_command`）后交给策略；`Tool` 增加可选声明 |
| `src/nemo/adapters/tools/shell.py` | `RunShellTool.shell_command()`：把命令原文声明给审批层 |
| `src/nemo/core/session.py` | `Session`：会话标识、历史、workspace；纯状态，无 I/O |
| `src/nemo/core/runtime/agent.py` | `run()` 支持 `history`：拷入历史，步数仍按轮重置 |
| `src/nemo/cli/main.py` | 参数解析、装配、REPL、`:mode`、`--session`、SIGINT 取消 |
| `src/nemo/cli/approver.py` | 终端询问（`y`/`a`/`n`），`input()` 走 `asyncio.to_thread` |
| `src/nemo/cli/transcript.py` | JSONL 读写、脱敏、`redacted` 标记、600 权限 |
| `src/nemo/cli/render.py` | 事件渲染与凭据遮蔽 |
| `src/nemo/cli/streams.py` | 唯一的终端出入口，让审批与 REPL 可注入、可测试 |
| `src/nemo/bootstrap.py` | `build_local_tools()`：工具集的唯一所有者 |
| `tests/test_approval.py` | 19 个用例：分类器、三档判定、会话记忆、fail closed、原因不泄露 |
| `tests/test_cli.py` | 13 个用例：答复解析、脱敏、transcript、会话、CLI 闭环与恢复 |

### M4c · Server 与 SQLite

尚未开始，无交付物。

### M4d · 指令分层

| 文件 | 作用 |
|---|---|
| `src/nemo/prompts/instructions.py` | 取代 `prompts/project.py`：`load_instructions()` 统一处理缺失/空白/不可读，另有 `load_user_instructions()` 与 `load_project_instructions()` |
| `src/nemo/core/context/builder.py` | `ContextBuilder` 新增 `user_instructions`；两个说明头分别是 `# User instructions (from ~/.nemo/AGENTS.md)` 与 `# Project instructions (from AGENTS.md)` |
| `src/nemo/prompts/local_agent.py` | 条款 `project_instructions` → `instruction_files`，覆盖两个来源 |
| `src/nemo/cli/main.py` | 加载两层并打印 `instructions:` 启动行 |
| `src/nemo/cli/render.py` | token 计数缺失时渲染成 `-` 而不是 `None` |
| `examples/local_agent.py` | 同步接线 |
| `tests/test_context.py`、`tests/test_cli.py`、`tests/test_prompts.py` | 新增 7 个用例（顺序、任一层缺失、独立截断、用户级加载、启动行、条款名） |

## 3. 关键决策与理由

### M4a · 上下文组装

| 决策 | 理由 | 放弃的替代方案 |
|---|---|---|
| 注入规则**由 `ContextBuilder` 强制执行**，`AgentRuntime` 只是用同一个谓词 `slot_open()` 决定该报告什么 | 不变量必须放在最难绕过的地方：任何调用方把 reminder 塞给 `build()` 都不会破坏「不改写已有消息」；同时事件报告的又确实是本轮真实注入的内容，不会出现「报了但没注入」 | 只让 Runtime 遵守规则：未来的调用方（CLI、Server）很容易绕过；或让 builder 记录 `last_injected` 供 Runtime 读取：并发 run 会串数据（M1 已用测试锁定过并发隔离） |
| reminder 只追加为新消息，永不修改已有消息 | 承接设计文档的修正：修改已发送内容会让同一位置的字节在后续轮次变化，前缀缓存从该点起全废 | 合并进最后一条 `user` 消息：省一条消息，代价是整个会话的缓存与历史语义 |
| 首轮不注入（`slot_open` 为假时不注入） | 用户刚发完消息时步数预算最充裕，提醒价值最低；换来「永不产生连续同角色消息」 | 首轮也追加：会产生 `[user, user]`，虽然实测 DeepSeek 接受，但不构成跨 provider 保证 |
| reminder 的 body 禁止包含 `</reminder>` | 否则内容可以逃出包裹标签、在模型看来变成对话文本 | 转义标签：多一层无谓复杂度，且我们完全控制 reminder 的来源，拒绝即可 |
| 删除原设计中的「合计 ≤ 600 字符」上限 | 测试证明它与「3 条 × 200 字符」完全重合、永远不可能触发。**冗余的约束会假装自己在保护什么** | 保留它作为「防御性代码」：一条永不生效的检查会误导后来的人以为总量是独立受限的 |
| 稳定前缀在构造时冻结，`build()` 只引用 | 从结构上杜绝「某处忘了保持稳定」；prefix 是不可变属性，改动必须显式重建 builder | 每次 `build()` 重新拼装：任何一处实现漂移都会静默废掉缓存 |
| 项目说明**追加在系统提示之后**，而不是设计要求里的「环境块与条款之间」 | 系统提示是一个整体拼好的字符串，插到中间需要拆散提示模板；两者同属 L1，位置固定即可，收益不明确 | 让 `build_system_prompt()` 接受项目说明并做插值：把内容拼装拆到两个模块，职责反而更乱 |
| `AGENTS.md` 在会话开始快照一次，读取放在 core 之外 | core 不做文件 I/O，与 M2「core 不读配置」一致；每轮重读会让前缀随文件抖动 | 每轮重读：缓存全废，模型的世界观也会前后不一致 |
| `model.started` 事件记录注入的 reminder（名称 + 字符数），未注入时写 `[]` | 「没注入」和「没记录」必须能区分，否则排查时会怀疑是事件丢了 | 只在有注入时写字段：消费方拿不到确定性 schema |
| 测试替身 `AdditionModel` 改为查找「最新的工具结果」，不再假设它是最后一条消息 | 实现 reminder 后，追加的 user 消息会排在工具结果之后，原来按「最后一条」判断的替身直接陷入死循环（`limit_reached`）。**这是个真实教训**：任何依赖「最后一条消息是什么」的模型实现都脆弱——包括将来接入真实模型时写的适配逻辑 | 保持替身不变、改成在末尾插入 reminder 之前的位置：那等于让实现迁就测试 |

### M4b · CLI 与三档审批

| 决策 | 理由 | 放弃的替代方案 |
|---|---|---|
| 审批通过扩展 `ToolPolicy` 实现，`AgentRuntime` 零改动 | M3 把 `decide()` 设成 async 就是为这一刻；Runtime 对审批零知识 | 在 Runtime 里加审批分支：Runtime 重新认识「策略」，M2 定下的边界失效 |
| `auto` 用机械规则，不让模型自评 | 提出操作的是模型本身，自评等于自证；且不可复现、多一次往返、受提示注入影响 | 让模型给危险度打分：结论不稳定，错了没有规则可改 |
| 只读 shell 命令在 `ask` 档也免问 | 分类器只有一个，两档共用；`ls` 没有副作用 | 第一档一律询问：语义更好解释，但每次查看目录都要按回车 |
| `ask` 与 `auto` 的分界放在 `unclassified` | `auto` 必须放行分类不出的操作，否则与 `ask` 完全重合 | 让 `auto` 也问 `unclassified`：那一档就没有存在意义 |
| 危险动词分「与参数无关」和「只与作用域有关」两类 | 工作区内重命名是常规操作，可按参数判定；删除不可逆，参数再干净也不放过 | 把 `mv` 也列为危险动词：`auto` 每次改名都打断，最后逼用户切到 `full` |
| 分类原因不回显命令内容 | `reason` 会进 `ToolError` 到达模型，命令行里可能内联凭据 | 回显命令帮助模型理解：等于把凭据写进日志与上下文 |
| 事实收成 `ToolCallFacts` 而不是继续加关键字参数 | M3 说「签名不用再切」，加 `shell_command` 时被证明是错的；下一次不该再切一遍 | 继续加参数：每个策略实现都要跟着改 |
| 命令原文由工具自己声明 | 策略需要命令原文，core 又不能 import 适配器；工具声明是最短的桥 | 策略按参数模型鸭子取值：字段改名后静默失效 |
| 不加 `tool.approval_requested` 事件 | 审批请求本身就是问用户的那句话；为没有消费者的场景先铺一条 registry→runtime 通路是本末倒置 | 现在就加：Server 还没影，结构大概率还要改 |
| `Session` 只存状态，transcript 归 CLI | 与「core 不做 I/O」的既有边界一致，config 与 prompt 加载也是这么分的 | `Session` 自己写文件：core 立刻依赖用户目录 |
| 运行接历史用 `run(history=…)` 而不是让 Session 持有 run | 保留 M1「每个 run 独立」的不变量，run 之间事件与步数互不干扰 | Session 持有可变的 `AgentState`：并发与取消语义立刻变复杂 |
| `step_count` 每轮重置 | `max_steps` 防的是单轮无限循环；跨轮累计会让长会话在第 N 轮撞死 | 跨轮累计：长会话不可用，reminder 语义也变成「会话剩余步数」 |
| transcript 用 JSONL 而非 SQLite | 会话模型未定时先定 schema 是规范里的红线；JSONL 可读可 grep | 直接上 SQLite：为 Server 设计的数据模型在 CLI 阶段几乎一定要改 |
| transcript 是脱敏副本，并打 `redacted` 标记 | 日志里出现过的密钥收不回来；标记让「恢复时历史被改写」可见而不是静默 | 存原文：违反「密钥不进日志」；静默脱敏：模型看到的历史与当初不一致且无人知情 |
| 非交互时 fail closed，不设 `--approve-all` | 没人在场就没有审批；脚本要放行就显式 `--mode full`，不需要第二个开关表达同一件事 | 非交互自动放行：脚本一跑就绕过全部审批 |
| 默认档取 `ask` | Codex 默认 Auto 是因为它有沙箱兜底；Nemo 没有，默认应取更保守的一档 | 默认 `auto`：首次使用的手感更好，但默认安全性依赖一份启发式规则 |

### M4d · 指令分层

| 决策 | 理由 | 放弃的替代方案 |
|---|---|---|
| 两个文件同名 `AGENTS.md`，靠位置区分作用域 | 与 Codex 的 `~/.codex/AGENTS.md` + 项目 `AGENTS.md` 是同一套心智模型；「就近的说明文件」这个约定用户已经熟悉 | 用户级改名 `instructions.md`：名字上不再混淆，但失去与既有约定的对应关系 |
| 用户级在前、项目级在后 | 越具体越靠后，越靠后越接近对话；项目约定只对当前 workspace 成立，冲突时应当压过通用偏好 | 项目级在前：与「就近优先」的直觉相反 |
| 两层各自独立截断，不设总上限 | 两个来源性质不同，截一个不应牵连另一个；先有度量再谈预算 | 设总上限并按优先级丢弃：引入一套用户看不见的取舍，现阶段没有数据支撑 |
| 条款改名 `project_instructions` → `instruction_files` | 条款名要描述它处理的失败模式，而这个失败模式现在覆盖两份文件 | 保留旧名：条款内容与名字不符，正是这次复盘要消灭的那类模糊 |
| CLI 打印 `instructions:` 启动行 | 不问代码就看不出模型吃了哪份说明书——这次的困惑就是这么来的 | 只在调试开关里显示：默认不可见，等于没有 |
| `prompts/project.py` 换成 `prompts/instructions.py` | 两个来源共用「读取 + 缺失处理」逻辑；文件名要覆盖它的实际职责 | 保留 `project.py` 再加一个 `user.py`：两份几乎相同的 I/O 代码 |

## 4. 实施过程

### M4a · 上下文组装

1. **契约与规则先行**：`reminder.py` 定义 `Reminder`（含闭合标签防护、长度与命名校验）、`step_budget()`、`slot_open()`、`fit_reminders()`、`describe()`。
2. **组装层**：`ContextBuilder` 改为三层结构，前缀在构造时冻结，`build()` 接收 `reminders` 只做追加。
3. **接线**：`AgentRuntime` 每轮调用 `slot_open(state)` 决定是否注入，并把 `describe(reminders)` 放进 `model.started` 事件。
4. **项目说明**：`load_project_instructions()` 放在 `prompts/project.py`（core 之外），示例接线。
5. **测试先暴露问题（转折点）**：首次运行全量测试出现 3 类失败、共 8 个用例——`AdditionModel` 死循环、M1 的两处断言依赖「最后一条是工具结果」、M2 集成测试断言 wire 格式的末条角色为 `tool`。三处都是同一个根因：**reminder 追加在末尾，改变了「最后一条」的含义**。修法是让断言与替身按角色查找，而不是按位置假设。
6. **补专项测试**：按设计文档 §9 的验收标准写 25 个用例，其中 `test_reminder_is_appended_not_merged` 断言「除新增的那条外，其余消息逐字不变」——初版合并规则会在这里失败。
7. **一处设计被测试否定**：`fit_reminders` 原本还有「合计 ≤ 600 字符」的上限，测试无法构造出触发它的数据；确认它与「3 × 200」重合后删除，并同步修正设计文档。
8. **真实端到端**：用 DeepSeek + `--read-only` 跑通，确认项目说明进入稳定前缀、第二轮缓存命中。

### M4b · CLI 与三档审批

1. **文档先行**：先写 `docs/design/cli-and-approval.md`（目标、三档、判定规则、询问形状、会话模型、验收标准、非目标），并把 `src/nemo/cli/`、`session.py` 补进 `AGENTS.md` 的目录约定。
2. **判定层**：实现 `command_rules.classify()` 与 `ApprovalPolicy`。判定输入只有机械事实：工具自报的 `read_only`、工具声明的命令原文、参数里的路径。
3. **发现接口必须改**：策略需要命令原文，而 `decide()` 只收 `tool_name`/`read_only`/`arguments`。M3 曾判断这个签名不用再切，实际推翻了——改成 `ToolCallFacts` 结构体，而不是继续加参数。`AgentRuntime` 因此仍然零改动。
4. **跑真实命令看规则表**：用约 30 条真实命令（`ls`、`git status`、`python -m pytest`、`mkdir -p src/nemo/cli`、`mv a b`、`cd /tmp && ls` …）打印分类结果，发现 `mv` 被列进危险动词会把常规重命名也打断，改为按作用域判定；`cat ~/.nemo/config.toml` 正确落成只读（读 workspace 之外不升级）。
5. **测试抓到软链 bug**：`mkdir /tmp/ws/build` 被判成越界。根因是 workspace 做了 `resolve()`（macOS 上 `/tmp` 是 `/private/tmp` 的软链）而参数没有，导致包含判断永远失败。修法是两边都解析，并让「没有 workspace」的默认变成保守值。
6. **会话层**：`Session` 只存状态；`AgentRuntime.run()` 加 `history`（拷贝而非接管）；`Session.record()` 只并入新消息，并拒绝来自别的会话的 run。
7. **CLI 层**：`Streams` 把终端收成一个可注入对象；`TerminalApprover` 的 `input()` 走 `asyncio.to_thread`，否则审批期间取消与超时全失效；SIGINT 通过 `add_signal_handler` 转成取消，让中断保留已发生的历史。
8. **脱敏**：`redact()` 只在三处生效——事件渲染、模型输出、transcript 写入。写入时打 `redacted` 标记，因为事后无法再扫描出「这里原本有密钥」。
9. **又一处实现推翻设计**：初版 `load_transcript()` 靠「重新扫一遍能不能再脱敏」统计受损条数，测试证明恒为 0——密钥已经没了，扫不出来。改为写入时打标记。
10. **真实闭环验证**：DeepSeek 上跑一次性任务、恢复会话、真实 TTY 下的审批拒绝与放行各一次。

### M4c · Server 与 SQLite

未开始。

### M4d · 指令分层

1. **从一次复盘开始**：确认「Nemo 传给模型的 `AGENTS.md` 从哪来」——只有 `<workspace>/AGENTS.md` 一层，Nemo 自己的 Agent 指令硬编码在 `prompts/local_agent.py` 里，用户改不了。
2. **文档先行**：`docs/design/context-assembly.md` 的 §5 从「项目说明注入」改写为「指令分层：用户级与项目级」，补上顺序、优先级、独立预算与可观测性；`AGENTS.md` 补目录约定。
3. **加载层**：新增 `prompts/instructions.py`（`load_instructions` 统一处理缺失/空白/不可读），删除 `prompts/project.py`。
4. **组装层**：`ContextBuilder` 增加 `user_instructions`，两个说明头分别命名，按「通用 → 具体」顺序拼进稳定前缀。
5. **模型侧可见性**：系统提示条款 `project_instructions` 改为 `instruction_files`，明确两份说明都只能补充约定、都不能放宽边界。
6. **可观测性**：CLI 启动行新增 `instructions:`，并在 `render.py` 里把「provider 未报告 usage」渲染成 `-`（原来是 `None`，读起来像计数失败）。
7. **真实模型验证**：临时 workspace 写一份带可见标记的项目规范，确认模型确实照它执行。

## 5. 验证证据

### M4a · 上下文组装

**测试**（当时 133 个用例全绿，其中 25 个为本次新增）：

```bash
conda run -n nemo python -m unittest discover -s tests
```

```text
Ran 133 tests in 3.409s

OK
```

**真实运行**（workspace 为本仓库，因而注入了本仓库的 `AGENTS.md`）：

```bash
conda run -n nemo python examples/local_agent.py --workspace /Users/jiayuli/Documents/Nemo \
  --read-only "读一下 AGENTS.md 的前 3 行……"
```

```text
 4 model.completed tools=1 prompt=3797 cached=   0 completion=55
 5 tool.started read AGENTS.md
10 model.completed tools=0 prompt=3903 cached=3712 completion=215
```

第二轮命中 3712 / 3903 = **95.1%**：稳定前缀（系统提示 + 项目说明 + 工具 schema）在两轮之间逐字未变，只有新追加的历史与 reminder 需要重算。

**覆盖情况**：

| 验收点 | 对应验证 |
|---|---|
| 前缀逐字稳定 | `test_prefix_is_byte_identical_between_turns`、`test_prefix_does_not_depend_on_run_state` |
| reminder 只追加不改写 | `test_reminder_is_appended_not_merged`（逐字比较除末条外的全部消息） |
| reminder 不进会话记录 | `test_reminder_never_touches_the_state`、`test_reminders_never_reach_the_conversation_record`、`test_reminders_do_not_accumulate_across_turns` |
| 首轮不注入 | `test_first_turn_gets_no_reminder` |
| 后续轮注入在末尾 | `test_later_turns_append_the_reminder_at_the_end` |
| 注入结果可观测 | `test_model_started_event_reports_the_injection` |
| 项目说明位置与截断 | `test_project_instructions_follow_the_system_prompt`、`test_project_instructions_are_truncated_with_a_marker` |
| 项目说明缺失 | `test_missing_project_instructions_add_no_header`、`test_loading_instructions_handles_missing_and_empty_files` |
| 项目说明是快照 | `test_project_instructions_are_a_snapshot`（改文件后前缀不变） |
| reminder 契约 | `ReminderTests` 全部（命名、长度、闭合标签防护、确定性取舍） |
| 系统提示为模板原文 | `test_system_prompt_from_the_template_is_used_verbatim` |

### M4b · CLI 与三档审批

**测试**（全量 174 个用例全绿，其中 41 个为 M4b 新增）：

```bash
conda run -n nemo python -m unittest discover -s tests
```

```text
Ran 174 tests in 3.593s

OK
```

真实模型一次性任务（`--mode auto`，workspace 为临时目录）：

```text
mode     : auto (Codex: Auto)
transcript: /Users/jiayuli/.nemo/sessions/20260918-104656-2ae1e3.jsonl
  4 model.completed tools=1 prompt=1804 cached=0 completion=56
  5 tool.started create hello.txt
 10 model.completed tools=1 prompt=1894 cached=1664 completion=38
 16 model.completed tools=0 prompt=1958 cached=1792 completion=19
status  : completed
```

恢复同一会话（历史 6 条消息，前缀缓存仍然命中）：

```text
session  : 20260918-104656-2ae1e3 (resumed, 6 messages)
  4 model.completed tools=0 prompt=1975 cached=1792 completion=18
```

真实 TTY 下的审批（`script -q /dev/null` 分配伪终端）：

```text
  5 tool.started $ mkdir build

  approval needed: $ mkdir build
  why: 'mkdir' is not on the read-only list
  allow? [y] once / [a] this session / [n] deny > y
  6 tool.completed $ mkdir build
 11 tool.started $ ls -ld build
 12 tool.completed $ ls -ld build
status  : completed
```

同一轮里第二条命令是只读的 `ls -ld build`，**没有触发询问**——这正是「只读免问」的现场证据。反向的拒绝路径也跑过：输入 `n` 后 `tool.failed [denied]`，模型明确表示不会绕过，run 以 `completed` 结束。

**覆盖情况**：

| 验收点 | 对应验证 |
|---|---|
| 只读调用在三档都不问 | `test_read_only_tools_never_ask_in_any_mode`、`test_read_only_shell_commands_never_ask_in_any_mode` |
| `ask` 与 `auto` 的分界只在 `unclassified` | `test_unclassified_shell_commands_are_confirmed`、`test_workspace_writes_and_unknown_commands_run` |
| `auto` 拦破坏性命令 | `test_destructive_commands_are_confirmed`（`rm`/`sudo`/`git push`/重定向/越界路径） |
| `mv` 按作用域判定 | `test_unclassified_commands`、`test_destructive_commands` 中的 `mv` 两例 |
| `full` 从不询问且真的执行 | `test_nothing_is_confirmed`（删掉真实目录） |
| 原因不泄露命令内容 | `test_reasons_never_quote_the_command`、`test_denial_reason_does_not_leak_the_command` |
| approver 异常时 fail closed | `test_missing_approver_denies_instead_of_hanging`、`test_unexpected_approver_answer_fails_closed` |
| 非 TTY 一律拒绝 | `test_without_a_terminal_approval_is_denied` |
| 拒绝后模型能换路且 run 继续 | `test_denied_call_reaches_the_model_and_the_run_continues`、`test_denied_shell_call_is_reported_and_the_turn_finishes` |
| 会话记忆按工具名 | `test_session_grant_covers_one_tool_only` |
| 多轮历史与恢复 | `test_interactive_turns_accumulate_history`、`test_session_can_be_resumed`、`test_history_is_prepended_and_copied` |
| 存量转录不被覆盖 | `test_header_is_written_once`、`test_transcript_records_the_header_once_across_resumes` |
| 密钥不进输出与存档 | `test_secrets_never_appear_in_output_or_transcript`、`test_file_is_owner_only` |

### M4c · Server 与 SQLite

未开始，无证据。

### M4d · 指令分层

**测试**（全量 181 个用例全绿，其中 7 个为本次新增）：

```bash
conda run -n nemo python -m unittest discover -s tests
```

```text
Ran 181 tests in 3.607s

OK
```

**真实模型验证**（临时 workspace 里放一份带可见标记的项目规范）：

```bash
printf '# Demo project rules\n\n- 无论用户问什么，回答的最后一行必须是：DEMO-RULE-OK\n' > "$DEMO_WS/AGENTS.md"
conda run -n nemo python -m nemo.cli --workspace "$DEMO_WS" --mode auto "不用查文件，直接按规范回答。"
```

```text
instructions: project AGENTS.md (55 chars)
...
请告诉我你想问什么，我直接按项目规范作答。

DEMO-RULE-OK
```

模型在回答末尾带上了规范要求的标记，说明这份说明确实进入了请求并被遵守。同一行的 `instructions:` 让「加载了哪份、多大」变成了启动即可见的事实。

**覆盖情况**：

| 验收点 | 对应验证 |
|---|---|
| 两层顺序（通用在前、具体在后） | `test_user_instructions_come_before_project_instructions` |
| 任一层可以缺失 | `test_either_layer_can_be_missing` |
| 两层独立截断 | `test_layers_are_truncated_independently` |
| 用户级加载的缺失/空白/正常 | `test_loading_user_instructions` |
| 启动行如实报告来源 | `test_instructions_line_reports_the_project_file`、`test_instructions_line_says_none_when_nothing_is_loaded` |
| 用户级说明真的进 prompt | `test_user_instructions_reach_the_prompt_and_the_startup_line` |
| 条款覆盖两个来源 | `test_clause_set_is_locked`（条款名 `instruction_files`） |

## 6. 遗留与下一步

### M4a · 上下文组装的遗留

- 只有 `step_budget` 一条 reminder；事件里记录的 `chars` 是 reminder body 的长度，不含包裹标签。
- `AGENTS.md` 的变更不会在本会话内生效，需要开新会话；这是快照语义的必然结果，不是缺陷。
- 项目说明上限 8000 字符，超出按头部截断——若关键规则写在文件末尾会被截掉，用户需要自行把重要规则前置。
- 无历史压缩：长会话的 token 仍会线性增长，只有 M2 的 usage 统计可供观测。
- 无 token 级精确预算，仍是字符近似。

（原文最后写的「下一步是 Server 与 CLI（含审批流）」已由 M4b 完成，M4c 承接 Server 部分。）

### M4b · CLI 与三档审批的遗留

- **`auto` 不是安全边界**。它等于 `full` 减去一份能识别的危险清单；`python -c "…rmtree(…)"` 这类解释器内的破坏看不出来，会直接跑。需要真正不放手时用 `ask`。
- 规则表是人工维护的闭集：漏一个模式是一次静默放行，漏一个白名单是多问一次。真实使用一段时间后需要按实际命令调整。
- 命令文本可以绕开字面匹配（变量拼命令、别名、`r""m`），Phase 1 不做 shell 解析。
- SIGINT 通过取消标志生效，只在 step 边界与工具执行前后检查；卡死在某个系统调用里的工具需要按两次 Ctrl-C 或等超时。
- transcript 是脱敏副本，恢复后的历史与模型当初看到的不完全一致（有标记，且恢复时会打印）。
- 恢复会话要求 workspace 与当初一致才不影响前缀缓存；不一致时会提示，但不阻止。
- 事件里仍没有实际使用的模型标识（M2 遗留）。
- Observability 承诺的「耗时」尚未记录：事件有 `timestamp`，但没有 span duration。

### M4d · 指令分层的遗留

- 用户级文件需要用户自己创建：Nemo 不会自动生成 `~/.nemo/AGENTS.md`（那是用户偏好文件，不该由程序代言）。文件不存在时静默跳过。
- 两层都只有 8000 字符上限，没有总量控制。真实使用后如果两份都写得很长，需要重新评估预算。
- 恢复会话时只报告「当初加载了什么」，不校验项目说明是否变了——多轮会话的 `AGENTS.md` 是快照，改了要开新会话（沿用 M4a 语义）。
- **向上查找未实现**：只读 workspace 根一层。人常在仓库子目录里工作时找不到仓库根的约定；Codex 与 Claude Code 都会带上工作目录以上的层级。已记入 [TODO.md](../TODO.md) 的 T1，卡在「走到仓库根还是文件系统根」这个决定上。
- **2026-09-21 补的两处**（原实现的问题，非设计取舍）：① transcript 载入容忍损坏行——原实现遇到被杀的进程留下的半行 JSON 会让整个会话读不出来；现在跳过并计数，恢复时明确报告。② 掩码由 `***` 改为 `[redacted]` 且保留形状（`Bearer [redacted]`、`sk-[redacted]`），让恢复会话的模型读得出「这里有个值被扣下了」，而不是把 `***` 当成字面内容。

### M4c 的入口

- 先定 CLI 与 Server 的关系（见 §1 M4c）：CLI 改为 HTTP 客户端，还是维持进程内直连 + 两套存储。架构文档 §3 建议前者。
- 再按 Sessions/Runs 的真实形状定 SQLite schema；`Session` 与 `Run` 的边界已经在 M4b 里跑出结果，可以直接照搬。
- `src/nemo/cli/` 的 `Streams` 与 `Approver` 是可复用接缝；SSE 事件流应复用同一套事件类型，不再另起一套说法。
