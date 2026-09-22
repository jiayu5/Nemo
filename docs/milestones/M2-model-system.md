# M2 · 模型系统

| 项 | 内容 |
|---|---|
| 状态 | 进行中：M2a 与 `openai_compatible` 已完成 |
| 完成日期 | M2a：2026-09-17 |
| 阶段 | Phase 1 |
| 代码范围 | `src/nemo/config/`、`core/models/`、`adapters/protocols/`、`adapters/httpx_transport.py`、`bootstrap.py` |

## 1. 目标与范围

让真实模型通过配置装配进 M1 Runtime，同时保持 Runtime 与厂商无关。

已完成配置/密钥加载、Registry/Resolver、统一 Model Client、HTTP transport、`openai_compatible` Tool Calling、错误脱敏和 token usage。未完成 `openai_responses`、`anthropic_messages`、token delta、跨协议历史转换和自动重试。

## 2. 交付物

- `config/loader.py`、`config/secrets.py`：TOML 配置、引用校验、环境变量与 `.env` 密钥。
- `core/models/registry.py`、`resolver.py`、`client.py`：选择解析和 Runtime-facing Model。
- `adapters/protocols/openai_compatible.py`：请求编码、Tool Call 解码、usage 和错误映射。
- `adapters/httpx_transport.py`：延迟创建的 `httpx.AsyncClient`。
- `bootstrap.py`：具体 adapter 的唯一装配点。

## 3. 关键决策

- **Providers are configuration, protocols are code**：新增同协议 Provider 不改 Runtime。
- 配置使用 TOML；密钥只通过变量名引用，进程环境优先于 `~/.nemo/.env`。
- Model、Alias、Profile 分离；选择优先级为 `run > session > agent > default`。
- Protocol adapter 负责编解码，transport 只负责 HTTP。
- 未实现协议明确失败，不静默降级。
- token delta 推迟到出现真实 UI 消费者之后，避免提前承担分片 Tool Call 的复杂度。

## 4. 实施摘要

先建立无厂商配置契约和两阶段引用校验，再实现 Registry/Resolver 与 Model Client。随后加入 HTTP transport、OpenAI-compatible adapter、Tool Calling fixture、密钥脱敏和 usage 事件，最后用真实配置完成手工冒烟验证。

## 5. 验证

```bash
conda run -n nemo python -m unittest discover -s tests -v
conda run -n nemo python examples/deepseek_agent.py "用一句话介绍你自己"
```

里程碑完成时 60 个自动化测试通过；常规测试使用 fixture，不依赖网络或真实密钥。

## 6. 遗留与下一步

- `openai_responses` 与 `anthropic_messages` 仍只有契约字面量，没有 adapter。
- 模型响应仍是非流式完整响应；SSE 后来只解决领域事件传输。
- Provider GUI 配置与安全写入归 M5c。

2026-09-22 结构整理将通用名 `transport.py` 重命名为 `httpx_transport.py`，协议与行为未改变。
