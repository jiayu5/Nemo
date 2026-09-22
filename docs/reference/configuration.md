# 配置参考

本文描述当前代码实际读取的模型配置、密钥和网络环境。图形化编辑尚未实现；M5c 完成前需要手动维护这些文件。

## 文件与优先级

| 位置 | 内容 | 是否可含密钥 |
|---|---|---|
| `~/.nemo/config.toml` | Provider、Model、Alias、Profile、默认选择 | 否 |
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
timeout_seconds = 60

[models.example-chat]
provider = "example"
model_id = "example-chat"
capabilities = ["tool_calling"]

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
| `headers` | 否 | 静态请求 header；默认空字典 |
| `timeout_seconds` | 否 | 单次模型请求超时，默认 `60`，必须大于 0 |

契约还接受 `openai_responses` 和 `anthropic_messages`，但当前没有对应 adapter，运行时会明确报错。

### Model

| 字段 | 必填 | 说明 |
|---|---|---|
| `provider` | 是 | 必须引用已定义 Provider |
| `model_id` | 是 | 发送给远端 API 的模型名称 |
| `capabilities` | 否 | 当前包括 `tool_calling`、`streaming`；声明能力不代表 adapter 已实现它 |
| `parameters` | 否 | 透传给协议 adapter 的模型参数 |

`model`、`messages`、`tools`、`stream` 由 adapter 管理，不允许出现在 `parameters`。

### Alias、Profile 与 Default

- Alias 把一个短名称映射到 Model。
- Profile 引用 Model，并可覆盖或增加参数。
- `default` 必须指向现有 Profile、Alias 或 Model。
- Alias 和 Profile 不能使用同一个名称。
- 运行时选择优先级为 `run > session > agent > default`。

## Proxy

模型请求和 Web 工具使用 `httpx`。默认客户端会读取标准进程环境变量：

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

Proxy 必须存在于 **Nemo Server 的进程环境**。`~/.nemo/.env` 当前由 `SecretLoader` 按名称读取，它不会把内容注入 `os.environ`；因此把 `HTTPS_PROXY` 只写进该文件不会让 `httpx` 自动使用它。这是当前实现边界，不应在文档中描述成已支持的 `.env` Proxy 功能。

客户端访问本地 Server 通常不应走代理，所以建议保留 `NO_PROXY=127.0.0.1,localhost`。

## 安全规则

- 不把真实 `config.toml`、`.env`、密钥或带凭据 URL 提交到仓库。
- `headers` 位于非密钥配置文件；当前没有 header 的 Secret 引用机制，因此不要把长期凭据写进静态 header。
- Server API 只返回 `api_key_env` 和 `secret_configured`，不回显密钥或 header。
- Provider URL 对外显示前会移除 userinfo、query 和 fragment。
- 配置修改后应重启当前 Server；M5c 尚未提供安全写入和热重载流程。
