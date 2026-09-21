# Web 工具设计

> 状态：**已实现**（2026-09-21）。实施过程与验证证据见 [M3-local-execution.md](../milestones/M3-local-execution.md) 的 M3b 小节。
> 对应阶段：Phase 1 · Core / M3b（工具集扩展）。
> 相关实现：`src/nemo/adapters/tools/web.py`、`src/nemo/bootstrap.py`。

## 1. 目标

给 Agent 真正的联网能力，而不是只有「能用 `run_shell` 调 `curl`」这一条隐含路径。

背景：模型此前会回答「我没有联网能力」。它这么说**不算错**——工具清单里确实没有联网工具——但也不对，因为 shell 能联网。系统提示对环境只字未提，模型只能猜。补两个一等工具比补一句提示更实在。

两条可检验的目标：

1. **能查**：给一个查询，拿回若干条「标题 + URL + 摘要」。
2. **能读**：给一个 URL，拿回可读正文（HTML 转文本），带上限与超时。

## 2. 接口与分层

两个工具，都放在 `src/nemo/adapters/tools/web.py`（与 Filesystem、Shell 同级），都由 `bootstrap.build_local_tools()` 注册：

| 工具 | 参数 | 返回 | `read_only` |
|---|---|---|---|
| `web_search` | `query`、`max_results`(1–10，默认 5) | 编号列表：标题 / URL / 摘要 | `True` |
| `fetch_url` | `url`（仅 http/https）、`max_chars` | 去标签后的正文，超出按 `truncate_text` 截断 | `True` |

**为什么标记成 `read_only`**：审批的语义是「改动前确认」，而 HTTP GET 不改动本机任何东西。三档里的只读工具都放行，这两条遵守同一条规则。数据外发的风险由**可见性**兜底，而不是由询问兜底：每次调用的查询词或 URL 都写进 `tool.started` 的 `summary`，终端和 transcript 里都看得到。

（这一点是可争论的：`curl` 在 `command_rules` 里是 `destructive`，而等价的 `fetch_url` 却更安静。取舍见 §3。）

## 3. 决策与理由

| 决策 | 理由 | 放弃的替代方案 |
|---|---|---|
| 独立工具，而不是只靠 `run_shell` + `curl` | 工具是模型的能力清单；模型看不到的能力约等于不存在。独立工具还能结构化返回、统一截断与超时 | 只加一句系统提示告诉它「你可以 curl」：每个模型对这句话的利用程度不同，且输出是原始 HTML |
| 搜索后端用 Bing HTML，不引入密钥 | 用户当前网络下 Google/DuckDuckGo 不可达，Bing 与 Baidu 可达；Baidu 返回反爬页，Bing 的 `b_algo` 结构可稳定解析。零配置即可用 | 上 Tavily / Brave 等 API：结果质量更好、是正式接口，但要用户先去注册并配密钥。**这是长期方向**，代码里把解析收在一个函数内便于替换 |
| 两个工具都 `read_only = True` | 审批的语义是「改动前确认」，GET 不改动任何东西；模型要外发数据时，一条 URL 里的内容你也看不出所以然，询问提供的保护有限 | 让它们非只读：`ask` 档每次搜索都打断一次，实际结果多半是用户切到 `auto` 然后什么都不问了 |
| HTTP 层用 `httpx` 流式读取并设字节上限 | 直接 `response.text` 会把整个响应读进内存，一个 1 GB 的响应能把进程拖死；流式读到上限就停 | 依赖 `Content-Length`：这个头经常缺失或撒谎 |
| 只允许 http/https，不吃 `file://` 之类 | 避免把本机文件读取伪装成「联网」绕开 workspace 边界 | 允许任意 scheme：等于给了一条绕过路径检查的通道 |
| HTML 转文本用正则，不引第三方解析库 | 目标是「让模型读懂」，不是精确渲染；`bs4`/`lxml` 是真实依赖，为这个收益不值 | 引入 HTML 解析器：依赖增加，收益是可读性而非正确性 |

## 4. 验收标准

| 验收点 | 验证方式 |
|---|---|
| 搜索结果可解析 | 用固定 HTML 夹具断言标题/URL/摘要都被提取出来 |
| 结果数量受控 | `max_results` 超界被参数校验拒绝 |
| 正文去标签 | `<script>`/`<style>` 内容不出现，标签被剥掉，实体被解码 |
| 正文有上限 | 超长响应按 `max_output_chars` 截断并带标记 |
| 协议受限 | `file:///etc/passwd` 被拒 |
| 超时可控 | 单次请求受 `ExecutionContext` 的超时约束 |
| 不联网也能测 | 用 `httpx.MockTransport` 注入响应，测试不依赖真实网络 |
| 真实可用 | 真实模型跑通「搜索 → 打开结果 → 回答」 |

## 5. 实测发现的限制（2026-09-21）

**Bing 对 Python 客户端返回的是降级结果集。** 同一条查询，`curl -L` 拿到的是与查询强相关的结果，我们的工具拿到的是一组品牌导航页：

| 客户端 | 查 `python asyncio` 的前三条 |
|---|---|
| `curl -L` | `liaoxuefeng.com/.../asyncio`、`runoob.com/python3/python-asyncio.html`、`docs.python.org/3/library/asyncio.html` |
| httpx（本工具） | `python.org` 首页、`python.org/downloads`、`docs.python.org` 根 |

排查过的方向，全部无效：`setlang` / `ensearch` / `mkt` / `cc` 四个参数、去掉 `Accept-Language`、`Accept-Encoding: identity`、直接照抄 curl 的头部、`format=rss`。HTTP/2 需要新增 `h2` 依赖，没有试。

**这意味着什么**：搜「DeepSeek 官网」这类**导航型**查询是准的（实测正确定位到 `deepseek.com` 并抓回正文），但搜技术细节时相关性不可靠。链接解码（`bing.com/ck/a?...&u=a1<base64>`）已修，所以拿到的至少是真实域名而不是跳转壳。

**真正的修法是换后端**：用带密钥的搜索 API（Tavily / Brave 之类）。已记入 [TODO.md](../TODO.md) 的 T2。

## 6. 非目标

- 搜索结果的排序、去重、缓存：先用引擎给的顺序。
- 多搜索后端与运行时切换：等出现第二个真实需求（比如换成 Tavily）再抽象。
- JS 渲染的页面：只取服务器返回的静态 HTML。
- 登录态、Cookie、表单提交：这是读取，不是浏览。
- PDF/图片等非 HTML 内容：留给专门的工具。
- 反爬对抗：被限流就如实报错，不做绕过。
- 伪装 TLS 指纹：`curl_cffi` 之类能拿到更好的结果，但那是新增依赖 + 对抗搜索引擎的策略，不在这一版。
