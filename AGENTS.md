# Nemo 工作空间规范

## 项目定位

Nemo 是个人 Agent OS 与 Agent 技术实验平台。基础设施使用成熟库，Agent Runtime 与核心智能机制自行实现。

## 当前阶段与目录约定

- M1（Agent Runtime 最小闭环）、M2a（配置驱动的真实模型接入、`openai_compatible` 协议、token 统计）、M3（本地执行）与 M4（Context、CLI、Server）已完成；M5 进行中，其中 M5a（UI 查询 API、模型/Provider 目录、Trace）与 M5b（React UI 最小闭环）已完成，M5c（Provider Settings）未开始。M5 记录见 [docs/milestones/M5-react-ui.md](docs/milestones/M5-react-ui.md)。
- `src/nemo/core/session.py` 只保存会话状态（会话标识、消息历史、workspace），不做文件 I/O；`src/nemo/cli/` 保存命令行客户端（参数解析、REPL、审批交互、事件渲染、JSONL transcript 与脱敏）；`src/nemo/core/tools/approval.py` 与 `command_rules.py` 保存审批策略与 shell 命令形态判定。审批只做策略，core 不做 I/O。
- `src/nemo/server/` 保存本地 FastAPI 宿主、Session/Run 应用服务、SSE 与远程审批协议；Server 只编排 Core，不实现第二套 Agent Loop。SQLite 实现位于 `src/nemo/adapters/sqlite.py`，不进入 Core。
- `apps/ui/` 保存 React + TypeScript + Vite 客户端；源码在 `src/`，前端测试与对应模块就近放置。开发态统一通过 Vite 的 `/api` 代理访问本地 Server，不直接读取 SQLite、配置文件或密钥。`package-lock.json` 提交，`node_modules/`、`dist/`、coverage、`*.tsbuildinfo` 与编译生成的 Vite 配置文件不提交；清理这些本地产物仍遵守删除前确认。
- 说明文件分两层，都是 `AGENTS.md`，靠位置区分作用域：用户级 `~/.nemo/AGENTS.md`（跨项目的 Agent 行为偏好）与项目级 `<workspace>/AGENTS.md`（该项目自己的约定）。两者都只做**追加**，由 `src/nemo/prompts/instructions.py` 读取（core 之外），会话开始时各快照一次；任何说明文件都不能放宽路径检查与审批，那是代码强制的边界。
- CLI 的三档审批：`ask`（默认，改动前确认）、`auto`（只确认识别得出的危险）、`full`（不确认）。判定规则是人工维护的表，不是沙箱；`auto` 的最坏情况等同 `full`，不得把它当安全边界。
- `src/nemo/core/contracts/` 保存 Pydantic 数据模型与 Protocol；`runtime/` 保存 Loop；`context/` 保存基础上下文组装；`tools/` 保存注册和执行边界。
- `src/nemo/core/models/` 保存模型注册表、解析器与 Model Client；`src/nemo/config/` 保存配置与密钥加载；`src/nemo/adapters/` 保存基础设施适配器（HTTP 传输、按协议划分的模型适配器）；`src/nemo/bootstrap.py` 是唯一的依赖装配入口，只有它认识具体适配器。
- `src/nemo/adapters/tools/` 保存工具实现（Filesystem、Shell、Web）；`src/nemo/core/tools/` 保存注册表、执行边界、路径与输出的共享约束；`src/nemo/core/context/` 负责系统提示与消息组装。工具集当前七个：`read_file`、`write_file`、`edit_file`、`list_dir`、`run_shell`、`web_search`、`fetch_url`。
- `src/nemo/prompts/` 保存库内置的系统提示模板：提示拆成具名短条款（每条对应一个具体失败模式，可独立增删），环境块运行时拼装，两者分开。
- `src/nemo/testing/` 保存无网络 Fake Model/Tool；`examples/` 保存可运行演示；`tests/` 保存 unittest 自动化测试，文件命名 `test_*.py`。
- `tests/` 平铺存放，一个模块一个文件，命名 `test_<模块>.py`；无网络替身统一放 `src/nemo/testing/`，不在测试文件里重复实现。
- 统一使用独立 Conda 环境 `nemo`；`environment.yml` 声明 Python/pip 环境，`pyproject.toml` 声明项目依赖，`requirements-lock.txt` 固定当前验证的 Python 依赖版本。先装 Conda 依赖，再通过环境内 pip 安装项目，不在 base 环境安装。
- 用户级配置固定在 `~/.nemo/config.toml`（非密钥）与 `~/.nemo/.env`（密钥，权限 600）；配置格式统一用 TOML，依赖标准库 `tomllib`，不引入 YAML 依赖。仓库内不得出现真实密钥或用户配置副本。
- 项目不使用 uv；旧 `.venv/` 与 `uv.lock` 已清理，不再执行 uv 命令。缓存与构建产物不提交，删除仍须用户同意。
- 根目录 `AGENTS.md` 保存协作规范；`NEMO_ARCHITECTURE.md` 保存架构图、模块边界与阶段规划；`docs/` 保存实施过程与决策记录。
- 正式开发前先确认工程方案，再按架构文档中的建议建立代码目录；新增目录前先在本文件补充用途、命名与清理规则。
- 文档使用中文，代码、接口及配置名称使用英文；Markdown 文件使用明确的英文名称，架构图使用 Mermaid。
- 不保存临时抓取网页、凭据或工具输出。临时产物需要清理时，删除前征求用户同意。

## 文档库

- `docs/README.md` 是文档库索引与写作约定；`docs/milestones/` 逐个记录已完成的里程碑，命名 `M<编号>-<英文短名>.md`；`docs/milestones/_MILESTONE_TEMPLATE.md` 为记录模板，下划线前缀用于排在里程碑文件之前。
- `docs/TODO.md` 保存「已决定要做、但还没有里程碑承接」的独立事项，做完即移除并归入对应里程碑记录；里程碑内部的下一步仍写在里程碑记录的 §6，两处不重复。
- `docs/design/` 保存尚未实现或部分实现的组件级设计，命名 `<组件>-<主题>.md`；至少包含目标、分层或接口、决策与理由、验收标准、非目标五部分，不套用里程碑的六节结构（那六节只描述已发生的事实）。
- 分工：`NEMO_ARCHITECTURE.md` 写「要做成什么」，可包含未实现的设计基线；里程碑记录写「已经做成了什么」，只写已发生的事实与可复现证据，不写计划。
- 每个里程碑固定六节：目标与范围、交付物、关键决策与理由、实施过程、验证证据、遗留与下一步；更细的内容作为小节放在对应章节内，不新增同级章节，以保证编号稳定。
- 里程碑按完成顺序追加，不覆盖、不回改历史记录；结论被后续推翻时，在新记录中写明取代关系，并在旧记录末尾追加一行指向新记录的说明。
- 一个里程碑拆成多步交付时，用 `M<编号><字母>` 编号（M4a、M4b、M4c），并在同一个里程碑文件里作为小节；不新增文件、不另起编号，目录里每个编号只出现一次。
- 一个里程碑一个文件，编号与 `NEMO_ARCHITECTURE.md` 的阶段里程碑保持一致；内容重复的临时草稿不入库。
- 单条决策复杂到需要独立讨论时，再引入 `docs/decisions/`，不提前预建空目录。

## 架构纪律

- 按 Phase 1 Core → Phase 2 Memory & Context → Phase 3 Agent OS 推进。
- Core 不依赖 GUI、FastAPI 或 Tauri；CLI、Desktop、Web 是客户端。
- 不以 LangChain、LangGraph、CrewAI、AutoGen、OpenAI Agents SDK 或 Claude Agent SDK 实现 Runtime。
- Providers are configuration, protocols are code；厂商判断不得泄漏进 Runtime。
- 厂商名称只允许出现在配置文件与 `src/nemo/adapters/` 内；`core/` 不得出现厂商品牌分支，协议标识只能作为类型字面量或装配映射出现。
- 工具必须通过 `ExecutionContext` 获取工作目录、超时与输出上限，不得自行读取全局状态或当前工作目录。
- 路径类工具必须把访问限制在 `ExecutionContext.workspace` 内，越界即拒绝并回填明确原因；Phase 1 不提供沙箱，`workspace` 是边界约定而非安全隔离。
- 工具的预期失败使用 `ToolFailure` 携带面向模型的安全消息；未预期异常保持不透明，不把原始异常文本交给模型。
- 系统提示属于上下文组装，放在消息序列最前且内容稳定；易变信息（时间、临时路径、随机 ID）不得进入稳定前缀。
- 系统提示的每一条款只处理一个失败模式，命名清晰、可独立增删；不写成长段散文，不写无法被验证的空泛要求。
- 后期模块在文档中标明阶段，不在 Phase 1 预建空壳实现。
- 区分聊天明确需求与工程补充建议；有冲突时采用聊天中较新的决定。

## 验证

- 文档变更检查 Mermaid 代码围栏完整、图中标识符和引用一致、阶段范围与模块依赖方向一致。
- 使用 `conda create --file environment.yml --override-channels -c conda-forge` 创建环境；`conda run -n nemo python -m unittest discover -s tests -v` 执行测试；`conda run -n nemo python examples/minimal_agent.py` 验证闭环演示。
- `conda run -n nemo python -m nemo.cli --help`、`conda run -n nemo python -m nemo.cli --workspace <dir> --mode auto "<任务>"` 验证 CLI；交互式审批需要真实 TTY，非 TTY 环境一律按拒绝处理（脚本要放行就显式用 `--mode full`）。
- React UI 使用 `npm --prefix apps/ui test -- --run`、`npm --prefix apps/ui run typecheck` 与 `npm --prefix apps/ui run build` 验证；开发时先启动 Nemo Server，再运行 `npm --prefix apps/ui run dev`，由 `/api` 代理保持同源。
- 更新依赖时同步 pyproject.toml 与 requirements-lock.txt，并重新验证；requirements-lock.txt 是版本快照，不是跨平台 Conda 二进制锁文件。
- M1 验收覆盖消息回填、未知工具、参数错误、执行失败、步数上限、取消和终态事件唯一性。
- M2 验收覆盖配置解析与引用校验、解析优先级、能力不匹配、同协议新增 Provider 仅改配置、密钥不出现在错误与事件中、token 统计与缓存命中可观测。
- M3 验收覆盖读文件、写文件、精确替换、列目录、执行命令、路径越界拒绝、输出截断、超时后进程组被清理、取消后无残留进程、系统提示位于稳定前缀。
- 提示模板变更检查条款命名唯一、相同输入产出逐字相同的文本（缓存前提）、关键条款有测试锁定。
- 依赖变更后执行 `conda run -n nemo python -m pip install -r requirements-lock.txt -e .` 与 `conda run -n nemo python -m pip check`，并确认 `requirements-lock.txt` 覆盖新增依赖的传递闭包。
- 文档库变更检查索引链接可达、里程碑编号连续、记录中的验证证据包含实际执行命令与结果。
- 不通过注释错误、跳过检查或绕过标记掩盖问题。

## 操作边界

- 大改动先给出计划并经用户确认后实施。
- 删除文件/目录/git 历史；修改 .env、密钥、token、CI/CD；数据库 schema 变更或迁移；git push/rebase/reset --hard/强推；安装全局依赖或修改系统配置；公开发布，均须先询问用户。
- 密钥、token、密码不得进入代码、commit 或日志。
