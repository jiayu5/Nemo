# Server API 参考

Nemo Server 是 CLI 和 React UI 共用的本地应用后端。默认地址为 `http://127.0.0.1:18765`，默认数据库为 `~/.nemo/nemo.db`。

```bash
conda run -n nemo python -m nemo.server
```

可选参数：`--host` 只接受 loopback 地址，`--port` 修改端口，`--database` 修改 SQLite 路径。`NEMO_SERVER_PORT` 同时决定 Server、CLI 和 Vite 开发代理的默认端口；改变它后需要重新启动各进程。若端口已有 Nemo Server，重复启动会直接提示已有实例；若是其他进程占用，会提示选择其他端口。

## 桌面 sidecar 访问条件

打包的 `Nemo.app` 由 Tauri 启动 `nemo.server.desktop`，它不是上面的命令行 Server：

| 项 | 桌面 sidecar |
|---|---|
| 数据库 | `~/.nemo/nemo.db`，与 CLI Server、浏览器端共用 Session、Run 和消息历史 |
| 地址 | `127.0.0.1` 上由系统分配的空闲端口，就绪后向 stdout 输出 `NEMO_READY:<port>` |
| 启动令牌 | Tauri 生成并放入子进程环境变量 `NEMO_DESKTOP_TOKEN`；缺失或长度不足 24 时拒绝启动 |
| 请求凭据 | 所有路径（含 `/health`、`/openapi.json`、SSE）都要求请求头 `X-Nemo-Token`，不匹配返回 `401` |
| Origin | 只接受 `tauri://localhost`、`http://tauri.localhost`、`http://127.0.0.1:5173`；其他 Origin 返回 `403` |
| 预检 | 允许的 Origin 的 `OPTIONS` 返回 `204`，允许 `Content-Type`、`X-Nemo-Token`、`Last-Event-ID` |
| 退出 | App 退出时结束 sidecar；Python 同时监测父进程，父进程消失即自行关闭 |

令牌只存在于 Rust、Python 进程内存和已加载窗口内，不写入仓库、配置或数据库。普通 CLI Server 与浏览器开发模式不使用令牌，也不受 Origin 白名单约束。Origin 检查只是浏览器层的附加限制，真正的访问凭据是随机令牌；它不是操作系统沙箱。

两个 Server 可同时使用同一 SQLite：不同 Session 的 Run 可并行；同一 Session 已有活动 Run 时，后续 `POST /sessions/{session_id}/runs` 返回 `409`。跨端查看事件、取消和审批由持久化协调，启动恢复与后台巡检只中断失去属主的 Run。旧 `desktop.db` 历史的备份与合并流程见 [macOS App 设计](../design/macos-app.md#启动与连接)。

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

`/models` 是完整选择目录，因此会同时返回 Profile、Alias 和 Model。React 输入器只展示 `kind = "model"` 的条目；Profile/Alias 仍可通过 API 与 CLI 使用，并可由 UI 解析为对应的实际 Model。

连接测试会访问外部 Provider，可能产生费用。它不返回模型正文、密钥、请求 header 或完整 URL 凭据。

### Provider Settings

| 方法 | 路径 | 请求 | 结果 |
|---|---|---|---|
| `GET` | `/settings/providers` | — | 安全的 Provider/Model、默认项及 API Key/Proxy 来源状态 |
| `POST` | `/settings/providers/{provider_id}/validate` | Provider/Model 草稿与密钥动作 | 只在内存校验，返回将写入的文件类型 |
| `PUT` | `/settings/providers/{provider_id}` | 与校验相同 | 原子保存并返回安全状态 |
| `DELETE` | `/settings/providers/{provider_id}` | — | 删除 Provider、所属模型及相关 Alias/Profile，返回新的默认模型 |

草稿一次新增或更新一个 Provider 和一个 Model。API Key 与 Proxy 分别使用 `keep`、`replace`、`delete`；只有 `replace` 接受新值。响应、验证错误和 OpenAPI 输出均不包含提交的值。进程环境来源是只读覆盖层，不能通过接口替换或删除。

保存会保留 UI 不编辑的 Alias、Profile、Provider headers 和 Model parameters。保存成功后目录查询、连接测试和新 Run 读取新配置；活动 Run 不热切换。保存本身不发起外部请求。

删除 Provider 是显式危险操作。Server 会级联删除其模型以及指向这些模型的 Alias/Profile；当前默认项受影响时改用第一个剩余模型。为保证配置始终可用，最后一个已配置模型不能删除。删除不会清理 `.env` 中的值，避免误删共用凭据。

### Session

| 方法 | 路径 | 请求 | 结果 |
|---|---|---|---|
| `POST` | `/sessions` | `workspace`、可选 `model`、`approval_mode` | `201` 创建 Session，并快照说明文件 |
| `GET` | `/sessions` | — | 按更新时间列出 Session |
| `GET` | `/sessions/{session_id}` | — | Session 元数据和消息数量 |
| `DELETE` | `/sessions/{session_id}` | — | `204` 删除 Session 及其消息、Run、事件等历史；不存在返回 `404`，有活动 Run 返回 `409` |
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

模型流式正文以 `model.delta`、推理以 `model.reasoning_delta` 事件分别传递。两者的 `payload.text` 都是经过脱敏的文本增量，`step` 指向本次模型调用。服务端合并短片段后写入事件表，避免逐字写 SQLite；`model.completed` 随后报告工具调用数量与可用的 token 用量。工具调用参数不随增量发送，必须等模型完整返回并通过校验后才会执行。Run 收敛时仍保存一条完整的助手消息，其中 `reasoning_content` 与 `content` 分开；取消中途已显示的文字也保存在会话历史中。未返回推理的模型不会产生推理事件。

客户端可用以下任一游标续读：

- 查询参数 `?after=<seq>`；
- 请求头 `Last-Event-ID: <seq>`。

两者同时存在时使用较大的值。断开 SSE 不会取消 Run；终态事件发送完毕后连接结束。长时间无事件时 Server 发送注释形式的 keep-alive。

## 生命周期与持久化

- Server 持有活动 Run task、取消信号和待审批请求。
- SQLite 保存 Session、Messages、Runs、Approvals 和 Events，并物化 Step/Tool Call 查询数据。
- Run 创建时立即保存脱敏 Prompt；Run 收敛时再把本轮消息追加到 Session，流式取消或失败时可能包含已显示的部分回复。
- Run 保存实际解析出的 selection、model、protocol 和 provider，避免配置后来变化后失去历史身份。
- Server 启动时把上次进程遗留的活动 Run 标记为 `interrupted`，不自动重放工具。
- 密钥不进入 SQLite、API 响应或 Trace。
- Provider Settings 通过临时文件、`fsync` 与 `os.replace` 写入；`.env` 权限固定为 `0600`。

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

Server 仅允许绑定 `127.0.0.1`、`localhost` 或 `::1`。命令行 Server 与浏览器开发模式没有请求认证和 Origin 检查；开发态 UI 使用 Vite `/api` 代理保持同源。打包应用由 Tauri sidecar 注入每次启动生成的 `X-Nemo-Token` 并收紧 Origin，见 [桌面 sidecar 访问条件](#桌面-sidecar-访问条件)。
