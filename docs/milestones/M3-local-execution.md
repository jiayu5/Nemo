# M3 · 本地执行

| 项 | 内容 |
|---|---|
| 状态 | 已完成：M3a 本地工具、M3b Web 工具 |
| 完成日期 | M3a：2026-09-17；M3b：2026-09-21 |
| 阶段 | Phase 1 |
| 代码范围 | `core/tools/`、`core/context/`、`adapters/tools/`、`prompts/` |

## 1. 目标与范围

让 Agent 能在明确边界内读写 workspace、执行 Shell 并访问公开网页。

M3a 包含五个本地工具、ExecutionContext、路径/输出/超时限制、进程组清理和 Policy 接口；M3b 增加 `web_search` 与 `fetch_url`。本步不提供沙箱、常驻 Shell、独立 Python 工具、浏览器自动化或交互式审批。

## 2. 交付物

- 文件工具：`read_file`、`write_file`、`edit_file`、`list_dir`。
- 进程工具：`run_shell`。
- Web 工具：`web_search`、`fetch_url`。
- `ExecutionContext`：workspace、默认/最大超时、输出上限。
- 路径与输出共享约束、Tool Policy 接口和本地 Agent 系统提示。

## 3. 关键决策

- 文件工具细粒度拆分，使权限与 schema 清晰；不使用单一万能工具。
- `run_shell` 每次启动独立进程并显式设置 cwd，不保留跨调用隐式状态。
- 文件真实路径必须位于 workspace，包括符号链接解析后路径。
- Shell 超时和取消终止整个进程组；输出超限时带明确截断标记。
- 不用黑名单声称安全；Shell 仍拥有当前用户权限。
- Web GET 标记为只读，但查询词和 URL 进入可见事件摘要。
- 搜索暂用 Bing HTML；解析失败与“零结果”分开报告。

## 4. 实施摘要

M3a 先建立 ExecutionContext 和共享限制，再实现文件工具、Shell、Policy 与提示条款。真实目录和子进程测试暴露并修复符号链接越界、输出截断和取消残留。M3b 后续补充 Web 参数、响应大小、重定向、HTML 提取和 MockTransport 测试。

## 5. 验证

```bash
conda run -n nemo python -m unittest discover -s tests -v
conda run -n nemo python examples/local_agent.py --workspace <temp-dir> "创建并读取 hello.txt"
```

M3a 完成时 96 个测试通过；加入 M3b 后全套 204 个测试通过。Web 测试不访问真实网络。

## 6. 遗留与下一步

- workspace 和审批都不是 Sandbox；操作系统隔离属于 Phase 3。
- 交互式审批在 M4b 完成。
- Bing HTML 搜索相关性不稳定，替换搜索 API 记录为 TODO T2。
- JavaScript 页面、登录态、浏览器交互和下载不在当前 Web 工具范围。
