# TODO

已经决定要做、但还没有里程碑承接的独立事项。

**收录规则**：

- 只放「已决定、有明确动机、但还没排进某个里程碑」的事。里程碑内部的下一步写在对应里程碑记录的 §6，不在这里重复。
- 做完就移除条目，并把过程与验证写进相应的里程碑记录——TODO 不是历史记录。
- 不写猜测性想法。一件事如果没有决定要做，它属于架构文档的讨论，不属于这里。

| 编号 | 事项 | 决定日期 | 动机 | 待定 |
|---|---|---|---|---|
| T1 | 项目说明向上查找：从 workspace 往上收集 `AGENTS.md`，按「根 → workspace」顺序注入，越接近 workspace 越靠后 | 2026-09-21 | 人常在仓库子目录里工作，现在只读 workspace 根一层就找不到仓库根的约定；Codex 会把根到 CWD 的链条都带上，Claude Code 也加载工作目录以上的层级 | 走到**仓库根**（用 `.git` 之类的 project root marker 判断，推荐）还是**文件系统根**（Codex 行为，代价是可能把 `~/AGENTS.md` 也吃进来） |
| T2 | `web_search` 换成带密钥的搜索 API（Tavily / Brave 等），Bing HTML 降级为可选后端 | 2026-09-21 | 实测 Bing 对 Python 客户端返回降级结果集：搜技术细节时相关性不可靠（搜 `python asyncio` 给的是 python.org 首页而不是 asyncio 文档页），而 `curl -L` 拿得到好结果。详见 [web-tools 设计](design/web-tools.md) §5 | 选哪家（Tavily 免费层对 Agent 场景更合适）、密钥放 `~/.nemo/.env` 的哪个变量名、是否需要“无密钥时自动回落到 Bing” |

**相关但不在本表的事**（已记录为刻意不做，见 [M4 记录](milestones/M4-context-cli-server.md) 与 [上下文组装设计](design/context-assembly.md) 的非目标）：子目录按需加载、`AGENTS.override.md`、`CLAUDE.md` 回退文件名、`CLAUDE.local.md` 等价物。
