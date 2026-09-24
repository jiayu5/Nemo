# M7 · 上下文窗口与压缩

| 项 | 内容 |
|---|---|
| 状态 | 已完成 |
| 完成日期 | 2026-09-24 |
| 阶段 | Phase 2 |
| 代码范围 | `src/nemo/core/context/`、模型配置、Runtime、Server、CLI、`apps/ui/` |

## 1. 目标与范围

长会话接近模型窗口时，先把较早的完整对话轮次压缩成摘要，再把摘要与近期原文送入模型。保留原始 Session 历史；不把估算窗口解释为 API 余额或精确 token 限额。本步不做检索、长期 Memory 或账户费用查询。

## 2. 交付物

- `ContextEngine`：估算请求大小、生成摘要、校验与复用压缩检查点，并在无法安全组装请求时明确失败。
- 模型配置：可选上下文窗口与回复预留量，支持 TOML 和 Connections 编辑。
- Server 与直连 CLI：分别在现有事件和 JSONL transcript 中保存脱敏检查点，无数据库 schema 变更。
- Trace 与客户端：显示压缩事件、摘要及额外模型调用可用的 token 用量。

## 3. 关键决策

- 以较早的完整轮次为压缩单位，保持当前用户请求及工具调用链原文。
- 摘要不替换原始消息；检查点带消息数和 SHA-256 校验值，历史变化时重新压缩。
- 未配置窗口时保持原有组装行为。模型间 token 规则不同，采用保守估算并明确其局限。
- 压缩请求由当前模型完成，不提供工具；失败时不静默丢弃历史。

## 4. 实施摘要

在 M4 Context Builder 外加入 Context Engine；模型配置经 Resolver 传入 Runtime。超过窗口时生成摘要并发出压缩事件。Server 复用现有事件表，直连 CLI 复用 transcript 记录；Connections 提供窗口设置和摘要查看入口。

## 5. 验证

- `conda run -n nemo python -m unittest discover -s tests -q`：251 个测试通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `conda run -n nemo python -m nemo.cli --help`：通过。
- `npm --prefix apps/ui test -- --run`：17 个测试通过。
- `npm --prefix apps/ui run typecheck`、`npm --prefix apps/ui run build`：通过。

## 6. 遗留与下一步

估算值不保证与 Provider 的计数一致；超大的单轮内容可能无法压缩。后续里程碑处理检索、长期 Memory 与更精确的模型计数器。
