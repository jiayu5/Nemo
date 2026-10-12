# Nemo 适配说明

此技能复用本机 Codex 内置 skill-creator 的完整指令、脚本、参考资料和图标；已替换产品名称和默认创建路径。许可见 ../license.txt。以下说明优先于原版中的宿主专属行为。

## 加载与工具

使用 read_skill 读取正文，按 next_offset 继续直至读完；附属资料和脚本通过 read_skill_resource 按需读取。内置目录只读，脚本不自动执行。

需要运行辅助脚本时，先通过 read_skill_resource 读取对应脚本，使用 write_file 将它和所需同目录模块保存到当前 workspace 的 `.nemo/skill-creator-tools/`，然后使用 run_shell 在安装 Nemo 的 Python 环境运行。例如初始化需 init_skill.py 与 generate_openai_yaml.py；生成 UI 元数据和校验不需要其他同目录模块。已有文件先检查，不覆盖用户内容。不通过 shell 访问 workspace 外目录。

默认目标为 `.nemo/skills/<name>/`。用户指定路径须仍位于 workspace；内置技能不能原地更新。用户级完整目录安装使用内置 skill-installer 的限定目标发布脚本；Creator 自身仍默认创建项目技能。沿用现有审批和工具权限。

## 格式与兼容性

Nemo front matter 仅读取单行 name、description，可使用配对引号，不支持多行值或转义。其他宿主的字段（如 metadata、license、allowed-tools）会被忽略且不生效；保留它们的指令仅适用于支持这些字段的环境，Nemo 技能不要依赖这些字段，需要时在正文说明。name 最多 64 字符且使用小写字母、数字和连字符；description 非空且最多 500 字符；正文非空且最多 16000 字符。UTF-8 文件最多 128 KiB，每技能最多检查 100 个文件和目录条目。不支持符号链接。

agents/openai.yaml 的名称及格式保留原版兼容性；生成器可生成它，但 Nemo 当前不消费 interface、policy 或 dependencies。它不会改变技能选择、工具权限或自动注册 MCP。不要将此文件的策略配置宣称为 Nemo 已生效的能力。

初始化及校验脚本已适配 Nemo 受限格式，使用已安装的 Nemo 解析器，无需 PyYAML。校验还检查未完成占位内容、目录资源及链接；新增业务脚本仍需实际测试。脚本脚手架的 TODO 是待完成内容，不能作为最终交付。

Nemo 已提供 spawn_subagents、get_subagents、wait_subagents、cancel_subagent、read_subagent_report 和 resume_subagent，可在复杂度或风险值得独立检查、且已获授权时进行只读验证。子 Agent 不能写文件、执行 Shell 或递归分派；需要生成验证产物或运行脚本时由父 Agent 完成，再把任务及必要路径明确交给子 Agent 检查。最终报告只列实际完成的验证。

## 会话生效

Session 使用期间目录冻结，list_skills 不重扫磁盘。创建或更新后重新打开会话才加载新目录或版本。完成时报告生成位置、实际验证及未验证项。
