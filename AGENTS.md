# Nemo 工作空间规范

## 项目与当前阶段

Nemo 是个人 Agent OS 与 Agent 技术实验平台：基础设施使用成熟库，Agent Runtime 与核心智能机制自行实现。

Phase 1 的 M1–M5 已完成，下一阶段是 M6 macOS App。权威状态见 [docs/README.md](docs/README.md)，目标架构见 [NEMO_ARCHITECTURE.md](NEMO_ARCHITECTURE.md)。

## 目录职责

- `src/nemo/core/`：领域契约、Runtime、Session、Context、模型与工具边界；不得依赖 FastAPI、SQLite、CLI 或 UI。
- `src/nemo/adapters/`：HTTP、协议、Filesystem、Shell 与 Web 等基础设施适配器；`adapters/persistence/` 保存 SQLite schema、事件投影与 Repository。
- `src/nemo/config/`：`config.toml`、密钥读取与原子配置编辑；具体适配器只由 `src/nemo/bootstrap.py` 装配。
- `src/nemo/server/`：FastAPI HTTP/SSE、应用外观，以及 Session、Run、Catalog、Trace、Settings 用例；只编排 Core，不复制 Agent Loop。
- `src/nemo/cli/`：`main.py` 只做参数与模式分派，`remote.py` 是默认 Server 客户端，`direct.py` 是直连恢复模式；其余模块负责审批、事件渲染、HTTP 客户端与 transcript。
- `src/nemo/prompts/`：具名、可独立测试的系统提示条款与项目说明加载。
- `src/nemo/testing/`：无网络 Fake Model/Tool；测试不得在各文件重复造替身。
- `apps/ui/`：React + TypeScript + Vite 客户端；`components/` 放视图组件，`hooks/` 放页面状态与 Server 生命周期；只访问 Server，不读取 SQLite、用户配置或密钥。
- `apps/ui/src-tauri/`：macOS 桌面壳、Python sidecar 的启动/退出与窗口权限；不得复制 Agent 逻辑。`apps/ui/build_sidecar.py` 只负责构建可打包的 Server 二进制。
- `tests/`：平铺的 `test_*.py`；一个模块对应一个测试文件。
- `examples/`：可运行演示，不承担生产客户端职责。
- `docs/design/`：仍然有效的组件设计；`docs/reference/`：当前接口与配置参考；`docs/milestones/`：已完成交付的简要记录。

新增顶层目录或职责层之前，先更新本节。

## 架构纪律

- 按 Phase 1 Core → Phase 2 Memory & Context → Phase 3 Agent OS 推进，不提前创建后期空壳。
- Core 不依赖 GUI、FastAPI、SQLite、Tauri 或具体厂商。
- 不用 LangChain、LangGraph、CrewAI、AutoGen、OpenAI Agents SDK 或 Claude Agent SDK 实现 Runtime。
- **Providers are configuration, protocols are code**：厂商名称只出现在配置和 adapters；Runtime 不写厂商分支。
- 工具必须从 `ExecutionContext` 取得 workspace、超时与输出限制，不读取隐式当前目录或全局状态。
- 文件工具必须拒绝 workspace 越界；`workspace` 与审批均不是沙箱，不能描述成安全隔离。
- 工具的预期失败使用 `ToolFailure` 返回安全消息；未预期异常不得把原始文本暴露给模型。
- 系统提示的稳定前缀不得包含时间、临时路径或随机 ID；每条条款只处理一个失败模式。
- 用户级 `~/.nemo/AGENTS.md` 与项目级 `<workspace>/AGENTS.md` 只追加，不得放宽代码强制的路径或审批边界。
- 审批模式为 `ask`、`auto`、`full`；`auto` 是人工规则，不是安全边界，最坏情况等同 `full`。

## 配置与产物

- Python 使用独立 Conda 环境 `nemo`；不使用 uv，不在 base 环境安装项目依赖。
- `environment.yml` 定义环境，`pyproject.toml` 定义项目依赖，`requirements-lock.txt` 固定已验证的 Python 依赖快照。
- 用户配置固定在 `~/.nemo/config.toml`；密钥放 `~/.nemo/.env` 且权限为 `600`。仓库不得保存真实配置副本、密钥或 token。
- `apps/ui/package-lock.json` 提交；`node_modules/`、`dist/`、coverage、`*.tsbuildinfo` 和编译生成的 Vite 配置不提交。
- 不保存临时网页、工具输出或凭据；需要清理本地产物时，删除前征求用户同意。

## 文档规则

- 文档使用中文；代码、接口、配置项和文件名使用英文；架构图使用 Mermaid。
- `README.md` 负责上手，Architecture 负责当前与目标边界，reference 负责现行事实，design 负责仍有效的设计，milestone 负责历史交付。
- 同一事实只保留一个权威来源，其他文档用链接引用，不复制大段内容。
- Milestone 固定六节：目标与范围、交付物、关键决策、实施摘要、验证、遗留。允许纠错和压缩，但不得改变当时的交付结论；Git 保存完整修改历史。
- Milestone 验证只保留命令、测试数量和结论，不粘贴逐项测试输出或临时路径。
- 已决定但未进入里程碑的事项写入 `docs/TODO.md`；进入里程碑后从 TODO 移除。
- 设计或接口变化时，同一提交内更新对应 reference/design、Architecture 状态和 docs 索引。

## 验证命令

```bash
conda run -n nemo python -m unittest discover -s tests -v
conda run -n nemo python -m compileall -q src tests
conda run -n nemo python -m nemo.cli --help
npm --prefix apps/ui test -- --run
npm --prefix apps/ui run typecheck
npm --prefix apps/ui run build
```

按改动范围运行最小充分集合；依赖变更后还需执行：

```bash
conda run -n nemo python -m pip install -r requirements-lock.txt -e .
conda run -n nemo python -m pip check
```

不通过注释错误、跳过检查或绕过标记掩盖问题。

## 操作边界

- 大改动先给出方案并取得确认。
- 删除文件或目录、修改 Git 历史、数据库 schema 或迁移、`.env`/密钥/token、CI/CD、全局依赖、系统配置、公开发布、`git push`、rebase、强推或 `reset --hard`，必须先询问用户。
- 密钥、token、密码不得进入代码、提交、日志、事件或文档。
