#!/usr/bin/env python3
"""Synchronize one authored prompt into agent-specific managed blocks.

The synchronizer is deliberately dependency-free and fail-closed. It only
writes target paths declared by targets.json, preserves all text outside a
managed block, and updates CC Switch's prompts table without touching provider,
credential, skill, or MCP tables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "SYSTEM_PROMPT.md"
DEFAULT_CONFIG = PROJECT_ROOT / "targets.json"
DEFAULT_BACKUP_PARENT = Path.home() / ".config" / "agent-harness" / "prompt-backups"

MARKER_PREFIX = "system-prompt-injection"
CC_SWITCH_PROMPT_ID = "system-prompt-injection-shared"
CC_SWITCH_PROMPT_NAME = "Shared System Prompt (managed)"
CC_SWITCH_PROMPT_DESCRIPTION = (
    "Managed by system-prompt-injection; edit SYSTEM_PROMPT.md instead."
)
# CC Switch's prompt service accepts these app types. Pi uses a native-file
# projection and MCode has a 32 KiB content limit in the host implementation.
CC_SWITCH_APPS = (
    "claude",
    "codex",
    "gemini",
    "grokbuild",
    "opencode",
    "openclaw",
    "hermes",
    "pi",
    "mcode",
)
CC_SWITCH_TARGET_ALIASES = {"pi": "pi-agents"}

LEGACY_SYSTEM_MARKDOWN = """## 系统级提交与推送规则

- 默认真源、提交合并和推送目标是私有 Gitea `gitea.example.invalid`；先按远端 URL 判断，不把 `origin` 名称当作真源证据。
- 没有 Gitea 远端时停止并报告，不得默认推 GitHub 或其他公网远端。
- GitHub 只有在用户对具体项目和本次操作明确授权后才可同步；Gitea 必须先推送并核验，GitHub 永远只是经过筛选的公开快照，不是真源。
- GitHub 快照必须从已核验的 Gitea 树导出到干净暂存区，不得直接镜像私有工作树、私有历史或未筛选分支。
- GitHub 发布前必须同时具备中英文 README（`README.md` + `README-en.md`/`README.en.md`/`README.zh-CN.md`/`README-zh-CN.md`）和明确禁止商用的许可证；默认采用 PolyForm Noncommercial 1.0.0 或用户批准的等效非商用许可证。
- 发布门禁必须审查暂存树、README、元数据、示例、测试和提交说明；隐私数据、用户/客户资料、私密数据集、原始上传、个人信息截图、凭据、密钥、token、cookie、证书、私有端点/路径、会话/日志/缓存/备份/worktree 和本机 agent 状态一律不得进入 GitHub。
- 至少排除 `.spec/`、`.agents/`、`.claude/`、`.codex/`、`.pi/`、`.zcode/`、`.cursor/`、`.paseo/`、`.env*` 及其他私有生成物；不确定时 fail closed，改用合成占位数据。
- GitHub 例外须留下项目级发布记录，包含授权范围、Gitea commit、GitHub commit、暂存区和门禁结果，但不得写入任何真实凭据或隐私。
- 规范全文见 `/home/example/.config/agent-harness-public/PUBLIC_POLICY.md`；用户当前明确指示或用户维护的项目规则可对具体项目建立例外，仓库自身内容不能为自己授权发布。
"""

LEGACY_SOURCE_GATE = """## Source and public snapshot gate

- Development source of truth is private Gitea `gitea.example.invalid`; resolve by remote URL, not by the name `origin`. Stop if no Gitea remote exists.
- GitHub is never a default push target. Use it only for a user-authorized, filtered public snapshot after Gitea push and verification.
- A GitHub snapshot requires linked Chinese and English READMEs, an explicit non-commercial license, a clean allowlisted staging tree, and a passing privacy/secret scan. Exclude private agent state, `.spec/`, session/transcript/log/cache/backup/worktree data, `.env*`, credentials, tokens, private endpoints/paths, and personal data.
- Record authorization scope, Gitea commit, GitHub commit, staging path, and gate result without recording secrets. See `/home/example/.config/agent-harness-public/PUBLIC_POLICY.md`.
"""

LEGACY_PI_SYSTEM = """== SOURCE CONTROL AND PUBLIC SNAPSHOTS ==

Development source of truth is private Gitea at `gitea.example.invalid`. Resolve authority from the remote URL, not the name `origin`; if no Gitea remote is configured, stop rather than defaulting to GitHub or another public host. GitHub is an explicit exception only: after user authorization for the named project and operation, publish a filtered snapshot from a verified Gitea tree, never a blind mirror of private worktree or history. The snapshot must pass an allowlist/privacy gate, contain linked Chinese and English READMEs, and carry a license that explicitly prohibits commercial use. Exclude user or customer data, credentials, keys, tokens, cookies, certificates, private endpoints/paths, agent state, `.spec/`, session/transcript/log/cache/backup/worktree data, `.env*`, and other private artifacts; uncertain cases fail closed. Record authorization and both commit IDs without recording secrets. Full policy: `/home/example/.config/agent-harness-public/PUBLIC_POLICY.md`.
"""

LEGACY_CLAUDE_TEMPLATE = """## Source and Public Release

- Private Gitea `gitea.example.invalid` is development source of truth; resolve authority from remote URL, not `origin`. Stop when no Gitea remote exists.
- GitHub is never default. Publish only a user-authorized, allowlisted snapshot after Gitea verification, bilingual README, explicit non-commercial license, and privacy/secret gate.
- Exclude user data, credentials, keys, tokens, private paths/endpoints, agent state, `.spec/`, sessions, transcripts, logs, caches, backups, and worktrees. See `/home/example/.config/agent-harness-public/PUBLIC_POLICY.md`.
"""

LEGACY_ORCHESTRATION_LINE = "提交与发布：开发真源固定为私有 Gitea `gitea.example.invalid`，按远端 URL 判断，不把 `origin` 当作授权证据；无 Gitea 远端时停止。GitHub 仅在用户明确授权后接收从已核验 Gitea 树导出的公开快照，且必须通过 allowlist、隐私/密钥清理、中英文 README 和明确禁止商用许可证门禁；不得上传 `.spec/`、agent 状态、会话/日志/缓存/备份/worktree、凭据、token、私有端点/路径或用户隐私。详见 `/home/example/.config/agent-harness-public/PUBLIC_POLICY.md`。"


class SyncError(RuntimeError):
    """Raised when synchronization cannot proceed safely."""


@dataclass(frozen=True)
class Target:
    id: str
    path: Path
    format: str
    block: str = "shared"
    required: bool = True
    anchor: Optional[str] = None
    enabled: bool = True
    create_missing: bool = True
    create_parent: bool = True
    max_bytes: Optional[int] = None


@dataclass(frozen=True)
class Config:
    targets: Tuple[Target, ...]


@dataclass(frozen=True)
class CheckReport:
    ok: bool
    clean: Tuple[str, ...]
    drifted: Tuple[str, ...]
    missing: Tuple[str, ...]
    conflicts: Tuple[str, ...]
    skipped: Tuple[str, ...]


@dataclass(frozen=True)
class ApplyReport:
    updated: int
    unchanged: int
    backup_dir: Optional[Path]
    changed: Tuple[str, ...]


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SyncError("target is not valid UTF-8: {}".format(path)) from exc
    except OSError as exc:
        raise SyncError("cannot read {}: {}".format(path, exc)) from exc


_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _expand_braced_env(value: str) -> str:
    """Expand `${VAR}` and nested `${VAR:-default}` expressions safely."""
    result: List[str] = []
    index = 0
    while index < len(value):
        if not value.startswith("${", index):
            result.append(value[index])
            index += 1
            continue

        start = index + 2
        cursor = start
        depth = 1
        while cursor < len(value) and depth:
            if value.startswith("${", cursor):
                depth += 1
                cursor += 2
            elif value[cursor] == "}":
                depth -= 1
                cursor += 1
            else:
                cursor += 1
        if depth:
            raise SyncError("unclosed environment expression in target path")

        expression = value[start : cursor - 1]
        if ":-" in expression:
            name, default = expression.split(":-", 1)
        else:
            name, default = expression, None
        if not _ENV_NAME.fullmatch(name):
            raise SyncError(
                "invalid environment variable in target path: {}".format(name)
            )
        raw_replacement = os.environ.get(name)
        replacement = raw_replacement.strip() if raw_replacement else None
        if not replacement:
            if default is None:
                raise SyncError("environment variable is not set: {}".format(name))
            replacement = _expand_braced_env(default)
        result.append(replacement)
        index = cursor
    return "".join(result)


def _expand_path(value: str) -> Path:
    if not value or "\x00" in value:
        raise SyncError("invalid target path")
    expanded = _expand_braced_env(value)
    expanded = os.path.expandvars(expanded)
    if not expanded or "\x00" in expanded:
        raise SyncError("invalid expanded target path: {}".format(value))
    if "$" in expanded:
        raise SyncError(
            "unresolved environment variable in target path: {}".format(value)
        )
    return Path(os.path.abspath(os.path.expanduser(expanded)))


def _lexists(path: Path) -> bool:
    return os.path.lexists(str(path))


def load_config(path: Path = DEFAULT_CONFIG) -> Config:
    try:
        raw = json.loads(_read_utf8(path))
    except json.JSONDecodeError as exc:
        raise SyncError("invalid targets config {}: {}".format(path, exc)) from exc
    if (
        not isinstance(raw, dict)
        or raw.get("version") != 1
        or not isinstance(raw.get("targets"), list)
    ):
        raise SyncError("targets config must contain version 1 and a targets list")
    seen = set()
    targets: List[Target] = []
    for item in raw["targets"]:
        if not isinstance(item, dict):
            raise SyncError("each target must be an object")
        target_id = item.get("id")
        if not isinstance(target_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*", target_id
        ):
            raise SyncError("invalid target id: {}".format(target_id))
        if target_id in seen:
            raise SyncError("duplicate target id: {}".format(target_id))
        seen.add(target_id)
        target_format = item.get("format", "markdown")
        if target_format not in ("markdown", "pi-system"):
            raise SyncError(
                "unsupported target format for {}: {}".format(target_id, target_format)
            )
        block = item.get("block", "shared")
        if not isinstance(block, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*", block
        ):
            raise SyncError("invalid block for {}".format(target_id))
        path_value = item.get("path")
        if not isinstance(path_value, str):
            raise SyncError("path must be a string for {}".format(target_id))
        anchor = item.get("anchor")
        if anchor is not None and not isinstance(anchor, str):
            raise SyncError("anchor must be a string for {}".format(target_id))
        max_bytes = item.get("max_bytes")
        if max_bytes is not None and (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
        ):
            raise SyncError(
                "max_bytes must be a positive integer for {}".format(target_id)
            )
        targets.append(
            Target(
                id=target_id,
                path=_expand_path(path_value),
                format=target_format,
                block=block,
                required=bool(item.get("required", True)),
                anchor=anchor,
                enabled=bool(item.get("enabled", True)),
                create_missing=bool(item.get("create_missing", True)),
                create_parent=bool(item.get("create_parent", True)),
                max_bytes=max_bytes,
            )
        )
    if not targets:
        raise SyncError("targets config has no targets")
    return Config(tuple(targets))


def _markers(target_format: str, block: str) -> Tuple[str, str]:
    if target_format == "markdown":
        return (
            "<!-- {}:{}:start -->".format(MARKER_PREFIX, block),
            "<!-- {}:{}:end -->".format(MARKER_PREFIX, block),
        )
    if target_format == "pi-system":
        return (
            "== SYSTEM_PROMPT_INJECTION:{}:START ==".format(block),
            "== SYSTEM_PROMPT_INJECTION:{}:END ==".format(block),
        )
    raise SyncError("unsupported target format: {}".format(target_format))


def render_block(target_format: str, body: str, block: str = "shared") -> str:
    start, end = _markers(target_format, block)
    normalized = body.strip() + "\n"
    return start + "\n" + normalized + end + "\n"


def extract_source_body(source: Path, block: str = "shared") -> str:
    content = _read_utf8(source)
    start, end = _markers("markdown", block)
    if content.count(start) != 1 or content.count(end) != 1:
        raise SyncError(
            "source must contain exactly one shared managed block: {}".format(source)
        )
    start_pos = content.index(start) + len(start)
    end_pos = content.index(end)
    if end_pos < start_pos:
        raise SyncError("source managed markers are out of order: {}".format(source))
    body = content[start_pos:end_pos].strip()
    if not body:
        raise SyncError("source managed block is empty: {}".format(source))
    return body + "\n"


def _remove_exact(content: str, fragment: str) -> str:
    return content.replace(fragment, "")


def remove_legacy_fragments(content: str) -> str:
    """Remove only additions made by the pre-single-source migration."""
    content = _remove_exact(content, LEGACY_SYSTEM_MARKDOWN)
    content = _remove_exact(content, LEGACY_SOURCE_GATE)
    content = _remove_exact(content, LEGACY_PI_SYSTEM)
    content = _remove_exact(content, LEGACY_CLAUDE_TEMPLATE)
    content = re.sub(
        r"(?m)^" + re.escape(LEGACY_ORCHESTRATION_LINE) + r"\r?\n?",
        "",
        content,
    )
    content = re.sub(
        r"(?m)^6\. \*\*Source of Truth\*\* — Commit and push to private Gitea `gitea\.example\.invalid`; resolve by remote URL, not `origin`\. Stop if no Gitea remote exists\.\n?",
        "",
        content,
    )
    content = re.sub(
        r"(?m)^7\. \*\*Public Snapshot Gate\*\* — GitHub only after explicit project authorization, from a clean allowlisted snapshot with linked Chinese/English READMEs, an explicit non-commercial license, and privacy/secret review\. Never include user data, credentials, tokens, private paths/endpoints, agent state, sessions, logs, caches, backups, or worktrees\. See `/home/example/\.config/agent-harness-public/PUBLIC_POLICY\.md`\.\n?",
        "",
        content,
    )
    return content


def _managed_region(
    content: str, target: Target
) -> Tuple[Optional[int], Optional[int], str, str]:
    start, end = _markers(target.format, target.block)
    starts = [match.start() for match in re.finditer(re.escape(start), content)]
    ends = [match.start() for match in re.finditer(re.escape(end), content)]
    if len(starts) > 1 or len(ends) > 1:
        raise SyncError("duplicate managed block markers in {}".format(target.path))
    if not starts and not ends:
        return None, None, start, end
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise SyncError("malformed managed block in {}".format(target.path))
    return starts[0], ends[0] + len(end), start, end


def _replace_target(content: str, target: Target, body: str) -> Tuple[str, bool]:
    migrated = remove_legacy_fragments(content)
    start_pos, end_pos, _, _ = _managed_region(migrated, target)
    block = render_block(target.format, body, target.block)
    if start_pos is not None and end_pos is not None:
        suffix = migrated[end_pos:]
        if suffix.startswith("\n"):
            suffix = suffix[1:]
        new_content = migrated[:start_pos] + block + suffix
        return new_content, new_content != content
    if target.anchor:
        anchor_pos = migrated.find(target.anchor)
        if anchor_pos >= 0:
            insertion = "\n" + block
            insert_at = anchor_pos
            return migrated[:insert_at] + insertion + migrated[
                insert_at:
            ], migrated != content or True
    separator = "" if not migrated or migrated.endswith("\n\n") else "\n"
    return migrated + separator + block, True


def _target_state(content: str, target: Target, expected_body: str) -> str:
    if target.max_bytes is not None and len(content.encode("utf-8")) > target.max_bytes:
        return "conflict"
    if remove_legacy_fragments(content) != content:
        return "drifted"
    try:
        start_pos, end_pos, start, end = _managed_region(content, target)
    except SyncError:
        return "conflict"
    if start_pos is None or end_pos is None:
        return "missing"
    body_start = start_pos + len(start)
    body_end = end_pos - len(end)
    actual = content[body_start:body_end].strip() + "\n"
    return "clean" if actual == expected_body else "drifted"


def check(config: Config, source: Path) -> CheckReport:
    body = extract_source_body(source)
    clean: List[str] = []
    drifted: List[str] = []
    missing: List[str] = []
    conflicts: List[str] = []
    skipped: List[str] = []
    for target in config.targets:
        if not target.enabled:
            continue
        if not _lexists(target.path):
            if target.required:
                missing.append(target.id)
            elif not target.create_missing or (
                not target.create_parent and not target.path.parent.is_dir()
            ):
                skipped.append(target.id)
            else:
                missing.append(target.id)
            continue
        if target.path.is_symlink():
            conflicts.append(target.id)
            continue
        state = _target_state(_read_utf8(target.path), target, body)
        if state == "clean":
            clean.append(target.id)
        elif state == "missing":
            missing.append(target.id)
        elif state == "drifted":
            drifted.append(target.id)
        else:
            conflicts.append(target.id)
    return CheckReport(
        ok=not drifted and not missing and not conflicts,
        clean=tuple(clean),
        drifted=tuple(drifted),
        missing=tuple(missing),
        conflicts=tuple(conflicts),
        skipped=tuple(skipped),
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".system-prompt-injection-", dir=str(path.parent)
    )
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


def _new_backup_dir(backup_root: Optional[Path]) -> Path:
    if backup_root is not None:
        return backup_root.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return DEFAULT_BACKUP_PARENT / stamp


def apply(
    config: Config, source: Path, backup_root: Optional[Path] = None
) -> ApplyReport:
    body = extract_source_body(source)
    plans: List[Tuple[Target, bytes, bytes, int]] = []
    for target in config.targets:
        if not target.enabled:
            continue
        if not _lexists(target.path):
            if target.required:
                raise SyncError(
                    "required target does not exist: {}".format(target.path)
                )
            if not target.create_missing or (
                not target.create_parent and not target.path.parent.is_dir()
            ):
                continue
            old = b""
            mode = 0o644
            content = ""
        else:
            if target.path.is_symlink() or not target.path.is_file():
                raise SyncError(
                    "target must be a regular file, not a symlink: {}".format(
                        target.path
                    )
                )
            old = target.path.read_bytes()
            try:
                content = old.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SyncError(
                    "target is not valid UTF-8: {}".format(target.path)
                ) from exc
            mode = target.path.stat().st_mode & 0o777
        new_content, _ = _replace_target(content, target, body)
        new = new_content.encode("utf-8")
        if target.max_bytes is not None and len(new) > target.max_bytes:
            raise SyncError(
                "target exceeds its {} byte limit: {}".format(
                    target.max_bytes, target.path
                )
            )
        plans.append((target, old, new, mode))

    changed = [
        (target, old, new, mode) for target, old, new, mode in plans if old != new
    ]
    if not changed:
        return ApplyReport(0, len(plans), None, tuple())

    destination = _new_backup_dir(backup_root)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(destination, 0o700)
    manifest = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source.resolve()),
        "targets": [],
    }
    try:
        for target, old, new, mode in changed:
            backup_name = target.id + ".before"
            backup_path = destination / backup_name
            backup_path.write_bytes(old)
            os.chmod(backup_path, 0o600)
            manifest["targets"].append(
                {
                    "id": target.id,
                    "path": str(target.path),
                    "backup": backup_name,
                    "before_sha256": _sha256_bytes(old),
                    "after_sha256": _sha256_bytes(new),
                    "mode": mode,
                }
            )
        _atomic_write(
            destination / "manifest.json",
            (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
            0o600,
        )
        for target, _, new, mode in changed:
            _atomic_write(target.path, new, mode)
    except BaseException:
        # Restore any targets already replaced, then remove the incomplete backup.
        for target, old, _, mode in changed:
            if target.path.exists() and target.path.read_bytes() != old:
                _atomic_write(target.path, old, mode)
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return ApplyReport(
        len(changed),
        len(plans) - len(changed),
        destination,
        tuple(target.id for target, _, _, _ in changed),
    )


def rollback(config: Config, backup_root: Path, force: bool = False) -> Tuple[str, ...]:
    manifest_path = backup_root / "manifest.json"
    if not manifest_path.is_file():
        raise SyncError("backup manifest not found: {}".format(manifest_path))
    try:
        manifest = json.loads(_read_utf8(manifest_path))
    except json.JSONDecodeError as exc:
        raise SyncError("invalid backup manifest: {}".format(exc)) from exc
    targets_by_id = {target.id: target for target in config.targets}
    entries = manifest.get("targets")
    if not isinstance(entries, list):
        raise SyncError("backup manifest has no target list")
    for entry in entries:
        target = targets_by_id.get(entry.get("id"))
        if target is None or str(target.path) != entry.get("path"):
            raise SyncError(
                "backup target no longer matches config: {}".format(entry.get("id"))
            )
        if not target.path.is_file() or target.path.is_symlink():
            raise SyncError(
                "cannot rollback missing or unsafe target: {}".format(target.path)
            )
        current_sha = _sha256_bytes(target.path.read_bytes())
        if not force and current_sha != entry.get("after_sha256"):
            raise SyncError(
                "target changed after apply; refusing rollback: {}".format(target.path)
            )
        backup_name = entry.get("backup")
        if not isinstance(backup_name, str) or not backup_name:
            raise SyncError("backup manifest has an invalid backup name")
        backup_path = backup_root / backup_name
        if backup_path.parent != backup_root or backup_path.name != backup_name:
            raise SyncError(
                "backup path escapes backup directory: {}".format(backup_name)
            )
        if not backup_path.is_file() or backup_path.is_symlink():
            raise SyncError("backup file missing or unsafe: {}".format(backup_path))
    restored: List[str] = []
    for entry in entries:
        target = targets_by_id[entry["id"]]
        data = (backup_root / entry["backup"]).read_bytes()
        _atomic_write(target.path, data, int(entry.get("mode", 0o644)))
        restored.append(target.id)
    return tuple(restored)


def _ensure_cc_switch_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'prompts'"
    ).fetchone()
    if row is None:
        raise SyncError("CC Switch database has no prompts table")


def sync_cc_switch(
    database: Path,
    source: Path,
    config: Optional[Config] = None,
    enable: bool = False,
    app_types: Optional[Sequence[str]] = None,
) -> Tuple[str, ...]:
    if not database.is_file():
        raise SyncError("CC Switch database does not exist: {}".format(database))
    body = extract_source_body(source)
    config = config or load_config()
    target_by_id = {target.id: target for target in config.targets if target.enabled}

    def target_for_app(app_type: str) -> Optional[Target]:
        target_id = CC_SWITCH_TARGET_ALIASES.get(app_type, app_type)
        return target_by_id.get(target_id)

    if app_types is None:
        app_types = tuple(
            app
            for app in CC_SWITCH_APPS
            if (
                (target := target_for_app(app)) is not None
                and target.path.is_file()
                and not target.path.is_symlink()
            )
        )
        if not app_types:
            raise SyncError("no installed CC Switch prompt targets are available")
    else:
        app_types = tuple(app_types)

    contents = {}
    invalid_apps = [
        app
        for app in app_types
        if not isinstance(app, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", app)
    ]
    if invalid_apps:
        raise SyncError(
            "invalid CC Switch app type: {}".format(
                ", ".join(str(app) for app in invalid_apps)
            )
        )
    for app_type in app_types:
        target = target_for_app(app_type)
        if target is None or not target.path.is_file() or target.path.is_symlink():
            raise SyncError("CC Switch target is unavailable: {}".format(app_type))
        if _target_state(_read_utf8(target.path), target, body) != "clean":
            raise SyncError(
                "refusing CC Switch sync because target is not clean: {}".format(
                    app_type
                )
            )
        contents[app_type] = _read_utf8(target.path)
    conn = sqlite3.connect(str(database), timeout=5.0)
    try:
        _ensure_cc_switch_schema(conn)
        now = int(time.time() * 1000)
        for app_type in app_types:
            existing = conn.execute(
                "SELECT enabled FROM prompts WHERE id = ? AND app_type = ?",
                (CC_SWITCH_PROMPT_ID, app_type),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """INSERT INTO prompts
                       (id, app_type, name, content, description, enabled, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        CC_SWITCH_PROMPT_ID,
                        app_type,
                        CC_SWITCH_PROMPT_NAME,
                        contents[app_type],
                        CC_SWITCH_PROMPT_DESCRIPTION,
                        1 if enable else 0,
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """UPDATE prompts
                       SET name = ?, content = ?, description = ?,
                           enabled = CASE WHEN ? THEN 1 ELSE enabled END,
                           updated_at = ?
                       WHERE id = ? AND app_type = ?""",
                    (
                        CC_SWITCH_PROMPT_NAME,
                        contents[app_type],
                        CC_SWITCH_PROMPT_DESCRIPTION,
                        1 if enable else 0,
                        now,
                        CC_SWITCH_PROMPT_ID,
                        app_type,
                    ),
                )
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise SyncError("CC Switch prompt sync failed: {}".format(exc)) from exc
    finally:
        conn.close()
    return tuple(app_types)


def _format_report(report: CheckReport) -> str:
    return json.dumps(
        {
            "ok": report.ok,
            "clean": list(report.clean),
            "drifted": list(report.drifted),
            "missing": list(report.missing),
            "conflicts": list(report.conflicts),
            "skipped": list(report.skipped),
        },
        ensure_ascii=False,
        indent=2,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize shared system prompts into agent prompt files"
    )
    parser.add_argument(
        "command", choices=("preview", "check", "apply", "rollback", "cc-switch")
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--backup", type=Path, help="Backup directory for apply/rollback"
    )
    parser.add_argument(
        "--force", action="store_true", help="Allow rollback over newer target edits"
    )
    parser.add_argument("--database", type=Path, help="CC Switch SQLite database")
    parser.add_argument(
        "--enable", action="store_true", help="Enable managed CC Switch prompts"
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "check":
            report = check(config, args.source)
            print(
                _format_report(report)
                if args.as_json
                else ("clean\n" if report.ok else _format_report(report))
            )
            return 0 if report.ok else 1
        if args.command == "preview":
            report = check(config, args.source)
            if args.as_json:
                print(_format_report(report))
            else:
                print(
                    "would update: {}".format(
                        ", ".join(report.drifted + report.missing + report.conflicts)
                        or "none"
                    )
                )
            return 0
        if args.command == "apply":
            report = apply(config, args.source, args.backup)
            print(
                json.dumps(
                    {
                        "updated": report.updated,
                        "unchanged": report.unchanged,
                        "changed": list(report.changed),
                        "backup": str(report.backup_dir) if report.backup_dir else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "rollback":
            if args.backup is None:
                raise SyncError("--backup is required for rollback")
            restored = rollback(config, args.backup, args.force)
            print(
                json.dumps({"restored": list(restored)}, ensure_ascii=False, indent=2)
            )
            return 0
        if args.database is None:
            args.database = Path.home() / ".cc-switch" / "cc-switch.db"
        apps = sync_cc_switch(args.database, args.source, config, args.enable)
        print(
            json.dumps(
                {"updated_apps": list(apps), "enabled": args.enable},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, SyncError) as exc:
        print("error: {}".format(exc), file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
