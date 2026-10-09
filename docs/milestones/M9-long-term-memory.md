# M9 · 长期记忆与会话追溯

| 项 | 内容 |
|---|---|
| 状态 | 已完成 |
| 完成日期 | 长期记忆：2026-10-08；项目说明继承：2026-10-09 |
| 阶段 | Phase 2 |
| 代码范围 | Core Memory 契约、文件适配器、记忆与会话工具、Server/CLI 装配、管理 UI、项目说明继承 |

## 1. 目标与范围

提供跨会话的用户偏好与项目记忆，并能按需查看原始会话依据。交付文件持久化、工具、管理界面与 M7 衔接；未引入 embedding、后台整理或数据库迁移。2026-10-09 收尾补齐项目说明继承：在仓库子目录创建会话时仍加载父级项目规则。

## 2. 交付物

- 用户及项目范围的 Markdown 条目、生成索引、来源字段和版本冲突检查。
- `search_memory`、`read_memory`、`save_memory`、`forget_memory` 工具。
- `search_sessions`、`read_session` 按需读取 Server 历史。
- HTTP 管理接口与 Memories 界面：查看、编辑、遗忘、来源及开关。
- Server 和直连 CLI 的文件记忆装配；开关使用既有配置文件。
- 项目说明加载器支持最近 Git 根到 workspace 的各层 `AGENTS.md`，共享 Server、CLI 与示例入口；规则见 [Context 设计](../design/context-assembly.md#3-稳定前缀)。
- [长期记忆设计](../design/long-term-memory.md)与 [Memory 参考](../reference/memory.md)。

## 3. 关键决策

项目归属使用规范化工作区路径，Session/Run 只记录来源。`AGENTS.md` 保留明确规则；记忆记录跨会话有价值的信息。正文按需读取，索引变化追加到历史，既有前缀保持不变。工具结果进入历史并可被 M7 压缩，源文件仍可重读。遗忘退出活动集合并保留退役副本，不代表擦除历史。

项目说明继承使用最近 Git 根作为边界，识别 `.git` 目录和文件；非 Git 目录只加载自身。说明按父级到子级排序，保持会话启动快照，不扩展普通文件工具的 workspace 边界。

## 4. 实施摘要

先建立存储端口及文件适配器，再接入工具、来源位置、Server/CLI 和管理界面。验证中补充了并发版本冲突、M7 压缩后重读、关闭行为以及配置开关不触碰密钥的检查。浏览器验收修正了弹窗按钮文字和顶栏布局。项目说明继承复用既有加载入口，标注来源相对路径，过滤根外符号链接和重复目标；无需改变数据库或客户端接口。

## 5. 验证

- `conda run -n nemo python -m unittest discover -s tests -q`：266 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `conda run -n nemo python -m nemo.cli --help`：通过。
- `npm --prefix apps/ui test -- --run`：20 项通过。
- `npm --prefix apps/ui run build`：TypeScript 检查及生产构建通过。
- 浏览器人工操作：独立测试存储下查看、保存、编辑记忆；按钮与顶栏显示正常。
- `git diff --check`：通过。

2026-10-09 项目说明继承收尾验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：268 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `git diff --check`：通过。

## 6. 遗留与下一步

真实模型的自动保存质量、召回准确性及调用成本已由用户在真实会话中验收（2026-10-09），未记录量化指标；自动测试仍使用 Fake Model，不消耗模型 API。目录移动和多个 worktree 不自动合并项目身份；历史工具不搜索直连 CLI transcript。其他首版边界见设计文档。

项目说明合并后共用原有长度上限，长父级说明可能导致后续内容截断；子目录按需加载与 override 文件暂不包含。
