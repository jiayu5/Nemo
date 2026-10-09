# M10 · Skills

| 项 | 内容 |
|---|---|
| 状态 | 已完成 |
| 完成日期 | 2026-10-09 |
| 阶段 | Phase 3 |
| 代码范围 | Core Skills 契约、文件适配器、读取工具、Context、Server/CLI、UI 恢复入口与技能管理 |

## 1. 目标与范围

为单 Agent 提供本地技能发现和按需工作流程加载。当前使用期间冻结目录，重新打开和重启时重新扫描；不持久化技能目录、不修改数据库 schema，不引入额外依赖。

## 2. 交付物

- 用户/项目技能目录、受限 front matter、名称冲突和限额诊断。
- 提前进入稳定头部的名称、描述与读取标识。
- `list_skills`、`read_skill`、`read_skill_resource` 三个只读工具及版本检查。
- Server 内存缓存、显式恢复 API、CLI 和 UI/桌面客户端恢复装配。
- 同日追加 Skills 管理面板及本地 Markdown 导入预览/保存，支持用户与当前项目范围。
- [Skills 设计](../design/skills.md)、[Skills 参考](../reference/skills.md)和更新的 [Server API](../reference/server-api.md)。

## 3. 关键决策

目录摘要提前提供给模型，避免先判断需要技能才能发现技能；正文与资源按需进入普通历史。冻结只覆盖本次使用期间，重新打开会话允许目录和前缀变化。文件变化以版本检查明确失败，历史结果不改写。技能不授予权限，脚本仍需显式调用既有工具及审批。

## 4. 实施摘要

先建立只读端口与文件适配器，再接入工具、具名提示条款和 Context 目录区块。Server 在创建或首次使用时缓存目录，恢复入口刷新闲置会话；活动 Run 保持原目录。CLI 和 React UI/桌面端接入恢复请求。离线集成测试覆盖连续 Run、Server 重启、transcript 恢复及 M7 压缩后重读。随后增加管理用例、文档预检与排他保存，并在独立测试存储下通过浏览器验收文件选择、预览、导入与列表更新。

后续界面调整：简化范围说明，将项目路径收进悬停提示，移除单文件导入入口；既有导入 API 保留兼容，后续对话创建由内置 Creator 提供。

内置 Creator 追加：随应用分发 skill-creator 指令，由 Bootstrap 接入发现与管理读取；支持通过现有工具创建完整项目技能目录，桌面构建收集包数据。

Creator 复用调整：替换精简重写版，直接采用 Codex 完整指令及辅助资源，保留许可；产品名称、默认路径和脚本格式校验适配 Nemo，宿主差异独立说明。read_skill 增加正文分页，保留完整工作流程。

Installer 追加：复用 Codex 的完整指令、脚本和许可，内置发现与管理复用既有链路；限定用户/当前项目根安装完整 GitHub 技能目录，正文最后发布，格式不兼容、重名与越界目标明确拒绝。

元数据兼容调整：加载只读取单行 `name`、`description`，其他宿主的字段（如 `license`、`allowed-tools`、`metadata`）忽略且不复制，字段名在预览与 Installer 输出中列出；缺失、重复或多行值改为指名具体字段的拒绝消息，避免笼统报错。

## 5. 验证

- `conda run -n nemo python -m unittest discover -s tests -q`：286 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `conda run -n nemo python -m nemo.cli --help`：通过。
- `npm --prefix apps/ui test -- --run`：20 项通过。
- `npm --prefix apps/ui run typecheck`：通过。
- `npm --prefix apps/ui run build`：通过。
- `git diff --check`：通过。

管理入口追加验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：295 项通过。
- `npm --prefix apps/ui test -- --run`：25 项通过。
- `conda run -n nemo python -m compileall -q src tests`、CLI `--help`：通过。
- `npm --prefix apps/ui run typecheck`、`npm --prefix apps/ui run build`：通过。
- 浏览器：独立测试存储下文件选择、校验预览、导入和列表更新通过。
- `git diff --check`：通过。

界面调整验证：

- `npm --prefix apps/ui test -- --run`：23 项通过。
- `npm --prefix apps/ui run typecheck`、`npm --prefix apps/ui run build`、`git diff --check`：通过。

内置 Creator 追加验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：298 项通过。
- `npm --prefix apps/ui test -- --run`：24 项通过。
- `npm --prefix apps/ui run build`（含 typecheck）：通过。
- `conda run -n nemo python -m compileall -q src tests apps/ui/build_sidecar.py`、CLI `--help`：通过。
- `conda run -n nemo python -m pip wheel --no-deps --wheel-dir <temporary-directory> .`：构建通过，wheel 包含内置 SKILL.md。
- `git diff --check`：通过。

Creator 复用调整验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：300 项通过。
- `conda run -n nemo python -m compileall -q src tests`、CLI `--help`：通过。
- `conda run -n nemo python src/nemo/prompts/skills/skill-creator/scripts/quick_validate.py src/nemo/prompts/skills/skill-creator`：通过。
- 初始化、UI 元数据生成、占位检查、格式拒绝及完整正文分页：离线测试通过。
- wheel 构建与资源清单校验：10 个 Creator 文件全部包含。
- `git diff --check`：通过。

管理界面精简：取消 Refresh 和逐项查看按钮，改为可点击双列条目；描述淡化并单行省略，范围收进悬停提示。验证：`npm --prefix apps/ui test -- --run`，24 项通过；`npm --prefix apps/ui run build`（含 typecheck）通过。

Installer 追加验证：

- `conda run -n nemo python -m unittest discover -s tests -q`：302 项通过。
- `conda run -n nemo python -m compileall -q src tests`、CLI `--help`：通过。
- Creator 校验器检查内置 Installer：通过。
- wheel 资源清单：Creator 与 Installer 共 19 个文件全部包含。
- `git diff --check`：通过。

提交前完整验证：

- `conda run -n nemo python -m unittest discover -s tests`：304 项通过。
- `conda run -n nemo python -m compileall -q src tests`、CLI `--help`：通过。
- `npm --prefix apps/ui test -- --run`：28 项通过。
- `npm --prefix apps/ui run typecheck`、`npm --prefix apps/ui run build`：通过。
- 真实网络安装未修改的上游 `anthropics/skills` 技能到临时目录：成功，`license` 列入被忽略字段。
- `git diff --check`：通过。

## 6. 遗留与下一步

真实模型触发、描述适配和工作流程遵循质量已由用户在真实会话中验收（2026-10-09），未记录量化指标；自动化测试仍使用 Fake Model，不消耗模型 API。首版不提供通用 YAML、父级技能继承、市场、远程安装、自动脚本执行或目录热更新；其他宿主的元数据字段被忽略并报告，`name`/`description` 仍要求单行。其他运行限制见现行参考。后续 Subagents 可复用技能端口与工具，具体范围仍需单独设计确认。
