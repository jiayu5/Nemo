# M5 · React UI

| 项 | 内容 |
|---|---|
| 状态 | 进行中：M5a、M5b 已完成；M5c 未开始 |
| 完成日期 | M5a/M5b：2026-09-21 |
| 阶段 | Phase 1 |
| 代码范围 | `server/`、`adapters/persistence/`、`apps/ui/` |

## 1. 目标与范围

- **M5a · UI API**：补齐消息、Run、Trace、模型目录、Provider 安全状态、会话模型切换和显式连接测试。
- **M5b · React UI**：在浏览器完成 Session/Run、审批、停止、模型与审批选择、事件和 Trace 主链。
- **M5c · Provider Settings**：安全编辑配置与密钥；尚未开始。

当前不包含 token delta、语法高亮、Tauri、多窗口或远程认证。

## 2. 交付物

- Server 查询接口和响应 schema；Run 持久化实际模型身份。
- Provider 脱敏视图与显式连接测试。
- 旧 SQLite 数据库的追加式向前迁移。
- React + TypeScript + Vite 单页客户端。
- Session 列表、Conversation、Run/Trace、审批卡片和 Provider 状态。
- 安全 Markdown/GFM 渲染、响应式布局和 Nemo 视觉系统。
- 输入器内的 Model/Approval、Enter 发送、Shift+Enter 换行和输入法保护。

现行接口见 [Server API](../reference/server-api.md)，UI/M5c 边界见 [设计文档](../design/ui-and-api.md)。

## 3. 关键决策

- UI 只调用 Server，不读取 SQLite、配置文件或密钥。
- 开发态通过 Vite `/api` proxy 保持同源，不放宽 Server CORS。
- 使用原生 `fetch` 和 `EventSource`，避免当前规模下不必要的状态框架。
- Provider 只返回安全元数据；连接测试由用户显式触发。
- Trace 耗时由事件时间戳派生，不重复存储可计算字段。
- SQLite 迁移只追加可空列，保留旧数据。
- Markdown 不执行原始 HTML；字体和图形不依赖远程资源。
- 输入器沿用成熟的键盘和布局习惯，但保留 Nemo 自己的深海蓝绿视觉语言。

## 4. 实施摘要

M5a 先固定 UI 所需 API 和脱敏边界，再补 Repository 查询、Trace 组装、模型目录与连接测试。M5b 建立三栏页面并完成真实 Server 联调；长历史测试后修复视口溢出，随后完成视觉重构、Markdown、字体、输入器键盘行为和 Model/Approval 迁移。2026-09-22 将页面状态与 Server 生命周期收敛到 `useNemoWorkspace`，并把 Session、Conversation、Composer、Trace 拆为独立组件；视觉和 API 行为不变。

## 5. 验证

```bash
conda run -n nemo python -m unittest discover -s tests -v
npm --prefix apps/ui test -- --run
npm --prefix apps/ui run typecheck
npm --prefix apps/ui run build
npm --prefix apps/ui audit --audit-level=moderate
```

M5a 完成时 Python 全套 213 个测试通过。M5b 最终有 2 个组件测试通过，类型检查和生产构建成功，npm audit 为 0；真实本地 Server 联调验证了 Session、Messages、Runs、Models、Providers、SSE 和 Trace。

## 6. 遗留与下一步

- M5c 需要校验预览、原子写入、密钥不回显、`.env` 600 权限和配置生效策略。
- “测试连接”需要明确的外部请求/可能计费提示。
- 模型仍返回完整响应；UI 消费的是领域事件 SSE，不是 token delta。
- Tauri sidecar、短期访问凭据、Origin 检查和桌面打包归 M6。
