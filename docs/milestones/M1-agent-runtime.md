# M1 · Agent Runtime 最小闭环

| 项 | 内容 |
|---|---|
| 状态 | 已完成 |
| 完成日期 | 2026-09-16；环境清理 2026-09-17 |
| 阶段 | Phase 1 |
| 代码范围 | `src/nemo/core/`、`src/nemo/testing/`、`examples/`、`tests/` |

## 1. 目标与范围

在不依赖真实模型、Shell、Server 或 UI 的条件下，跑通 `Model → Tool → Model → Final`，并锁定状态、错误、取消与事件语义。

本步包含领域契约、AgentState、Tool Registry、顺序 Agent Loop、Fake Model/Tool 和最小 Context Builder；不包含外部 I/O、持久化、客户端、重试、流式 token 或沙箱。

## 2. 交付物

- `core/contracts/`：消息、模型请求/响应、Tool Call/Result、事件和错误。
- `core/runtime/agent.py`：步数限制、工具回填、终态和取消。
- `core/tools/registry.py`：注册、schema、严格参数校验与错误归一。
- `testing/fakes.py`：无网络、确定性的模型与工具替身。
- `examples/minimal_agent.py`：固定 `12 + 30 = 42` 的闭环演示。

## 3. 关键决策

- Runtime 只依赖 Protocol 和契约，不认识 Provider 或客户端。
- Tool Call 错误以 ToolResult 回填，使模型有机会修正；未知内部异常保持不透明。
- `max_steps` 按模型调用计数；一次响应中的工具顺序执行。
- `cancel=Event` 返回 cancelled 结果；直接取消 task 清理后保留 `CancelledError` 语义。
- 每个 Run 只提交一个终态；观察回调失败不能改变业务结果。

## 4. 实施摘要

先固定消息和工具契约，再实现 Registry 和 Loop，最后补齐未知工具、参数错误、执行失败、步数耗尽、取消和 observer 失败测试。环境随后统一到 Conda `nemo`，移除旧 uv/`.venv` 路线。

## 5. 验证

```bash
conda run -n nemo python -m unittest discover -s tests -v
conda run -n nemo python examples/minimal_agent.py
```

里程碑完成时 13 个测试通过，演示得到工具结果 `42` 和 completed 终态。

## 6. 遗留与下一步

M1 的 Model 和 Tool 都是 Fake；真实模型由 M2 接入，本地副作用工具由 M3 接入，Session/客户端/持久化由 M4 接入。
