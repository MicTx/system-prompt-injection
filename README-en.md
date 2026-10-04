# system-prompt-injection

[中文](README.md) | **English**

[![License: PolyForm-NC-1.0.0](https://img.shields.io/badge/License-PolyForm--NC--1.0.0-blue)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)](pyproject.toml)
[![Tests: 33 unittest cases](https://img.shields.io/badge/Tests-33%20unittest%20cases-brightgreen)](tests)


A minimal single-file TUI that syncs one shared prompt block into every local agent config file. Single file, zero dependencies, automatic backups before every change.

## Why you need it

A single machine often runs several agents at once: Codex, Claude Code, Gemini CLI... each keeps a config file in its own home directory. Some rules hold for all of them: never push to a public host, keep credentials out of the conversation. Today those rules travel by hand, copied into every file.

Copying by hand fails in two ways. Miss one copy and an agent keeps running on stale rules until something breaks. Overwrite the whole file to save effort and you wipe each client's own settings. What's missing is not a better rule set — it's a way to touch only the part that should change.

The managed block is that way. Write the shared rules once, in `SYSTEM_PROMPT.md`. On sync, only the content between the markers in each target file is replaced; everything outside the markers stays byte-for-byte the same. Edit once, press one key, every target is up to date. The boundary, stated plainly: this is shared-fragment synchronization, not config merging, and it attaches no meaning to any client's fields.

## Up and running in 30 seconds

Requirements: Python 3.9+, standard library only, nothing to install. From the repository directory:

```bash
./prompt_sync.py          # TUI: status, diff, apply, undo, edit the source
./prompt_sync.py check    # headless status (exit code 1 when out of date)
./prompt_sync.py apply    # headless sync
```

Two steps for first use:

1. Write your shared rules inside the markers of `SYSTEM_PROMPT.md`.
2. List your target files in `targets.json`; paths support `~` and `${VAR:-default}`.

## How it works

`SYSTEM_PROMPT.md` is the single source of truth. Each target file marks the managed span with a pair of markers; content outside the span is never touched:

```text
<!-- system-prompt-injection:shared:start -->
...synced content...
<!-- system-prompt-injection:shared:end -->
```

Pi's `SYSTEM.md` uses a different pair (declare `"format": "pi-system"` in `targets.json`):

```text
== SYSTEM_PROMPT_INJECTION:shared:START ==
...synced content...
== SYSTEM_PROMPT_INJECTION:shared:END ==
```

After a scan, every target sits in one of five states:

| State | Meaning |
| --- | --- |
| `ok` | in sync |
| `out` | markers present, content stale |
| `new` | block pending (new file, or markers to append) |
| `skip` | host not installed (parent directory missing) |
| `err` | unreadable (symlink, non-regular file, or bad markers) |

Three safety guarantees:

- Before `apply`, every changed file is backed up to `~/.config/system-prompt-injection/backups/<timestamp>/`.
- Writes go through a temp file + `os.replace` atomic swap; no half-written files.
- Press `u` in the TUI to restore the latest backup; `check` never writes.

## TUI keys

| Key | Action |
| --- | --- |
| `↑/↓` `j/k`, `g`/`G` | move selection, jump to first / last |
| `d` or Enter | preview the pre-apply diff for the selected target |
| `p` | apply to the selected target only |
| `a` | apply to all out-of-date targets |
| `e` | edit `SYSTEM_PROMPT.md` with `$EDITOR` (fallback `VISUAL`, `vi`) |
| `u` | restore the latest backup |
| `r` | rescan |
| `q` / `ESC` | quit |

## Configuration

`targets.json` holds a `targets` array; each entry has three fields:

```json
{
  "targets": [
    { "id": "codex", "path": "~/.codex/AGENTS.md" },
    { "id": "pi-system", "path": "~/.pi/agent/SYSTEM.md", "format": "pi-system" }
  ]
}
```

- `id`: identifier, used for display and single-target apply.
- `path`: target file path. Supports `~` and `${VAR:-default}`, e.g. `${MINIMAX_DATA_DIR:-~/.minimax}/AGENTS.md`.
- `format`: marker format. Defaults to the markdown comment pair; `pi-system` uses the `== ... ==` line markers.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## License

Code is licensed under [PolyForm Noncommercial 1.0.0](LICENSE): free for personal and non-commercial use; commercial use requires separate written permission.
