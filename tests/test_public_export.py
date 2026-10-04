"""Publishing gates run against synthetic trees, never live agent files."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import export_public


class PublicExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="prompt-public-export-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "public"
        self.commit = "a" * 40
        for name in export_public.ALLOWLIST:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic fixture\n", encoding="utf-8")
        (self.source / "README.md").write_text(
            "# Prompt Sync\n\n[English](README-en.md)\n", encoding="utf-8"
        )
        (self.source / "README-en.md").write_text(
            "# Prompt Sync\n\n[简体中文](README.md)\n", encoding="utf-8"
        )
        (self.source / "LICENSE").write_text(
            "# PolyForm Noncommercial License 1.0.0\n\n"
            "## Noncommercial Purposes\n"
            "Any noncommercial purpose is a permitted purpose.\n",
            encoding="utf-8",
        )
        (self.source / "targets.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "targets": [
                        {
                            "id": "claude",
                            "path": "~/.claude/CLAUDE.md",
                            "required": True,
                        },
                        {"id": "pi-agents", "path": "~/.pi/agent/AGENTS.md"},
                        {"id": "pi-system", "path": "~/.pi/agent/SYSTEM.md"},
                        {"id": "local-template", "path": "~/private/template.md"},
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_export_uses_allowlist_and_sanitizes_private_references(self) -> None:
        private_host = ".".join(("git", "mxk", "dev"))
        private_path = "/" + "Users/dawud/fixture"
        escaped_host = private_host.replace(".", "\\\\.")
        (self.source / "src/sync_prompts.py").write_text(
            f"{private_host} {private_path} {escaped_host}\n", encoding="utf-8"
        )
        (self.source / "private-state.json").write_text("not exported\n")
        manifest = export_public.export_tree(self.source, self.output, self.commit)
        export_public.check_tree(self.output)
        self.assertFalse((self.output / "private-state.json").exists())
        code = (self.output / "src/sync_prompts.py").read_text()
        self.assertIn("gitea.example.invalid", code)
        self.assertIn("/home/example/fixture", code)
        self.assertNotIn("dawud", code)
        self.assertEqual(manifest["source_commit"], self.commit)
        self.assertEqual(set(manifest["files"]), set(export_public.ALLOWLIST))
        targets = json.loads((self.output / "targets.json").read_text())["targets"]
        self.assertEqual(
            [item["id"] for item in targets], ["claude", "pi-agents", "pi-system"]
        )
        self.assertTrue(all(not item["required"] for item in targets))
        self.assertTrue(all(not item["create_parent"] for item in targets))

    def test_export_refuses_existing_destination(self) -> None:
        self.output.mkdir()
        with self.assertRaises(export_public.ExportError):
            export_public.export_tree(self.source, self.output, self.commit)

    def test_export_rejects_missing_or_symlinked_allowlisted_input(self) -> None:
        path = self.source / "src/sync_prompts.py"
        path.unlink()
        with self.assertRaises(export_public.ExportError):
            export_public.export_tree(self.source, self.output, self.commit)
        outside = self.root / "outside.py"
        outside.write_text("synthetic fixture\n")
        path.symlink_to(outside)
        with self.assertRaises(export_public.ExportError):
            export_public.export_tree(self.source, self.output, self.commit)

    def test_export_blocks_secrets_and_leaves_no_partial_tree(self) -> None:
        assignment = "api" + "_key = '"
        (self.source / "SYSTEM_PROMPT.md").write_text(
            assignment + "sk-" + "x" * 40 + "'\n", encoding="utf-8"
        )
        with self.assertRaises(export_public.ExportError):
            export_public.export_tree(self.source, self.output, self.commit)
        self.assertFalse(self.output.exists())

    def test_gate_rejects_modified_payload_and_extra_files(self) -> None:
        export_public.export_tree(self.source, self.output, self.commit)
        source = self.output / "SYSTEM_PROMPT.md"
        source.write_text("modified fixture\n")
        with self.assertRaises(export_public.ExportError):
            export_public.check_tree(self.output)
        source.write_text("synthetic fixture\n")
        (self.output / ".env").write_text("secret fixture\n")
        with self.assertRaises(export_public.ExportError):
            export_public.check_tree(self.output)

    def test_gate_requires_linked_readmes_and_noncommercial_license(self) -> None:
        for filename, content in (
            ("README.md", "# Prompt Sync\n"),
            ("LICENSE", "MIT License\n"),
        ):
            with self.subTest(filename=filename):
                original = (self.source / filename).read_text()
                (self.source / filename).write_text(content)
                with self.assertRaises(export_public.ExportError):
                    export_public.export_tree(self.source, self.output, self.commit)
                (self.source / filename).write_text(original)

    def test_export_rejects_bad_commits_and_invalid_json(self) -> None:
        with self.assertRaises(export_public.ExportError):
            export_public.export_tree(self.source, self.output, "not-a-commit")
        (self.source / "targets.json").write_text("{invalid json\n")
        with self.assertRaises(export_public.ExportError):
            export_public.export_tree(self.source, self.output, self.commit)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
