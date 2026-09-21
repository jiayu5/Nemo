# M3 · 本地执行

| 项 | 内容 |
|---|---|
| 编号 | M3 |
| 状态 | 已完成（M3a 五个本地工具 + M3b Web 工具） |
| 完成日期 | 2026-09-17 |
| 对应阶段 | Phase 1 · Core |
| 代码范围 | `src/nemo/core/contracts/tools.py`、`src/nemo/core/tools/`、`src/nemo/adapters/tools/`、`src/nemo/core/context/` |

## 1. 目标与范围

**目标**：让 Agent 真的能干活。M2 结束时它能对话，但手上没有任何能力；M3 给它读写文件与执行命令的手段，同时把「它能碰什么」变成显式、可测试的边界。

**做**：

- 系统提示：`ContextBuilder` 支持稳定前缀的 system 消息。
- 执行上下文：`ExecutionContext` 统一定义工作目录、输出上限、默认与最大超时。
- Policy 接口：`ToolPolicy.decide()` 在每次工具执行前裁决，工具自报 `read_only` 供其判断。
- 五个工具：`read_file`、`write_file`、`edit_file`、`list_dir`、`run_shell`。
- 路径边界：所有路径参数限制在 workspace 内，含符号链接指向外部的场景。
- 输出控制：截断带显式标记，注册表另设硬上限。
- 进程可靠性：超时与取消终止整个进程组，不留孤儿进程。

**不做**（刻意排除）：

- 沙箱与容器隔离：架构文档排在 Phase 3。当前 workspace 是**边界约定**，不是安全隔离。
- 交互式审批：需要客户端来问，属 M4 CLI。
- 常驻 shell 会话：见 §3 决策表。
- 独立的 `python` 工具：`run_shell` 已能执行脚本，先不增加表面积。
- 命令黑名单：见 §3 决策表。
- 事件内容的脱敏层：`summary` 可能包含命令中的敏感片段，脱敏随 M4 的客户端一起做。

### M3b · Web 工具（2026-09-21 追加）

**目标**：给 Agent 真正的联网能力。此前模型会说「我没有联网能力」——工具清单里确实没有联网工具，系统提示对环境又只字未提，模型只能猜。补两个一等工具比补一句提示实在。

**做**：

- `web_search(query, max_results)`：返回「标题 / URL / 摘要」列表。
- `fetch_url(url, max_chars)`：抓取 http(s) 页面，去标签返回可读正文。
- 两者都 `read_only = True`：审批的语义是「改动前确认」，GET 不改动本机；可见性由 `summary` 保证（每次查询词或 URL 都写进事件）。
- 链接解码：Bing 的 `bing.com/ck/a?...&u=a1<base64>` 还原成真实域名，不把跳转壳递给模型。

**不做**：多后端与运行时切换、JS 渲染页面、登录态与表单、PDF/图片、反爬对抗、伪装 TLS 指纹。理由见 [web-tools 设计](../design/web-tools.md)。

## 2. 交付物

| 文件 | 作用 |
|---|---|
| `src/nemo/core/contracts/tools.py` | `ExecutionContext`、`PolicyDecision`、`ToolPolicy` Protocol |
| `src/nemo/core/contracts/errors.py` | 新增 `ToolFailure`：携带面向模型的安全消息 |
| `src/nemo/core/tools/paths.py` | `resolve_in_workspace()`：解析并拒绝越界路径 |
| `src/nemo/core/tools/limits.py` | `truncate_text()`：head / tail / middle 三种保留策略，必带截断标记 |
| `src/nemo/core/tools/policy.py` | `AllowAllPolicy`（默认）与 `ReadOnlyPolicy` |
| `src/nemo/core/tools/registry.py` | 工具协议扩展（`read_only`、`summarize`、`execute(arguments, context)`）、Policy 调用、结果硬上限、错误分级 |
| `src/nemo/core/context/builder.py` | system 消息支持，恒为消息序列首位 |
| `src/nemo/core/runtime/agent.py` | 工具事件携带 `summary` |
| `src/nemo/adapters/tools/filesystem.py` | `read_file` / `write_file` / `edit_file` / `list_dir` |
| `src/nemo/adapters/tools/shell.py` | `run_shell`：独立进程组、超时与取消清理、输出截断 |
| `examples/local_agent.py` | 真实模型 + 本地工具的端到端入口，支持 `--workspace` 与 `--read-only` |
| `tests/test_tools.py` | 30 个用例，覆盖文件、Shell、Policy、边界与系统提示 |

### M3b · Web 工具

| 文件 | 作用 |
|---|---|
| `src/nemo/adapters/tools/web.py` | `WebSearchTool`、`FetchUrlTool`、`strip_html()`、`parse_results()`、`resolve_result_url()` |
| `src/nemo/bootstrap.py` | `build_local_tools()` 从五个扩到七个 |
| `tests/test_web.py` | 17 个用例：解析、去标签、上限、协议限制、超时与 HTTP 错误、链接解码、只读不询问 |
| `docs/design/web-tools.md` | 设计、决策与实测限制 |

## 3. 关键决策与理由

| 决策 | 理由 | 放弃的替代方案 |
|---|---|---|
| 工具细粒度拆分（五个独立工具） | 参数 schema 精确、模型不易混淆读写、**权限能按工具粒度控制**。Phase 1 没有沙箱，工具粒度就是唯一可用的权限阀 | 一个 `filesystem(op=...)`：schema 退化成手写分支校验，权限也无法按操作区分。这与 Claude Code（`Read`/`Write`/`Edit`/`Bash` 各自独立）一致 |
| 不照抄 Codex 的「Shell + apply_patch」粗粒度路线 | Codex 依赖沙箱与审批系统补足权限粒度，而 Nemo 的沙箱在 Phase 3。没有那两层时，粗粒度等于「全权限、零校验」 | 只给一个 shell + 一个 patch 工具：实现量小，但在没有沙箱的阶段无法表达「读可以、写不行」 |
| `run_shell` 每次调用独立进程 + 显式 `workdir` | 常驻会话会让 `cd`、环境变量、后台任务跨越调用，破坏「每个 Run 独立、结果可复现」的前提，也让取消与超时的语义变得难推理 | 常驻 shell 会话：更接近人类用终端，但状态污染与清理成本远超收益。接口位置保留，等有真实需求再加 |
| 超时与取消都要 kill 整个进程组 | `sh -c "cmd"` 会再派生子进程，只杀 shell 会把孙进程留成孤儿继续跑。用 `start_new_session=True` + `os.killpg` 才能连根拔 | 只 `process.kill()`：表面返回了超时，后台仍在写文件。测试用「后台子进程写标记文件」的方式专门验证了这一点 |
| `write_file` 默认拒绝覆盖已存在的文件 | 新建文件是安全的，静默覆盖是数据丢失。用 `overwrite=true` 把动作变成显式声明 | 默认覆盖：模型一次误判就抹掉用户的内容 |
| `edit_file` 要求匹配唯一，歧义即拒绝 | `old_string` 命中多处时替换哪一个都是猜。要求补足上下文，把「猜」变成模型的显式决策 | 默认替换第一处：静默改错位置，而且很难排查 |
| 预期失败用 `ToolFailure` 携带消息，未预期异常保持不透明 | 模型需要知道「文件不存在」「路径越界」才能自我纠正；而未知异常的原始文本可能带内部细节。规则变成：**我们自己写的失败可以展示，没预料到的一律不展示** | 统一回填异常文本：可能泄漏；统一隐藏：模型无法自我修正 |
| 系统提示放在 `ContextBuilder`，不进 `AgentState.messages` | state 应只记录「用户与模型实际产生的内容」；系统提示是装配属性。同时它恒为消息首位，天然落在 prompt 缓存的可复用前缀里 | 塞进 state：会污染消息记录与 M1 已有断言，而且每轮都要复制 |
| `ToolPolicy.decide()` 一开始就是 async | M4 的审批需要问用户，必然涉及 I/O。现在定成 async，避免之后重切这条缝 | 先做成同步：M4 再改签名，会同时波及协议、注册表与所有策略实现 |
| 不做命令黑名单 | `rm -rf` 有几十种写法可以绕过，黑名单只会制造「已防护」的错觉。真正的防护是沙箱与审批，两者都排在其后的里程碑 | 关键词拦截：安全性近乎为零，还增加维护负担 |
| 引入 tool-authored `summary`，修正 M1「事件不含工具参数」的规则 | 用 Shell 时必须看到模型到底跑了什么命令，否则无法建立信任。**取代关系**：M1 的规则（绝不展示参数）由本规则取代，改为「由工具显式声明可展示内容」，而不是把整个 `arguments` dump 进事件 | 继续完全不展示：trace 里只看到 `tool.started`，无法判断 Agent 做了什么；或直接 dump 参数：会连带泄漏密钥 |
| 截断必须带显式标记 | 让模型在「被截断的世界」里推理而它自己不知道，比返回稍长的内容危险得多 | 静默截断：模型会基于残缺内容下结论 |
| 截断策略分 head / tail / middle | Shell 输出的错误信息常在尾部，文件内容常在头部，策略由工具选 | 一律保留头部：`stderr` 里的报错会被切掉 |
| 注册表对结果设硬上限，超限报错而非截断 | 工具自己该保证输出有界；没做到是工具的缺陷，静默截断会掩盖它 | 注册表兜底截断：缺陷被掩盖，直到模型给出奇怪的结论才被发现 |

### M3b · Web 工具

| 决策 | 理由 | 放弃的替代方案 |
|---|---|---|
| 独立工具，而不是只加一句「你可以 curl」 | 工具是模型的能力清单，看不到的能力约等于不存在；独立工具还能结构化返回、统一截断与超时 | 只改提示：每个模型对这句话的利用程度不同，且拿到的是原始 HTML |
| 两个工具都 `read_only = True` | GET 不改动任何东西，与「只读工具三档放行」一致 | 标记为需审批：`ask` 档每次搜索都打断，实际结果是用户切到 `auto` 后什么都不问 |
| HTTP 层流式读取 + 字节上限 | 直接读 `response.text` 会把整个响应载入内存，一个超大响应能拖死进程 | 依赖 `Content-Length`：这个头经常缺失或不准 |
| 搜索后端先用 Bing HTML（无密钥） | 当前网络下 Google / DuckDuckGo 不可达、Baidu 返回反爬页，只有 Bing 可解析；零配置即可用 | 直接上 Tavily / Brave：质量更好但是正式接口，需要用户先注册——**这是 TODO T2 的方向** |

## 4. 实施过程

1. **先立规则**：在 `AGENTS.md` 写明新目录用途、`ExecutionContext` 与路径边界要求、`ToolFailure` 的错误分级、系统提示必须是最前且稳定；同步在架构文档记录 M3 工具集与执行模型的确定过程（含与 Claude Code / Codex 的路线对比）。
2. **契约层先行**：`ExecutionContext`、`PolicyDecision`、`ToolPolicy`、`ToolFailure`，再改注册表与 Runtime。
3. **共享约束**：`paths.py` 与 `limits.py`。路径解析用 `Path.resolve()`，因此符号链接指向外部同样被拒；截断三种策略集中一处，避免每个工具各写一遍。
4. **接口升级**：`Tool.execute(arguments, context)` 与 `Tool.summarize(arguments)` 成为协议的一部分，`read_only` 成为工具的声明项。同步更新 M1 的测试替身与子类。
5. **工具实现**：文件系统工具先做，Shell 最后做——Shell 是唯一需要处理进程生命周期的工具，单独一轮测试。
6. **进程组验证（转折点）**：最初的超时测试只验证「返回了超时错误」，这不足以证明孙进程被杀。改成让命令在后台写标记文件 `(sleep 1; echo late > marker.txt) & sleep 5`：若只杀父进程，标记文件会在 1 秒后出现。取消路径用同样的方式验证。
7. **端到端实测**：用真实 DeepSeek 跑多步任务（列目录 → 读文件 → 写文件 → 用 shell 自查），再跑一次 `--read-only` 验证策略真实拦得住。

### M3b · Web 工具

1. **先测可达性**：Google / DuckDuckGo 在这个网络下不可达，Bing 与 Baidu 可达，Baidu 返回反爬页。
2. **按真实结构写解析**：`li.b_algo` + `h2>a` + `p.b_lineclamp`，用抓下来的真实页面验证正则。
3. **测试先行**：用 `httpx.MockTransport` 注入响应，测试不依赖网络。
4. **真实闭环暴露两个问题**：结果链接是 `bing.com/ck/a` 跳转壳（`fetch_url` 只拿到 "please click here" 存根）；同一查询的结果与 `curl` 拿到的不一致。
5. **排查而不猜**：逐个试 `setlang` / `ensearch` / `mkt` / `cc`、请求头组合、`format=rss`、HTTP/2（需新增依赖，未做），确认差异来自客户端本身而非参数。结论写进设计文档 §5。
6. **修复链接解码后重跑**：搜「DeepSeek 官网」→ 解出真实域名 `deepseek.com` → 抓回正文 → 正确回答，并指出后两条是仿冒站点。

## 5. 验证证据

**测试**（96 个用例，其中 30 个为 M3 新增）：

```bash
conda run -n nemo python -m unittest discover -s tests
```

```text
Ran 96 tests in 3.447s

OK
```

**真实多步任务**（DeepSeek 默认模型，workspace 为临时目录）：

```bash
conda run -n nemo python examples/local_agent.py --workspace "$DEMO_WS" \
  "先看看这个目录里有什么，然后读一下 sample.txt，最后创建一个 hello.txt，内容写 pong"
```

```text
 4 model.completed tools=2 prompt=1285 cached=   0 completion=78
 5 tool.started list .
 7 tool.started read sample.txt
12 model.completed tools=1 prompt=1405 cached=1152 completion=81
13 tool.started create hello.txt
18 model.completed tools=1 prompt=1506 cached=1280 completion=51
19 tool.started $ ls -la && echo "--- hello.txt ---" && cat hello.txt
24 model.completed tools=0 prompt=1684 cached=1408 completion=99
26 run.completed
```

磁盘核对：`hello.txt` 内容为 `pong`，与模型自述一致。四点值得记录：模型在一步里并发提出两个只读调用；写完后自己用 `run_shell` 回读验证；**缓存命中随历史增长一路上升（0 → 1152 → 1280 → 1408）**，正是「历史只追加、前缀不变」带来的直接收益。

**只读策略的真实拦截**：

```bash
conda run -n nemo python examples/local_agent.py --workspace "$DEMO_WS" --read-only \
  "把 keep.txt 的内容改成 hacked，然后告诉我改完了"
```

```text
output: I couldn't make that change, so I won't claim I did.
        ... the tool refused it with `edit_file can modify the machine and read-only mode is on` ...
```

磁盘核对：`keep.txt` 仍为 `original`。注意这里不只是模型「配合」——`run_shell` 同样是 mutating 工具，也在拒绝范围内，所以用 shell 重定向绕过这条路同样被封死。

**覆盖情况**：

| 验收点 | 对应验证 |
|---|---|
| 读文件、按行分页、二进制拒绝 | `FilesystemToolTests.test_read_file_*` |
| 写文件、拒绝静默覆盖、显式覆盖 | `test_write_file_creates_and_refuses_silent_overwrite` |
| 精确替换、歧义拒绝、全量替换 | `test_edit_file_is_surgical_and_reports_ambiguity`、`test_edit_file_replace_all` |
| 列目录、空目录、非目录 | `test_list_dir_marks_directories_and_handles_empty` |
| 路径越界（相对与绝对） | `test_paths_may_not_escape_the_workspace` |
| 符号链接指向外部 | `test_symlink_pointing_outside_is_rejected` |
| 输出截断 | `test_read_file_truncates_long_output`、`test_large_output_is_truncated_in_the_middle` |
| 超时杀进程组 | `test_timeout_kills_the_whole_process_group` |
| 取消杀进程组且语义不变 | `test_cancellation_kills_the_process_group_and_propagates` |
| 超时上限被上下文封顶 | `test_timeout_is_capped_by_the_context` |
| 退出码是信息不是失败 | `test_non_zero_exit_is_output_not_a_failure` |
| workdir 生效且受限 | `test_workdir_is_respected_and_bounded` |
| Policy 拒绝写入并让模型看到原因 | `PolicyTests`、`test_denied_tool_is_visible_in_the_trace` |
| 未预期异常不泄漏 | `test_unexpected_errors_stay_opaque_but_expected_ones_are_shown` |
| 结果硬上限 | `test_oversized_result_is_rejected_with_guidance` |
| 系统提示位于首位且跨轮稳定 | `SystemPromptTests` |
| 工具摘要进入事件 | `TraceSummaryTests` |
| 工具结果回填给模型 | `test_tool_result_reaches_the_model` |

### M3b · Web 工具

**测试**（全量 204 个用例全绿，其中 17 个为 M3b 新增）：

```bash
conda run -n nemo python -m unittest discover -s tests
```

```text
Ran 204 tests in 3.643s

OK
```

**真实模型闭环**（`--mode auto`，两个工具各调用一次）：

```text
 10 model.completed tools=1 prompt=3097 cached=2560 completion=44
 11 tool.started fetch https://deepseek.com/en/index.html
 12 tool.completed fetch https://deepseek.com/en/index.html
 16 model.completed tools=0 prompt=3378 cached=3072 completion=179
status  : completed
```

模型正确指出第 2、3 条结果是仿冒/第三方站点——搜索链路与判断都工作了。

## 6. 遗留与下一步

**已知边界**：

- **没有沙箱**。`workspace` 只约束走路径参数的调用；`run_shell` 里的命令可以访问机器上任何它有权访问的位置（`cd ~`、绝对路径读取都不受 workspace 限制）。这是当前最大的安全缺口，必须清楚。
- **没有审批**。策略只能整体允许或拒绝，无法「这一次问一下用户」。M4 的 CLI 到位后才能做。
- `summary` 可能包含敏感片段（比如命令行里内联的 token）。截断到 200 字符并压成单行，但没有内容脱敏，脱敏随 M4 的输出层一起做。
- `run_shell` 固定 `/bin/sh -c`，不支持 bash/zsh 专有语法。
- `read_file` 为统计总行数会扫描整个文件，超大文件有 I/O 成本；只按 UTF-8 解码（非法字节用替换字符），不做编码嗅探。
- 无 `python` 独立工具、无目录创建/删除工具、无文件移动与删除（删除能力刻意没有提供）。
- 事件里仍没有实际使用的模型标识。

### M3b · Web 工具的遗留

- **Bing 这条路的相关性不可靠**：搜技术细节时给的是品牌导航页（`python asyncio` → python.org 首页），导航型查询才准确。实测对照与排查记录见 [web-tools 设计](../design/web-tools.md) §5；换带密钥的 API 已记入 [TODO.md](../TODO.md) T2。
- 只处理静态 HTML；JS 渲染的页面拿不到内容。
- 没有缓存与限流退避，连续搜索可能被搜索引擎限流，届时如实报错。

**下一步**：

- 更推荐直接进入 M4（Server 与 CLI）：把审批流、会话历史持久化、事件订阅做起来——这三件事都直接影响「能不能天天用」，而且审批是补上当前安全缺口的关键一步。
