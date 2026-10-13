# Nemo

Nemo 是一个从底层实现的个人 Agent Runtime 与 Agent OS 实验平台。它已经具备真实模型接入、本地文件与 Shell 工具、Web 工具、会话持久化、HTTP/SSE Server、CLI、审批、Trace、React UI、会话上下文压缩、工作区文本检索、用户/项目记忆与历史会话追溯、Skills 与异步并行只读 Subagents。

Phase 1 的单 Agent 闭环与 Phase 2 的 M7–M9 已交付；M2 的其他协议适配仍未完成，包含可打包的 **Nemo.app**。完整状态见 [文档索引](docs/README.md)，系统边界见 [架构文档](NEMO_ARCHITECTURE.md)。

macOS Apple Silicon 可从 [GitHub Release](https://github.com/jiayu5/Nemo/releases/latest) 下载打包应用；当前版本为 `v0.2.0`。

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

右上角 **Connections** 打开独立配置弹窗，可以新增或更新 Provider/Model、设置默认模型、配置 API Key 与可选 Proxy，并在保存后显式测试连接。密钥旧值不会回显；来自 Server 进程环境的值是只读覆盖层。

### CLI

```bash
conda run -n nemo python -m nemo.cli --workspace . "检查当前项目"
conda run -n nemo python -m nemo.cli --workspace .
conda run -n nemo python -m nemo.cli --sessions
conda run -n nemo python -m nemo.cli --session <session-id>
```

交互模式使用 `:exit` 或 `:quit` 退出；Session 仍保存在 Server 的 SQLite 中。`--direct` 是绕过 Server 的旧直连恢复模式，不是默认路径。

可以要求“把这项调查交给子 Agent，再汇总证据”。主 Agent 可调用 `delegate_task`；当前子 Agent 只读、使用独立上下文，详细限制见 [Subagents 参考](docs/reference/subagents.md)。

### macOS App

桌面应用不需要手动启动 Server：Tauri 会拉起因打包而内嵌的 Python sidecar，用随机 loopback 端口和每次启动生成的令牌通信。用户无需预装 Python。构建需要 Rust 工具链，其余依赖与快速开始一致。

```bash
PATH="$HOME/.cargo/bin:$PATH" npm --prefix apps/ui run desktop:dev    # 开发模式，热更新前端
PATH="$HOME/.cargo/bin:$PATH" npm --prefix apps/ui run desktop:build  # 构建 .app
open apps/ui/src-tauri/target/release/bundle/macos/Nemo.app
```

首次使用前先在 `~/.nemo/config.toml` 配好 Provider/Model，并把密钥写入权限为 `600` 的 `~/.nemo/.env`；否则应用能启动但没有可用模型。桌面端、CLI 和浏览器端共用 `~/.nemo/nemo.db` 中的会话历史；同一 Session 同时只运行一个任务。旧版桌面历史需要在两端 Server 都退出后运行 `conda run -n nemo python -m nemo.adapters.persistence.merge_desktop` 合并（会备份并保留原库）。构建产物未签名、未公证，首次打开若被 Gatekeeper 拦截，用右键“打开”确认一次即可。访问条件见 [Server API 参考](docs/reference/server-api.md#桌面-sidecar-访问条件)。

顶栏 **Memories** 可管理用户与当前项目记忆，并查看来源；使用规则见 [Memory 参考](docs/reference/memory.md)。

桌面端点击“Open a workspace”或“Browse…”会打开 macOS 目录选择器；浏览器端仍可直接输入目录。Session 可从左侧列表删除，删除前需要二次确认；活动 Run 所在的 Session 不可删除。

顶栏 **Skills** 可查看用户与当前项目已安装的技能，并查看正文。内置 `skill-installer` 可通过对话从 GitHub 安装完整技能目录；`skill-creator` 支持通过对话创建完整项目技能，可直接说“把这个流程保存为技能”。名称和描述提前提供给模型，正文按需加载；技能目录变更后重新打开会话生效。格式及恢复行为见 [Skills 参考](docs/reference/skills.md)。

## 当前能力

- 配置驱动的 Provider、Model、Alias 和 Profile；选择优先级为 `run > session > agent > default`。
- `openai_compatible` 协议和 Tool Calling；其他协议名称已预留，但适配器尚未实现。
- 本地文件、Shell、Web、工作区检索、长期记忆与历史会话与 Skills 工具；可用范围见 [架构文档](NEMO_ARCHITECTURE.md#tool)。
- `ask`、`auto`、`full` 三档审批；`auto` 依赖启发式规则，不是沙箱。
- Session/Run、消息、事件、步骤、工具调用和模型身份持久化。
- SSE 领域事件、取消、审批回传、断线续读和 Run Trace。
- React UI 中的会话恢复、Markdown 消息、模型与审批选择、停止、审批和执行轨迹。
- Provider Settings 的内存校验、原子保存、密钥来源状态、显式连接测试和按 Provider 配置的 Proxy 引用。
- 可打包的 macOS 应用：Tauri 窗口 + 内嵌 Python sidecar、随机端口、启动令牌、Origin 白名单和进程收敛。

## 重要边界

- Nemo 在本机执行，但远程模型会接收发送给 Provider 的上下文。
- 文件工具限制在 workspace 内；`run_shell` 仍拥有当前用户的系统权限。workspace 和审批都不是操作系统沙箱。
- Server 默认只监听 `127.0.0.1:18765`；打包应用使用随机端口并要求每次启动生成的 `X-Nemo-Token`，同时对浏览器 Origin 做白名单检查。两者都不是操作系统沙箱，拥有当前用户权限的本地程序仍可绕过。
- 桌面应用与 CLI Server 共用 SQLite 历史；同一 Session 的活动 Run 互斥，跨端取消与审批由 Run 属主协调。
- `openai_compatible` 模型可流式输出正文和 Provider 提供的推理；Server 分别通过 `model.delta`、`model.reasoning_delta` SSE 事件传递增量，UI 单独展示推理。工具调用在完整组装和校验后执行。
- 不自动重试模型或有副作用的工具；Server 重启会把未完成 Run 标记为 `interrupted`，不会重放。
- UI 暂不编辑高级 Alias、Profile、静态 headers 或模型 parameters；这些仍可在配置文件中维护，UI 保存时会保留它们。

## 验证

```bash
conda run -n nemo python -m unittest discover -s tests -v
conda run -n nemo python examples/minimal_agent.py
npm --prefix apps/ui test -- --run
npm --prefix apps/ui run typecheck
npm --prefix apps/ui run build
PATH="$HOME/.cargo/bin:$PATH" npm --prefix apps/ui run desktop:build
```

API 见 [Server API 参考](docs/reference/server-api.md)，开发约束见 [AGENTS.md](AGENTS.md)。

MCP 外部工具可在顶部 **MCP** 弹窗添加、测试和启用；需要账号的 HTTP 连接选择 Browser sign-in 登录，本地 stdio 与远程 Streamable HTTP 的配置和示例见 [MCP 参考](docs/reference/mcp.md)。
