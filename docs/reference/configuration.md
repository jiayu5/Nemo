# 配置参考

本文描述当前代码实际读取的模型配置、密钥和网络环境。常用 Provider/Model/API Key/Proxy 可在 React UI 的 **Connections** 中维护；高级字段仍可手工编辑。

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
proxy_env = "NEMO_HTTPS_PROXY"
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
| `parameters` | 否 | 透传给协议 adapter 的模型参数 |

`model`、`messages`、`tools`、`stream` 由 adapter 管理，不允许出现在 `parameters`。

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
