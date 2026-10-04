# 贡献指南

这是一个刻意保持极简的项目，贡献前请先接受三条约束：

1. 单文件：所有逻辑留在 `prompt_sync.py`，Python 3.9+、只用标准库，不引入第三方依赖。
2. 标记字节兼容：`<!-- system-prompt-injection:shared:start/end -->` 与 `== SYSTEM_PROMPT_INJECTION:shared:START/END ==` 两对标记一个字节都不能改。
3. 安全语义不回退：apply 前备份、临时文件 + `os.replace` 原子替换、undo 可恢复——这三条任何改动都不能省。

提交流程：

- 改完先跑测试：`python3 -m unittest discover -s tests`，全绿再提 PR。
- PR 描述写清楚动机与验证方式；文档改动（双语 README 两版结构保持一致）同样欢迎。
- Issue 请带版本号、平台与复现步骤；涉及本机路径的内容请先脱敏。

提交 PR 即表示贡献按 PolyForm Noncommercial 1.0.0 授权（全文见仓库根目录 `LICENSE`）。

## English

This project is deliberately minimal: single-file `prompt_sync.py`, stdlib only, marker bytes frozen, and the backup/atomic-write/undo guarantees are non-negotiable. Run `python3 -m unittest discover -s tests` before opening a PR; keep the two READMEs structurally in sync. Contributions are licensed under PolyForm Noncommercial 1.0.0.
