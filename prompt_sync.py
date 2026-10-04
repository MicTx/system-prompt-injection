#!/usr/bin/env python3
"""system-prompt-injection — sync one shared prompt block into agent files.

SYSTEM_PROMPT.md is the single source; targets.json lists destinations.
Only the marked block is managed, everything else in a target file is
left alone. Applying backs up first and replaces files atomically.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

try:
    import curses
except ImportError:  # curses is stdlib on Unix; headless commands still work without it
    curses = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "SYSTEM_PROMPT.md"
DEFAULT_CONFIG = ROOT / "targets.json"
DEFAULT_BACKUP_ROOT = Path.home() / ".config" / "system-prompt-injection" / "backups"

PREFIX = "system-prompt-injection"
BLOCK = "shared"
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

OK, OUT, NEW, SKIP, ERR = "ok", "out", "new", "skip", "err"


class SyncError(RuntimeError):
    """Raised when the operation cannot proceed safely."""


@dataclass(frozen=True)
class Target:
    id: str
    path: Path
    format: str = "markdown"


@dataclass(frozen=True)
class Row:
    target: Target
    state: str


@dataclass(frozen=True)
class ApplyResult:
    changed: Tuple[str, ...]
    backup_dir: Optional[Path]


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SyncError("cannot read {}: {}".format(path, exc)) from exc


def expand_path(value: str) -> Path:
    def sub(match: "re.Match[str]") -> str:
        name, default = match.group(1), match.group(2)
        env = os.environ.get(name)
        if env and env.strip():
            return env.strip()
        if default is None:
            raise SyncError("environment variable is not set: {}".format(name))
        return default

    expanded = ENV_PATTERN.sub(sub, value)
    expanded = os.path.expandvars(expanded)
    if "$" in expanded or "\x00" in expanded or not expanded:
        raise SyncError("cannot resolve target path: {}".format(value))
    return Path(os.path.abspath(os.path.expanduser(expanded)))


def load_targets(config: Path = DEFAULT_CONFIG) -> List[Target]:
    try:
        raw = json.loads(read_text(config))
    except json.JSONDecodeError as exc:
        raise SyncError("invalid config {}: {}".format(config, exc)) from exc
    items = raw.get("targets") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        raise SyncError("{}: expected a non-empty targets list".format(config))
    seen = set()
    targets: List[Target] = []
    for item in items:
        if not isinstance(item, dict):
            raise SyncError("each target must be an object")
        target_id = item.get("id")
        if not isinstance(target_id, str) or not ID_PATTERN.match(target_id):
            raise SyncError("invalid target id: {!r}".format(target_id))
        if target_id in seen:
            raise SyncError("duplicate target id: {}".format(target_id))
        seen.add(target_id)
        target_format = item.get("format", "markdown")
        if target_format not in ("markdown", "pi-system"):
            raise SyncError("unknown format for {}: {}".format(target_id, target_format))
        path_value = item.get("path")
        if not isinstance(path_value, str) or not path_value:
            raise SyncError("missing path for {}".format(target_id))
        targets.append(Target(target_id, expand_path(path_value), target_format))
    return targets


def markers(target_format: str) -> Tuple[str, str]:
    if target_format == "markdown":
        return (
            "<!-- {}:{}:start -->".format(PREFIX, BLOCK),
            "<!-- {}:{}:end -->".format(PREFIX, BLOCK),
        )
    if target_format == "pi-system":
        return (
            "== SYSTEM_PROMPT_INJECTION:{}:START ==".format(BLOCK),
            "== SYSTEM_PROMPT_INJECTION:{}:END ==".format(BLOCK),
        )
    raise SyncError("unknown format: {}".format(target_format))


def body_of(source: Path) -> str:
    content = read_text(source)
    start, end = markers("markdown")
    if content.count(start) != 1 or content.count(end) != 1:
        raise SyncError("{} must hold exactly one managed block".format(source))
    body = content[content.index(start) + len(start) : content.index(end)].strip()
    if not body:
        raise SyncError("managed block in {} is empty".format(source))
    return body + "\n"


def render_block(target_format: str, body: str) -> str:
    start, end = markers(target_format)
    return "{}\n{}\n{}\n".format(start, body.strip(), end)


def block_span(content: str, target_format: str) -> Optional[Tuple[int, int]]:
    start, end = markers(target_format)
    starts = [m.start() for m in re.finditer(re.escape(start), content)]
    ends = [m.start() for m in re.finditer(re.escape(end), content)]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise SyncError("malformed or duplicated managed block")
    return starts[0], ends[0] + len(end)


def state_of(content: str, target_format: str, body: str) -> str:
    try:
        span = block_span(content, target_format)
    except SyncError:
        return ERR
    if span is None:
        return NEW
    start, end = markers(target_format)
    inner = content[span[0] + len(start) : span[1] - len(end)]
    return OK if inner.strip() + "\n" == body.strip() + "\n" else OUT


def next_content(content: str, target_format: str, body: str) -> str:
    block = render_block(target_format, body)
    span = block_span(content, target_format)
    if span is None:
        separator = "" if not content or content.endswith("\n\n") else "\n"
        return content + separator + block
    tail = content[span[1]:]
    if tail.startswith("\n"):
        tail = tail[1:]
    return content[: span[0]] + block + tail


def scan(targets: Sequence[Target], source: Path) -> List[Row]:
    body = body_of(source)
    rows: List[Row] = []
    for target in targets:
        if not os.path.lexists(target.path):
            rows.append(Row(target, NEW if target.path.parent.is_dir() else SKIP))
        elif target.path.is_symlink() or not target.path.is_file():
            rows.append(Row(target, ERR))
        else:
            try:
                state = state_of(read_text(target.path), target.format, body)
            except SyncError:
                state = ERR
            rows.append(Row(target, state))
    return rows


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".prompt-sync-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, str(path))
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def apply(
    targets: Sequence[Target],
    source: Path,
    backup_root: Path = DEFAULT_BACKUP_ROOT,
) -> ApplyResult:
    body = body_of(source)
    plans: List[Tuple[Target, bytes, bytes, int]] = []
    for target in targets:
        if not os.path.lexists(target.path):
            if not target.path.parent.is_dir():
                continue
            old, mode, content = b"", 0o644, ""
        else:
            if target.path.is_symlink() or not target.path.is_file():
                raise SyncError("{}: not a regular file: {}".format(target.id, target.path))
            old = target.path.read_bytes()
            try:
                content = old.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SyncError("{}: not valid UTF-8".format(target.path)) from exc
            mode = target.path.stat().st_mode & 0o777
        new = next_content(content, target.format, body).encode("utf-8")
        if new != old:
            plans.append((target, old, new, mode))

    if not plans:
        return ApplyResult((), None)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = Path(backup_root) / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(backup_dir, 0o700)
    manifest = {"created": datetime.now(timezone.utc).isoformat(), "targets": {}}
    for target, old, _, mode in plans:
        copy = backup_dir / target.id
        copy.write_bytes(old)
        os.chmod(copy, 0o600)
        manifest["targets"][target.id] = {
            "path": str(target.path),
            "mode": mode,
        }
    _atomic_write(
        backup_dir / "manifest.json",
        (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
        0o600,
    )

    written: List[Tuple[Target, bytes, int]] = []
    try:
        for target, old, new, mode in plans:
            _atomic_write(target.path, new, mode)
            written.append((target, old, mode))
    except BaseException:
        for target, old, mode in written:
            try:
                _atomic_write(target.path, old, mode)
            except OSError:
                pass
        raise
    return ApplyResult(tuple(target.id for target, _, _, _ in plans), backup_dir)


def latest_backup(backup_root: Path = DEFAULT_BACKUP_ROOT) -> Optional[Path]:
    root = Path(backup_root)
    if not root.is_dir():
        return None
    dirs = sorted(entry for entry in root.iterdir() if entry.is_dir())
    return dirs[-1] if dirs else None


def undo(backup_dir: Path) -> List[str]:
    try:
        manifest = json.loads(read_text(backup_dir / "manifest.json"))
        entries = manifest["targets"]
    except (SyncError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SyncError("invalid backup manifest in {}: {}".format(backup_dir, exc)) from exc
    if not isinstance(entries, dict) or not entries:
        raise SyncError("backup manifest lists no targets: {}".format(backup_dir))
    restored: List[str] = []
    for target_id, entry in sorted(entries.items()):
        path = Path(entry["path"])
        copy = backup_dir / target_id
        if not copy.is_file():
            raise SyncError("backup file missing: {}".format(copy))
        _atomic_write(path, copy.read_bytes(), int(entry.get("mode", 0o644)))
        restored.append(target_id)
    return restored


# ---------------------------------------------------------------- TUI


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def _abbrev_home(path: Path) -> str:
    text = str(path)
    home = str(Path.home())
    if text.startswith(home + os.sep):
        return "~" + text[len(home):]
    return text


def _summarize(ids: Sequence[str], limit: int = 3) -> str:
    names = list(ids)
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + " +{} more".format(len(names) - limit)


STATE_COLOR = {OK: 1, OUT: 2, NEW: 4, ERR: 3, SKIP: 0}
STATE_DIM = {SKIP}


def _state_attr(state: str, colors: bool) -> int:
    attr = curses.A_DIM if state in STATE_DIM else curses.A_BOLD
    if colors:
        pair = STATE_COLOR.get(state)
        if pair:
            attr |= curses.color_pair(pair)
    return attr


def _diff_for(row: Row, body: str) -> List[str]:
    if row.state in (SKIP, ERR):
        return []
    current = read_text(row.target.path) if os.path.lexists(row.target.path) else ""
    new = next_content(current, row.target.format, body)
    return list(
        difflib.unified_diff(
            current.splitlines(),
            new.splitlines(),
            fromfile="current",
            tofile="next",
            lineterm="",
        )
    )


def _draw_list(stdscr, rows, sel, top, message, colors, source_name):
    height, width = stdscr.getmaxyx()
    id_w = min(30, max(16, max((len(row.target.id) for row in rows), default=16)))
    stdscr.addnstr(0, 0, "system-prompt-injection", width - 1, curses.A_BOLD)
    ok_count = sum(1 for row in rows if row.state == OK)
    stdscr.addnstr(
        1,
        0,
        _clip(
            "{} · {} targets · {}/{} in sync".format(
                source_name, len(rows), ok_count, len(rows)
            ),
            width - 1,
        ),
        width - 1,
        curses.A_DIM,
    )
    stdscr.addnstr(
        3,
        0,
        _clip("{:<{w}} {:<4} {}".format("ID", "ST", "PATH", w=id_w), width - 1),
        width - 1,
        curses.A_UNDERLINE,
    )
    visible = max(1, height - 7)
    for offset, row in enumerate(rows[top : top + visible]):
        y = 4 + offset
        line = "{:<{w}} {:<4} {}".format(
            _clip(row.target.id, id_w), row.state, _abbrev_home(row.target.path), w=id_w
        )
        attr = _state_attr(row.state, colors)
        if offset + top == sel:
            attr = curses.A_REVERSE | (attr & ~curses.A_DIM)
        stdscr.addnstr(y, 0, _clip(line, width - 1), width - 1, attr)
    if message:
        stdscr.addnstr(height - 2, 0, _clip(message, width - 1), width - 1)
    stdscr.addnstr(
        height - 1,
        0,
        "↑/↓ select · d diff · p this · a all · e edit · u undo · r refresh · q quit",
        width - 1,
        curses.A_DIM,
    )


def _draw_diff(stdscr, title, lines, top, colors):
    height, width = stdscr.getmaxyx()
    stdscr.addnstr(0, 0, _clip(title, width - 1), width - 1, curses.A_BOLD)
    for index, line in enumerate(lines[top : top + height - 3]):
        attr = curses.A_NORMAL
        if line.startswith(("---", "+++", "@@")):
            attr = curses.A_DIM
        elif colors and line.startswith("+"):
            attr = curses.color_pair(1)
        elif colors and line.startswith("-"):
            attr = curses.color_pair(3)
        stdscr.addnstr(2 + index, 0, _clip(line, width - 1), width - 1, attr)
    stdscr.addnstr(
        height - 1, 0, "↑/↓ scroll · q back", width - 1, curses.A_DIM
    )


def _confirm(stdscr, prompt: str) -> bool:
    height, width = stdscr.getmaxyx()
    stdscr.addnstr(height - 2, 0, _clip(prompt, width - 1), width - 1, curses.A_BOLD)
    stdscr.refresh()
    while True:
        key = stdscr.getch()
        if key in (ord("y"), ord("Y")):
            return True
        if key in (ord("n"), ord("N"), 27, ord("q")):
            return False


def _edit_source(stdscr, source: Path) -> None:
    """Open the authored source in $EDITOR (VISUAL, then vi), then return to curses."""
    import shlex
    import subprocess

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    try:
        argv = shlex.split(editor) + [str(source)]
    except ValueError as exc:
        raise SyncError("invalid EDITOR {!r}".format(editor)) from exc
    curses.def_prog_mode()
    curses.endwin()
    try:
        if subprocess.call(argv) != 0:
            raise SyncError("editor exited with an error")
    finally:
        curses.reset_prog_mode()
        stdscr.clear()  # the editor dirtied the screen; force a full repaint


def _run_tui(stdscr, targets: Sequence[Target], source: Path, backup_root: Path) -> int:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)
    colors = False
    if curses.has_colors():
        try:
            curses.start_color()
            curses.use_default_colors()
            for pair, color in ((1, curses.COLOR_GREEN), (2, curses.COLOR_YELLOW), (3, curses.COLOR_RED), (4, curses.COLOR_CYAN)):
                curses.init_pair(pair, color, -1)
            colors = True
        except curses.error:
            colors = False

    rows = scan(targets, source)
    sel = top = 0
    message = ""
    diff_lines: List[str] = []
    diff_title = ""
    diff_top = 0
    in_diff = False

    def _rescan() -> Optional[str]:
        """Refresh rows; return an error message or None."""
        nonlocal rows, sel
        try:
            rows = scan(targets, source)
            sel = min(sel, max(0, len(rows) - 1))
            return None
        except SyncError as exc:
            return str(exc)

    while True:
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        if height < 8 or width < 20:
            stdscr.addnstr(0, 0, "terminal too small", max(width - 1, 1), curses.A_BOLD)
        elif in_diff:
            diff_top = max(0, min(diff_top, max(0, len(diff_lines) - 1)))
            _draw_diff(stdscr, diff_title, diff_lines, diff_top, colors)
        else:
            visible = max(1, height - 7)
            top = max(0, min(top, len(rows) - visible))
            if sel < top:
                top = sel
            if sel >= top + visible:
                top = sel - visible + 1
            _draw_list(stdscr, rows, sel, top, message, colors, source.name)
        stdscr.refresh()

        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            continue
        if in_diff:
            if key in (ord("q"), 27, ord("d"), 10, curses.KEY_ENTER):
                in_diff = False
            elif key in (curses.KEY_UP, ord("k")):
                diff_top = max(0, diff_top - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                diff_top += 1
            continue

        if key in (ord("q"), 27):
            return 0
        rows_count = len(rows)
        if key in (curses.KEY_UP, ord("k")) and rows_count:
            sel = (sel - 1) % rows_count
        elif key in (curses.KEY_DOWN, ord("j")) and rows_count:
            sel = (sel + 1) % rows_count
        elif key == ord("g") and rows_count:
            sel = 0
        elif key == ord("G") and rows_count:
            sel = rows_count - 1
        elif key == ord("r"):
            message = _rescan() or ""
        elif key in (ord("d"), 10, curses.KEY_ENTER) and rows_count:
            row = rows[sel]
            if row.state in (SKIP, ERR):
                message = "{}: {}".format(
                    row.target.id,
                    "not installed" if row.state == SKIP else "unreadable",
                )
            elif row.state == OK:
                message = "{}: in sync".format(row.target.id)
            else:
                try:
                    diff_lines = _diff_for(row, body_of(source))
                except SyncError as exc:
                    message = str(exc)
                    continue
                diff_title = "diff · {} · {} lines".format(
                    row.target.id, len(diff_lines)
                )
                diff_top = 0
                in_diff = True
        elif key == ord("p") and rows_count:
            row = rows[sel]
            if row.state == OK:
                message = "{}: in sync".format(row.target.id)
            elif row.state in (SKIP, ERR):
                message = "{}: {}".format(
                    row.target.id,
                    "not installed" if row.state == SKIP else "unreadable",
                )
            elif _confirm(stdscr, "apply to {}? y/n".format(row.target.id)):
                try:
                    result = apply([row.target], source, backup_root)
                except (SyncError, OSError) as exc:
                    message = str(exc)
                    continue
                message = _rescan() or "updated {} · backup {}".format(
                    _summarize(result.changed), _abbrev_home(result.backup_dir)
                )
            else:
                message = ""
        elif key == ord("a"):
            pending = sum(1 for row in rows if row.state in (OUT, NEW))
            if not pending:
                message = "nothing to apply"
            elif _confirm(stdscr, "apply to {} target(s)? y/n".format(pending)):
                try:
                    result = apply(targets, source, backup_root)
                except (SyncError, OSError) as exc:
                    message = str(exc)
                    continue
                message = _rescan() or "updated {} ({}) · backup {}".format(
                    len(result.changed),
                    _summarize(result.changed),
                    _abbrev_home(result.backup_dir),
                )
            else:
                message = ""
        elif key == ord("u"):
            backup_dir = latest_backup(backup_root)
            if backup_dir is None:
                message = "no backups in {}".format(_abbrev_home(Path(backup_root)))
            elif _confirm(stdscr, "restore latest backup {}? y/n".format(backup_dir.name)):
                try:
                    restored = undo(backup_dir)
                except (SyncError, OSError) as exc:
                    message = str(exc)
                    continue
                message = _rescan() or "restored {}".format(_summarize(restored))
            else:
                message = ""
        elif key == ord("e"):
            try:
                _edit_source(stdscr, source)
            except (SyncError, OSError) as exc:
                message = str(exc)
                continue
            error = _rescan()
            if error:
                message = "source invalid after edit: {}".format(error)
            else:
                message = "edited {} · {} pending".format(
                    source.name, sum(1 for row in rows if row.state in (OUT, NEW))
                )


def tui(targets: Sequence[Target], source: Path, backup_root: Path) -> int:
    if curses is None:
        raise SyncError("curses is required for the TUI; use check/apply instead")
    try:
        return curses.wrapper(_run_tui, targets, source, backup_root)
    except curses.error as exc:
        raise SyncError("terminal does not support curses: {}".format(exc)) from exc


# ---------------------------------------------------------------- CLI


def _print_rows(rows: List[Row]) -> int:
    for row in rows:
        print("{:>4}  {:<24} {}".format(row.state, row.target.id, row.target.path))
    pending = sum(1 for row in rows if row.state in (OUT, NEW, ERR))
    print("{}/{} in sync".format(sum(1 for row in rows if row.state == OK), len(rows)))
    return 1 if pending else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prompt_sync",
        description="Sync the shared prompt block into local agent files (TUI by default)",
    )
    parser.add_argument("command", nargs="?", choices=("tui", "check", "apply"), default="tui")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    args = parser.parse_args(argv)

    try:
        targets = load_targets(args.config)
        if args.command == "check":
            return _print_rows(scan(targets, args.source))
        if args.command == "apply":
            result = apply(targets, args.source, args.backup_root)
            if not result.changed:
                print("already in sync")
            else:
                print("updated {}: {}".format(len(result.changed), ", ".join(result.changed)))
                print("backup: {}".format(result.backup_dir))
            return 0
        return tui(targets, args.source, args.backup_root)
    except (SyncError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
