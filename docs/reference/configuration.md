# 配置参考

本文描述当前代码实际读取的模型配置、密钥和网络环境。常用 Provider/Model/API Key/Proxy 可在 React UI 的 **Connections** 中维护；高级字段仍可手工编辑。

## 文件与优先级

| 位置 | 内容 | 是否可含密钥 |
|---|---|---|
| `~/.nemo/config.toml` | Provider、Model、Alias、Profile、默认选择、记忆开关与子任务预算 | 否 |
| `~/.nemo/.env` | Provider API Key | 是，权限必须为 `600` |
| 进程环境变量 | 临时覆盖 API Key、HTTP Proxy、证书环境 | 是，优先级最高 |

密钥解析顺序是：**进程环境变量 > `~/.nemo/.env`**。空值视为未配置。

## 完整示例

```toml
default = "chat"

[providers.example]
protocol = "openai_compatible"
base_url = "https://api.example.com/v1"
api_key_env = "EXAMPLE_API_KEY"
proxy_env = "NEMO_HTTPS_PROXY"
timeout_seconds = 60

[models.example-chat]
provider = "example"
model_id = "example-chat"
capabilities = ["tool_calling"]
context_window_tokens = 8192
response_reserve_tokens = 2048

[models.example-chat.parameters]
temperature = 0.2

[aliases]
fast = "example-chat"

[profiles.chat]
model = "example-chat"

[profiles.chat.parameters]
temperature = 0.1
```

密钥文件：

```dotenv
EXAMPLE_API_KEY=replace-with-your-key
NEMO_HTTPS_PROXY=http://127.0.0.1:7890
```

```bash
chmod 600 ~/.nemo/.env
```

## 字段

### Provider

| 字段 | 必填 | 说明 |
|---|---|---|
| `protocol` | 是 | 当前可运行值只有 `openai_compatible` |
| `base_url` | 是 | HTTP(S) API 根路径；末尾 `/` 会被移除 |
| `api_key_env` | 是 | 密钥变量名，不是密钥值 |
| `proxy_env` | 否 | 显式 Proxy 变量名；值从进程环境或 `~/.nemo/.env` 读取 |
| `headers` | 否 | 静态请求 header；默认空字典 |
| `timeout_seconds` | 否 | 单次模型请求超时，默认 `60`，必须大于 0 |

契约还接受 `openai_responses` 和 `anthropic_messages`，但当前没有对应 adapter，运行时会明确报错。

### Model

| 字段 | 必填 | 说明 |
|---|---|---|
| `provider` | 是 | 必须引用已定义 Provider |
| `model_id` | 是 | 发送给远端 API 的模型名称 |
| `capabilities` | 否 | 当前包括 `tool_calling`、`streaming`；声明能力不代表 adapter 已实现它 |
| `context_window_tokens` | 否 | 模型上下文窗口；设置后启用压缩，最小 1024；应以所用模型的官方值为准 |
| `response_reserve_tokens` | 否 | 为回复预留的容量，默认 2048，必须小于已设置的窗口 |
| `parameters` | 否 | 透传给协议 adapter 的模型参数 |

`model`、`messages`、`tools`、`stream` 由 adapter 管理，不允许出现在 `parameters`。

窗口配置控制一次模型请求的上下文大小，不表示 Nemo 能读取 API 账户余额。Nemo 使用保守的本地字节估算；摘要由所选模型额外生成，可能产生费用。未配置 `context_window_tokens` 时不触发自动压缩。具体组装与失败规则见 [Context 设计](../design/context-assembly.md#8-m7-上下文窗口与压缩)。

### Alias、Profile 与 Default

- Alias 把一个短名称映射到 Model。
- Profile 引用 Model，并可覆盖或增加参数。
- `default` 必须指向现有 Profile、Alias 或 Model。
- Alias 和 Profile 不能使用同一个名称。
- 运行时选择优先级为 `run > session > agent > default`。

## Proxy

模型请求和 Web 工具使用 `httpx`。Provider 未设置 `proxy_env` 时，默认客户端继续读取标准进程环境变量：

- `HTTP_PROXY`
- `HTTPS_PROXY`
- `ALL_PROXY`
- `NO_PROXY`

例如：

```bash
HTTP_PROXY=http://127.0.0.1:7890 \
HTTPS_PROXY=http://127.0.0.1:7890 \
NO_PROXY=127.0.0.1,localhost \
conda run -n nemo python -m nemo.server
```

Provider 设置了 `proxy_env` 后，Nemo 使用与 API Key 相同的解析顺序：Server 进程环境优先，`~/.nemo/.env` 回退。值只交给该 Provider 的 HTTP client，不注入全局 `os.environ`。这适合只让特定模型端点经过 VPN/Proxy；标准变量则继续影响所有遵循 `httpx` 环境规则的请求。

客户端访问本地 Server 通常不应走代理，所以建议保留 `NO_PROXY=127.0.0.1,localhost`。

## 安全规则

- 不把真实 `config.toml`、`.env`、密钥或带凭据 URL 提交到仓库。
- `headers` 位于非密钥配置文件；当前没有 header 的 Secret 引用机制，因此不要把长期凭据写进静态 header。
- Server API 只返回 `api_key_env` 和 `secret_configured`，不回显密钥或 header。
- Connections 只返回 `environment`、`file` 或缺失状态；环境变量来源不能由 UI 覆盖或删除。
- Provider URL 对外显示前会移除 userinfo、query 和 fragment。
- UI 保存先校验完整候选配置，再原子替换文件；新 Run 立即读取新配置，活动 Run 保持原 Model Client。

## 长期记忆开关

顶层 `memory_enabled` 控制模型的记忆读写，默认 `true`。存储与管理语义见 [Memory 参考](memory.md)。

## Subagents 预算

用户在 `~/.nemo/config.toml` 中设置默认值和硬上限；无需每次分派手工配置。未配置本节时使用下列默认值：

```toml
[subagents]
default_max_steps = 15
max_steps = 40
max_model_calls = 160
```

三个字段均为严格正整数，不接受布尔值或字符串。`default_max_steps` 不得超过 `max_steps`；`max_model_calls` 必须大于 `max_steps`，为父任务留出调用空间。父 Agent 在工具调用的结构化参数 `max_steps` 中按复杂度选择步数；省略使用默认值，超出上限整批拒绝，不启动子任务。每一步为一次子 Runtime 模型调用，包含最后的汇总步骤；压缩另计入共享预算。

默认值与上限写入本 Run 的工具 schema，共享调用预算写入分派工具说明。Server 在每个新 Run 装配时读取配置，当前 Run 不受中途修改影响；直连 CLI 在启动时读取，修改后需重新启动。Connections 暂无这些字段的编辑入口，但保存 Provider/Model 或记忆开关会保留本节。

共享预算覆盖父、子和压缩请求，限制请求次数而非 token 或费用；失败和取消的请求也计数。它不预先按子任务分配额度，不能保证每个任务用满指定步数。并行任务或多批分派需要相应提高共享预算；超时、取消与父 Run 结束仍可提前终止。现行运行规则见 [Subagents](subagents.md)。

## MCP 连接

`mcp_servers` 保存外部工具服务的连接配置、凭据环境变量引用、只读与子任务白名单。配置格式、生效时间和代理规则见 [MCP 参考](mcp.md#配置)；可在顶部 MCP 弹窗维护。浏览器登录得到的 OAuth 凭据不写入本文件或 `.env`，按连接指纹保存在 `~/.nemo/mcp-oauth/`。
