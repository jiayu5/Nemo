# Nemo

Nemo 是一个从底层实现的个人 Agent Runtime 与 Agent OS 实验平台。它已经具备真实模型接入、本地文件与 Shell 工具、Web 工具、会话持久化、HTTP/SSE Server、CLI、审批、Trace 和 React UI。

M1–M5 已完成，下一阶段是 **M6 macOS App**。完整状态见 [文档索引](docs/README.md)，系统边界见 [架构文档](NEMO_ARCHITECTURE.md)。

## 快速开始

要求：Conda、Node.js/npm，以及 Python 3.12 环境。

```bash
conda create --file environment.yml --override-channels -c conda-forge
conda run -n nemo python -m pip install -r requirements-lock.txt -e .
npm --prefix apps/ui ci
```

Nemo 的模型路由位于 `~/.nemo/config.toml`，密钥位于权限为 `600` 的 `~/.nemo/.env`。完整字段、示例和 Proxy 行为见 [配置参考](docs/reference/configuration.md)。

先启动本地 Server（默认端口 `18765`）：

```bash
conda run -n nemo python -m nemo.server
```

然后选择一个客户端。

### React UI

```bash
npm --prefix apps/ui run dev
```

打开 `http://127.0.0.1:5173`。Vite 将 `/api` 代理到本地 Server；UI 不直接读取数据库、配置文件或密钥。若要换 Server 端口，在启动 Server、CLI 和 Vite 前设置同一个 `NEMO_SERVER_PORT` 环境变量。

右上角 **Connections** 可以新增或更新 Provider/Model、设置默认模型、配置 API Key 与可选 Proxy，并在保存后显式测试连接。密钥旧值不会回显；来自 Server 进程环境的值是只读覆盖层。

### CLI

```bash
conda run -n nemo python -m nemo.cli --workspace . "检查当前项目"
conda run -n nemo python -m nemo.cli --workspace .
conda run -n nemo python -m nemo.cli --sessions
conda run -n nemo python -m nemo.cli --session <session-id>
```

交互模式使用 `:exit` 或 `:quit` 退出；Session 仍保存在 Server 的 SQLite 中。`--direct` 是绕过 Server 的旧直连恢复模式，不是默认路径。

## 当前能力

- 配置驱动的 Provider、Model、Alias 和 Profile；选择优先级为 `run > session > agent > default`。
- `openai_compatible` 协议和 Tool Calling；其他协议名称已预留，但适配器尚未实现。
- 七个工具：`read_file`、`write_file`、`edit_file`、`list_dir`、`run_shell`、`web_search`、`fetch_url`。
- `ask`、`auto`、`full` 三档审批；`auto` 依赖启发式规则，不是沙箱。
- Session/Run、消息、事件、步骤、工具调用和模型身份持久化。
- SSE 领域事件、取消、审批回传、断线续读和 Run Trace。
- React UI 中的会话恢复、Markdown 消息、模型与审批选择、停止、审批和执行轨迹。
- Provider Settings 的内存校验、原子保存、密钥来源状态、显式连接测试和按 Provider 配置的 Proxy 引用。

## 重要边界

- Nemo 在本机执行，但远程模型会接收发送给 Provider 的上下文。
- 文件工具限制在 workspace 内；`run_shell` 仍拥有当前用户的系统权限。workspace 和审批都不是操作系统沙箱。
- Server 默认只监听 `127.0.0.1:18765`，但当前尚未实现桌面端的短期访问凭据与 Origin 防护。
- 模型 token 增量流式尚未实现；SSE 当前传输的是 Run 领域事件。
- 不自动重试模型或有副作用的工具；Server 重启会把未完成 Run 标记为 `interrupted`，不会重放。
- UI 暂不编辑高级 Alias、Profile、静态 headers 或模型 parameters；这些仍可在配置文件中维护，UI 保存时会保留它们。

## 验证

```bash
conda run -n nemo python -m unittest discover -s tests -v
conda run -n nemo python examples/minimal_agent.py
npm --prefix apps/ui test -- --run
npm --prefix apps/ui run typecheck
npm --prefix apps/ui run build
```

API 见 [Server API 参考](docs/reference/server-api.md)，开发约束见 [AGENTS.md](AGENTS.md)。
