#!/usr/bin/env python3
"""Build and verify a sanitized, allowlisted GitHub snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = (
    ".gitignore",
    "README.md",
    "README-en.md",
    "SYSTEM_PROMPT.md",
    "LICENSE",
    "pyproject.toml",
    "targets.json",
    "src/__init__.py",
    "src/sync_prompts.py",
    "tests/test_sync_prompts.py",
    "tests/test_public_export.py",
    "tools/__init__.py",
    "tools/export_public.py",
)
PUBLIC_MANIFEST = "PUBLIC_MANIFEST.json"
PUBLIC_TARGET_IDS = {
    "pi-agents",
    "pi-system",
    "codex",
    "gemini",
    "claude",
    "claude-common-agents",
    "grokbuild",
    "opencode",
    "openclaw",
    "hermes",
    "mcode",
    "zcode",
}
PUBLIC_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
PRIVATE_HOST = ".".join(("git", "mxk", "dev"))
PRIVATE_POLICY = "PUBLISHING_" + "POLICY.md"
PRIVATE_PATH_PREFIX = "/" + "Users/dawud"
PRIVATE_CONFIG_FRAGMENT = "/" + ".config" + "/agent-harness/"
PRIVATE_CONFIG_TILDE = "~/.config/agent-harness-public/"
LOCAL_HOST = "local" + "host"
SECRET_PATTERN = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----)"
)
FORBIDDEN_PATTERNS = (
    ("home path", re.compile(re.escape("/" + "Users/") + r"[A-Za-z0-9._-]+")),
    ("private Gitea host", re.compile(re.escape(PRIVATE_HOST), re.I)),
    (
        "credential assignment",
        re.compile(r"(?i)\b(?:password|api[_-]?key|secret|token)\s*[:=]"),
    ),
    (
        "private host",
        re.compile(
            r"(?i)\b(?:" + re.escape(LOCAL_HOST) + r"|127\.0\.0\.1|10\.|192\.168\.)"
        ),
    ),
    (
        "private publishing policy",
        re.compile(
            re.escape(PRIVATE_CONFIG_FRAGMENT) + "|" + re.escape(PRIVATE_POLICY)
        ),
    ),
    ("secret token", SECRET_PATTERN),
)


class ExportError(RuntimeError):
    """Raised when a public snapshot cannot pass its release gates."""


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ExportError(f"cannot read {path}: {exc}") from exc


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _sha256(path: Path) -> str:
    return hashlib.sha256(_read(path)).hexdigest()


def _require_commit(commit: str) -> None:
    if not isinstance(commit, str) or not PUBLIC_COMMIT_RE.fullmatch(commit):
        raise ExportError("source commit must be a 40-character hexadecimal commit")


def _sanitize_public_bytes(data: bytes) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExportError("public source contains invalid UTF-8") from exc
    escaped_host = PRIVATE_HOST.replace(".", r"\.")
    escaped_policy = PRIVATE_POLICY.replace(".", r"\.")
    escaped_config = PRIVATE_CONFIG_FRAGMENT.replace(".", r"\.")
    for old, new in (
        (PRIVATE_HOST, "gitea.example.invalid"),
        (escaped_host, r"gitea\.example\.invalid"),
        (PRIVATE_PATH_PREFIX, "/home/example"),
        (PRIVATE_POLICY, "PUBLIC_POLICY.md"),
        (escaped_policy, r"PUBLIC_POLICY\.md"),
        (PRIVATE_CONFIG_TILDE, "~/.config/agent-harness-public/"),
        (PRIVATE_CONFIG_FRAGMENT, "/.config/agent-harness-public/"),
        (escaped_config, r"/\.config/agent-harness-public/"),
    ):
        text = text.replace(old, new)
    return text.encode("utf-8")


def _check_no_forbidden(path: Path, data: bytes) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExportError(f"public file is not UTF-8: {path}") from exc
    for label, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(text):
            raise ExportError(f"public payload contains forbidden {label}: {path}")


def _sanitize_targets(data: bytes) -> bytes:
    try:
        config = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError("public targets.json must be valid UTF-8 JSON") from exc
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ExportError("public targets.json must use version 1")
    targets = config.get("targets")
    if not isinstance(targets, list):
        raise ExportError("public targets.json has no targets list")
    public_targets: List[Dict[str, object]] = []
    for item in targets:
        if not isinstance(item, dict):
            raise ExportError("public targets.json contains a non-object target")
        if not item.get("enabled", True):
            continue
        target_id = item.get("id")
        target_format = item.get("format", "markdown")
        if not isinstance(target_id, str) or not isinstance(target_format, str):
            raise ExportError("public targets.json contains an invalid target")
        if target_id not in PUBLIC_TARGET_IDS:
            continue
        public_targets.append(
            {
                "id": target_id,
                "path": f"./targets/{target_id}.md",
                "format": target_format,
                "block": item.get("block", "shared"),
                "required": False,
                "create_missing": False,
                "create_parent": False,
                **({"max_bytes": item["max_bytes"]} if "max_bytes" in item else {}),
            }
        )
    config["targets"] = public_targets
    return (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _readme_ok(root: Path) -> None:
    chinese = root / "README.md"
    english = root / "README-en.md"
    if not chinese.is_file() or not english.is_file():
        raise ExportError("public snapshot requires README.md and README-en.md")
    chinese_text = chinese.read_text(encoding="utf-8")
    english_text = english.read_text(encoding="utf-8")
    if "README-en.md" not in chinese_text or "README.md" not in english_text:
        raise ExportError("Chinese and English READMEs must link to each other")


def _license_ok(root: Path) -> None:
    license_path = root / "LICENSE"
    if not license_path.is_file():
        raise ExportError("public snapshot requires LICENSE")
    text = license_path.read_text(encoding="utf-8")
    required = ("PolyForm Noncommercial", "Noncommercial Purposes")
    if not all(marker in text for marker in required):
        raise ExportError("public snapshot requires an explicit non-commercial license")


def _manifest_for(root: Path, source_commit: str) -> Dict[str, object]:
    return {
        "version": 1,
        "source_commit": source_commit,
        "files": list(ALLOWLIST),
        "sha256": {name: _sha256(root / name) for name in ALLOWLIST},
    }


def _write_manifest(root: Path, source_commit: str) -> Dict[str, object]:
    manifest = _manifest_for(root, source_commit)
    _write(root / PUBLIC_MANIFEST, (json.dumps(manifest, indent=2) + "\n").encode())
    return manifest


def export_tree(
    source: Path, destination: Path, source_commit: str
) -> Dict[str, object]:
    """Export only the allowlisted source files into a new destination."""
    _require_commit(source_commit)
    source = source.resolve()
    if destination.exists() or destination.is_symlink():
        raise ExportError(f"destination already exists: {destination}")
    for name in ALLOWLIST:
        path = source / name
        if not path.is_file() or path.is_symlink():
            raise ExportError(f"allowlisted source is missing or unsafe: {path}")
        data = _read(path)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExportError(f"allowlisted source is not UTF-8: {path}") from exc
        if SECRET_PATTERN.search(data.decode("utf-8")):
            raise ExportError(f"source contains sensitive material: {path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix="system-prompt-public-", dir=str(destination.parent))
    )
    try:
        for name in ALLOWLIST:
            data = _sanitize_public_bytes(_read(source / name))
            if name == "targets.json":
                data = _sanitize_targets(data)
            _check_no_forbidden(Path(name), data)
            _write(staging / name, data)
        _readme_ok(staging)
        _license_ok(staging)
        manifest = _write_manifest(staging, source_commit)
        check_tree(staging)
        staging.rename(destination)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def check_tree(root: Path) -> Dict[str, object]:
    """Fail closed if a staged tree is modified, expanded, private, or malformed."""
    if not root.is_dir() or root.is_symlink():
        raise ExportError(f"public staging tree is not a regular directory: {root}")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ExportError("public staging tree contains a symlink")
    expected = set(ALLOWLIST) | {PUBLIC_MANIFEST}
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if actual != expected:
        raise ExportError(
            f"public staging tree differs from allowlist: {sorted(actual)}"
        )
    _readme_ok(root)
    _license_ok(root)
    manifest_path = root / PUBLIC_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExportError("invalid PUBLIC_MANIFEST.json") from exc
    _require_commit(manifest.get("source_commit"))
    if manifest.get("files") != list(ALLOWLIST):
        raise ExportError("PUBLIC_MANIFEST.json has an invalid file list")
    expected_manifest = _manifest_for(root, manifest["source_commit"])
    if manifest.get("sha256") != expected_manifest["sha256"]:
        raise ExportError("public payload does not match PUBLIC_MANIFEST.json")
    for name in ALLOWLIST:
        _check_no_forbidden(root / name, _read(root / name))
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("export", "check"))
    parser.add_argument("--source", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--commit")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "export":
            if args.destination is None or args.commit is None:
                raise ExportError("export requires --destination and --commit")
            manifest = export_tree(args.source, args.destination, args.commit)
        else:
            manifest = check_tree(args.source)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    except (ExportError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
