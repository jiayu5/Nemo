# M9a · 项目说明继承

| 项 | 内容 |
|---|---|
| 状态 | 已完成 |
| 完成日期 | 2026-10-09 |
| 阶段 | Phase 2 收尾 |
| 代码范围 | `prompts/instructions.py` |

## 1. 目标与范围

完成 T1，进入仓库子目录创建会话时仍能加载父级项目规则。

## 2. 交付物

项目说明加载器支持最近 Git 根到 workspace 的各层 `AGENTS.md`，共享 Server、CLI 与示例入口。具体行为见 [Context 设计](../design/context-assembly.md#3-稳定前缀)。

## 3. 关键决策

使用最近 Git 根作为边界，识别 `.git` 目录和文件；非 Git 目录只加载自身。按父级到子级排序，继续使用会话启动快照，不扩展普通文件工具的 workspace 边界。

## 4. 实施摘要

复用既有说明加载入口，合并多层说明并标注来源相对路径；过滤根外符号链接和重复目标。无需改变数据库或客户端接口。

## 5. 验证

- `conda run -n nemo python -m unittest discover -s tests -q`：268 项通过。
- `conda run -n nemo python -m compileall -q src tests`：通过。
- `git diff --check`：通过。

## 6. 遗留与下一步

合并说明共用原有长度上限，长父级说明可能导致后续内容截断。子目录按需加载与 override 文件暂不包含。
