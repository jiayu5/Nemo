# M12 · MCP 连接与账号授权

## 1. 目标与范围

2026-10-13 完成 MCP 外部工具连接与浏览器账号授权：本地 stdio、远程 Streamable HTTP、工具发现和调用、审批、子任务白名单、OAuth 登录与凭据复用，以及独立管理弹窗。本记录合并原 M12 连接与原 M13 OAuth 交付，保留各阶段验证结论；后续统一归入 M12。

## 2. 交付物

- Core 连接与 OAuth 配置契约、ToolRegistry 的 JSON Schema 参数适配扩展。
- 官方 Python MCP SDK adapter：连接所有权、结果规范化、脱敏、输出限额、超时与清理。
- Server 与直连 CLI 装配、管理/授权 API、只读与子 Agent 白名单、既有审批与 Trace。
- 浏览器 PKCE 授权、一次性回环回调、用户级凭据存储、刷新、取消与断开。
- 独立管理弹窗：按连接信息、凭据、账号连接、工具权限排列，底部固定保存与测试操作。
- 本地协议与 OAuth demo、共享测试替身、集成与回归测试、依赖锁文件。
- [现行参考](../reference/mcp.md)与[组件设计](../design/mcp.md)。

## 3. 关键决策

协议与 OAuth 基础设施复用官方 SDK 1.x，Runtime 保持自行实现，Core 不依赖 SDK 或 HTTP。Server 每个 Run 连接已启用服务并冻结工具目录；直连 CLI 持有进程期连接。SDK context manager 由专用任务进入和退出，父 Runtime 排空子任务后关闭连接。

服务端只读提示不授予权限，子任务能力另设白名单。手动凭据只通过环境变量引用加载；OAuth 凭据按连接指纹保存在用户目录的 0600 文件，不写入项目、配置或事件。普通 Run 和连接测试只复用授权并静默刷新，不打开浏览器。断开使用代际校验拒绝迟到的凭据写入，只清除本地授权。代理必须显式配置；业务调用失败不自动重试，不改变数据库 schema。

## 4. 实施摘要

接入连接契约、原子配置编辑、SDK 工具 adapter、Server/CLI 装配与管理弹窗，再追加 OAuth 动态注册、预注册客户端支持、回调、凭据存储与刷新协调。覆盖参数/schema、审批、子任务白名单、结果脱敏和限额、取消及资源清理。

GitHub 验收期间修正令牌兑换/刷新请求的 JSON 协商、安全失败提示的传递，以及授权成功后前端异步清理导致工具发现结果丢失的问题。网络检查确认当前环境直连 GitHub 授权主机超时、使用代理可达；未代改用户代理配置或凭据。随后精简字号、说明、操作顺序与滚动布局，账号连接前校验凭据变量名。

## 5. 验证

- 初始连接交付：Python 367 项、前端 41 项通过；初始 OAuth 交付：Python 377 项、前端 42 项通过。
- `conda run -n nemo python -m unittest discover -s tests -q`：收尾 379 项通过。
- `conda run -n nemo python -m compileall -q src tests examples`：通过。
- `conda run -n nemo python -m nemo.cli --help`：通过。
- `npm --prefix apps/ui test -- --run`：收尾 44 项通过。
- `npm --prefix apps/ui run typecheck` 与 `npm --prefix apps/ui run build`：通过。
- `conda run -n nemo python -m pip install -r requirements-lock.txt -e .`：依赖变更时通过。
- `conda run -n nemo python -m pip check`：无依赖冲突。
- `git diff --check`：通过。

真实本地 stdio 与 Streamable HTTP 发现、add(20,22)=42 和连接清理通过；真实 loopback OAuth 授权、回调、凭据保存、免登录工具发现及断开通过。自动化验证动态注册、PKCE、状态校验、刷新与并发协调、取消、代际封锁、脱敏及错误提示；隔离配置下检查窄窗口布局与固定底栏。自动化不调用收费模型或第三方账号业务。

## 6. 遗留

约定的 MCP 连接与浏览器授权功能已交付，本阶段收尾。GitHub 真实账号最终连通尚未验收，不能以本地示例通过替代第三方验证。预注册应用和固定回调地址仍需手动配置；仅支持本机回环回调，未提供设备码、远程回调或服务端授权撤销。

Resources/Prompts 主动目录、sampling、elicitation 与服务市场未实现。未重新打包 Nemo.app。MCP 服务自行定义访问范围，workspace、连接及审批不是操作系统沙箱。
