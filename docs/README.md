# Nemo 文档库

这里记录 Nemo 的实施过程：每个里程碑做了什么、为什么这么做、怎么验证的。

## 与架构文档的分工

| 文档 | 回答的问题 | 内容属性 |
|---|---|---|
| [NEMO_ARCHITECTURE.md](../NEMO_ARCHITECTURE.md) | 要做成什么 | 设计基线，可包含尚未实现的内容 |
| [AGENTS.md](../AGENTS.md) | 怎么做、边界在哪 | 协作与工程规则 |
| `docs/milestones/` | 已经做成了什么 | 已发生的事实与可复现证据 |

里程碑记录**不写计划**。计划属于架构文档；记录只描述已经发生的事，并且每条结论都要能追溯到实际执行过的命令或代码位置。

## 目录结构

```text
docs/
├── README.md                      本文件：索引与写作约定
├── TODO.md                        已决定但还没有里程碑承接的事项
├── design/
│   ├── context-assembly.md        M4 上下文组装设计（已实现）
│   ├── cli-and-approval.md        M4 CLI 与三档审批设计（已实现）
│   └── server-and-persistence.md  M4c Server、SSE 与 SQLite 设计（已实现）
└── milestones/
    ├── _MILESTONE_TEMPLATE.md     记录模板,下划线前缀排在正文之前
    ├── M1-agent-runtime.md        Agent Runtime 最小闭环（已完成）
    ├── M2-model-system.md         模型系统（M2a 已完成）
    ├── M3-local-execution.md      本地执行（已完成）
    └── M4-context-cli-server.md   M4 一个里程碑一个文件，内部按 M4a/M4b/M4c 分节
```

## 命名与编号

- 文件名格式：`M<编号>-<英文短名>.md`，例如 `M1-agent-runtime.md`。
- 编号与 `NEMO_ARCHITECTURE.md` 中的里程碑编号一致（M1、M2、M3……），不另起一套序号。
- 一个里程碑一个文件。按完成顺序追加，不覆盖、不回改历史记录。
- 一个里程碑拆成多步交付时，**用 `M<编号><字母>` 编号并在同一文件内作为小节**（例如 M4 里的 M4a/M4b/M4c），不新增文件、不另起编号：目录里每个编号只出现一次。
- 已决定但还没有里程碑承接的独立事项写进 [TODO.md](TODO.md)；做完即移除，并把过程写进对应里程碑记录。里程碑内部的下一步仍写在里程碑记录的 §6。
- 若后续结论推翻了旧记录，在新记录中写明取代关系，并在旧记录末尾补一行指向新记录，保留当时的判断。

## 写作约定

每个里程碑固定六节（更细的内容作为小节放在对应章节内，不新增同级章节，保证编号稳定）：

1. **目标与范围**：要解决什么问题，明确列出「做」与「不做」。
2. **交付物**：新增或修改了哪些文件，各自作用。
3. **关键决策与理由**：为什么这么做，以及放弃了哪些替代方案。
4. **实施过程**：实际走过的步骤，包括方案中途调整的转折点。
5. **验证证据**：实际执行的命令与结果，不是「应该能跑」。
6. **遗留与下一步**：已知边界、刻意未做的部分、下一步的入口。

文档使用中文，代码、接口与文件名使用英文；架构图使用 Mermaid。不记录密钥、token 或临时工具输出。

## 里程碑索引

| 编号 | 里程碑 | 状态 | 完成日期 | 记录 |
|---|---|---|---|---|
| — | 架构基线（`NEMO_ARCHITECTURE.md`） | 已完成 | 2026-09-16 | 见架构文档本身 |
| M1 | Agent Runtime 最小闭环 | 已完成 | 2026-09-16 | [M1-agent-runtime.md](milestones/M1-agent-runtime.md) |
| M2 | 模型系统（配置驱动、协议适配器） | 进行中 | M2a：2026-09-17 | [M2-model-system.md](milestones/M2-model-system.md) |
| M3 | 本地执行（系统提示、五个工具、Policy） | 已完成（M3b 可选扩展未做） | 2026-09-17 | [M3-local-execution.md](milestones/M3-local-execution.md) |
| M4 | Context、CLI 与 Server（M4a 上下文组装 / M4b CLI 与三档审批 / M4c Server 与 SQLite / M4d 指令分层） | 已完成 | M4a：2026-09-17；M4b：2026-09-18；M4c、M4d：2026-09-21 | [M4-context-cli-server.md](milestones/M4-context-cli-server.md) |

状态取值：进行中 / 已完成 / 已被取代。

> 架构基线未单独建里程碑记录：它的产物就是 `NEMO_ARCHITECTURE.md`，重复存一份容易产生两个版本的真相。

## 设计文档索引

`docs/design/` 保存尚未实现或部分实现的组件级设计。与里程碑记录的区别：这里写的是**将要怎么做**，因此允许包含未落地内容；一旦实现完成，实施过程与验证证据写入对应的里程碑记录，设计文档只保留仍然有效的设计本身。

| 文档 | 主题 | 状态 |
|---|---|---|
| [context-assembly.md](design/context-assembly.md) | M4a 上下文组装：稳定前缀、项目说明快照、每轮注入 | 已实现 |
| [cli-and-approval.md](design/cli-and-approval.md) | M4b CLI：会话模型、三档审批（`ask`/`auto`/`full`）、事件与脱敏 | 已实现 |
| [server-and-persistence.md](design/server-and-persistence.md) | M4c 本地 Server：Session/Run API、SSE、审批回传与 SQLite | 已实现 |
