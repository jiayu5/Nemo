# TODO

这里只记录“已经决定要做、但尚未进入里程碑”的独立事项。进入里程碑后移除；实施历史写入对应 Milestone。

| 编号 | 事项 | 动机 | 待定决策 |
|---|---|---|---|
| T1 | 从 workspace 向上收集 `AGENTS.md`，按根到 workspace 的顺序注入 | 当前只读 workspace 根，进入仓库子目录后可能漏掉项目规则 | 停在 Git 仓库根（推荐），还是继续到文件系统根 |
| T2 | 将 `web_search` 改为带密钥的搜索 API，Bing HTML 作为可选降级 | Bing 对程序化请求的技术搜索相关性不稳定，HTML 结构也容易变化 | 选择 Tavily/Brave、密钥变量名，以及无密钥时是否自动回落 |

子目录按需加载、`AGENTS.override.md`、`CLAUDE.md` 回退和 `CLAUDE.local.md` 等价物目前没有决定实施，不进入本表。
