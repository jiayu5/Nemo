# M2 · 模型系统

| 项 | 内容 |
|---|---|
| 编号 | M2 |
| 状态 | 进行中（M2a 与 `openai_compatible` 已完成，其余协议适配器待做） |
| 完成日期 | M2a：2026-09-17 |
| 对应阶段 | Phase 1 · Core |
| 代码范围 | `src/nemo/config/`、`src/nemo/core/models/`、`src/nemo/adapters/`、`src/nemo/bootstrap.py`、`examples/deepseek_agent.py` |

## 1. 目标与范围

**目标**：让真实模型通过配置装配进 Runtime，且 Runtime 依然不认识任何厂商。M1 证明的是「骨架能跑」，M2 决定「骨架能不能长在真实模型上」。

**做**：

- 配置与密钥加载：`~/.nemo/config.toml`（TOML）与 `~/.nemo/.env`（权限 600）。
- 模型注册表与解析器：provider、model、alias、profile，解析优先级 `per-run > session > agent > default`。
- Model Client：把解析结果装配成满足 Runtime `Model` Protocol 的对象，对 Runtime 零接口改动。
- 协议适配器：`openai_compatible`（覆盖 DeepSeek 等兼容端点），含请求编码、响应解码、Tool Calling、错误归一。
- HTTP 传输：`httpx`，并把连接失败与错误状态分别归一。
- 密钥防泄漏：`SecretValue` 包装、`EncoderRequest` 脱敏 repr、错误信息整值替换。
- Token 统计：解析 provider 的 `usage`（含缓存命中数），进入 `model.completed` 事件，使「重发全量历史」的代价可测量。

**不做**（刻意排除）：

- 流式（streaming）：**经用户确认推迟到 M4/M5**，理由见 §3。
- `openai_responses` 与 `anthropic_messages` 适配器：契约已预留，实现留到后续；配置到未实现协议会明确报错，不会静默降级。
- 跨协议历史转换、模型重试、超时重试、连接池调优。
- Provider 配置的 GUI 编辑、连接测试按钮（属于 M5）。
- 在事件里标注实际使用的模型标识（需要再动 Runtime，留到观测里程碑）。

## 2. 交付物

| 文件 | 作用 |
|---|---|
| `src/nemo/core/contracts/errors.py` | `NemoError` 体系，每个错误带可安全展示的 `public_message` |
| `src/nemo/core/contracts/secrets.py` | `SecretValue`：`repr`/`str` 恒为 `***`，提供 `redact()` |
| `src/nemo/core/contracts/types.py` | 新增 `ModelUsage`；`ModelResponse` 增加 `usage` 字段 |
| `src/nemo/core/contracts/model_config.py` | `ProviderConfig` / `ModelConfig` / `ProfileConfig` / `AppConfig` / `ResolvedModel` |
| `src/nemo/core/contracts/model_transport.py` | `EncodedRequest`（repr 脱敏）、`HttpResponse`（含非 JSON 文本兜底）、`Transport` 与 `ProtocolAdapter` Protocol |
| `src/nemo/config/loader.py` | 读取并校验 `config.toml`：结构校验 + 引用完整性校验 |
| `src/nemo/config/secrets.py` | `SecretLoader`：环境变量优先、`.env` 兜底、权限提示 |
| `src/nemo/core/models/registry.py` | `ModelRegistry`：名称查找与 profile/alias/model 解析 |
| `src/nemo/core/models/resolver.py` | `ModelResolver`：优先级解析，参数合并，产出 `ResolvedModel` |
| `src/nemo/core/models/client.py` | `ModelClient`：能力检查 + 编码 → 传输 → 解码 → 错误归一 |
| `src/nemo/core/runtime/agent.py` | `model.completed` 事件携带 `prompt_tokens` / `completion_tokens` / `cached_prompt_tokens` |
| `src/nemo/adapters/protocols/openai_compatible.py` | chat-completions 协议的编码/解码/错误处理 |
| `src/nemo/adapters/transport.py` | `HttpxTransport`：真实 HTTP，懒创建 client，异常归一 |
| `src/nemo/bootstrap.py` | 唯一装配入口；`ADAPTERS` 映射即协议支持矩阵 |
| `examples/deepseek_agent.py` | 真实模型的端到端冒烟脚本，永不打印密钥 |
| `tests/test_config.py` / `tests/test_models.py` | 47 个新用例（配置 17、模型与适配器 30） |

## 3. 关键决策与理由

| 决策 | 理由 | 放弃的替代方案 |
|---|---|---|
| 配置格式用 TOML（`config.toml`） | 标准库 `tomllib` 可用，零新依赖；避免 YAML 的隐式类型转换（`on`/`yes`/`1e5` 被解析成布尔或浮点） | YAML（架构文档原方案）：要多引入 PyYAML，且类型陷阱在密钥/布尔字段上代价高。此项经用户确认后同步改架构文档 |
| 流式推迟到 M4/M5 | 当前没有消费者（Server/UI 未存在），写完只能自测；流式引入 Tool Call 参数分片拼接、中途断流等难验证的边界 | 在 M2 一并实现：会产出无法验证的代码，并推迟 M3 真实工具接入 |
| `ResolvedModel` 只存 `api_key_env`（名字），不存密钥值 | 该对象可以安全序列化进事件与 Trace，不会因为「有人顺手 dump 一下」泄漏凭证 | 把密钥值放进解析结果：任何一次日志或事件序列化都会泄漏 |
| `SecretValue` 用类型而不是约定来防泄漏 | `repr`/`str` 恒为 `***`，即使被误扔进 f-string、日志或异常链也不会泄漏；`redact()` 用于处理 provider 回显 | 靠「记得别打印」的编码规范：一次疏忽就泄漏，且无法测试 |
| `EncodedRequest.__repr__` 脱敏 headers | 编码结果必然带 `Authorization`，默认 dataclass repr 会直接打印它 | 用 Pydantic 模型承载编码结果：repr 会带出 headers |
| 协议是 `Literal`，但真实支持矩阵在 `bootstrap.ADAPTERS` | 配置里写出未实现协议会立刻报错，且错误信息列出已实现协议；支持矩阵只有一处真相 | 契约只列已实现的协议：用户把 `anthropic_messages` 写错成 `anthropic` 时，得到的是「字段值非法」而不是「尚未实现」 |
| 校验分两遍：Pydantic 结构 + 引用完整性 | 结构错误和「指向不存在的 provider」需要不同的错误信息与定位 | 只用 Pydantic：跨字段引用只能靠自定义 validator，错误定位更差 |
| 禁止配置覆盖 adapter 拥有的键（`model`/`messages`/`tools`/`stream`） | 请求体是适配器的职责，让配置去覆盖它会产生难查的错发请求 | 允许覆盖：一个 profile 写错就能悄悄改掉请求结构 |
| 密钥来源：环境变量 > `.env` | 支持临时覆盖与 CI 注入，不必改用户文件 | 文件优先：临时换 key 必须编辑用户文件，且容易留下陈旧值 |
| 错误分层（config / reference / secret / capability / provider / transport / response） | 每类错误的处置方式不同：配置错误要改文件，能力不匹配要换模型，额度问题要等 | 统一抛 `RuntimeError`：调用方无法区分，重试与提示都会做错 |
| Runtime 增加一行：`state.error` 采用 `public_message` | **偏离原计划的「Runtime 零改动」**。接真实模型后失败必须可诊断——否则第一次 401 只能看到 `Runtime execution failed`，无法区分是密钥错、路径错还是额度用尽。改动只读一个属性，未泄漏原始异常文本，M1 的脱敏保证不变 | 保持固定字符串：把「失败原因」推迟到观测里程碑，代价是真实接入阶段完全不可调试 |
| 传输层保留非 JSON 的错误文本（截断 300 字符） | 实测 DeepSeek 的 401 返回纯文本 `Authentication Fails (governor)`，丢弃文本会让最常见的认证失败没有任何线索 | 只信 JSON：主流协议的报错并非都走 JSON |
| `ModelRegistry` 保持只读、`bootstrap` 独占装配 | 装配是唯一需要认识具体适配器的地方；`core/` 因此可以做到零厂商、零适配器 import | 在 Resolver 内部直接 new 适配器：`core/` 立刻依赖 `adapters/`，边界失效 |
| `usage` 放进 `ModelResponse` 契约，而不是挂在 `ModelClient` 的属性上 | 统计属于「这次调用的结果」；放进契约后 Runtime 无需知道 client 的内部状态即可读，也让 Fake Model 能产出同样的形状 | 由 client 保存 `last_usage`：Runtime 要反向依赖具体实现，Fake 与真实路径行为不一致 |
| 未报告 usage 时为 `None`，不伪造 `0` | `0` 与「provider 没报」是两件事：前者是「没有缓存命中」，后者是「无法判断」。混用会让命中率统计失真 | 统一填 0：所有缺失数据看起来都像「缓存完全没命中」 |
| 事件字段即使无 usage 也保留 key（值为 `None`） | 消费方（Trace/UI）需要稳定 schema，不能因为 provider 不同就少字段 | 缺省则不输出：消费方必须写 `payload.get(...)` 兜底 |
| `cached_prompt_tokens` 按 `prompt_tokens` 截断 | provider 报出的命中数不可能超过 prompt 本身；截断让 `cache_hit_rate()` 永远 ≤ 100% | 原样透传：一个上游的脏数据就能让统计出现 >100% 的命中率 |
| 兼容两套缓存字段：`prompt_cache_hit_tokens`（DeepSeek）与 `prompt_tokens_details.cached_tokens`（OpenAI） | 实测 DeepSeek 同时返回两家字段，OpenAI 兼容网关则多只有后者；读两套的成本极低 | 只读一家：换一个兼容网关就丢失统计 |

## 4. 实施过程

1. **约束先行**：先在 `AGENTS.md` 写明 `config/`、`core/models/`、`adapters/` 的用途与命名规则、`~/.nemo/` 的固定位置与 TOML 决定，再建目录；同步把架构文档的 YAML 改为 TOML，并记录流式推迟的决定。
2. **契约层**：先定 `errors.py`、`secrets.py`、`model_config.py`、`model_transport.py`。错误与密钥的类型先立起来，后续所有模块都对着它们写。
3. **配置层**：`loader.py` 做两遍校验，`secrets.py` 定义来源优先级与权限提示。
4. **核心层**：`registry.py` → `resolver.py` → `client.py`，全程不 import 任何适配器。
5. **适配器层**：`openai_compatible.py` 与 `transport.py`，并把「哪些协议可用」集中到 `bootstrap.ADAPTERS`。
6. **依赖变更**：新增 `httpx`，按已验证的传递闭包重写 `requirements-lock.txt`（用 `importlib.metadata` 走一遍依赖图，而不是手抄）；重装后 `pip check` 通过。
7. **测试**：先跑通 41 个新用例，再用 `httpx.MockTransport` 覆盖真实传输代码路径（不发包）。
8. **真实端点探测（转折点）**：直接用假密钥打 `https://api.deepseek.com/v1/chat/completions`，得到 **401** 而非 404，确认路径正确；同时发现错误体是**纯文本**，于是回头给 `HttpResponse` 加 `text` 字段并让 `decode_error` 兜底。这一步避免了「用户填了 key 后才发现 404 或看不到失败原因」。
9. **真实配置文件**：经用户同意创建 `~/.nemo/config.toml`（DeepSeek provider、`deepseek-chat`、`deepseek-reasoner`、alias `fast`/`think`、profile `chat`/`reasoning`）与 `~/.nemo/.env`（600，值为空，等用户填写）。
10. **冒烟脚本**：`examples/deepseek_agent.py`，打印模型标识、协议与密钥**来源**，不打印密钥；按模型能力决定是否挂载工具。
11. **补 token 统计（2026-09-17，用户提出 KV Cache 复用问题后）**：先打真实请求导出 `usage` 原始结构，再实现解析——顺序不能反，否则字段名只能靠猜。实测 DeepSeek 同时返回 `prompt_cache_hit_tokens` 与 `prompt_tokens_details.cached_tokens`。
12. **缓存验证**：连跑同一请求三次，确认前缀缓存真实生效（2082 token 的提示，第 2、3 次命中 1920，命中率 92.2%），并确认我们的编码是稳定的——若序列化不稳定，相同请求不会得到相同的命中数。
13. **冒烟脚本升级**：`model.completed` 事件按 token 明细打印，缓存命中直接可见。

## 5. 验证证据

**单元与集成测试**（60 个用例全绿，其中 47 个为 M2 新增）：

```bash
conda run -n nemo python -m unittest discover -s tests
```

```text
Ran 60 tests in 0.039s

OK
```

**依赖闭包完整性**：

```bash
conda run -n nemo python -m pip install -r requirements-lock.txt -e . && conda run -n nemo python -m pip check
```

```text
No broken requirements found.
```

**真实端点路径探测**（不带密钥，只关心路由是否存在）：

```text
POST https://api.deepseek.com/v1/chat/completions -> HTTP 401
body: Authentication Fails (governor)
```

**真实链路的失败路径**（真实 HTTPS，假密钥，验证错误归一与脱敏）：

```bash
conda run -n nemo python -c "..."   # build_model_client(environ={'DEEPSEEK_API_KEY': 'sk-invalid-...'})
```

```text
status: failed
error : Provider returned HTTP 401: Authentication Fails, Your api key: ****test is invalid
```

**真实配置文件解析**：

```text
default -> deepseek-chat | deepseek-chat | openai_compatible | https://api.deepseek.com/v1 | ['tool_calling']
run override -> deepseek-reasoner []
permission warning: None
```

**未填密钥时的行为**（命令行返回码 2）：

```text
MissingSecretError: DEEPSEEK_API_KEY is not set; add it to /Users/jiayuli/.nemo/.env or export it in the environment
```

**覆盖情况**：

| 验收点 | 对应验证 |
|---|---|
| 配置解析与引用校验 | `test_config.LoaderTests`（缺文件、语法错、未知字段、未知协议、非法 base_url、未知 provider/alias/default、alias 与 profile 重名、保留参数） |
| 密钥来源优先级与解析 | `test_config.SecretLoaderTests`（环境变量优先、`.env` 兜底、引号与 `export` 前缀、缺失报错含变量名与路径、权限提示） |
| 密钥不泄漏 | `test_secret_never_prints_itself`、`test_redaction_...`、`test_encoded_request_repr_is_redacted`、`test_secret_never_reaches_the_result_or_the_events` |
| 解析优先级 | `ResolverTests.test_precedence_run_then_session_then_agent` |
| 参数与能力来自正确的层 | `test_profile_parameters_override_model_parameters`、`test_capabilities_and_headers_come_from_the_selected_model` |
| 请求编码正确 | `AdapterTests.test_encode_request_shape`、`test_tool_messages_round_trip_into_wire_format` |
| 响应解码与畸形负载 | `test_decode_response_with_tool_calls`、`test_decode_rejects_malformed_payloads` |
| 错误状态归一与脱敏 | `test_decode_error_redacts_the_secret`、`test_decode_error_uses_plain_text_bodies` |
| 能力不匹配在发包前被拦 | `test_capability_mismatch_is_rejected_before_encoding`（并断言传输层零请求） |
| 同协议新增 Provider 只改配置 | `test_same_protocol_new_provider_needs_config_only`（两个 provider 走同一份代码，URL 不同） |
| Model Client 与 Runtime 直连 | `test_client_drives_the_unchanged_runtime_loop`（工具调用 → 结果回填 → 最终回答） |
| Runtime 不含厂商/协议/传输知识 | `test_runtime_stays_vendor_and_transport_agnostic`（源码扫描 + 禁止 import adapters/config/models） |
| 真实传输代码路径 | `HttpxTransportTests`（用 `httpx.MockTransport` 覆盖发包、非 JSON 体、连接失败） |
| token 统计解析 | `test_decodes_usage_shape_captured_from_a_real_response`（真实响应结构）、`test_decodes_openai_style_cached_tokens_only`、`test_usage_may_be_absent_or_malformed`、`test_usage_values_are_clamped_and_optional` |
| 统计进入事件且 schema 稳定 | `test_usage_reaches_the_model_completed_event`、`test_usage_keys_stay_present_when_the_provider_reports_nothing` |

**前缀缓存的真实测量**（同一请求连续三次，`~/.nemo/config.toml` 默认模型）：

| 调用 | prompt tokens | 命中缓存 | 命中率 | completion |
|---|---|---|---|---|
| 第 1 次 | 2082 | 0 | 0% | 1 |
| 第 2 次 | 2082 | 1920 | 92.2% | 1 |
| 第 3 次 | 2082 | 1920 | 92.2% | 1 |

结论：**「每轮重发全量历史」不等于「每轮重新计算」**。命中 1920 = 30 × 64，说明 provider 按 64 token 的块对齐缓存，尾部不足一块的部分不计入命中。命中率不高时，先怀疑前缀被改动（时间戳、重排、插入检索内容），而不是先怀疑协议设计。

多步任务里的实测（`add` 工具闭环，连续三次运行）：

```text
run1 step=1 prompt= 317 cached= 128 ( 40.4%) completion=63
run1 step=2 prompt= 393 cached= 256 ( 65.1%) completion=10
run2 step=1 prompt= 317 cached= 128 ( 40.4%) completion=52
run2 step=2 prompt= 382 cached= 128 ( 33.5%) completion=10
run3 step=1 prompt= 317 cached= 128 ( 40.4%) completion=52
run3 step=2 prompt= 382 cached= 128 ( 33.5%) completion=10
```

短提示的命中率明显更低且不稳定（40% / 33%），原因有两层：块对齐对短提示的损耗更大；更关键的是**模型自己输出的那段文本每轮都不一样**（completion 63 与 52 就说明第一轮回复不同），而它位于第二轮的前缀里——前缀一 diverged，后面的缓存就全部失效。这部分不可控，也不该由我们去「修正」。

## 6. 遗留与下一步

**已知边界**：

- 真实模型调用尚未用真密钥跑通：用户填写 `~/.nemo/.env` 后执行 `conda run -n nemo python examples/deepseek_agent.py "你好"` 即可验证。
- `deepseek-reasoner` 按当前官方说明不支持 Function Calling，配置中未声明 `tool_calling`；若官方支持，改配置即可。
- 只实现了 `openai_compatible`；`openai_responses` 与 `anthropic_messages` 待实现。
- 流式未实现（接口位置已保留）。
- 跨协议历史转换未实现：不同协议混用时可能无法转换历史，当前没有检测机制。
- 事件里没有实际模型标识，跨模型对比仍需看配置。
- `session` 与 `agent` 两级选择目前只是可选入参，还没有对应实体，等 Server/Agent Manager 落地。
- provider 可能回显**掩码后**的密钥片段（实测 DeepSeek 显示末 4 位），当前只做整值替换；完整密钥不会出现在错误信息中。
- 未做模型重试与超时重试；`timeout_seconds` 只是单次请求超时。
- 缓存命中率依赖 provider 的内部策略，短提示命中不稳定，我们不控制也无法承诺；能做的只是测量。
- 尚无显式的缓存控制参数（如 OpenAI 的 `prompt_cache_key`、Anthropic 的 `cache_control`）；这些是协议特定能力，需要时关在对应适配器内实现。

**下一步**：

- 补 `openai_responses` 与 `anthropic_messages` 适配器，协议合同测试用脱敏 fixture，不依赖线上 API。
- 或按 Roadmap 推进 M3（本地执行：Filesystem / Shell / Python + 输出与超时控制），先让 Agent 真正能干活。
