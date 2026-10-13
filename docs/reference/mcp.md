# MCP 参考

M12 使用官方 Python MCP SDK 1.x 接入外部工具。Server（Web/Desktop、默认 CLI）与直连 CLI 支持本地 stdio 和远程 Streamable HTTP。工具执行复用既有 ToolRegistry、审批、Runtime 与 Trace，不使用外部 Agent 框架。

## 管理入口与生效

顶部 **MCP** 打开独立弹窗。可添加、编辑、启用、移除连接，为 HTTP 连接登录账号，测试已保存的配置，查看发现的工具和参数，并为工具勾选只读及子 Agent 权限。列表显示上次测试结果与授权状态，不代表长期连接在线；保存和打开列表不启动进程、不访问 MCP 服务。Test 会执行初始化与工具发现，结束后关闭连接，不调用模型或业务工具。

需要账号的 HTTP 连接用 **Browser sign-in** 登录，见 [账号授权](#账号授权oauth)。未授权的连接不能用于 Run，启用后会在本 Run 明确失败并提示先登录。

新连接默认禁用。启用后的连接在每个 Server Run 启动时建立，工具目录在本 Run 内冻结；修改配置影响下一 Run，当前 Run 继续使用原连接。直连 CLI 启动时建立，退出后关闭，变更需重启直连 CLI。连接失败或发现失败使本 Run 明确失败，不悄悄漏掉已启用工具。连接初始化先于模型调用。

MCP 配置保存在 `~/.nemo/config.toml`，需要先配置模型。保存 MCP 配置不修改 `.env`，Provider 或 Memory 设置编辑会保留 MCP 配置。

## 配置

```toml
[mcp_servers.demo]
transport = "stdio"
enabled = false
command = "/absolute/path/to/python"
args = ["/absolute/path/to/Nemo/examples/mcp_demo.py"]
timeout_seconds = 30
read_only_tools = ["add"]
subagent_tools = ["add"]
env = {}

[mcp_servers.remote]
transport = "streamable_http"
enabled = false
url = "https://service.example/mcp"
auth_mode = "oauth"
timeout_seconds = 30
headers = {}
read_only_tools = []
subagent_tools = []
```

| 字段 | 规则 |
|---|---|
| 连接 ID | 1–32 个英文字母、数字、下划线或短横线 |
| `transport` | `stdio` 或 `streamable_http` |
| `enabled` | 默认 false，启用后新 Run 自动连接 |
| `command` / `args` | stdio 必须有 command；args 为独立参数列表，不经过 Shell 拆分；最多 64 项 |
| `url` | HTTP(S) 地址；禁止内嵌账号密码、query 与 fragment；stdio 不允许设置 |
| `env` | stdio 子进程变量名到已有环境变量名的映射；例如 `{ API_KEY = "SERVICE_KEY" }` |
| `headers` | HTTP header 名到已有环境变量名的映射，禁止直接填写密钥；Authorization 对应变量保存完整 header 值，包括需要的 Bearer 前缀 |
| `auth_mode` | 默认 `none`；`oauth` 使用浏览器登录，仅限 HTTP，且禁止同时配置 Authorization header |
| `oauth_client_id` / `oauth_issuer` | 可选，服务要求预注册应用时成对提供；默认改用动态客户端注册 |
| `oauth_client_secret_env` | 可选，client secret 的环境变量名，需要 client ID；密钥只从环境或 `~/.nemo/.env` 读取 |
| `oauth_redirect_port` | 可选回环回调端口，0 表示自动选择；固定端口需在服务的应用注册中登记回调地址 `http://127.0.0.1:<端口>/oauth/callback` |
| `oauth_client_metadata_url` | 可选，HTTPS 客户端元数据文档 URL，作为 URL 型 client ID 使用，需带文档路径 |
| `proxy_env` | HTTP 可选代理变量名，值通过密钥加载器读取；默认不使用隐式进程 HTTP_PROXY；stdio 禁止设置 |
| `timeout_seconds` | 大于 0、不超过 300 秒；初始化与调用均受约束 |
| `read_only_tools` | 明确确认可信的只读工具原始名称；默认空 |
| `subagent_tools` | 允许子任务使用的原始名称，必须是 read_only_tools 的子集；默认空 |

引用值遵循进程环境优先于 `~/.nemo/.env` 的既有读取规则。不要在 URL、命令参数、表单或仓库里放凭据。stdio 进程仅使用 SDK 的基础继承环境与明确映射变量，工作目录来自 ExecutionContext.workspace；stderr 不转发到模型或事件。

## 账号授权（OAuth）

弹窗按连接信息、Credentials、账号连接、工具权限排列。Connect account 位于凭据设置下方；预注册应用信息和高级设置可折叠，代理变量入口保持可见。保存、测试和启用操作固定在底栏；工具默认仅显示名称与简短描述，展开后修改权限或查看参数。连接账号前先校验表单，凭据变量名不符合格式时不启动授权。

选择 **Browser sign-in** 保存后点击 Connect account：Nemo 在本机回环地址启动一次性回调服务，打开系统浏览器完成授权，再用回调结果换取访问令牌。弹窗在等待期间显示授权状态，并在浏览器未自动打开时给出授权地址链接。授权流程有 300 秒上限，可随时取消；编辑或删除该连接会取消进行中的授权。

默认走服务的动态客户端注册；服务要求预注册应用时填写 client ID 与 issuer，必要时提供 secret 环境变量和已登记的回调端口。PKCE（S256）、state 校验与 RFC 8707 resource 参数由官方 SDK 客户端执行；除本机 `127.0.0.1`/`localhost` 开发外，OAuth 端点必须是 HTTPS。

凭据保存在 `~/.nemo/mcp-oauth/<连接指纹>.json`（权限 600，同目录加锁写入），不写入 `config.toml` 或 `.env`。连接指纹包含 URL、应用标识与回调策略，修改这些字段会失效旧凭据。Disconnect account 递增代际再删除令牌，因此授权完成后才到达的迟到写入会被拒绝；令牌只在发请求时使用，不进入模型上下文、事件或 Trace，状态查询也不返回凭据值。

授权状态取值：`not_required`（不需要登录）、`not_connected`、`authorizing`、`connected`、`needs_authorization`（令牌过期且无法刷新）、`failed`、`cancelled`。需要登录的 HTTP 连接在 Run、连接测试与直连 CLI 中都不会打开浏览器，只使用已保存的令牌；缺少有效令牌时明确失败并要求在 MCP 设置中登录。

令牌兑换与刷新明确请求 `application/json`，避免授权服务器默认返回表单格式而导致 SDK 解析失败。服务返回的凭据拒绝、回调地址不匹配、授权码过期等已知错误映射为安全提示，不展示原始响应。浏览器接受授权不等于 Nemo 已连接：仍需完成兑换与工具发现；前端等待发现完成后再结束授权状态轮询。

浏览器使用的网络代理不一定被 Nemo Server 继承。需要代理时，在 Proxy environment variable 填写已有变量名（例如 `HTTPS_PROXY`），其值由 Server 环境或 `~/.nemo/.env` 提供；同一代理用于 MCP 服务及 OAuth 发现、兑换与刷新。HTTP adapter 保留网络超时、连接失败及安全授权错误的提示，即使 SDK 后台写任务吞掉原异常也能向连接界面报告，原始响应和异常不展示。

## 模型工具与权限

发现的每个工具独立注册，名称以 `mcp_<server_id>_` 开头；非标准字符或长度超过 64 时使用稳定哈希后缀，避免名称碰撞。工具描述标注来源；服务器的输入 JSON Schema 用于模型 schema 和本地调用前校验，拒绝不匹配参数。仅支持本地 schema 引用，禁止远程 `$ref` / `$dynamicRef`，发现异常 schema 或重复名称时失败。每连接最多发现 64 个工具，最多读取 16 页工具目录。

默认外部工具不是只读。服务返回的 readOnlyHint 不会自动授予权限；只有配置白名单决定 Nemo 的只读分类。`ask` 模式下未分类工具走现有审批，`auto` 和 `full` 保持现行行为；详见 [审批设计](../design/cli-and-approval.md)。子 Agent 除了只读分类，还必须满足 subagent_tools 白名单，不能通过继承工具目录绕过该限制。

workspace 只用于连接归属、启动目录和超时/输出约束，MCP 实现可访问哪些文件由外部服务自身决定；MCP 子进程和只读白名单不是操作系统沙箱。

## 调用结果与生命周期

调用返回文本内容与 `structured_content`。文本 resource 可以转为文本，图片、音频和其他非文本内容返回类型及不支持提示，不把二进制塞进模型上下文。读取的凭据值和通用凭据模式会从描述、schema 和结果中脱敏。输出遵守 ExecutionContext.max_output_chars；超长结果返回带截断标记的 JSON 摘要，元数据和 JSON 转义也计入限额。

服务器 isError、协议/连接失败及超时转成安全 ToolFailure，未预期原始异常、stderr 和响应诊断不会直接进入模型。Nemo 不自动重试业务调用。超时或调用取消会取消对应 SDK 请求；对外部服务已发生的副作用不承诺撤销。断连后拒绝继续使用该连接，新 Run 或显式测试重新建立连接。

连接由专用任务持有，保证 SDK AnyIO 上下文在同一任务进入和退出。父 Runtime 排空子任务后，Server/CLI 关闭 MCP 请求、transport 与 stdio 子进程；重复取消不会提前放弃清理。配置测试在请求完成、取消或 Server 关闭时清理连接。工具调用使用既有 tool.started/completed/failed 事件，Activities 与 Trace 可查看来源名称和结果；子任务调用沿用父嵌套事件。

## HTTP API

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/mcp` | 已保存配置、上次测试状态和工具清单，不返回凭据值 |
| PUT | `/mcp/{server_id}` | 保存 McpServerConfig，不发起连接 |
| DELETE | `/mcp/{server_id}` | 移除配置，不影响已运行连接 |
| POST | `/mcp/{server_id}/test` | 请求 `{workspace}`，初始化并发现工具，关闭后返回 `ok` 或 `failed` |
| POST | `/mcp/{server_id}/oauth/start` | 请求 `{workspace}`，启动浏览器授权，立即返回当前状态与授权地址 |
| GET | `/mcp/{server_id}/oauth` | 当前授权状态，不返回令牌 |
| POST | `/mcp/{server_id}/oauth/cancel` | 取消进行中的授权 |
| POST | `/mcp/{server_id}/oauth/disconnect` | 取消授权后清除本地凭据和上次测试结果 |

配置参数错误返回 400/422；未知连接、非 HTTP 连接或非 OAuth 连接调用授权接口返回 400；连接测试失败返回安全错误和空工具清单。并发测试同一连接被拒绝；结果与测试时配置绑定，编辑或断连后失效。测试结果只保存在当前 Server 内存中，重启后显示 not_tested。

## 验证示例

仓库 `examples/mcp_demo.py` 提供无外部数据的真实 MCP 服务，含 add 与 wait 工具：

```bash
conda run -n nemo python examples/mcp_demo.py --transport streamable-http --port 8767
```

HTTP 配置 URL 为 `http://127.0.0.1:8767/mcp`。stdio 使用 nemo 环境 Python 的绝对路径和脚本绝对路径，由 Nemo 启动，无需另起服务。模型请求调用 add(20,22) 应得到 42；wait 可以用于超时或取消检查。测试结束后停止手动启动的 HTTP 服务。

需要账号的服务可以用同样方式验证浏览器登录：

```bash
conda run -n nemo python examples/mcp_oauth_demo.py --port 8768
```

HTTP 配置 URL 为 `http://127.0.0.1:8768/mcp`，Authorization 选择 Browser sign-in，Connect account 后在浏览器完成授权，模型调用 add(20,22) 应得到 42。示例立即批准授权且只在内存保存令牌，仅用于本机开发验证。

MCP tools 与浏览器账号授权已接入，不包含 MCP Resources/Prompts 的主动目录入口、sampling、elicitation 或服务市场。
