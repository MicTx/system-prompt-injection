# ruff: noqa: E402, I001

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import sync_prompts  # noqa: E402


SHARED = """## Shared rule

- Keep one source.
- Fail closed.
"""


class SyncPromptTests(unittest.TestCase):
    def make_project(self) -> tuple[Path, Path, Path]:
        temp = Path(tempfile.mkdtemp(prefix="system-prompt-injection-test-"))
        source = temp / "SYSTEM_PROMPT.md"
        source.write_text(
            "# Shared System Prompt\n\n"
            "<!-- system-prompt-injection:shared:start -->\n"
            f"{SHARED}"
            "<!-- system-prompt-injection:shared:end -->\n",
            encoding="utf-8",
        )
        targets = temp / "targets.json"
        targets.write_text(
            json.dumps(
                {
                    "version": 1,
                    "targets": [
                        {
                            "id": "markdown",
                            "path": str(temp / "AGENTS.md"),
                            "format": "markdown",
                            "block": "shared",
                        },
                        {
                            "id": "pi",
                            "path": str(temp / "SYSTEM.md"),
                            "format": "pi-system",
                            "block": "shared",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (temp / "AGENTS.md").write_text(
            "# Local\n\nKeep this section.\n", encoding="utf-8"
        )
        (temp / "SYSTEM.md").write_text(
            "== LOCAL ==\n\nKeep this section.\n", encoding="utf-8"
        )
        return temp, source, targets

    def test_apply_is_idempotent_and_preserves_unmanaged_content(self) -> None:
        temp, source, targets = self.make_project()
        config = sync_prompts.load_config(targets)

        first = sync_prompts.apply(config, source, backup_root=temp / "backups")
        first_contents = {
            target.id: target.path.read_text(encoding="utf-8")
            for target in config.targets
        }
        second = sync_prompts.apply(config, source, backup_root=temp / "backups")
        second_contents = {
            target.id: target.path.read_text(encoding="utf-8")
            for target in config.targets
        }

        self.assertEqual(first.updated, 2)
        self.assertEqual(second.updated, 0)
        self.assertEqual(first_contents, second_contents)
        self.assertIn("Keep this section.", first_contents["markdown"])
        self.assertIn("Keep this section.", first_contents["pi"])
        self.assertGreaterEqual(len(list((temp / "backups").rglob("*"))), 2)
        self.assertEqual(stat.S_IMODE((first.backup_dir).stat().st_mode), 0o700)
        for backup_file in first.backup_dir.iterdir():
            self.assertEqual(stat.S_IMODE(backup_file.stat().st_mode), 0o600)

    def test_check_detects_drift_without_writing(self) -> None:
        temp, source, targets = self.make_project()
        config = sync_prompts.load_config(targets)
        sync_prompts.apply(config, source, backup_root=temp / "backups")
        target = temp / "AGENTS.md"
        target.write_text(
            target.read_text(encoding="utf-8").replace(
                "Fail closed.", "Changed locally."
            ),
            encoding="utf-8",
        )
        before = target.stat().st_mtime_ns

        report = sync_prompts.check(config, source)

        self.assertFalse(report.ok)
        self.assertIn("markdown", report.drifted)
        self.assertEqual(before, target.stat().st_mtime_ns)

    def test_mcode_style_target_max_bytes_fails_closed(self) -> None:
        temp, source, targets = self.make_project()
        config_data = json.loads(targets.read_text(encoding="utf-8"))
        config_data["targets"][0]["max_bytes"] = 8
        targets.write_text(json.dumps(config_data), encoding="utf-8")
        config = sync_prompts.load_config(targets)
        before = (temp / "AGENTS.md").read_bytes()

        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.apply(config, source, backup_root=temp / "backups")
        self.assertEqual((temp / "AGENTS.md").read_bytes(), before)
        self.assertFalse((temp / "backups").exists())

    def test_validation_rejects_malformed_inputs(self) -> None:
        temp, source, targets = self.make_project()
        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.render_block("unknown", SHARED)
        malformed_source = temp / "malformed.md"
        malformed_source.write_text("no markers\n", encoding="utf-8")
        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.extract_source_body(malformed_source)
        invalid_config = temp / "invalid.json"
        invalid_config.write_text("{not json", encoding="utf-8")
        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.load_config(invalid_config)
        duplicate_config = temp / "duplicate.json"
        duplicate_config.write_text(
            json.dumps(
                {
                    "version": 1,
                    "targets": [
                        {"id": "same", "path": str(temp / "a"), "format": "markdown"},
                        {"id": "same", "path": str(temp / "b"), "format": "markdown"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.load_config(duplicate_config)
        missing_config = sync_prompts.load_config(targets)
        missing_target = temp / "AGENTS.md"
        missing_target.unlink()
        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.apply(missing_config, source, backup_root=temp / "backups")

    def test_optional_target_can_wait_for_an_existing_parent(self) -> None:
        temp, source, targets = self.make_project()
        config_data = json.loads(targets.read_text(encoding="utf-8"))
        config_data["targets"].append(
            {
                "id": "future-agent",
                "path": str(temp / "future" / "SOUL.md"),
                "format": "markdown",
                "required": False,
                "create_missing": True,
                "create_parent": False,
            }
        )
        targets.write_text(json.dumps(config_data), encoding="utf-8")
        config = sync_prompts.load_config(targets)

        result = sync_prompts.apply(config, source, backup_root=temp / "backups")
        self.assertEqual(result.updated, 2)
        report = sync_prompts.check(config, source)
        self.assertTrue(report.ok)
        self.assertIn("future-agent", report.skipped)
        self.assertFalse((temp / "future" / "SOUL.md").exists())

        (temp / "future").mkdir()
        report = sync_prompts.check(config, source)
        self.assertFalse(report.ok)
        self.assertIn("future-agent", report.missing)
        sync_prompts.apply(config, source, backup_root=temp / "backups-2")
        self.assertTrue((temp / "future" / "SOUL.md").exists())

    def test_disabled_target_is_not_read_or_written(self) -> None:
        temp, source, targets = self.make_project()
        disabled = temp / "disabled.md"
        disabled.write_bytes(b"not utf-8: \xff")
        config_data = json.loads(targets.read_text(encoding="utf-8"))
        config_data["targets"].append(
            {
                "id": "disabled",
                "path": str(disabled),
                "format": "markdown",
                "enabled": False,
            }
        )
        targets.write_text(json.dumps(config_data), encoding="utf-8")
        config = sync_prompts.load_config(targets)

        report = sync_prompts.apply(config, source, backup_root=temp / "backups")

        self.assertEqual(report.updated, 2)
        self.assertEqual(disabled.read_bytes(), b"not utf-8: \xff")

    def test_apply_fails_closed_on_duplicate_managed_blocks(self) -> None:
        temp, source, targets = self.make_project()
        target = temp / "AGENTS.md"
        block = sync_prompts.render_block("markdown", SHARED)
        target.write_text(block + "\n" + block, encoding="utf-8")
        config = sync_prompts.load_config(targets)

        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.apply(config, source, backup_root=temp / "backups")
        self.assertFalse((temp / "backups").exists())

    def test_legacy_policy_block_is_migrated_once(self) -> None:
        temp, source, targets = self.make_project()
        legacy = """## Source and public snapshot gate

- Development source of truth is private Gitea `gitea.example.invalid`; resolve by remote URL, not by the name `origin`. Stop if no Gitea remote exists.
- GitHub is never a default push target. Use it only for a user-authorized, filtered public snapshot after Gitea push and verification.
- A GitHub snapshot requires linked Chinese and English READMEs, an explicit non-commercial license, a clean allowlisted staging tree, and a passing privacy/secret scan. Exclude private agent state, `.spec/`, session/transcript/log/cache/backup/worktree data, `.env*`, credentials, tokens, private endpoints/paths, and personal data.
- Record authorization scope, Gitea commit, GitHub commit, staging path, and gate result without recording secrets. See `/home/example/.config/agent-harness-public/PUBLIC_POLICY.md`.
"""
        target = temp / "AGENTS.md"
        target.write_text("# Local\n\n" + legacy, encoding="utf-8")
        config = sync_prompts.load_config(targets)

        sync_prompts.apply(config, source, backup_root=temp / "backups")
        result = target.read_text(encoding="utf-8")

        self.assertNotIn("## Source and public snapshot gate", result)
        self.assertIn("<!-- system-prompt-injection:shared:start -->", result)
        self.assertIn("# Local", result)

    def test_check_reports_legacy_policy_as_drift(self) -> None:
        temp, source, targets = self.make_project()
        legacy = """## Source and public snapshot gate

- Development source of truth is private Gitea `gitea.example.invalid`; resolve by remote URL, not by the name `origin`. Stop if no Gitea remote exists.
- GitHub is never a default push target. Use it only for a user-authorized, filtered public snapshot after Gitea push and verification.
- A GitHub snapshot requires linked Chinese and English READMEs, an explicit non-commercial license, a clean allowlisted staging tree, and a passing privacy/secret scan. Exclude private agent state, `.spec/`, session/transcript/log/cache/backup/worktree data, `.env*`, credentials, tokens, private endpoints/paths, and personal data.
- Record authorization scope, Gitea commit, GitHub commit, staging path, and gate result without recording secrets. See `/home/example/.config/agent-harness-public/PUBLIC_POLICY.md`.
"""
        target = temp / "AGENTS.md"
        target.write_text(
            "# Local\n\n" + sync_prompts.render_block("markdown", SHARED) + legacy,
            encoding="utf-8",
        )
        report = sync_prompts.check(sync_prompts.load_config(targets), source)

        self.assertFalse(report.ok)
        self.assertIn("markdown", report.drifted)

    def test_rollback_refuses_newer_target(self) -> None:
        temp, source, targets = self.make_project()
        config = sync_prompts.load_config(targets)
        sync_prompts.apply(config, source, backup_root=temp / "backups")
        target = temp / "AGENTS.md"
        target.write_text(
            target.read_text(encoding="utf-8") + "\nnew edit\n", encoding="utf-8"
        )

        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.rollback(config, backup_root=temp / "backups", force=False)
        self.assertIn("new edit", target.read_text(encoding="utf-8"))

    def test_rollback_rejects_backup_path_escape(self) -> None:
        temp, source, targets = self.make_project()
        config = sync_prompts.load_config(targets)
        sync_prompts.apply(config, source, backup_root=temp / "backups")
        manifest_path = temp / "backups" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["targets"][0]["backup"] = "../outside"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.rollback(config, temp / "backups")

    def test_apply_rejects_invalid_utf8_before_writing_other_targets(self) -> None:
        temp, source, targets = self.make_project()
        (temp / "AGENTS.md").write_bytes(b"invalid\xff\n")
        pi_before = (temp / "SYSTEM.md").read_bytes()
        config = sync_prompts.load_config(targets)

        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.apply(config, source, backup_root=temp / "backups")
        self.assertEqual((temp / "SYSTEM.md").read_bytes(), pi_before)
        self.assertFalse((temp / "backups").exists())

    def test_check_marks_symlink_target_as_conflict(self) -> None:
        temp, source, targets = self.make_project()
        outside = temp / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (temp / "AGENTS.md").unlink()
        (temp / "AGENTS.md").symlink_to(outside)
        config = sync_prompts.load_config(targets)

        report = sync_prompts.check(config, source)

        self.assertFalse(report.ok)
        self.assertIn("markdown", report.conflicts)

    def test_dangling_symlink_is_not_treated_as_optional_missing(self) -> None:
        temp, source, _ = self.make_project()
        target = temp / "optional.md"
        target.symlink_to(temp / "missing.md")
        config_path = temp / "optional-targets.json"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "targets": [
                        {
                            "id": "optional",
                            "path": str(target),
                            "format": "markdown",
                            "required": False,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        config = sync_prompts.load_config(config_path)

        report = sync_prompts.check(config, source)
        self.assertIn("optional", report.conflicts)
        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.apply(config, source, backup_root=temp / "backups")

    def test_cc_switch_prompt_matrix_matches_prompt_capable_hosts(self) -> None:
        config = sync_prompts.load_config(sync_prompts.DEFAULT_CONFIG)
        target_ids = {target.id for target in config.targets if target.enabled}
        expected_apps = {
            "claude",
            "codex",
            "gemini",
            "grokbuild",
            "opencode",
            "openclaw",
            "hermes",
            "pi",
            "mcode",
        }
        expected_target_ids = expected_apps - {"pi"}
        self.assertTrue(expected_target_ids <= target_ids)
        self.assertIn("pi-agents", target_ids)
        self.assertEqual(set(sync_prompts.CC_SWITCH_APPS), expected_apps)

    def test_target_path_supports_mcode_environment_override(self) -> None:
        original_minimax = os.environ.get("MINIMAX_DATA_DIR")
        original_mavis = os.environ.get("MAVIS_DATA_DIR")
        template = "${MINIMAX_DATA_DIR:-${MAVIS_DATA_DIR:-~/.minimax}}/AGENTS.md"
        try:
            os.environ.pop("MINIMAX_DATA_DIR", None)
            os.environ.pop("MAVIS_DATA_DIR", None)
            default = sync_prompts._expand_path(template)
            self.assertEqual(default, Path.home() / ".minimax" / "AGENTS.md")

            mavis_override = Path(tempfile.mkdtemp(prefix="mavis-home-"))
            os.environ["MAVIS_DATA_DIR"] = str(mavis_override)
            self.assertEqual(
                sync_prompts._expand_path(template), mavis_override / "AGENTS.md"
            )

            minimax_override = Path(tempfile.mkdtemp(prefix="minimax-home-"))
            os.environ["MINIMAX_DATA_DIR"] = str(minimax_override)
            self.assertEqual(
                sync_prompts._expand_path(template), minimax_override / "AGENTS.md"
            )
        finally:
            if original_minimax is None:
                os.environ.pop("MINIMAX_DATA_DIR", None)
            else:
                os.environ["MINIMAX_DATA_DIR"] = original_minimax
            if original_mavis is None:
                os.environ.pop("MAVIS_DATA_DIR", None)
            else:
                os.environ["MAVIS_DATA_DIR"] = original_mavis

    def test_cc_switch_sync_only_updates_prompts(self) -> None:
        temp, source, targets = self.make_project()
        config = sync_prompts.load_config(targets)
        sync_prompts.apply(config, source, backup_root=temp / "backups")
        db = temp / "cc-switch.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, settings_config TEXT);
            CREATE TABLE prompts (
                id TEXT NOT NULL, app_type TEXT NOT NULL, name TEXT NOT NULL, content TEXT NOT NULL,
                description TEXT, enabled BOOLEAN NOT NULL DEFAULT 1, created_at INTEGER, updated_at INTEGER,
                PRIMARY KEY (id, app_type)
            );
            INSERT INTO providers VALUES ('provider-1', 'claude', 'unchanged', 'secret-bearing-config');
            """
        )
        conn.commit()
        conn.close()

        sync_prompts.sync_cc_switch(
            db, source, config, enable=False, app_types=("markdown",)
        )

        conn = sqlite3.connect(db)
        provider = conn.execute(
            "SELECT name, settings_config FROM providers"
        ).fetchone()
        prompt = conn.execute(
            "SELECT app_type, name, content, enabled FROM prompts WHERE id = ?",
            (sync_prompts.CC_SWITCH_PROMPT_ID,),
        ).fetchone()
        conn.close()
        self.assertEqual(provider, ("unchanged", "secret-bearing-config"))
        self.assertEqual(prompt[0], "markdown")
        self.assertEqual(prompt[1], sync_prompts.CC_SWITCH_PROMPT_NAME)
        self.assertEqual(prompt[2], (temp / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertEqual(prompt[3], 0)

    def test_cc_switch_refuses_drifted_target(self) -> None:
        temp, source, targets = self.make_project()
        config = sync_prompts.load_config(targets)
        sync_prompts.apply(config, source, backup_root=temp / "backups")
        (temp / "AGENTS.md").write_text("local edit\n", encoding="utf-8")
        db = temp / "cc-switch.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE prompts (id TEXT, app_type TEXT, name TEXT, content TEXT, description TEXT, enabled INTEGER, created_at INTEGER, updated_at INTEGER, PRIMARY KEY(id, app_type))"
        )
        conn.commit()
        conn.close()

        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.sync_cc_switch(
                db, source, config, enable=False, app_types=("markdown",)
            )

    def test_cli_commands_cover_check_preview_apply_cc_switch_and_rollback(
        self,
    ) -> None:
        temp, source, _ = self.make_project()
        targets = temp / "cli-targets.json"
        targets.write_text(
            json.dumps(
                {
                    "version": 1,
                    "targets": [
                        {
                            "id": "claude",
                            "path": str(temp / "AGENTS.md"),
                            "format": "markdown",
                        },
                        {
                            "id": "codex",
                            "path": str(temp / "SYSTEM.md"),
                            "format": "pi-system",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        args = ["--config", str(targets), "--source", str(source)]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(sync_prompts.main(["check", *args]), 1)
            self.assertEqual(sync_prompts.main(["preview", *args]), 0)
            self.assertEqual(
                sync_prompts.main(["apply", *args, "--backup", str(temp / "backups")]),
                0,
            )
            self.assertEqual(sync_prompts.main(["check", *args, "--json"]), 0)
        db = temp / "cli.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE prompts (id TEXT, app_type TEXT, name TEXT, content TEXT, description TEXT, enabled INTEGER, created_at INTEGER, updated_at INTEGER, PRIMARY KEY(id, app_type))"
        )
        conn.commit()
        conn.close()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                sync_prompts.main(["cc-switch", *args, "--database", str(db)]),
                0,
            )
            self.assertEqual(
                sync_prompts.main(
                    ["rollback", *args, "--backup", str(temp / "backups")]
                ),
                0,
            )
        self.assertIn('"updated": 2', output.getvalue())

    def test_cc_switch_rejects_non_string_app_type(self) -> None:
        temp, source, targets = self.make_project()
        config = sync_prompts.load_config(targets)
        db = temp / "cc-switch.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE prompts (id TEXT, app_type TEXT, name TEXT, content TEXT, description TEXT, enabled INTEGER, created_at INTEGER, updated_at INTEGER, PRIMARY KEY(id, app_type))"
        )
        conn.commit()
        conn.close()

        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.sync_cc_switch(
                db, source, config, enable=False, app_types=(123,)
            )

    def test_cc_switch_rejects_database_without_prompts_table(self) -> None:
        temp, source, targets = self.make_project()
        config = sync_prompts.load_config(targets)
        db = temp / "empty.db"
        sqlite3.connect(db).close()
        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.sync_cc_switch(
                db, source, config, enable=False, app_types=("markdown",)
            )

    def test_cc_switch_requires_existing_database(self) -> None:
        temp, source, _ = self.make_project()
        with self.assertRaises(sync_prompts.SyncError):
            sync_prompts.sync_cc_switch(temp / "missing.db", source, enable=False)


if __name__ == "__main__":
    unittest.main()
