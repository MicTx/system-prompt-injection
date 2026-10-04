# system-prompt-injection

[简体中文](README.md) | **English**

Sync one shared prompt block from `SYSTEM_PROMPT.md` into every local agent config file. Single file, zero dependencies, with a TUI.

```bash
./prompt_sync.py          # TUI (default)
./prompt_sync.py check    # headless status (exit code 1 when targets are pending)
./prompt_sync.py apply    # headless sync
```

## Design

- `SYSTEM_PROMPT.md` is the single source; `targets.json` lists destinations (`~` and `${VAR:-default}` are allowed in paths).
- Only the marked block is managed; everything else in a target file stays untouched:

  ```text
  <!-- system-prompt-injection:shared:start -->
  ...synced content...
  <!-- system-prompt-injection:shared:end -->
  ```

  Pi's `SYSTEM.md` uses `== SYSTEM_PROMPT_INJECTION:shared:START/END ==` markers (`format: "pi-system"`).

- Target states: `ok` synced · `out` stale · `new` to insert · `skip` host not installed · `err` unreadable.
- Applying backs up changed files to `~/.config/system-prompt-injection/backups/<timestamp>/` first, then replaces them atomically via a temp file and `os.replace`; press `u` in the TUI to restore the latest backup.

## TUI keys

| Key | Action |
| --- | --- |
| `↑/↓` `j/k`, `g`/`G` | Move selection; jump to first / last row |
| `d` or Enter | Preview the diff for the selected target |
| `p` | Apply to the selected target only |
| `a` | Apply to all pending targets |
| `e` | Edit `SYSTEM_PROMPT.md` in `$EDITOR` (falls back to `VISUAL`, then `vi`) |
| `u` | Restore the latest backup |
| `r` | Rescan |
| `q` / `ESC` | Quit |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE](LICENSE). Commercial use requires separate written authorization.
