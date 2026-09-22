# Server API 参考

Nemo Server 是 CLI 和 React UI 共用的本地应用后端。默认地址为 `http://127.0.0.1:8765`，默认数据库为 `~/.nemo/nemo.db`。

```bash
conda run -n nemo python -m nemo.server
```

可选参数：`--host` 只接受 loopback 地址，`--port` 修改端口，`--database` 修改 SQLite 路径。

## Prompt 如何传递

用户输入通过 JSON 请求体发送：

```http
POST /sessions/{session_id}/runs
Content-Type: application/json

{"prompt":"检查当前项目","max_steps":10}
```

请求解析阶段数据在内存中；Server 创建 Run 时把脱敏后的 Prompt 写入 Run 记录。Run 正常收敛后，本轮完整消息才追加到 Session 历史。不会生成或落盘一个独立的 Prompt JSON 文件。

## 接口

### 健康与目录

| 方法 | 路径 | 请求 | 结果 |
|---|---|---|---|
| `GET` | `/health` | — | `{"status":"ok"}` |
| `GET` | `/models` | — | 可选 Profile、Alias、Model 及实际 Provider/Model 信息 |
| `GET` | `/providers` | — | 脱敏 Provider 元数据和密钥配置状态 |
| `POST` | `/providers/{provider_id}/test` | `{"model": string|null}` | 发起最小真实请求并返回模型身份和延迟 |

连接测试会访问外部 Provider，可能产生费用。它不返回模型正文、密钥、请求 header 或完整 URL 凭据。

### Session

| 方法 | 路径 | 请求 | 结果 |
|---|---|---|---|
| `POST` | `/sessions` | `workspace`、可选 `model`、`approval_mode` | `201` 创建 Session，并快照说明文件 |
| `GET` | `/sessions` | — | 按更新时间列出 Session |
| `GET` | `/sessions/{session_id}` | — | Session 元数据和消息数量 |
| `PATCH` | `/sessions/{session_id}` | 可选 `model`、`approval_mode` | 修改后续 Run 的设置 |
| `PUT` | `/sessions/{session_id}/model` | `{"model":"selection"}` | 校验并切换后续 Run 的模型 |
| `GET` | `/sessions/{session_id}/messages` | — | 返回脱敏后的消息历史 |
| `GET` | `/sessions/{session_id}/runs` | — | 返回该 Session 的 Run |

`workspace` 必须是 Server 主机上已经存在的目录。`approval_mode` 为 `ask`、`auto` 或 `full`。

### Run

| 方法 | 路径 | 请求 | 结果 |
|---|---|---|---|
| `POST` | `/sessions/{session_id}/runs` | `prompt`、`max_steps` | `202` 创建后台 Run |
| `GET` | `/runs/{run_id}` | — | Run 状态、输出、安全错误和实际模型身份 |
| `GET` | `/runs/{run_id}/trace` | — | Run、Step、模型调用、工具调用和事件 |
| `POST` | `/runs/{run_id}/cancel` | — | `accepted` 或 `already_terminal` |
| `POST` | `/runs/{run_id}/approvals/{request_id}` | `outcome` | 回答等待中的审批 |
| `GET` | `/runs/{run_id}/events` | `after` 或 `Last-Event-ID` | SSE 事件流 |

`prompt` 至少一个字符；`max_steps` 范围为 1–100，默认 10。同一 Session 同时只允许一个活动 Run，否则返回 `409`。

审批结果为：

- `allow_once`：仅放行当前调用；
- `allow_session`：放行当前审批策略实例中的后续同工具调用；
- `deny`：拒绝执行并把结果回填模型。

默认 Server 会为每个 Run 新建审批策略，因此 `allow_session` 当前只覆盖该 Run，不会跨后续 Run 持久化。名称表达的是策略层原始语义；跨 Run 的 Session grant 尚未实现。直连 CLI 在交互循环中复用策略，行为不同。

## SSE

`GET /runs/{run_id}/events` 以数据库事件序号作为 SSE `id`，领域事件类型作为 `event`，完整 Event JSON 作为 `data`。

客户端可用以下任一游标续读：

- 查询参数 `?after=<seq>`；
- 请求头 `Last-Event-ID: <seq>`。

两者同时存在时使用较大的值。断开 SSE 不会取消 Run；终态事件发送完毕后连接结束。长时间无事件时 Server 发送注释形式的 keep-alive。

## 生命周期与持久化

- Server 持有活动 Run task、取消信号和待审批请求。
- SQLite 保存 Session、Messages、Runs、Approvals 和 Events，并物化 Step/Tool Call 查询数据。
- Run 创建时立即保存脱敏 Prompt；正常收敛时再把本轮完整消息追加到 Session。
- Run 保存实际解析出的 selection、model、protocol 和 provider，避免配置后来变化后失去历史身份。
- Server 启动时把上次进程遗留的活动 Run 标记为 `interrupted`，不自动重放工具。
- 密钥不进入 SQLite、API 响应或 Trace。

## 客户端命令映射

| 用户动作 | 客户端处理 | Server 接口 |
|---|---|---|
| 普通自然语言 | 原文放入 `prompt` | `POST /sessions/{id}/runs` |
| 切换模型 | 发送选择名称 | `PUT /sessions/{id}/model` |
| `:mode ask|auto|full` | CLI 解析本地命令 | `PATCH /sessions/{id}` |
| Ctrl-C / 停止按钮 | 请求取消 | `POST /runs/{id}/cancel` |
| 审批回答 | 发送 outcome | `POST /runs/{id}/approvals/{request_id}` |

Server 不解析 `:mode` 等上层 UI 命令；它只处理明确的资源和动作接口。

## 当前安全边界

Server 仅允许绑定 `127.0.0.1`、`localhost` 或 `::1`，但还没有请求认证和 Origin 检查。开发态 UI 使用 Vite `/api` 代理保持同源；M6 将由 Tauri sidecar 注入短期访问凭据并收紧 Origin。
