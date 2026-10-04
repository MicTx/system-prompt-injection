# system-prompt-injection

**简体中文** | [English](README-en.md)

集中维护多个本机 agent 的**公共系统提示词规则**，并把同一份内容安全同步到 CC Switch 当前支持的提示词宿主、ZCode 及 Dawud Flow 模板。配置声明 14 个目标，其中未安装宿主会跳过。

## 设计

```text
SYSTEM_PROMPT.md + targets.json
          │
          ├── preview / check
          ├── apply（备份 + 原子替换）
          ├── rollback（拒绝覆盖后续编辑）
          └── cc-switch（只更新 prompts 表）
```

`SYSTEM_PROMPT.md` 只负责公共规则；各客户端原有的专属系统提示词和本地规则保留在目标文件中。同步器只管理带有 `system-prompt-injection` 标记的区块，不覆盖整文件。`agents-orchestration` 的 route snippet 仍由其安装器管理；配置中的 3 个 `enabled: false` 条目仅用于兼容此前备份的回滚，不会被同步器读写。

## 宿主路径矩阵

路径与宿主文件名按 CC Switch 的真实映射维护：

| 宿主 | 目标文件 | 状态 |
| --- | --- | --- |
| Claude | `~/.claude/CLAUDE.md` | 已安装 |
| Codex | `~/.codex/AGENTS.md` | 已安装 |
| Gemini CLI | `~/.gemini/GEMINI.md` | 可选 |
| Grok Build | `~/.grok/AGENTS.md` | 可选 |
| OpenCode | `~/.config/opencode/AGENTS.md` | 已安装 |
| OpenClaw | `~/.openclaw/AGENTS.md` | 可选 |
| Hermes | `~/.hermes/SOUL.md` | 可选 |
| Pi | `~/.pi/agent/AGENTS.md` | 已安装 |
| MCode | `${MINIMAX_DATA_DIR:-${MAVIS_DATA_DIR:-~/.minimax}}/AGENTS.md` | 可选 |

Claude Desktop 不支持 Prompts，因此不加入同步目标。MCode 按 CC Switch 解析 `MINIMAX_DATA_DIR`、`MAVIS_DATA_DIR` 后回退到 `~/.minimax`；未安装宿主或其父目录不存在时，目标保持 skipped，不会创建无关目录。

## 使用

```bash
cd ~/dev/system-prompt-injection
python3 -m src.sync_prompts check --json
python3 -m src.sync_prompts preview --json
python3 -m src.sync_prompts apply
```

默认先预览；`apply` 会：

- 校验源文件和所有目标；
- 迁移本项目此前写入的旧发布策略区块；
- 只更新托管区块，保留其他内容；
- 在 `~/.config/agent-harness-public/prompt-backups/` 创建备份；
- 使用同目录临时文件和 `os.replace` 原子替换。

回滚：

```bash
python3 -m src.sync_prompts rollback --backup /path/to/backup
```

若目标文件在 apply 后被其他进程修改，回滚会拒绝覆盖；确认后才使用 `--force`。

## CC Switch

CC Switch 只作为各宿主**完整目标文件**的可选目录，不是真实源。同步前先同步目标文件，再执行：

```bash
python3 -m src.sync_prompts apply
python3 -m src.sync_prompts cc-switch
python3 -m src.sync_prompts cc-switch --enable
```

`cc-switch` 默认同步当前已存在且 clean 的 Claude、Codex、Gemini、Grok Build、OpenCode、OpenClaw、Hermes、Pi 和 MCode 目标；未安装宿主会跳过。MCode 通过 `apply` 直接维护其 `AGENTS.md` 文件，并执行 CC Switch 同样的 32 KiB 上限校验。也可以通过 Python API 传入 `app_types` 精确选择宿主。同步器只把完整目标文件写入 `prompts.content`，默认保持新记录禁用并保留已有启用状态；不会修改 providers、密钥、模型、skills、MCP 或其他表。`--enable` 必须显式使用，因为 CC Switch 启用后可能把完整提示词重新写回客户端文件。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile src/sync_prompts.py
```

本项目仅管理系统提示词公共区块，不修改 Spec 技能包。
