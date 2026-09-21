# M5 · React UI

| 项 | 内容 |
|---|---|
| 编号 | M5 |
| 状态 | 进行中（M5a、M5b 已完成；M5c 未开始） |
| 完成日期 | M5a、M5b：2026-09-21 |
| 对应阶段 | Phase 1 · Core |
| 代码范围 | `src/nemo/server/`、`src/nemo/adapters/sqlite.py`、`apps/ui/` |

## 1. 目标与范围

### M5a · UI API Contract

**目标**：把 M4c 的执行后端补成 React UI 可以完整读取的应用后端，同时不让 UI 直接读取 SQLite、模型配置文件或密钥。

**做**：会话消息与 Run 列表、Run Trace、模型选择目录、Provider 安全元数据、会话模型切换、用户显式触发的 Provider 连接测试、实际模型身份持久化与旧数据库向前迁移。

**不做**：React/Vite 工程、配置和密钥写入、token 增量流式、远程访问认证。

### M5b · React UI 最小闭环

**目标**：在浏览器中完成与 CLI 相同的本地 Session/Run 闭环，并把执行事件、审批和 Trace 变成可视界面。

**做**：三栏布局、Session 创建与恢复、消息历史、提交/停止 Run、SSE 事件、审批回传、Run/Trace、模型与审批模式切换、Provider 配置状态、响应式布局。

**不做**：Provider/密钥写入、token 增量流式、Tauri 打包、富文本 Markdown/代码高亮、多窗口。

> 后续视觉改版已引入安全 Markdown 排版，但仍不执行原始 HTML，也不做语法高亮；它取代上面的初版纯文本范围。

## 2. 交付物

| 文件 | 作用 |
|---|---|
| `docs/design/ui-and-api.md` | M5 信息架构、API 边界、验收标准与非目标 |
| `src/nemo/server/app.py` | 新增 Messages、Runs、Trace、Models、Providers 与模型切换接口 |
| `src/nemo/server/schemas.py` | 新增目录、连接测试与 Trace 响应契约 |
| `src/nemo/server/service.py` | 配置目录解析、连接测试、Trace 组装与实际模型记录 |
| `src/nemo/adapters/sqlite.py` | Run 模型身份迁移、Run/Step/Tool Call 查询 |
| `tests/test_server.py` | API、迁移、脱敏、Trace 与连接测试覆盖 |

### M5b · React UI 最小闭环

| 文件 | 作用 |
|---|---|
| `apps/ui/src/App.tsx` | Session、对话、Run/Trace、审批与状态栏的一页式交互 |
| `apps/ui/src/api.ts` | HTTP 请求、错误归一化与 SSE 订阅 |
| `apps/ui/src/types.ts` | 与 Server 响应对应的 TypeScript 类型 |
| `apps/ui/src/styles.css` | 三栏桌面布局与窄屏降级 |
| `apps/ui/src/theme.css` | “潜航控制台”视觉系统、响应式布局、Markdown 排版与动效降级 |
| `apps/ui/vite.config.ts` | React、Vitest 与 `/api` 开发代理 |
| `apps/ui/src/App.test.tsx` | Server 状态、模型/Provider 与空会话入口的组件测试 |
| `apps/ui/package.json`、`package-lock.json` | 可复现的前端依赖与验证脚本 |

## 3. 关键决策与理由

| 决策 | 理由 | 放弃的替代方案 |
|---|---|---|
| UI 只调用 Server | 避免浏览器绑定 SQLite schema 或本地文件路径 | UI 直接读数据库：客户端与内部表结构强耦合 |
| Provider 只返回安全元数据 | header、URL userinfo/query 都可能藏凭据 | 原样返回配置：设置页可能把凭据写进日志或开发者工具 |
| 目录展示 profile、alias、model | 这些才是用户实际可选择的名称 | 只列底层模型：profile 参数预设在 UI 中消失 |
| 连接测试发送最小真实请求 | 只有实际走完密钥、传输和协议才能证明配置可用 | 只检查配置语法：无法发现 401、网络或协议错误 |
| 连接测试必须显式触发 | 它可能计费，页面加载不应产生模型调用 | 自动健康检查：无意中产生费用和外部流量 |
| 耗时由事件时间戳派生 | 已有数据足够，不为可计算字段扩 schema | 给每种 span 增加 duration 列：产生重复状态 |
| SQLite 迁移只追加可空列 | 保留现有用户数据，旧 Run 自然显示未知模型 | 重建 runs 表：风险和停机复杂度不成比例 |

### M5b · React UI 最小闭环

| 决策 | 理由 | 放弃的替代方案 |
|---|---|---|
| 开发态使用 Vite `/api` proxy | 浏览器与 UI 保持同源，不为开发方便放宽 Server CORS | Server 开 `*` CORS：本地执行 API 可被任意网页调用 |
| 第一版一个页面、一个活动 Session | 先验证完整交互闭环，避免路由和全局状态框架抢占复杂度 | 先搭多页设置系统：执行主链反而最后才验证 |
| 使用原生 `fetch` 与 `EventSource` | API 规模小，平台能力足够，避免引入状态/请求库 | 先上 Redux/React Query：依赖与抽象多于当前状态量 |
| 普通文本渲染模型输出 | React 默认转义，安全边界清楚 | 直接渲染 Markdown/HTML：需要单独处理链接、HTML 与代码安全 |
| UI 不自动调用 Provider test | 真实请求可能计费，必须由用户显式触发 | 页面加载即测试：产生隐蔽费用和外部流量 |
| 不加载远程字体或图片 | 本地 UI 启动不应额外访问第三方 | 使用在线字体 CDN：离线和隐私表现都变差 |

### M5b · 视觉改版

| 决策 | 理由 | 放弃的替代方案 |
|---|---|---|
| 采用“本地 Agent 潜航控制台”视觉方向 | Nemo 的执行与 Trace 是产品特征，界面应像工作台而不是通用 SaaS 仪表盘 | 暖白背景、紫色渐变和圆角卡片：与大量模板产品同质化 |
| 仅在空状态使用声呐扫描动效 | 把视觉记忆点集中在 Nemo 待命状态，不让装饰干扰长对话 | 每个列表项和面板都做入场动画：分散注意力 |
| 使用 `react-markdown` 安全渲染模型正文 | 真实回复大量使用标题、列表和代码；组件默认不执行原始 HTML | 继续显示 Markdown 源字符：长回答难以阅读；启用原始 HTML：扩大攻击面 |
| 字体和图形全部使用本地能力 | 保持离线可用，不产生第三方字体或图片请求 | 引入在线字体、图标 CDN 或装饰图片 |
| 界面统一使用系统无衬线字体栈 | 与 Codex 桌面端的阅读体验一致，并避免本机 Avenir/衬线字体混排产生字形跳变 | 为品牌、标题和正文分别指定字体：中文回退结果不一致 |
| 输入器采用单体深色面板 | 输入、辅助动作和发送键形成一个清晰控制面，减少与正文区的视觉混淆 | 输入框和发送按钮分离：操作关系弱且占用更多横向空间 |
| `Enter` 发送、`Shift+Enter` 换行 | 与 Codex 和主流 Agent 对话习惯一致；输入法组合态明确禁止误发送 | 只允许点击发送：键盘工作流中断 |

## 4. 实施过程

1. 先把 UI 所需接口和安全边界写入 `ui-and-api.md`，明确本步不创建前端目录。
2. 为现有 `runs` 表追加五个可空模型身份列；启动 Repository 时检查并迁移旧库。
3. 增加消息、Run、Step、Tool Call 查询，并由 Service 把事件配对成模型调用 span。
4. 从同一份 `AppConfig` 生成可选模型和 Provider 视图；Provider URL 去掉 userinfo、query 与 fragment，header 完全不返回。
5. 会话创建、PATCH 与专用 PUT 都校验模型选择，避免无效选择拖到 Run 执行时才失败。
6. Provider 连接测试复用 `ModelClient` 发一条无工具的最小请求，只返回解析身份和延迟，不返回正文。
7. OpenAPI 路径、旧库迁移、模型身份、消息/Run 恢复、Trace 耗时和凭据不泄露均加入离线测试。

### M5b · React UI 最小闭环

1. 先在根 `AGENTS.md` 登记 `apps/ui/` 的用途、代理边界和构建产物规则，再创建目录。
2. 用 `types.ts` 与 `api.ts` 固定 Server 契约；SSE 对每个领域事件注册监听，终态后主动关闭连接并刷新持久化历史。
3. `App.tsx` 实现左侧 Session、中间 Conversation、右侧 Run/Trace 和底部会话设置；审批作为对话中的显式卡片。
4. 初版布局在长历史下把整个页面撑出视口；真实浏览器截图暴露后，把应用固定为三行网格并让消息/事件各自在面板内滚动。
5. npm 首次审计发现旧 Vitest 传递依赖的两个中危路径遍历告警；兼容升级到 Vitest 5.0.1 后重新审计为 0。
6. 使用真实本地 Server 做只读浏览器联调，成功恢复已有 Session 的 12 条消息、3 个 Run、Trace、模型目录与 Provider 状态；未创建 Run、未修改 workspace。
7. 视觉改版以深海墨色任务舱、海雾对话区、执行轨迹仪表和声呐空状态建立 Nemo 自己的产品语言；真实历史消息检查后补上安全 Markdown 渲染。
8. 根据真实界面反馈统一为系统字体栈，把输入器重构为 Codex 风格的深色圆角面板；增加回车发送、Shift+回车换行和输入法组合态保护，并把 Markdown 表格的横向滚动限制在表格内部。
9. 输入器只沿用 Codex 的结构与键盘习惯，配色回归 Nemo 的深海蓝绿、运行青和信号橙；桌面三栏实图复查确认它与导航和 Trace 面板保持同一视觉语言。

## 5. 验证证据

```bash
conda run -n nemo python -m compileall -q src tests
conda run -n nemo python -m unittest discover -s tests -v
```

```text
Ran 213 tests in 3.924s

OK
```

其中 `tests/test_server.py` 的 8 个集成测试覆盖 M4c 与 M5a；Provider 连接测试使用 Fake Model，不访问网络或真实密钥。

### M5b · React UI 最小闭环

```bash
npm --prefix apps/ui run typecheck
npm --prefix apps/ui test -- --run
npm --prefix apps/ui run build
npm --prefix apps/ui audit --audit-level=moderate
```

```text
Test Files  1 passed (1)
Tests       1 passed (1)
vite v7.3.6 ... built in 356ms
found 0 vulnerabilities
```

真实浏览器联调中，Vite `/api` 代理对 `/health`、`/sessions`、`/models`、`/providers`、`/messages`、`/runs` 与 `/trace` 全部返回 200；三栏固定布局和底部状态栏经截图检查。

视觉改版再次使用真实会话截图检查窄屏降级、长消息滚动与 Markdown 标题/列表排版；复验结果为 1 个组件测试通过、Vite 7.3.6 在 515ms 内完成构建、npm audit 为 0。

字体、输入器与键盘交互调整后再次截图检查；最终复验为 2 个组件测试通过（覆盖 Enter、Shift+Enter 和输入法组合态）、Vite 7.3.6 在 500ms 内完成构建、npm audit 为 0。

## 6. 遗留与下一步

- React UI 第一版已经消费领域事件 SSE，不等待 token streaming。
- 浏览器开发态优先使用 Vite proxy 保持同源；生产态的 Origin 检查与短期访问凭据在 M6 Tauri sidecar 一并落地。
- M5c 再设计 `config.toml` 与 `.env` 的原子、安全写入，不在 M5a 暴露写接口。
- M5c 还需要给“测试连接”按钮增加明确的计费/外部请求提示，并实现配置校验预览后再保存。
