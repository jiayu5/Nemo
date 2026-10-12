# Nemo 文档

本目录保存 Nemo 的现行参考、组件设计、里程碑记录和待办。产品入口见 [README](../README.md)，系统总览见 [Architecture](../NEMO_ARCHITECTURE.md)，开发规则见 [AGENTS](../AGENTS.md)。

## 文档职责

| 类型 | 回答的问题 | 更新原则 |
|---|---|---|
| `README.md` | 如何安装、启动和使用 | 面向首次使用者，只保留主路径 |
| `NEMO_ARCHITECTURE.md` | 系统现在如何组成、未来如何演进 | 描述稳定边界，不记录实施流水账 |
| `AGENTS.md` | 修改项目时必须遵守什么 | 只保留可执行规则 |
| `reference/` | 当前接口和配置究竟是什么 | 必须与代码同步 |
| `design/` | 某个组件为什么这样设计、下一步怎么扩展 | 保留有效设计，淘汰内容明确标注 |
| `milestones/` | 某一阶段实际交付了什么 | 简要事实、决策、验证和遗留 |
| `TODO.md` | 哪些已决定事项尚未进入里程碑 | 开始实施后移出 |

同一事实只在一个地方详细说明，其他文档链接到权威来源。

## 当前状态

| 里程碑 | 状态 | 完成日期 | 记录 |
|---|---|---|---|
| M1 · Agent Runtime | 已完成 | 2026-09-16 | [M1](milestones/M1-agent-runtime.md) |
| M2 · 模型系统 | 进行中：`openai_compatible` 已完成 | 2026-09-17 | [M2](milestones/M2-model-system.md) |
| M3 · 本地执行与 Web 工具 | 已完成 | 2026-09-17；M3b 追加于 2026-09-21 | [M3](milestones/M3-local-execution.md) |
| M4 · Context、CLI 与 Server | 已完成 | 2026-09-17 至 2026-09-21 | [M4](milestones/M4-context-cli-server.md) |
| M5 · React UI | 已完成 | 2026-09-21 至 2026-09-22 | [M5](milestones/M5-react-ui.md) |
| M6 · macOS App | 已完成 | 2026-09-23 | [M6](milestones/M6-macos-app.md) |
| M7 · 上下文窗口与压缩 | 已完成 | 2026-09-24 | [M7](milestones/M7-context-compaction.md) |
| M8 · 工作区检索 | 已完成 | 2026-09-24 | [M8](milestones/M8-workspace-retrieval.md) |
| M9 · 长期记忆、会话追溯与项目说明继承 | 已完成 | 2026-10-08 至 2026-10-09 | [M9](milestones/M9-long-term-memory.md) |
| M10 · Skills | 已完成 | 2026-10-09 | [M10](milestones/M10-skills.md) |
| M11 · Subagents | 进行中：异步并行只读分派已实现，真实模型待验收 | — | [M11](milestones/M11-subagents.md) |

Phase 1 单 Agent 闭环与 Phase 2 已交付；M2 的其他协议适配仍未完成。Phase 3 的 M10 Skills 已实现，M11 Subagents 已实现异步并行只读分派。M9 记忆与 M10 技能的真实模型使用已由用户在真实会话中验收；自动化测试仍使用 Fake Model，不消耗模型 API。

## 现行参考

- [Subagents](reference/subagents.md)：异步并行分派、独立上下文、预算与部分报告、能力限制、嵌套事件与可查看详情。

- [Skills](reference/skills.md)：本地技能格式、加载时机、模型工具、复用 Codex 的内置 Skill Creator/Installer、正文分页、管理界面与兼容导入 API。

- [Memory](reference/memory.md)：用户/项目文件记忆、工具、管理 API 与来源追溯。
- [Server API](reference/server-api.md)：HTTP/SSE、Prompt 传递、生命周期和持久化。
- [配置参考](reference/configuration.md)：Provider、Model、Profile、密钥、环境变量和 Proxy。

## 组件设计

| 文档 | 状态 | 主题 |
|---|---|---|
| [subagents](design/subagents.md) | 首版已实现 | 异步并行只读分派、Run 生命周期与共享预算 |
| [skills](design/skills.md) | 已实现 | Session 使用期间冻结目录、按需加载、管理界面与内置 Skill Creator/Installer |
| [context-assembly](design/context-assembly.md) | 已实现 | 稳定前缀、项目说明、Reminder 与上下文压缩 |
| [long-term-memory](design/long-term-memory.md) | 已实现 | 用户/项目记忆、来源会话、追加式索引和管理界面 |
| [workspace-retrieval](design/workspace-retrieval.md) | 已实现；embedding 待评估 | 模型按需搜索、词法排序、短预览与历史复用 |
| [cli-and-approval](design/cli-and-approval.md) | 已实现 | Session、三档审批、终端交互与脱敏 |
| [server-and-persistence](design/server-and-persistence.md) | 已实现 | Server、SQLite、SSE 与生命周期 |
| [model-streaming](design/model-streaming.md) | 已实现 | 模型增量、工具调用组装、事件回放与客户端显示 |
| [ui-and-api](design/ui-and-api.md) | 已实现 | React UI、查询 API 与 Provider Settings 边界 |
| [provider-settings](design/provider-settings.md) | 已实现 | Provider/Model 写入、密钥、Proxy 与生效语义 |
| [web-tools](design/web-tools.md) | 已实现 | `web_search`、`fetch_url` 与网络边界 |
| [macos-app](design/macos-app.md) | 已实现 | Tauri 桌面壳、共享历史与 Run 属主协调、桌面访问保护及 `onedir` 打包 |

## Milestone 写法

每个 Milestone 固定六节：

1. 目标与范围
2. 交付物
3. 关键决策
4. 实施摘要
5. 验证
6. 遗留与下一步

记录允许纠错和压缩，但不得改变当时的交付结论。验证只保留执行命令、测试数量和结果；完整变更历史由 Git 保存。模板见 [_MILESTONE_TEMPLATE](milestones/_MILESTONE_TEMPLATE.md)。
