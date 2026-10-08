# M9 · 长期记忆与会话追溯

| 项 | 内容 |
|---|---|
| 状态 | 已完成 |
| 完成日期 | 2026-10-08 |
| 阶段 | Phase 2 |
| 代码范围 | Core Memory 契约、文件适配器、记忆与会话工具、Server/CLI 装配、管理 UI |

## 1. 目标与范围

提供跨会话的用户偏好与项目记忆，并能按需查看原始会话依据。交付文件持久化、工具、管理界面与 M7 衔接；未引入 embedding、后台整理或数据库迁移。

## 2. 交付物

- 用户及项目范围的 Markdown 条目、生成索引、来源字段和版本冲突检查。
- `search_memory`、`read_memory`、`save_memory`、`forget_memory` 工具。
- `search_sessions`、`read_session` 按需读取 Server 历史。
- HTTP 管理接口与 Memories 界面：查看、编辑、遗忘、来源及开关。
- Server 和直连 CLI 的文件记忆装配；开关使用既有配置文件。
- [长期记忆设计](../design/long-term-memory.md)与 [Memory 参考](../reference/memory.md)。

## 3. 关键决策

项目归属使用规范化工作区路径，Session/Run 只记录来源。`AGENTS.md` 保留明确规则；记忆记录跨会话有价值的信息。正文按需读取，索引变化追加到历史，既有前缀保持不变。工具结果进入历史并可被 M7 压缩，源文件仍可重读。遗忘退出活动集合并保留退役副本，不代表擦除历史。

## 4. 实施摘要

先建立存储端口及文件适配器，再接入工具、来源位置、Server/CLI 和管理界面。验证中补充了并发版本冲突、M7 压缩后重读、关闭行为以及配置开关不触碰密钥的检查。浏览器验收修正了弹窗按钮文字和顶栏布局。

## 5. 验证

- `conda run -n nemo python -m unittest discover -s tests -q`：266 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `conda run -n nemo python -m nemo.cli --help`：通过。
- `npm --prefix apps/ui test -- --run`：20 项通过。
- `npm --prefix apps/ui run build`：TypeScript 检查及生产构建通过。
- 浏览器人工操作：独立测试存储下查看、保存、编辑记忆；按钮与顶栏显示正常。
- `git diff --check`：通过。

## 6. 遗留与下一步

真实模型的自动保存质量、召回准确性及调用成本尚未验收；自动测试使用 Fake Model，不消耗模型 API。目录移动和多个 worktree 不自动合并项目身份；历史工具不搜索直连 CLI transcript。其他首版边界见设计文档。下一步在真实会话中验证记住偏好、同项目新会话召回、不同项目隔离、纠正和遗忘行为。
