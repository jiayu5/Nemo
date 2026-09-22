# CLI 与审批设计

> 状态：M4b 已实现；默认 CLI 已迁移到 Server，`--direct` 保留旧直连模式。

## 1. 目标与边界

CLI 是 Server 的终端客户端，负责参数、REPL、事件显示、Ctrl-C 取消和交互式审批。审批策略位于 Core；终端 I/O 位于 CLI。

CLI 不拥有默认路径下的 Session/Run 生命周期，不实现 Agent Loop，也不把本地命令语法交给 Server 解析。

代码按运行方式分层：`main.py` 只解析参数并分派；`remote.py` 实现默认 HTTP/SSE 客户端；`direct.py` 保留进程内 Runtime 与 JSONL 恢复路径。审批、渲染、Server Client 和 transcript 均为独立模块。

## 2. Session 与命令

- 无 `--session` 时创建 Session；指定后恢复 Server 中的历史。
- 普通输入原样作为 Prompt 创建 Run。
- `:mode ask|auto|full` 由 CLI 解析并更新 Session。
- `:exit`、`:quit` 退出客户端，不删除 Session。
- Ctrl-C 请求取消当前 Run，不直接杀死 Server。
- `--direct` 在本进程装配 Runtime，并将 transcript 写入 JSONL；仅用于测试和恢复。

Server 接口映射见 [Server API](../reference/server-api.md#客户端命令映射)。

## 3. 三档审批

| 动作分类 | `ask` | `auto` | `full` |
|---|---|---|---|
| 只读 | 执行 | 执行 | 执行 |
| 已识别危险 | 询问 | 询问 | 执行 |
| 无法分类 | 询问 | 执行 | 执行 |

文件工具首先受 workspace 路径检查约束。Shell 分类器只根据命令文本、命令形态和路径参数做机械判定：

- 只读命令白名单直接放行；
- 删除、提权、远程访问、重定向、命令替换和高风险 Git 子命令标记为危险；
- 其余命令归为无法分类；
- 审批理由只描述命令形态，不回显可能带密钥的原始参数。

`auto` 的含义是“放行无法证明危险的操作”，不是“自动保证安全”。变量、别名、解释器和动态脚本都可能隐藏真实行为，因此没有 Sandbox 时它最坏等同 `full`。

## 4. 审批交互

审批请求只包含工具名、安全摘要和原因。用户可以：

- `allow_once`：放行一次；
- `allow_session`：当前审批策略实例后续同工具放行；
- `deny`：拒绝并把 ToolResult 回填模型。

没有可用 Approver 时默认拒绝。策略不直接读 stdin，Runtime 也不包含 CLI 分支；CLI 与 Server UI 分别提供 Approver 实现。

当前直连 CLI 在交互循环中复用策略，因此 grant 可跨多轮；Server 每个 Run 新建策略，因此同名 grant 只在当前 Run 生效。跨 Run 的 Session grant 是现有实现缺口，不在文档中伪装成已完成。

## 5. 事件与脱敏

CLI 显示领域事件，但不显示原始工具参数。输出、错误和 transcript 在写出前经过脱敏；直连 transcript 权限为 `600`。

默认 Server 模式的持久化由 SQLite 承担，JSONL 不再是主数据源。

## 6. 验收标准

- 单轮和交互模式都能创建或恢复 Session。
- 三档审批对只读、危险和无法分类命令的行为符合表格。
- 非 TTY 或无 Approver 时需要确认的调用被拒绝。
- Ctrl-C 转换为 Run 取消，终端退出不删除历史。
- 事件、错误和 transcript 不泄漏已知密钥。

## 7. 非目标

审批不是操作系统权限、命令解析器或沙箱；CLI 不实现多窗口、终端复用或常驻 Shell。
