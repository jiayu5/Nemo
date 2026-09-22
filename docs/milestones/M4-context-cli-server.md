# M4 · Context、CLI 与 Server

| 项 | 内容 |
|---|---|
| 状态 | 已完成 |
| 完成日期 | M4a：2026-09-17；M4b：2026-09-18；M4c/M4d：2026-09-21 |
| 阶段 | Phase 1 |
| 代码范围 | `core/context/`、`core/session.py`、`prompts/`、`cli/`、`server/`、`adapters/persistence/` |

M4 分为四条子线：M4a 上下文组装、M4b CLI/审批、M4c Server/SQLite、M4d 用户级与项目级说明分层。

## 1. 目标与范围

- **M4a**：稳定系统前缀、Session 历史和单轮 Reminder 分层。
- **M4b**：跨轮 Session、CLI REPL、三档审批、事件显示和脱敏。
- **M4c**：本地 FastAPI Server、SQLite、HTTP/SSE、远程审批和取消。
- **M4d**：`~/.nemo/AGENTS.md` 与 `<workspace>/AGENTS.md` 在 Session 开始时快照。

本步不做长期 Memory、Context 压缩、操作系统沙箱、远程多用户 Server、token delta 或桌面打包。

## 2. 交付物

- `ContextBuilder` 与 `Reminder`：稳定层/历史/临时注入。
- `Session`：只保存会话状态，不做 I/O。
- `ApprovalPolicy` 与命令分类器：`ask`、`auto`、`full`。
- CLI：默认 Server 客户端、REPL、SSE、审批、取消；`--direct` 保留旧 JSONL 路线。
- Server：Session/Run API、后台 task、SSE 续读和审批回传。
- SQLite：Messages、Runs、Events、Steps、Tool Calls 和启动恢复。
- 指令加载：用户级和项目级说明，读取发生在 Core 之外。

现行接口见 [Server API](../reference/server-api.md)，设计分别见 [Context](../design/context-assembly.md)、[CLI/审批](../design/cli-and-approval.md) 和 [Server/持久化](../design/server-and-persistence.md)。

## 3. 关键决策

- Context 稳定前缀在 Session 内冻结；Reminder 只进入当前模型请求。
- Session 与 Run 分离：历史跨 Run，终态和步数按 Run 独立。
- 审批策略只使用工具事实和机械命令分类，不让模型评估自身风险。
- `auto` 放行无法分类操作，因此不是安全边界。
- 默认客户端统一经 HTTP/SSE 使用 Server；Core 不依赖 FastAPI。
- Prompt 通过 JSON 进入内存并写入脱敏 Run 记录；正常收敛后再物化 Session Messages，不落独立 JSON 文件。
- SQLite 事件序号同时作为 SSE 重连游标；断线不取消 Run。
- 重启后活动 Run 标记为 `interrupted`，不重放有副作用的工具。
- 用户级说明先于项目级说明；两者都不能覆盖代码边界。

## 4. 实施摘要

M4a 先固定 Context 分层与缓存语义。M4b 用直连 CLI 验证 Session 手感和审批模型，并采用 JSONL 保存敏感 transcript。M4d 在 Server 前补齐用户/项目说明分层。M4c 最后把生命周期迁入 Server 和 SQLite，CLI 改为 HTTP/SSE 客户端，同时保留 `--direct` 作为恢复入口。

## 5. 验证

```bash
conda run -n nemo python -m unittest discover -s tests -v
conda run -n nemo python -m nemo.cli --help
conda run -n nemo python -m nemo.server
```

阶段验证节点分别达到：M4a 133、M4b 174、M4d 181、M4c 211 个测试通过。集成测试覆盖 Session、并发 Run 拒绝、SSE 续读、取消、审批、重启恢复与脱敏。

## 6. 遗留与下一步

- 项目说明只读取 workspace 根；向上查找记录为 TODO T1。
- 命令分类表需要人工维护，不能取代 Sandbox。
- Server 的 `allow_session` 当前只在单个 Run 的策略实例内生效，跨 Run grant 尚未持久化。
- Server 当前只有 loopback 约束，没有访问 token 和 Origin 检查；归 M6。
- 默认持久化已迁移到 SQLite；直连 JSONL 不主动迁移。
- UI 查询、模型目录和 Trace 在 M5a 补齐。

2026-09-22 结构整理把单体 Server service 拆为应用外观及 Session、Run、Catalog、Trace 用例，把 SQLite schema、事件投影和 Repository 移入 `adapters/persistence/`；接口、数据库 schema 与执行语义未改变。
