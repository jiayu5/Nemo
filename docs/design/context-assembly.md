# Context 组装设计

> 状态：M4a/M4d 已实现；向上查找项目说明仍是 TODO T1。

## 1. 目标与边界

Context Builder 负责决定一次模型调用实际收到哪些消息，同时保证稳定前缀可缓存、Session 历史不被修改、临时运行信息不累积。

它不负责读取文件、长期 Memory、检索、压缩或 token 预算优化。说明文件由 `nemo.prompts.instructions` 读取后作为文本传入 Core。

## 2. 三层 Context

| 层 | 生命周期 | 内容 |
|---|---|---|
| 稳定前缀 | Session | 系统提示、用户级说明、项目级说明 |
| 会话历史 | 跨 Run | 用户、assistant、tool 消息 |
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

T1 将决定项目说明向上查找到 Git 仓库根还是文件系统根。Phase 2 再加入 Context Budget、检索、压缩与 Memory；这些能力不改变客户端或 Runtime 获取统一 message 列表的接口。
