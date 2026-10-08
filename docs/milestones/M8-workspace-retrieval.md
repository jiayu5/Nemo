# M8 · 工作区检索

| 项 | 内容 |
|---|---|
| 状态 | 已完成 |
| 完成日期 | 2026-09-24 |
| 阶段 | Phase 2 |
| 代码范围 | `src/nemo/adapters/workspace_retrieval.py`、`src/nemo/adapters/tools/search.py`、Runtime、Server、CLI |

## 1. 目标与范围

由模型按需搜索工作区文本并读取指定文件范围；工具结果进入会话历史，较早轮次由 M7 按需压缩。不在每个 Run 开始时预扫或临时注入文件内容，不引入 embedding、向量索引或数据库迁移。

## 2. 交付物

- `search_workspace` 只读工具：查询词、可选路径范围和结果数量；返回带路径、行号的短预览。
- 有界本地词法检索：限制扫描文件数、字节数、单文件大小、时间、目录和路径边界。
- 历史与 Context：搜索结果作为正常工具消息保存；已知文件仍使用 `read_file` 按行读取。
- 当前轮次窗口检查：只读工具结果自身过大时，返回可恢复的错误，提示缩小范围。

## 3. 关键决策

- 文件检索由模型决定何时调用，普通问题不扫描工作区。
- 使用本地词法匹配；embedding 的收益和成本留待真实查询评估。
- 搜索结果保持短小，并进入会话历史，以支持后续轮次和前缀缓存。
- M7 只压缩较早完整轮次，不能替当前轮次管理单次工具输出。

## 4. 实施摘要

复用工作区扫描与片段排序适配器，将它接入 `search_workspace` 工具，并在生产 Server 与直连 CLI 注册。移除原先 Runtime 每轮预检索、请求临时注入及其专用事件。Runtime 对当前轮次工具输出增加窗口下限检查。Server 沿用现有工具消息持久化，不新增 schema。

## 5. 验证

执行 `conda run -n nemo python -m unittest discover -s tests -q`（260 个测试通过）、`conda run -n nemo python -m compileall -q src tests`、`conda run -n nemo python -m nemo.cli --help`、`npm --prefix apps/ui test -- --run`（17 个测试通过）、`npm --prefix apps/ui run typecheck`、`npm --prefix apps/ui run build` 和 `git diff --check`，均通过。

## 6. 遗留与下一步

词法匹配可能漏掉同义表达；大仓库受扫描上限影响。M7 对极小窗口或过大的较早轮次仍可能无法压缩。后续用固定查询集评估检索质量、延迟与缓存命中，再决定是否增加 embedding 或索引。
