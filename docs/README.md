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
| M5 · React UI | 进行中：M5a、M5b 已完成；M5c 未开始 | 2026-09-21 | [M5](milestones/M5-react-ui.md) |
| M6 · macOS App | 未开始 | — | 见 [Architecture](../NEMO_ARCHITECTURE.md#8-roadmap) |

## 现行参考

- [Server API](reference/server-api.md)：HTTP/SSE、Prompt 传递、生命周期和持久化。
- [配置参考](reference/configuration.md)：Provider、Model、Profile、密钥、环境变量和 Proxy。

## 组件设计

| 文档 | 状态 | 主题 |
|---|---|---|
| [context-assembly](design/context-assembly.md) | 已实现；T1 待定 | 稳定前缀、项目说明与每轮 Reminder |
| [cli-and-approval](design/cli-and-approval.md) | 已实现 | Session、三档审批、终端交互与脱敏 |
| [server-and-persistence](design/server-and-persistence.md) | 已实现 | Server、SQLite、SSE 与生命周期 |
| [ui-and-api](design/ui-and-api.md) | M5a/M5b 已实现，M5c 待设计 | React UI、查询 API 与 Provider Settings 边界 |
| [web-tools](design/web-tools.md) | 已实现；搜索后端待升级 | `web_search`、`fetch_url` 与网络边界 |

## Milestone 写法

每个 Milestone 固定六节：

1. 目标与范围
2. 交付物
3. 关键决策
4. 实施摘要
5. 验证
6. 遗留与下一步

记录允许纠错和压缩，但不得改变当时的交付结论。验证只保留执行命令、测试数量和结果；完整变更历史由 Git 保存。模板见 [_MILESTONE_TEMPLATE](milestones/_MILESTONE_TEMPLATE.md)。
