# system-prompt-injection

[简体中文](README.md) | **English**

Single-source synchronization for shared system-prompt rules across local agent hosts.

## Features

- **One authored source** — edit `SYSTEM_PROMPT.md`; `targets.json` declares destinations.
- **Managed blocks** — preserve host-specific instructions outside marked regions.
- **Fail-closed writes** — validate UTF-8, duplicate markers, symlinks, paths, and conflicts before writing.
- **Atomic updates** — create private mode-700 backups and replace files atomically.
- **CC Switch integration** — update only the `prompts` table and preserve enabled state by default.
- **Host matrix** — Claude, Codex, Gemini CLI, Grok Build, OpenCode, OpenClaw, Hermes, Pi, ZCode, Dawud Flow templates, and optional MCode support.

## Quick start

Requires Python 3.9+ and the standard library.

```bash
cd system-prompt-injection
python3 -m unittest discover -s tests -v
python3 -m src.sync_prompts check --json
python3 -m src.sync_prompts apply
```

`apply` updates only declared managed blocks. Optional hosts are skipped when their parent directory is absent. Backups are written under `~/.config/agent-harness-public/prompt-backups/`.

## Host paths

| Host | Prompt file |
| --- | --- |
| Claude | `~/.claude/CLAUDE.md` |
| Codex | `~/.codex/AGENTS.md` |
| Gemini CLI | `~/.gemini/GEMINI.md` |
| Grok Build | `~/.grok/AGENTS.md` |
| OpenCode | `~/.config/opencode/AGENTS.md` |
| OpenClaw | `~/.openclaw/AGENTS.md` |
| Hermes | `~/.hermes/SOUL.md` |
| Pi | `~/.pi/agent/AGENTS.md` |
| MCode | `${MINIMAX_DATA_DIR:-${MAVIS_DATA_DIR:-~/.minimax}}/AGENTS.md` |

Claude Desktop does not support prompts in this matrix. MCode is limited to 32 KiB, matching the host integration.

## Commands

```bash
python3 -m src.sync_prompts preview --json
python3 -m src.sync_prompts check --json
python3 -m src.sync_prompts apply
python3 -m src.sync_prompts cc-switch
python3 -m src.sync_prompts cc-switch --enable
python3 -m src.sync_prompts rollback --backup /path/to/backup
```

CC Switch synchronization writes complete host prompt files to its `prompts` table. New records stay disabled; existing enabled state changes only with `--enable`. Provider, credential, model, skill, and MCP tables are not modified.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile src/sync_prompts.py tools/export_public.py
ruff check src tests tools
```

The public snapshot is generated from an allowlist and verified by `tools/export_public.py`. Local agent state, private paths, credentials, backups, and workflow records are excluded.

## License

[PolyForm Noncommercial License 1.0.0](LICENSE). Commercial use is not permitted under the default license terms.
