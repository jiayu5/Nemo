# Skills 参考

## 目录与格式

用户级技能放在 `~/.nemo/skills/<directory>/SKILL.md`，项目级技能放在 `<workspace>/.nemo/skills/<directory>/SKILL.md`。只扫描直接子目录，不继承父目录技能，不支持符号链接。没有用户或项目技能目录是正常情况。应用还随包分发内置技能，当前提供 `builtin:skill-creator`、`builtin:skill-installer`；无需在用户目录复制或安装。

```markdown
---
name: review-python
description: 检查 Python 改动的错误处理、取消传播和验证覆盖。
---

先阅读改动和领域契约，再检查取消传播，最后运行适当的测试。
需要时读取 references/checklist.md。
```

front matter 是受限格式，只读取单行 `name`、`description`，可使用配对单/双引号；不解析完整 YAML、转义或多行值。名称使用小写英文、数字及连字符，最多 64 字符；描述非空且最多 500 字符；正文非空且最多 16000 字符。UTF-8 文本文件最多 128 KiB。其他宿主使用的字段（如 `license`、`allowed-tools`、`metadata`）被忽略，不影响发现与读取，字段名在导入预览和 Installer 输出中列出；`name`/`description` 缺失、重复或写成多行，以及非法编码与同名冲突，均明确记录诊断。

技能标识为 `builtin:<name>`、`user:<name>`、`project:<name>`。不同范围同名同时保留；同范围重名全部拒绝。目录最多检查 200 个候选，序列化摘要最多 16000 字符；超过限制会阻止此次加载，不静默省略技能。普通目录条目也计入候选数。

## 加载时机

新建 Session 时扫描内置、用户和项目技能目录，将名称、描述与读取标识放入稳定头部。Server 中连续 Run 复用内存目录，正文与资源按需读取。目录不保存到数据库或 transcript。

React UI/桌面端启动加载历史会话、手动选择会话，以及 CLI `--session` 恢复时，调用 `POST /sessions/{session_id}/resume` 重新扫描。普通消息、列表和 Trace 查询不刷新目录。Server 重启后直接提交 Run，也会在首次使用 Session 时重新扫描。两个 Server 的目录缓存各自独立。

恢复入口遇到活动 Run 时返回会话元数据，不切换该 Run 的目录；Run 收敛后再次打开会话才能刷新。恢复过程只刷新技能，不改写已有 AGENTS 快照或会话历史。

目录冻结同时记录 `SKILL.md` 摘要。磁盘正文改变或丢失时，读取明确失败，需重新打开会话。重新打开后以新目录和正文为准；历史中的旧工具结果保留，不自动重放旧工作流程。

## 模型工具

| 工具 | 参数 | 行为 |
|---|---|---|
| `list_skills` | `offset=0`、`limit=10`（1–20） | 分页读取当前目录与诊断，不扫描磁盘；两个集合分别按相同 offset/limit 分页 |
| `read_skill` | `skill_id`、`offset=0`、`limit=4000`（1–8000） | 检查正文版本，分页返回正文、摘要、可读资源相对路径和 `next_offset`；读完全部页面后使用工作流程 |
| `read_skill_resource` | `skill_id`、`path`、`offset=0`、`limit=4000`（1–8000） | 读取已加载技能的文本资源；offset/limit 按字符计数，返回 `next_offset` |

附属资源需先通过 `read_skill` 建立清单和版本，每个技能最多检查 100 个文件/目录条目；二进制、超大、不可读资源与符号链接不进入可读清单。资源修改后必须重新读取技能。资源路径必须位于所属技能目录内，拒绝绝对路径及 `..`。

所有工具遵守 ExecutionContext 的 workspace、超时和输出限制，默认输出上限 8000 字符，Registry 另检查完整结果大小。正文分页读取，使用 next_offset 续读至 null；每页重新检查正文版本。单页正文及资源列表超过输出上限仍明确失败，可缩小 limit；不静默截断工作流程。长材料仍宜移到附属资源按段读取。

模型根据目录描述选择技能；用户明确点名时提示条款要求读取。匹配质量取决于模型与描述，不是确定性路由。目录只提供摘要，正文及资源通过普通工具历史参与 M7 压缩，仍可重读。

## 管理界面与导入

顶栏 **Skills** 展示磁盘上当前内置、用户级和当前 Session 项目级技能，可查看描述、范围与正文。未选 Session 时仍能管理用户技能。其他项目没有全局目录登记，不扫描所有历史工作区。发现诊断单独显示；无效技能不作为可用条目列出。

面板每次打开自动扫描磁盘，不提供手动 Refresh 或单文件导入。技能以两列紧凑条目展示，窄屏改为单列；点击条目查看详情。范围与项目完整路径收进条目悬停提示。描述使用较小的灰色次要文字，单行省略；悬停或打开详情可查看全文。可以在对话中要求创建技能，或明确点名 `$skill-creator`。模型按既有提示条款读取 `builtin:skill-creator`，再用现有文件工具创建项目级完整目录；触发由模型判断，不增加确定性命令路由。

现有单文件预检和导入 API 保留兼容；前端不再调用。

用户范围保存到 `~/.nemo/skills/<name>/SKILL.md`，项目范围保存到当前 Session 的 `<workspace>/.nemo/skills/<name>/SKILL.md`。同范围同名（即使在其他目录）和已有目标目录均拒绝覆盖。导入使用完整文件暂存及排他目标创建；预览和保存都校验格式、字节限额、目录限额、符号链接及凭据模式，含凭据形状的文档拒绝写入。

导入只包含当前 Markdown，不自动复制 references、scripts 或 assets，也不执行文档命令。管理列表读取磁盘，不修改 Session 的内存目录；导入后需要重新打开会话才能使用。

管理 API：

| 方法 | 路径 | 请求 | 返回 |
|---|---|---|---|
| `GET` | `/skills` | 可选 query `session_id` | `entries`（ID、名称、描述、范围）、`diagnostics` |
| `GET` | `/skills/detail` | query `skill_id`、可选 `session_id` | ID、名称、描述、范围和正文 |
| `POST` | `/skills/preview` | JSON `content`、`scope`（默认 `user`）、可选 `session_id` | 校验后的预览，不写文件 |
| `POST` | `/skills/import` | 同预览请求 | `201` 返回导入详情 |

项目范围由 Server 按 Session ID 解析，不接受客户端目标路径。缺失 Session 返回 `404`；格式、冲突或存储限额返回 `409`；请求契约错误返回 `422`；未预期存储异常返回安全 `500`。具体 API 入口另见 [Server API](server-api.md)。

预览与导入结果包含 `ignored_fields`，列出被 Nemo 忽略的其他宿主元数据字段名；字段为空时表示文档只使用 `name`、`description`。

## 内置 Skill Creator

指令文件随 Python 包分发，位于 `src/nemo/prompts/skills/skill-creator/SKILL.md`；Bootstrap 为 Runtime 与管理用例注入同一个内置根，桌面 sidecar 构建收集包数据。Skills 面板显示 Built-in 范围，无 Session 时也可查看。

Creator 复用 Codex 原版完整指令、初始化/校验/UI 元数据生成脚本、参考资料及图标，并保留 Apache 2.0 许可。产品名称及默认路径适配 Nemo，宿主差异单独写在 `references/nemo.md`。它通过对话明确用途和触发条件，用现有工具创建或更新 `<workspace>/.nemo/skills/<name>/SKILL.md`，按需包含 scripts、references、assets，并指导格式、引用及脚本验证。正文按需读取，不提前加入稳定前缀。创建后重新打开会话才能发现新技能；更新也遵守既有版本检查。

内置范围只用于发现与读取，导入 API 不接受 builtin 范围；定制应保存为不同名称的项目技能。不同范围同名不覆盖彼此，通过带范围的 ID 选择。Creator 仍默认创建项目技能；用户级外部目录安装使用下述 Installer 的限定目标发布脚本，不放宽普通文件工具边界。辅助脚本按需读取并保存到工作区后，在安装 Nemo 的 Python 环境运行，使用 Nemo 自身格式解析器；无需新增 PyYAML。agents/openai.yaml 保留兼容格式，但 Nemo 尚不消费其 UI、策略或工具依赖字段。

## 内置 Skill Installer

`builtin:skill-installer` 同样随应用分发并对所有项目可用，包含复用 Codex 的指令、GitHub 列表/下载/安装脚本、图标、agents 元数据和原版许可，以及 Nemo 适配参考。支持查看 openai/skills 的 curated/experimental 列表，或指定 GitHub 仓库与路径安装完整目录，不执行下载的脚本。

辅助脚本按需读取到 workspace 后，通过 run_shell 在安装 Nemo 的 Python 环境运行。安装需显式 `--workspace`，默认 `--scope user` 保存到固定 `~/.nemo/skills`，`--scope project` 保存到 `<workspace>/.nemo/skills`。原版 --dest 只接受对应范围的固定根；NEMO_HOME/CODEX_HOME 不改变目标。该受限脚本负责目录发布，普通文件工具仍拒绝 workspace 越界。

安装前校验 Nemo 元数据、候选与上下文预算、目录资源条目及符号链接；外部技能的额外元数据字段被忽略并在输出中列出，`name`/`description` 的缺失、重复或多行值仍明确拒绝，不自动改写正文。复制完整目录，正文最后完整发布，不覆盖已有目标或同范围同名技能。多路径安装逐项完成，后续失败不撤销之前成功项。安装后重新打开会话才能使用。

此能力不包括自动迁移所有外部格式、用户级原地编辑或覆盖更新；私有仓库认证复用环境/既有 Git 凭据，不记录 token。网络、文件发布沿用 shell 审批与超时，workspace 和审批均不是操作系统沙箱。

## 权限与执行

技能加载是只读操作，不授予新工具或审批许可，不覆盖用户当前要求和代码边界。附属脚本不会自动运行；项目内脚本需要显式调用既有 `run_shell`，用户级目录不提供专用脚本执行机制。普通 `read_file` 的 workspace 边界不变。

技能内容可能进入远程模型上下文；正文、描述与资源沿用既有凭据模式脱敏。审批与 workspace 都不是操作系统沙箱。实现边界及验收见 [Skills 设计](../design/skills.md)。
