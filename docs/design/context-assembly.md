# Context 组装设计

> 状态：M4a/M4d、M7 上下文压缩与 M8 按需检索工具已实现；向上查找项目说明仍是 TODO T1。

## 1. 目标与边界

Context Builder 负责决定一次模型调用实际收到哪些消息，同时保证稳定前缀可缓存、Session 历史不被修改、临时运行信息不累积。

`ContextBuilder` 仍只负责稳定前缀、历史和 Reminder 的组装。M7 的 `ContextEngine` 在外层选择历史并按需压缩；M8 的搜索结果作为正常工具消息进入历史，再由 Builder 组装。Core 不读取文件；M9 Memory 端口与索引历史规则见 [长期记忆设计](long-term-memory.md)。说明文件由 `nemo.prompts.instructions` 读取后作为文本传入 Core。

## 2. 三层 Context

| 层 | 生命周期 | 内容 |
|---|---|---|
| 稳定前缀 | Session | 系统提示、用户级说明、项目级说明 |
| 会话历史 | 跨 Run | 用户、assistant、tool 消息，以及 M9 应用数据索引 |
| Reminder | 单次模型调用 | step budget 等临时状态 |

组装顺序固定为：系统行为条款 → `~/.nemo/AGENTS.md` → `<workspace>/AGENTS.md` → Session 历史 → Reminder。

用户说明先于项目说明，使越接近项目的约定越靠近对话；两层说明都只能追加行为偏好，不能放宽代码强制的路径和审批边界。

## 3. 稳定前缀

`ContextBuilder` 构造时组装一次 `prefix`，后续 `build()` 不重新读取或拼接。相同 Session 的前缀保持逐字一致，以便 Provider 使用前缀缓存。

约束：

- 时间、随机 ID、剩余步数和临时路径不得进入前缀。
- 用户级与项目级说明各自可选，空文件或不可读文件视为不存在。
- 说明文本默认最多 8000 字符，超出时使用统一截断标记。
- 当前项目说明只读取 workspace 根部的 `AGENTS.md`，不会向父目录查找。

## 4. Reminder

Reminder 是追加在历史之后的独立 user 消息，遵守三条规则：

1. 不修改已有消息；
2. 不写入 `AgentState`；
3. 只在不会产生连续两个 user 消息时注入。

每条 Reminder 使用 snake_case 名称，正文不超过 200 字符；单次最多 3 条并按名称排序，保证相同输入得到相同输出。Trace 只记录名称和长度，不记录正文。

当前唯一实际 Reminder 是 `step_budget`。

## 5. 关键决策

| 决策 | 理由 |
|---|---|
| 文件 I/O 放在 prompts 层 | Core 只接收文本，保持可测试和可嵌入 |
| 说明在 Session 开始时快照 | 避免会话中规则漂移和缓存失效 |
| Reminder 不写入历史 | 防止每轮累积相同运行状态 |
| 稳定层与易变层分开 | 兼顾正确性与 Provider prefix cache |

## 6. 验收标准

- 相同输入构造出的稳定前缀逐字一致。
- Model 不能通过修改请求对象污染 Session 历史。
- Reminder 不累积、不覆盖历史，并受数量和长度限制。
- 缺失、空或不可读的说明文件不会阻止 Session 创建。
- 用户级说明位于项目级说明之前，代码边界不受二者影响。

## 7. 后续

T1 将决定项目说明向上查找到 Git 仓库根还是文件系统根。工作区检索见 [M8 设计](workspace-retrieval.md)；M9 长期 Memory 见 [长期记忆设计](long-term-memory.md)。

## 8. M7 上下文窗口与压缩

模型可选配置 `context_window_tokens` 和 `response_reserve_tokens`。未配置窗口时沿用 M4 的完整历史组装；配置后，`ContextEngine` 以 UTF-8 JSON 字节数加固定消息开销保守估算输入大小，再减去回复预留量。这个值用于提前规划请求，**不是 Provider 的精确 token 计数、API 余额或费用上限**。Provider 仍可能因自身计数规则拒绝请求。

超出窗口时，Engine 把最早的完整对话轮次交给当前模型生成摘要；近期用户请求及其后的工具调用链保留原文。摘要作为标明来源的临时 assistant 消息插入稳定前缀之后，原始 Session 消息不改写，也不获得 system 指令的优先级。压缩请求不提供工具；单次摘要限制为 1200 UTF-8 字节。若较早的单轮内容本身装不进压缩请求、当前轮本身超限、压缩失败或摘要不能缩小请求，本轮明确失败，不静默裁剪历史。

每次压缩生成 `covered_messages`、前缀 SHA-256 校验值和摘要。Server 将脱敏后的检查点保存在现有 Run 事件中，直连 CLI 将其保存在 JSONL transcript 中；恢复时只复用与当前原始历史匹配的检查点。`context.compression_started`、`context.compression_completed` 记录额外模型调用及可用的 token 用量，`context.compacted` 记录可核查摘要。压缩会增加 Provider 调用，可能产生费用。
