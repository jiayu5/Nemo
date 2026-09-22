# React UI 与 Provider Settings 设计

> 状态：M5a 查询 API、M5b React UI 已实现；M5c Provider Settings 尚未设计完成。

## 1. 目标与边界

React UI 通过 Nemo Server 完成 Session/Run 主链，并把审批和 Trace 变成可视交互。UI 不直接读取 SQLite、`config.toml` 或 `.env`，也不持有 Provider 密钥。

## 2. 当前页面

桌面布局包含三个主要区域：

- Session 导航：创建、选择和恢复会话；
- Conversation：Markdown 消息、输入器、模型与审批选择、发送和停止；
- Run/Trace：事件、步骤、模型调用、工具调用和 Provider 状态。

窄屏下布局降级为单列。输入器支持 `Enter` 发送、`Shift+Enter` 换行，并在输入法组合态禁止误发送。Markdown 使用 `react-markdown` 与 GFM，不执行原始 HTML。

`App.tsx` 只组合页面；`hooks/useNemoWorkspace.ts` 管理 Session、Run、SSE 和查询状态；`components/` 分别保存 Session 导航、Conversation、Composer 与 Trace 视图。API 协议、事件类型和数据类型继续集中维护，避免组件自行发请求。

## 3. API 边界

M5a 增加消息、Run 历史、Trace、模型目录、Provider 安全元数据、会话模型切换和显式连接测试。完整路径与 schema 统一维护在 [Server API](../reference/server-api.md)。

客户端只使用原生 `fetch` 与 `EventSource`；开发态通过 Vite `/api` proxy 保持同源，不为方便而给 Server 开放通配 CORS。

Provider 页面只看到：协议、脱敏 URL、密钥变量名、是否已配置、超时和关联模型。连接测试必须由用户显式触发，因为它会产生真实外部请求并可能计费。

## 4. M5c 设计约束

Provider Settings 需要同时处理 `config.toml` 与 `.env`，实现前必须先确定独立写入协议：

- 先校验预览，再显式保存；
- 配置原子写入，失败时保留旧文件；
- `.env` 权限固定为 `600`；
- 已有密钥永不回显，只允许“保持”“替换”或“删除”；
- 明确区分进程环境密钥与文件密钥，不能伪装覆盖环境变量；
- 保存后刷新 Server 配置目录，并明确对活动 Run 的生效时点；
- 连接测试显示外部请求/计费提示；
- API、日志、错误、事件和前端状态均不保存密钥值。

M5c 尚未实现，因此当前 UI 只显示 Provider 状态，配置修改仍按 [配置参考](../reference/configuration.md) 手工完成。

## 5. 关键决策

| 决策 | 理由 |
|---|---|
| UI 只调用 Server | 避免绑定数据库 schema 和本地文件权限 |
| 原生 fetch/EventSource | 当前状态量小，无需引入额外请求框架 |
| 模型与审批放入输入器 | 它们直接影响下一次提交 |
| 安全 Markdown，不执行 HTML | 提升可读性同时保持清晰的内容边界 |
| 不自动测试 Provider | 避免隐式费用和外部流量 |
| 使用本地系统字体和资源 | 离线可用，不产生第三方字体请求 |

## 6. 验收标准

当前 M5b：创建/恢复 Session、提交/停止 Run、审批、模型切换、SSE、Trace 和响应式布局均可在浏览器完成。

M5c：新增 Provider → 设置密钥 → 校验预览 → 原子保存 → 显式测试连接 → 选择模型 → 发起 Run；全过程不回显或持久化密钥到错误位置。

## 7. 非目标

当前不做 token delta、语法高亮、多窗口、远程部署认证或 Tauri 打包。桌面进程与本地 API 防护属于 M6。
