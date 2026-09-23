# M6 · macOS App

| 项 | 内容 |
|---|---|
| 状态 | 已完成 |
| 完成日期 | 2026-09-23 |
| 阶段 | Phase 1 |
| 代码范围 | `apps/ui/src-tauri/`、`apps/ui/build_sidecar.py`、`apps/ui/src/`、`server/` |

## 1. 目标与范围

- **M6a · 桌面壳**：Tauri 承载现有 React UI，Rust 只管理窗口、Python sidecar 的启动/退出和一次启动内的连接凭据。
- **M6b · 本地访问保护**：随机 loopback 端口、每启动随机令牌、Origin 检查与 CORS 预检。
- **M6c · 打包**：PyInstaller 单文件 Server 经 Tauri `externalBin` 放入 `Nemo.app`，用户不需要预装 Python。

当前不包含签名与公证、Intel 架构产物、安装包分发渠道、多窗口和远程访问。

## 2. 交付物

- `apps/ui/src-tauri/`：`nemo-desktop` Rust 应用、capability、CSP、图标和构建配置。
- `apps/ui/build_sidecar.py`：按宿主架构在 Conda `nemo` 环境构建单文件 Server 二进制。
- `nemo.server.desktop`：读取令牌、绑定 `127.0.0.1:0`、输出 `NEMO_READY:<port>`、监测父进程。
- `nemo.server.desktop_access`：`X-Nemo-Token` 校验、Origin 白名单和预检响应。
- `create_app(desktop_token=...)`：只在桌面 sidecar 上安装访问保护，普通 Server 行为不变。
- React 桌面传输：`desktop_connection` 命令注入端口与令牌，HTTP 与 SSE 都携带令牌。
- 桌面默认工作目录：新 Session 使用桌面壳上报的用户主目录。
- npm 脚本 `desktop:sidecar`、`desktop:dev`、`desktop:build`。

现行访问条件见 [Server API](../reference/server-api.md)，设计取舍见 [macOS App 设计](../design/macos-app.md)。

## 3. 关键决策

- 桌面壳不复制 Agent Loop、模型、工具或配置逻辑，只做进程与凭据管理。
- 令牌由 Rust 生成并经子进程环境传递；Python 启动后立即从进程环境移除，不落盘、不进日志。
- sidecar 绑定系统分配的空闲端口，避免与手动启动的 `NEMO_SERVER_PORT` Server 冲突；桌面端首版使用独立的 `~/.nemo/desktop.db`，不接管或结束用户手动启动的 Server。
- 桌面 Shell 不能依赖 WebView 的同源策略，因此令牌是真正的访问凭据，Origin 检查只是浏览器层的附加限制；文档明确它不构成操作系统沙箱。
- 桌面 SSE 改用带令牌的 `fetch` 流并按事件序号续读；浏览器开发模式保留原生 `EventSource` 与 `/api` 代理。
- 打包后 sidecar 的工作目录没有用户含义，默认 workspace 由桌面壳上报主目录，避免新 Session 落在 `/`。
- Python 每 0.5 秒检查父进程，App 意外退出时主动关闭 Server；未完成 Run 仍按既有启动恢复逻辑标记为 `interrupted`。

## 4. 实施摘要

先扩展 `create_app` 只接受可选 `desktop_token`，把令牌校验、Origin 白名单与预检收敛到一个中间件，普通 Server 与浏览器开发模式保持原有行为。随后加入 `nemo.server.desktop`：弹出令牌、绑定随机 loopback 端口、用 `NEMO_READY:<port>` 通知父进程，并监测父进程存活。

Rust 侧建立最小 Tauri 应用：setup 阶段生成令牌、拉起步 sidecar、解析就绪端口，`desktop_connection` 命令在端口就绪后把端口、令牌和桌面默认主目录交给窗口，`RunEvent::Exit` 时结束 sidecar。capability 只允许 `main` 窗口调用该命令，CSP 只放开 `127.0.0.1`。

React 侧把请求地址与请求头收敛到 `endpoint()`，桌面模式经 `invoke` 取一次连接信息后复用；SSE 在桌面模式改为鉴权流解析，处理跨网络片段的分帧、终态退出与重连。最后用 `build_sidecar.py` 与 `externalBin` 打通 `.app` 打包。

## 5. 验证

```bash
conda run -n nemo python -m unittest discover -s tests -v
conda run -n nemo python -m compileall -q src tests
npm --prefix apps/ui test -- --run
npm --prefix apps/ui run typecheck
npm --prefix apps/ui run build
PATH="$HOME/.cargo/bin:$PATH" npm --prefix apps/ui run desktop:build
```

Python 全套 237 个测试、React 14 个测试通过，类型检查与生产构建成功。`desktop:build` 依次完成 PyInstaller 单文件、Vite 生产构建、`cargo` release 编译和 `Nemo.app` 打包。打包后的 sidecar 实测：无令牌 `401`、错误令牌 `401`、非白名单 Origin `403`、正确令牌 `200`，退出时随父进程结束。

## 6. 遗留与下一步

- 构建产物未签名、未公证，不作为公开发布包；分发方式与更新机制未定。
- 当前验证范围是 Apple Silicon；Intel Mac 需在 `x86_64-apple-darwin` 目标下重新构建。
- 桌面 sidecar 与 CLI Server 使用不同数据库，会话历史暂不互通，需要后续设计单实例或跨进程所有权。
- 尚未用真实 Provider 在打包应用内完成一轮端到端试用。
