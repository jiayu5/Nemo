# M4 CLI 与审批设计

> 状态：**已实现**（2026-09-18）。实施过程与验证证据见 [M4-context-cli-server.md](../milestones/M4-context-cli-server.md) 的 M4b 小节；本文只保留仍然有效的设计本身。
> 整理日期：2026-09-18。
> 对应阶段：Phase 1 · Core / M4 的第一步——先做 CLI 与审批；Server、SQLite、SSE 留到 M4 的下一步。
> 相关实现：`src/nemo/core/contracts/tools.py`（`ToolPolicy`）、`src/nemo/core/tools/policy.py`、`src/nemo/core/tools/registry.py`、`examples/local_agent.py`。

## 1. 目标

让 Nemo 从「一条命令跑一次」变成「能天天用的会话式 CLI」，同时补上 M3 遗留的最大安全缺口：现在策略只能整体允许或拒绝，做不到「这一次问一下用户」。

三条可检验的目标：

1. **能连续用**：一个会话内可连续多轮交互，历史在会话内保持，退出后可恢复。
2. **危险操作可控**：三档审批模式，从「每次都问」到「全放行」，由用户显式选择。
3. **可解释**：每一次审批请求与结果都出现在事件流里，且输出与存档都不含密钥。

## 2. 三档审批模式（分层）

对齐 Codex 的三档（桌面端命名：Read Only / Auto / Full Access）。

| 档位 | 代码值 | 行为 | 判定者 |
| --- | --- | --- | --- |
| 每次都问 | `ask` | 写类工具先询问；`run_shell` 先看命令分类，**只读命令免问** | 人 |
| 自动判定 | `auto` | 代码按规则判定，只有识别得出来的危险才询问 | 机械规则 |
| 全权放行 | `full` | 不询问，直接执行 | 无 |

只读工具（`read_file`、`list_dir`）与只读 shell 命令（`ls`、`cat`、`git status` 一类）在三档里都直接放行——它们没有副作用，询问只是浪费一次交互。这条分类对 `ask` 与 `auto` 是同一套，区别只在**分类不出来**时怎么办（见 §4）。

**默认档是 `ask`**。Codex 默认 Auto，但它的 Auto 背后有沙箱；Nemo 没有，所以默认取更保守的一档，日常嫌打扰再 `--mode auto`。

**命名说明**：Codex 把第一档叫「Read Only」，但它的实际行为是「改动前先问」，不是「不许改」。代码里用 `ask` 更贴行为；CLI 输出里同时标注 Codex 的叫法，避免两边对照时对不上。

**三档共用的硬边界**：workspace 路径检查在三档下都生效，审批不能让 `resolve_in_workspace` 失效。`full` 放行的是「这个工具可以跑」，不是「路径检查被关掉」。要突破 workspace 只有 `run_shell` 一条路——这正是下一节的重点。

## 3. 决策与理由

| 决策 | 理由 | 放弃的替代方案 |
| --- | --- | --- |
| 审批通过扩展 `ToolPolicy` 实现，不动 `AgentRuntime` | M3 把 `decide()` 设成 async 就是为这一刻；Runtime 对审批零知识，分层不破 | 在 Runtime 里加审批分支：Runtime 重新认识「策略」这件事，M2 定下的边界失效 |
| `auto` 用机械规则，不让模型自评 | 提出操作的是模型本身，自评等于自证；且不可复现、多一次往返、受提示注入影响 | 让模型给危险度打分：结论不稳定，错了没有规则可改 |
| 白名单闭集 + 默认怀疑 | 漏判的代价是「多问一次」，而不是「少拦一次」 | 黑名单命中才问：漏一个模式就是一次静默破坏 |
| 只读 shell 命令在 `ask` 档也免问 | 分类器只有一个，两档共用；`ls` 没有副作用，为它中断一次是纯损耗 | 第一档一律询问：语义更容易解释，但每次查看目录都要按一次回车 |
| 拒绝不终止 run，回填 `denied` 让模型换路 | 一次误判不该毁掉整个任务；`denied` 已是合法错误码，链路已通 | 拒绝即取消 run：模型没有补救机会，用户还要重新描述需求 |
| 会话记忆按工具名，随会话丢弃 | 粒度与 `read_only` 同层，好解释；不落盘，避免审批状态跨会话漂移 | 按具体命令记住：需要一份稳定的命令指纹，复杂度与收益不划算 |
| `mv`/`cp` 按作用域判定，`rm` 一律询问 | 工作区内重命名是常规操作，可按参数判定；删除不可逆，参数再安全也不放过 | 把 `mv` 也列为危险动词：`auto` 档每次改名都打断，最后会逼用户切到 `full` |
| 事实用 `ToolCallFacts` 结构体传给策略，而不是继续加关键字参数 | M3 说「签名不用再切」已经被 `shell_command` 证明是错的；再加一个事实不该再切一次 | 继续加参数：每加一个事实，所有策略实现都要改一遍签名 |
| shell 命令文本由工具自己声明（`RunShellTool.shell_command`） | 策略需要的是命令原文，core 又不能 import 适配器；让工具声明是最短的桥 | 策略按参数模型鸭子取值：字段改名后静默失效 |
| 不加 `tool.approval_requested` 事件 | 审批请求本身就是问用户的那个提示，CLI 直接渲染；为一个没有消费者的场景先铺一条 registry→runtime 的数据通路，是本末倒置 | 现在就加事件：Server 还没影，事件结构大概率还要改一次 |
| transcript 用 JSONL 而非 SQLite | 会话模型未定时先定 schema 是规范里的红线；JSONL 可读可 grep，也是未来迁移的数据源 | 直接上 SQLite：为 Server 设计的数据模型在 CLI 阶段几乎一定要改 |
| `step_count` 每轮重置 | `max_steps` 防的是单轮无限循环；跨轮累计会让长会话在第 N 轮撞死 | 跨轮累计：长会话不可用，reminder 语义也变成「会话剩余步数」，与护栏目的不符 |
| 非交互（stdin 非 TTY）时 fail closed | 没有人在场就没有审批；默认拒绝比默认放行安全 | 非交互时自动放行：脚本一跑就绕过了全部审批 |

## 4. 命令分类与升级判据

**先说清定位：`auto` 是减少打扰的启发式，不是安全保证。**

Codex 的 Auto 之所以可信，是因为底下有沙箱：写入被内核限制在 workspace 内，只有越界才需要审批。Nemo 的沙箱排在 Phase 3，现在没有那一层。**`auto` 等于 `full` 减去一份它认识得出来的危险清单**——这就是全部含义。需要「绝对不放手」时用 `ask`。

判定输入有三类，全部是机械事实：

| 来源 | 事实 | 用途 |
| --- | --- | --- |
| 工具自报 | `read_only` 标志 | 只读工具直接放行 |
| **命令形态** | `run_shell` 的命令原文（由工具自己声明） | `auto` 的主要判据 |
| 参数解析 | 路径参数是否落在 workspace 内 | 越界的写操作升级为询问 |

**为什么不让模型自己判断。** 提出这个操作的正是模型本身，「你觉得这个危险吗」等于让它给自己打分：结论不确定、每次多一次模型往返、还会被刚读到的文件内容影响（提示注入）。规则判定可复现、可测试，漏了能补规则。

分类结果是三个值（`command_rules.classify()`）：

| 分类 | 含义 | 例子 |
| --- | --- | --- |
| `read_only` | 闭集白名单里的只读命令，且不含任何结构信号 | `ls -la`、`cat f`、`grep -rn x .`、`git status`、`find . -name '*.py'`、`ls \| head -5` |
| `destructive` | 危险动词、范围不可界的结构，或指向 workspace 之外的路径 | `rm -rf build`、`sudo ls`、`echo x > f`、`cat $(…)`、`… \| bash`、`mkdir /tmp/x`、`cd /tmp && …` |
| `unclassified` | 无法归类 | `mkdir build`、`mv a.txt b.txt`、`python -m pytest`、`make`、`git commit -m x` |

**结构信号**（命中即 `destructive`，不再看后面的内容）：重定向 `>`/`>>`（`2>&1` 不算，它是合并错误流）、命令替换 `$()`/反引号、管道进 shell，以及包装器（`env`、`xargs`、`time`、`timeout`、`nohup`、`nice`、`watch` 等）——包装器会递归到内层命令，递归不出来的按 `destructive` 处理。shell 本身（`sh`/`bash`/`zsh`…）无条件算 `destructive`：在 shell 里再起一个 shell，影响范围从文本上就界定不了。

**动词分两类**，这是这版规则里最关键的一条切分：

- **与参数无关的危险动词**：`rm`、`rmdir`、`unlink`、`shred`、`dd`、`mkfs`、`chmod`、`sudo`、`curl`、`ssh`、`kill`、`crontab` 等。参数再干净也不放过——删除不可逆，网络外发收不回。
- **只与作用域有关的动词**：`mv`、`cp`、`mkdir`、`touch` 等。工作区内是常规操作，落到 `unclassified`；参数指向 workspace 之外时才升级为 `destructive`。

**档位映射**（三档的全部差异就在 `unclassified` 这一行）：

| 分类 | `ask` | `auto` | `full` |
| --- | --- | --- | --- |
| `read_only` | 放行 | 放行 | 放行 |
| `destructive` | 询问 | 询问 | 放行 |
| `unclassified` | **询问** | **放行** | 放行 |

非 shell 工具同理：`read_file`/`list_dir`（`read_only=true`）三档放行；`write_file`/`edit_file` 在 `ask` 询问、在 `auto` 与 `full` 放行（它们已经被 workspace 路径检查约束）。

**「默认怀疑」的落点**：白名单是闭集，未命中就不算只读。`ask` 档因此对一切说不清的操作都问一次；`auto` 档则必须放行 `unclassified`，否则它与 `ask` 完全重合，这一档就没有存在意义——**所以 `auto` 的宽松不是妥协，而是它的定义**。

**已知局限**（必须明说，不能假装它是沙箱）：

- 命令文本可以绕开字面匹配（变量拼命令、别名、`r""m` 之类），Phase 1 不做 shell 解析。
- 解释器内的破坏看不出来：`python -c "…rmtree(…)"` 是 `unclassified`。这是没有沙箱的必然结果。
- 白名单动词仍有非只读用法（`find -delete` 已拦，`git log --output=` 这类没拦），靠人工维护规则表逐步补齐。
- 引号内的 `>` 会被当成数据而忽略，把 `-o` 之后的值当作参数检查——都是启发式，不是解析器。

## 5. 询问的交互形状

| 项 | 决定 |
| --- | --- |
| 展示内容 | 工具名 + 工具自报的 `summary`（已有 `Tool.summarize()`）+ 询问原因 |
| 不展示 | 工具参数的原始 JSON。`summary` 是工具自己写的安全描述 |
| 选项 | `y` 本次允许 / `a` 本会话内该工具不再询问 / `n` 拒绝 |
| 拒绝后 | 一次 `ToolResult(error.code="denied")` 回填，循环**继续**，模型可以换个做法；不是终止 run |
| 会话记忆 | 记在 `ApprovalPolicy` 实例上，按工具名，随会话结束丢弃；可测试、可重置 |
| 非交互（stdin 非 TTY） | fail closed：`ask` 档全部按拒绝处理；`auto` 的升级项同理。脚本要放行就显式切到 `full`，不另设 `--approve-all` 开关——它和 `full` 是同一件事 |
| Ctrl-C 在询问中 | 取消整个 run（符合终端直觉）。单次拒绝用 `n`，不用 Ctrl-C |

**实现要点**：`input()` 必须放进 `asyncio.to_thread`。直接在协程里调用会阻塞事件循环，导致审批期间超时和取消全部失效——这是最容易踩且最难排查的一个坑，测试要专门锁住它。

## 6. 会话模型

| 概念 | 内容 |
| --- | --- |
| `Session` | 持有 `session_id`、`messages`、`workspace`。**只保存状态，不做 I/O**；模型与前缀属于装配，不属于会话 |
| `Run` | 一轮：一次用户输入到终态。沿用 M1 已有的 `AgentRuntime.run()` |
| `session_id` | 会话标识，进程内生成，随 transcript 持久化 |
| `run_id` | 每轮新生成，沿用现状 |
| `step_count` | **每轮重置**，`max_steps` 是「这一轮最多几步」的护栏 |

**前缀冻结**：`ContextBuilder` 在会话开始时构造一次，会话内不重建。这与 M4 已有的快照语义一致——`AGENTS.md` 改了要开新会话才生效。

**step budget 每轮重置**（此处更正我在讨论中的倾向）。跨轮累计会让长会话在第 N 轮直接撞上上限后无法继续，而 `max_steps` 要防的是「单轮无限循环」，不是「会话太长」。每轮重置后，reminder 的含义是「本轮还剩多少步」，仍然有意义。

**运行如何接上历史**：`AgentRuntime.run()` 新增 `history` 参数，把历史拷进一次新 run，run 之间的 `run_id`、事件流与步数互不影响。会话不持有 run，run 也不改写会话——`Session.record()` 只在 run 结束后把新消息并进来，所以「已发送的消息不可改写」这条不变量仍然成立。

**持久化用 JSONL，不用 SQLite**：一行一条记录，写到 `~/.nemo/sessions/<session-id>.jsonl`，权限 600（transcript 含文件内容，按敏感数据处理）。理由是 SQLite 属于 Server 那一步，现在引入就要先定 schema，而 schema 变更在项目规范里是红线。JSONL 可读、可 grep、可手工核对，未来由 SQLite 接管时它就是迁移的数据源。

transcript 是**脱敏后的副本**：写入时任何形如凭据的文本都会被替换，并在该行打 `redacted` 标记。代价要说清楚——恢复会话时，模型看到的是脱敏后的历史，而不是它当初看到的那一份。所以恢复时会明确打印「本次转写中有 N 条消息被脱敏」，**让这个差异可见，而不是静默改写历史**。原始内容不落盘是硬规则：日志里出现过的密钥收不回来。

## 7. 事件与脱敏

| 项 | 决定 |
| --- | --- |
| `run.started` payload | 增加 `approval_mode`，让事件流能自证当时用的哪一档 |
| 允许 | 复用现有 `tool.started` / `tool.completed` |
| 拒绝 | 复用现有 `tool.failed`，`error_code="denied"` |
| 脱敏 | `summary` 已有「压单行 + 截断 200 字符」，这一步补密钥形态遮蔽：`Bearer …`、`api_key/token/secret/password=…`、`sk-…`，替换为 `[redacted]` 且**保留形状**（`Bearer [redacted]`）。**永不打印工具参数原文**；模式是安全网，不是保证 |

两处刻意不新增事件类型：

- **拒绝**不新增，因为 `denied` 已经是 `ToolError.code` 的合法取值，链路已经通了；新增只会制造两套说法。
- **审批请求**不新增。审批请求本身就是「问用户的那句话」，由 CLI 直接渲染；为一个还没有消费者的场景先铺一条 registry→runtime 的数据通路是本末倒置。Server 到位后如果确实需要，再用同一个 `Approver` 接缝实现。

**分类原因不携带命令内容**：`ApprovalRequest.reason` 会进入 `ToolError` 并到达模型，因此规则原因只说动词或形态（`'rm' can destroy data…`、`a path argument points outside the workspace`），**从不回显参数**——命令里可能内联了凭据。这条由测试锁定。

## 8. 接口形状

```python
class ApprovalMode(StrEnum):
    ASK = "ask"
    AUTO = "auto"
    FULL = "full"


class ApprovalOutcome(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY = "deny"


@dataclass(frozen=True)
class ApprovalRequest:
    tool_name: str
    summary: str
    reason: str          # 为什么需要询问：命中了哪条规则，或因为哪一档


Approver = Callable[[ApprovalRequest], Awaitable[ApprovalOutcome]]


class ApprovalPolicy:
    """Implements the ToolPolicy Protocol; the Runtime stays untouched."""

    def __init__(self, mode: ApprovalMode, *, approver: Approver | None = None) -> None: ...

    @property
    def session_grants(self) -> frozenset[str]: ...


class ToolCallFacts(Contract):
    """What the registry knows about one call; the policy's only input."""

    tool_name: str
    read_only: bool
    summary: str = ""
    shell_command: str | None = None   # 工具声明，缺席 = 这个调用不跑 shell
```

**`ToolPolicy.decide()` 的签名改了一次**：M3 说「async 签名以后不用再切」，实际加 `shell_command` 时还是得切。改法是收成一个 `ToolCallFacts` 结构体，而不是继续加关键字参数——下次再多一个事实，策略实现不用再改一遍。`AgentRuntime` 仍然零改动。

core 不做 I/O：真正的「问人」由 CLI 通过 `Approver` 注入；没接 approver 时策略一律拒绝（fail closed），所以非交互场景不需要额外分支。

文件落点：

| 文件 | 作用 |
| --- | --- |
| `src/nemo/core/tools/approval.py` | `ApprovalMode`、`ApprovalOutcome`、`ApprovalRequest`、`ApprovalPolicy` |
| `src/nemo/core/tools/command_rules.py` | `run_shell` 命令形态分类：`classify(command) -> Classification` |
| `src/nemo/core/session.py` | `Session`：会话标识、消息历史、workspace；纯状态，无 I/O |
| `src/nemo/cli/transcript.py` | JSONL 读写：脱敏、`redacted` 标记、600 权限、恢复时报告受损条数 |
| `src/nemo/cli/` | `python -m nemo.cli`：参数解析、REPL、`Approver` 实现、事件渲染、脱敏 |

CLI 入口先用 `python -m nemo.cli`，不加 console script——打包成 `nemo` 命令属于 M6。

## 9. 验收标准

| 验收点 | 验证方式 |
| --- | --- |
| `ask` 档写类调用必问 | 脚本化 `Approver` 断言被调用，且 `summary` 与工具自报一致 |
| 只读调用在三档都不问 | `read_file`、`list_dir`、`ls -la`、`cat /etc/hosts` 不触发 approver |
| `ask` 与 `auto` 的分界只在 `unclassified` | 同一条 `mkdir build`：`ask` 询问、`auto` 不询问 |
| `auto` 询问破坏性命令 | `rm -rf build`、`sudo ls`、`git push`、`echo x > f`、`mkdir /tmp/outside` 触发 approver |
| `mv` 按作用域判定 | `mv a.txt b.txt` 在 `auto` 放行；`mv a.txt /tmp/b.txt` 触发 approver |
| `full` 从不询问 | 断言 approver 从未被调用 |
| 分类器不因畸形输入崩溃 | 空串、未闭合引号、纯 `|`/`&&` 都返回合法分类 |
| 原因不泄露命令内容 | 含 `sk-` 与 `Bearer` 的命令被拒绝后，`ToolResult` 全文不含该串 |
| approver 返回非法值时 fail closed | 返回 `"yes please"` 仍按拒绝处理 |
| 拒绝后循环继续 | 拒绝 → `denied` 回填 → 下一轮模型换做法，run 以 `completed` 结束 |
| 会话记忆生效 | 选 `a` 后同一工具第二次不再询问；不同工具仍会问 |
| 非交互 fail closed | `stdin` 非 TTY 时 `ask` 档按拒绝处理 |
| 审批不阻塞事件循环 | 询问挂起期间，取消仍能在预期时间内生效 |
| 多轮会话历史保持 | 第 2 轮请求包含第 1 轮的完整消息，且前缀逐字未变 |
| step budget 每轮重置 | 第 2 轮的首个模型请求不含 `<reminder>`，`step` 从 1 起计 |
| 输出与存档脱敏 | 事件流与 transcript 中都不含 API key |
| 真实闭环 | 真实 DeepSeek 上完成「建文件 → 改文件」，中途拒绝一次写入 |

## 10. 非目标

- Server、FastAPI、SSE、SQLite：M4 的下一步，等 CLI 定了会话手感再定数据模型。
- 流式输出：等 M5 的 UI 有真实消费者。
- 沙箱、容器隔离：Phase 3。因此 `auto` 只能是启发式。
- 完整 shell 解析与命令黑名单完善：Phase 1 接受启发式的已知局限。
- workspace 之外的文件读写：**仍然硬拒绝**，本步不给审批开口子。要读别处就换 `--workspace`。
- 多会话并发、TUI、命令补全、历史检索。
- 工具删除能力：现状刻意没有，不在本步增加。

## 11. 与 Codex 的对照

| | Codex | Nemo 本步 |
| --- | --- | --- |
| 档位 | Read Only / Auto / Full Access | `ask` / `auto` / `full` |
| Auto 的判定依据 | 沙箱边界（越界才问）+ 规则 | 仅规则（无沙箱可用） |
| 判定的可信度 | 机制保证 | 启发式，最坏等同 `full` |
| 询问选项 | 允许一次 / 本会话允许 / 拒绝 | 同 |
| 拒绝的后果 | 回填给模型，继续 | 同（`ToolError.code="denied"`） |

这张表里唯一实质差异是「Auto 的判定依据」：Codex 靠沙箱把范围问题变成机制问题，我们只有规则表。**这不是可以靠努力弥补的差距，而是 Phase 3 之前的结构性限制**——因此文档里不给 `auto` 任何安全承诺。
