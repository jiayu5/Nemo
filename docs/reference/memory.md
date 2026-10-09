# Memory 参考

## 存储

记忆开关使用 `~/.nemo/config.toml` 的顶层 `memory_enabled`，默认 `true`。首次更改前须完成模型配置；更改开关不读写密钥文件。

默认根目录为 `~/.nemo/memory/`：

```text
memory/
├── MEMORY.md                 # 用户记忆的生成索引
├── entry-<id>.md              # 用户记忆
├── forgotten/                # 已退役的用户记忆
└── projects/<project-id>/
    ├── MEMORY.md             # 项目记忆的生成索引
    ├── entry-<id>.md          # 项目记忆
    └── forgotten/            # 已退役的项目记忆
```

`project-id` 为工作区规范化绝对路径的 SHA-256 前 24 位；不同目录不共享项目记忆。文件包含 `---` 分隔的 JSON 元数据和 Markdown 正文。`MEMORY.md` 是可阅读的派生目录，不是正文权威来源；运行时从条目生成有界索引，手动编辑生成索引不会修改记忆。

建议通过 UI 或工具编辑条目，保留字段结构。损坏文件会明确报错，不静默跳过或覆盖。退出应用不会清除这些文件。记忆根目录由 Bootstrap 装配，模型不能指定任意存储路径。

| 字段 | 含义 |
|---|---|
| `id` | 32 位十六进制条目 ID |
| `scope` | `user` 或 `project` |
| `title` / `content` | 标题与正文 |
| `kind` | `preference`、`feedback`、`project`、`reference` |
| `workspace` | 项目工作区规范路径；用户记忆为 null |
| `revision` | 返回时计算的文件 SHA-256，更新与遗忘必须提供 |
| `updated_at` | 最近写入的 UTC 时间 |
| `source_session_id` | 最新保存操作的来源会话；手动保存为 null |
| `source_message_index` | 来源用户消息的零基位置 |
| `source_run_id` | 来源 Run；直连 CLI 可为空 |

手动编辑标记为手动来源；模型更新使用当前会话来源。此版本不保留完整修改历史。来源会话被删除后，记忆仍在，但无法再读取已删除的依据。

每范围最多 100 条活动记忆；标题最多 100 字符，正文最多 3000 字符；单文件读取最多 24000 字节。输入索引约 3500 字符，超出时给出搜索提示。写入采用文件锁（最多等待 2 秒）和原子替换；多文件更新不是数据库事务，生成索引可由后续保存重建，条目文件为权威数据。

## 模型工具

| 工具 | 参数 | 行为 |
|---|---|---|
| `search_memory` | `query=""`、`scope="all"`、`offset=0`、`limit=5` | 用户与当前项目词法检索；空查询列出；limit 最多 10；返回标题、160 字符预览和下一页 |
| `read_memory` | `scope`、`id` | 返回完整条目、版本和来源 |
| `save_memory` | `scope="project"`、`title`、`content`、`kind="project"`、可选 `id/revision` | 新建或按版本更新；来源由执行上下文绑定 |
| `forget_memory` | `scope`、`id`、`revision` | 移至 `forgotten/`，不再参与正常召回 |
| `search_sessions` | `query=""`、可选 `workspace`、`offset=0`、`limit=5` | 默认同工作区，排除当前会话；最多扫描一页 50 个会话的最近 20 个 Run 的用户请求和最终回答；limit 最多 10 |
| `read_session` | `session_id`、可选 `workspace`、`offset=0`、`limit=4`、`content_offset=0`、`max_chars=1600` | 分页读取消息；limit 最多 10，max_chars 为 100–4000；返回消息位置和续读位置 |

历史工具中的 `workspace` 必须是显式绝对路径，用来选择用户请求查看的其他项目历史。检索不是全库语义搜索，也不扫描每条工具输出；查询可能漏掉同义表达。`read_session` 可以按来源位置读取工具结果和参数，不返回 reasoning 字段。超出 `ExecutionContext.max_output_chars` 时返回错误，要求缩小范围；只读检索还受执行上下文超时约束。

Memory 开关控制模型的四个 memory 工具和索引内容，不控制独立的历史会话工具。关闭后禁止新记忆读取和生成，历史里的旧内容仍存在；应用会在下轮追加关闭通知。提示遵守与否不是代码层的内容擦除保证。

## 管理界面

Memories 每次打开自动读取当前记忆，默认采用与 Skills 一致的紧凑双列列表，窄屏单列。列表显示标题和淡色单行内容摘要，范围及项目路径收进悬停提示，无手动 Refresh。

点击条目展开完整正文、分类、更新时间和管理操作；Edit、Forget（仍需单独确认）、来源查看只在详情展示。Add memory 按需展开新增表单，保存成功收起，错误时保留草稿。顶部保留 Memory enabled 开关，控制模型召回与保存，不删除记忆或既有聊天。

## HTTP 管理 API

| 方法 | 路径 | 请求 / 返回 |
|---|---|---|
| GET | `/sessions/{session_id}/memories` | `{enabled, entries}`，用户与该工作区的活动记忆 |
| POST | `/sessions/{session_id}/memories` | `scope/title/content/kind`，更新附带 `id/revision`；返回条目 |
| POST | `/sessions/{session_id}/memories/forget` | `scope/id/revision`；返回 `{status: "forgotten"}` |
| PUT | `/memory/settings` | `{enabled: boolean}`；返回相同开关状态 |

参数不合法返回 422，Session 不存在返回 404，版本冲突/记忆缺失/存储格式错误返回安全的 409，文件访问失败返回安全的 500。管理界面的人工操作不经过模型审批，应用现有 HTTP 访问条件仍然适用。手动来源不伪装为该 Session 的某条用户消息。

## 使用

选中会话后点击顶栏 **Memories**，查看用户与项目记忆，新增、编辑、查看来源或确认遗忘。开关作用于本机所有项目；Refresh 可刷新其他会话刚保存的内容。条目更新冲突时刷新列表，重新点击 Edit 后再修改。

对模型说“记住，我希望先给结论，再给依据”，观察 `save_memory` 和所需审批。新建同工作区会话后，询问已保存的偏好，观察索引与 `read_memory`。查询原始讨论时可说“找一下这个项目以前讨论过上下文缓存的会话，给出来源”。

“Forget”仅移出活动记忆；本地退役副本、旧聊天及已发送模型请求不被清除。稳定规则仍放 `AGENTS.md`，项目事实仍以现行文件为准。设计原因与 M7/M8 联动见 [长期记忆设计](../design/long-term-memory.md)。
