# Nemo

个人 Agent OS 与 Agent 技术实验平台。M1 完成最小单 Agent 内核，M2 完成配置驱动的真实模型接入，M3 完成本地工具，M4 完成 Context、CLI 与本地 Server。

## 运行

统一使用 Conda 的独立 `nemo` 环境，Python 3.12；不在 base 环境安装项目依赖。在项目根目录执行：

```bash
conda create --file environment.yml --override-channels -c conda-forge
conda activate nemo
python -m unittest discover -s tests -v
python examples/minimal_agent.py
```

自动化执行可用 `conda run -n nemo python ...`，无需激活环境。

`environment.yml` 管理 Python/pip 环境，pip 安装 `pyproject.toml` 定义的项目；`requirements-lock.txt` 固定当前验证的 Python 依赖版本。修改依赖后，在已激活的环境执行：

```bash
python -m pip install -r requirements-lock.txt -e .
python -m pip check
```

版本快照不包含完整 Conda 二进制环境及包哈希，不等同于跨平台锁文件。项目不使用 uv，旧 `.venv/` 和 `uv.lock` 已清理。

演示固定执行 `12 + 30`，模型先提出 `add` 调用，再检查 Runtime 回填的 `42`，返回最终回答。这是闭环验证，不具备自然语言计算能力。

## Server 与 CLI

Server 持有 Session、Run、事件、审批与 SQLite 持久化；CLI 默认通过 HTTP/SSE 连接本地 Server。先启动 Server：

```bash
conda run -n nemo python -m nemo.server
```

再在另一个终端使用 CLI：

```bash
conda run -n nemo python -m nemo.cli --workspace . "检查当前项目"
conda run -n nemo python -m nemo.cli --session <session-id>
conda run -n nemo python -m nemo.cli --sessions
```

Server 默认只监听 `127.0.0.1:8765`，数据库为 `~/.nemo/nemo.db`。CLI 保留 `--direct` 作为故障恢复与嵌入式调试入口；该模式仍使用旧 JSONL transcript，不是默认路径。

开发态 React UI 需要保持 Server 运行，再开一个终端：

```bash
npm --prefix apps/ui ci
npm --prefix apps/ui run dev
```

浏览器打开 `http://127.0.0.1:5173`。Vite 把相对路径 `/api` 代理到本地 Server；UI 不直接读取 SQLite、配置文件或密钥。前端验证命令为 `npm --prefix apps/ui test -- --run`、`npm --prefix apps/ui run typecheck` 与 `npm --prefix apps/ui run build`。

## 接入真实模型

路由配置与密钥放在 `~/.nemo/`，不进仓库：

| 文件 | 内容 | 权限 |
|---|---|---|
| `~/.nemo/config.toml` | provider、model、alias、profile | 常规 |
| `~/.nemo/.env` | `DEEPSEEK_API_KEY=...` 等密钥 | 必须 600 |

最小配置示例：

```toml
default = "chat"

[providers.deepseek]
protocol = "openai_compatible"
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"

[models.deepseek-chat]
provider = "deepseek"
model_id = "deepseek-chat"
capabilities = ["tool_calling"]

[profiles.chat]
model = "deepseek-chat"
```

跑一次真实调用：

```bash
conda run -n nemo python examples/deepseek_agent.py "用一句话介绍你自己"
conda run -n nemo python examples/deepseek_agent.py --model think "9.11 和 9.9 哪个大"
```

几点约定：

- `base_url` 只写到 API 根，路径由适配器拼接（`openai_compatible` 用 `{base_url}/chat/completions`）。
- `capabilities` 决定 Runtime 能不能给这个模型发工具；未声明 `tool_calling` 的模型收到工具会立刻报错，而不是静默降级。
- 模型选择的优先级是 `per-run > session > agent > default`。
- **新增一个使用相同协议的 provider 只需改配置**，不写代码；只有出现新协议才需要加适配器。
- 密钥来源优先级：环境变量 > `.env`，便于临时覆盖。
- 失败时 `state.error` 只包含脱敏后的摘要（如 `Provider returned HTTP 401: ...`），不含密钥与原始异常。
- 每次模型调用的 token 统计（含缓存命中数）会进入 `model.completed` 事件：`prompt=2082 cached=1920 completion=1`。

## 本地工具

| 工具 | 作用 | 只读 |
|---|---|---|
| `read_file` | 按行读取文本文件，返回总行数以便分页 | 是 |
| `write_file` | 创建文件；覆盖已有文件必须显式 `overwrite=true` | 否 |
| `edit_file` | 精确字符串替换；匹配不唯一时拒绝执行 | 否 |
| `list_dir` | 列目录，子目录带 `/` 后缀 | 是 |
| `run_shell` | `/bin/sh -c` 执行命令，返回退出码、stdout、stderr | 否 |
| `web_search` | 搜索公开网页并返回标题、URL 与摘要 | 是 |
| `fetch_url` | 读取 HTTP/HTTPS 静态页面并转为文本 | 是 |

```bash
DEMO_WS=$(mktemp -d)
conda run -n nemo python examples/local_agent.py --workspace "$DEMO_WS" \
  "在这个目录里创建 hello.txt，内容写 pong，然后确认内容"
conda run -n nemo python examples/local_agent.py --workspace "$DEMO_WS" --read-only "这个目录里有什么"
```

三条必须知道的边界：

- **`workspace` 是约定，不是沙箱。** 路径参数会被限制在 workspace 内（含符号链接指向外部的情况），但 `run_shell` 执行的命令可以访问机器上任何它有权访问的位置。沙箱排在 Phase 3。
- **审批不是沙箱。** 默认 Server CLI 支持 `ask` / `auto` / `full`；`auto` 依靠命令形态规则，最坏情况等同 `full`。上方 `examples/local_agent.py` 是旧直连演示，只支持 `--read-only` 整体拒绝写入，不提供逐次审批。
- **超时与取消会终止整个进程组。** 不会留下后台孤儿进程；`run_shell` 每次都是新进程，`cd` 与环境变量不跨调用保留，必须显式传 `workdir`。

## 当前边界

- Core 数据契约使用 Pydantic；模型通过异步 `generate` Protocol 接入，`ModelClient` 满足该协议，厂商差异全部关在 `adapters/`。
- 已实现的协议适配器只有 `openai_compatible`；`openai_responses` 与 `anthropic_messages` 已在契约中预留，尚未实现，配置到未实现协议会给出明确错误。
- 模型 token 流式（streaming）尚未实现；Server 已通过 SSE 流式传输领域事件，后续 UI 可直接复用。
- Context Builder 为每次模型调用生成独立消息快照。
- 工具按顺序执行，使用严格参数校验；未知工具、参数错误、执行错误作为 ToolResult 回填。
- 每个 Run 拥有独立状态、连续编号的事件和唯一终态；max_steps 按模型调用轮次计数。
- `cancel=asyncio.Event()` 可取消当前模型/工具并返回 cancelled 结果；对 run Task 调用 `cancel()` 会清理后保留 asyncio.CancelledError 语义。
- `on_event` 是同步、非阻塞的观测回调；回调异常生成 observer.failed，不改变执行结果。事件不包含工具参数或原始异常。
- 不自动重试工具与模型；不提供进程隔离、流式 token、跨协议历史转换或崩溃后恢复执行。Server 重启时会把遗留 Run 标记为 `interrupted`，不重放工具。
- 工具实现须协作响应 asyncio 取消；CPU 阻塞代码或吞掉取消的实现不在当前取消保证内。

实施过程与决策记录见 [docs/README.md](docs/README.md)，架构与后续规划见 [NEMO_ARCHITECTURE.md](NEMO_ARCHITECTURE.md)，开发规则见 [AGENTS.md](AGENTS.md)。
