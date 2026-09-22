# Web 工具设计

> 状态：M3b 已实现；搜索后端升级记录在 TODO T2。

## 1. 目标与边界

`web_search` 负责发现候选页面，`fetch_url` 负责读取指定页面。二者都是模型可调用的只读工具，不属于 Model Provider 接入。

当前只支持 HTTP/HTTPS 静态页面，不执行 JavaScript，不操作浏览器，不下载文件，也不绕过登录、付费墙或访问控制。

## 2. 接口

| 工具 | 输入 | 输出 |
|---|---|---|
| `web_search` | `query`、1–10 的 `max_results` | 标题、最终 URL 和摘要 |
| `fetch_url` | 绝对 `url`、可选 `max_chars` | 去除 HTML 标签后的文本 |

所有请求遵守 `ExecutionContext` 的超时和输出上限；响应体最多读取 2 MB；重定向最多 5 次。工具摘要会显示查询词或 URL，使用户知道哪些信息离开了本机。

## 3. 当前实现

- 搜索使用 Bing HTML 结果页并解析候选项。
- Bing 跳转链接在返回模型前解码为目标 URL。
- `fetch_url` 只接受 `http` 和 `https`，拒绝 `file:`、`data:` 等本地绕行协议。
- HTML 使用轻量文本提取，脚本、样式和标签被移除。
- 页面结构变化或无法解析时明确失败，不伪装成“没有结果”。
- 网络请求使用 `httpx`，Proxy 行为见 [配置参考](../reference/configuration.md#proxy)。

## 4. 为什么标为只读

审批确认的是本机改动；HTTP GET 不修改 workspace。网络可见性由 `tool.started` 的安全摘要和事件承担。

“只读”不代表没有隐私影响：查询词、URL 和请求上下文会发送到外部服务。未来 Permission Engine 可以引入独立的网络权限，而不是把它混入当前文件改动审批。

## 5. 已知限制

Bing 对 Python 客户端可能返回降级结果，技术查询相关性不稳定；HTML 结构改变也会使解析失效。T2 计划改为带密钥的搜索 API，并决定无密钥时是否回落到 Bing。

`fetch_url` 不是浏览器：客户端渲染页面、复杂正文抽取、Cookie、登录态、下载和交互页面均不在范围内。

## 6. 验收标准

- 参数、协议、超时、响应大小、重定向和输出长度均受限制。
- 搜索结果包含可继续传给 `fetch_url` 的 HTTP(S) URL。
- 网络和解析失败以安全 `ToolFailure` 返回，不泄漏完整异常。
- 测试使用 MockTransport，不依赖真实网络。
