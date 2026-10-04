"""Tests for the minimal prompt sync core."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import prompt_sync as ps

BODY = "- rule one\n- rule two\n"
SOURCE = "# Shared\n\n<!-- system-prompt-injection:shared:start -->\n{}<!-- system-prompt-injection:shared:end -->\n".format(
    BODY
)
# Byte layout produced by the previous generator; the new tool must stay
# compatible so existing target files remain "clean".
INSTALLED_TARGET = (
    "personal notes\n"
    "\n"
    "<!-- system-prompt-injection:shared:start -->\n"
    "- rule one\n"
    "- rule two\n"
    "<!-- system-prompt-injection:shared:end -->\n"
    "\n"
    "trailing section\n"
)
STALE_TARGET = INSTALLED_TARGET.replace("- rule two", "- old two")


class Helper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.source = self.home / "SYSTEM_PROMPT.md"
        self.source.write_text(SOURCE, encoding="utf-8")

    def target(self, name, fmt="markdown"):
        return ps.Target(name, self.home / name, fmt)


class MarkersAndBody(Helper):
    def test_markdown_markers(self):
        self.assertEqual(
            ps.markers("markdown"),
            ("<!-- system-prompt-injection:shared:start -->",
             "<!-- system-prompt-injection:shared:end -->"),
        )

    def test_pi_system_markers(self):
        start, end = ps.markers("pi-system")
        self.assertEqual(start, "== SYSTEM_PROMPT_INJECTION:shared:START ==")
        self.assertEqual(end, "== SYSTEM_PROMPT_INJECTION:shared:END ==")

    def test_unknown_format_rejected(self):
        with self.assertRaises(ps.SyncError):
            ps.markers("bogus")

    def test_body_of_extracts_block(self):
        self.assertEqual(ps.body_of(self.source), BODY)

    def test_body_of_requires_exactly_one_block(self):
        self.source.write_text(SOURCE + SOURCE, encoding="utf-8")
        with self.assertRaises(ps.SyncError):
            ps.body_of(self.source)

    def test_body_of_rejects_empty_block(self):
        self.source.write_text(
            "# Shared\n<!-- system-prompt-injection:shared:start -->\n"
            "<!-- system-prompt-injection:shared:end -->\n",
            encoding="utf-8",
        )
        with self.assertRaises(ps.SyncError):
            ps.body_of(self.source)


class ContentUpdate(Helper):
    def test_legacy_file_layout_stays_clean(self):
        state = ps.state_of(INSTALLED_TARGET, "markdown", BODY)
        self.assertEqual(state, ps.OK)

    def test_replace_preserves_surroundings(self):
        updated = ps.next_content(INSTALLED_TARGET, "markdown", "- new\n")
        self.assertTrue(updated.startswith("personal notes\n\n"))
        self.assertTrue(updated.endswith("trailing section\n"))
        self.assertIn("<!-- system-prompt-injection:shared:start -->\n- new\n", updated)

    def test_append_when_block_absent(self):
        updated = ps.next_content("notes\n", "markdown", BODY)
        self.assertEqual(updated, "notes\n\n" + ps.render_block("markdown", BODY))

    def test_append_to_empty_content(self):
        updated = ps.next_content("", "markdown", BODY)
        self.assertEqual(updated, ps.render_block("markdown", BODY))

    def test_drift_detected_when_body_differs(self):
        self.assertEqual(ps.state_of(INSTALLED_TARGET, "markdown", "- other\n"), ps.OUT)

    def test_missing_block_reported_as_new(self):
        self.assertEqual(ps.state_of("plain file\n", "markdown", BODY), ps.NEW)

    def test_duplicated_block_is_error(self):
        self.assertEqual(
            ps.state_of(ps.render_block("markdown", BODY) * 2, "markdown", BODY),
            ps.ERR,
        )

    def test_pi_system_roundtrip(self):
        rendered = ps.render_block("pi-system", BODY)
        self.assertEqual(ps.state_of(rendered, "pi-system", BODY), ps.OK)
        self.assertEqual(
            ps.next_content(rendered, "pi-system", "- new\n"),
            ps.render_block("pi-system", "- new\n"),
        )


class PathExpansion(unittest.TestCase):
    def test_plain_home_path(self):
        path = ps.expand_path("~/x/AGENTS.md")
        self.assertEqual(path, Path(os.path.expanduser("~/x/AGENTS.md")).absolute())

    def test_default_used_when_env_missing(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROMPT_SYNC_TEST_VAR", None)
            path = ps.expand_path("${PROMPT_SYNC_TEST_VAR:-~/.fallback}/AGENTS.md")
        self.assertEqual(path, Path(os.path.expanduser("~/.fallback/AGENTS.md")).absolute())

    def test_env_value_wins_over_default(self):
        env = {"PROMPT_SYNC_TEST_VAR": str(Path("/tmp/env-dir"))}
        with unittest.mock.patch.dict(os.environ, env):
            path = ps.expand_path("${PROMPT_SYNC_TEST_VAR:-~/.fallback}/AGENTS.md")
        self.assertEqual(path, Path("/tmp/env-dir/AGENTS.md"))

    def test_unset_variable_without_default_errors(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROMPT_SYNC_TEST_VAR", None)
            with self.assertRaises(ps.SyncError):
                ps.expand_path("${PROMPT_SYNC_TEST_VAR}/AGENTS.md")


class ConfigLoad(Helper):
    def write_config(self, payload):
        config = self.home / "targets.json"
        config.write_text(json.dumps(payload), encoding="utf-8")
        return config

    def test_minimal_schema_defaults(self):
        config = self.write_config(
            {"targets": [{"id": "a", "path": "~/a.md"}, {"id": "b", "path": "~/b.md", "format": "pi-system"}]}
        )
        targets = ps.load_targets(config)
        self.assertEqual(targets[0].format, "markdown")
        self.assertEqual(targets[1].format, "pi-system")

    def test_duplicate_ids_rejected(self):
        config = self.write_config(
            {"targets": [{"id": "a", "path": "~/a.md"}, {"id": "a", "path": "~/b.md"}]}
        )
        with self.assertRaises(ps.SyncError):
            ps.load_targets(config)

    def test_unknown_format_rejected(self):
        config = self.write_config({"targets": [{"id": "a", "path": "~/a.md", "format": "bogus"}]})
        with self.assertRaises(ps.SyncError):
            ps.load_targets(config)

    def test_empty_list_rejected(self):
        config = self.write_config({"targets": []})
        with self.assertRaises(ps.SyncError):
            ps.load_targets(config)


class ScanAndApply(Helper):
    def test_scan_states(self):
        installed = self.home / "installed.md"
        installed.write_text(INSTALLED_TARGET, encoding="utf-8")
        stale = self.home / "stale.md"
        stale.write_text(INSTALLED_TARGET.replace("- rule one", "- old one"), encoding="utf-8")
        fresh_dir = self.home / "fresh-dir"
        fresh_dir.mkdir()
        symlink = self.home / "link.md"
        symlink.symlink_to(installed)
        targets = [
            ps.Target("installed", installed),
            ps.Target("stale", stale),
            ps.Target("fresh", fresh_dir / "fresh.md"),
            ps.Target("absent", self.home / "nope" / "absent.md"),
            ps.Target("link", symlink),
        ]
        states = {row.target.id: row.state for row in ps.scan(targets, self.source)}
        self.assertEqual(states["installed"], ps.OK)
        self.assertEqual(states["stale"], ps.OUT)
        self.assertEqual(states["fresh"], ps.NEW)
        self.assertEqual(states["absent"], ps.SKIP)
        self.assertEqual(states["link"], ps.ERR)

    def test_apply_updates_and_backs_up(self):
        existing = self.home / "existing.md"
        existing.write_text(STALE_TARGET, encoding="utf-8")
        existing.chmod(0o600)
        backup_root = self.home / "backups"
        targets = [ps.Target("existing", existing), ps.Target("created", self.home / "created.md")]

        result = ps.apply(targets, self.source, backup_root)

        self.assertEqual(result.changed, ("existing", "created"))
        self.assertEqual(ps.state_of(existing.read_text(encoding="utf-8"), "markdown", BODY), ps.OK)
        self.assertEqual(ps.state_of(self.home.joinpath("created.md").read_text(encoding="utf-8"), "markdown", BODY), ps.OK)
        self.assertEqual(existing.stat().st_mode & 0o777, 0o600)
        backup_dir = result.backup_dir
        self.assertTrue((backup_dir / "manifest.json").is_file())
        manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(manifest["targets"]), ["created", "existing"])
        self.assertEqual((backup_dir / "existing").read_text(encoding="utf-8"), STALE_TARGET)

    def test_apply_is_idempotent(self):
        existing = self.home / "existing.md"
        existing.write_text(STALE_TARGET, encoding="utf-8")
        targets = [ps.Target("existing", existing)]
        first = ps.apply(targets, self.source, self.home / "backups")
        second = ps.apply(targets, self.source, self.home / "backups")
        self.assertEqual(first.changed, ("existing",))
        self.assertEqual(second.changed, ())
        self.assertIsNone(second.backup_dir)

    def test_apply_preserves_surrounding_text(self):
        existing = self.home / "existing.md"
        existing.write_text(STALE_TARGET, encoding="utf-8")
        ps.apply([ps.Target("existing", existing)], self.source, self.home / "backups")
        updated = existing.read_text(encoding="utf-8")
        self.assertTrue(updated.startswith("personal notes\n\n"))
        self.assertTrue(updated.endswith("trailing section\n"))

    def test_undo_restores_latest_backup(self):
        existing = self.home / "existing.md"
        existing.write_text(STALE_TARGET, encoding="utf-8")
        targets = [ps.Target("existing", existing)]
        backup_root = self.home / "backups"
        ps.apply(targets, self.source, backup_root)
        existing.write_text("clobbered by someone else\n", encoding="utf-8")

        restored = ps.undo(ps.latest_backup(backup_root))

        self.assertEqual(restored, ["existing"])
        self.assertEqual(existing.read_text(encoding="utf-8"), STALE_TARGET)

    def test_latest_backup_none_when_empty(self):
        self.assertIsNone(ps.latest_backup(self.home / "nowhere"))


class DisplayHelpers(unittest.TestCase):
    def test_abbrev_home(self):
        with unittest.mock.patch.object(
            Path, "home", staticmethod(lambda: Path("/home/test"))
        ):
            self.assertEqual(ps._abbrev_home(Path("/home/test/a/b.md")), "~/a/b.md")
            self.assertEqual(ps._abbrev_home(Path("/elsewhere/b.md")), "/elsewhere/b.md")
            self.assertEqual(ps._abbrev_home(Path("/home/testing/c.md")), "/home/testing/c.md")

    def test_summarize_short_list_kept(self):
        self.assertEqual(ps._summarize(["a", "b"]), "a, b")

    def test_summarize_long_list_truncated(self):
        self.assertEqual(
            ps._summarize(["a", "b", "c", "d", "e"]), "a, b, c +2 more"
        )


class SingleTargetApply(Helper):
    def test_apply_subset_touches_only_that_target(self):
        first = self.home / "first.md"
        second = self.home / "second.md"
        for path in (first, second):
            path.write_text(STALE_TARGET, encoding="utf-8")
        backup_root = self.home / "backups"

        result = ps.apply([ps.Target("first", first)], self.source, backup_root)

        self.assertEqual(result.changed, ("first",))
        self.assertEqual(ps.state_of(first.read_text(encoding="utf-8"), "markdown", BODY), ps.OK)
        self.assertEqual(second.read_text(encoding="utf-8"), STALE_TARGET)
        self.assertEqual(sorted(p.name for p in result.backup_dir.iterdir()),
                         ["first", "manifest.json"])


class Cli(unittest.TestCase):
    def test_check_reports_and_exits_nonzero_when_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            source = home / "SYSTEM_PROMPT.md"
            source.write_text(SOURCE, encoding="utf-8")
            config = home / "targets.json"
            config.write_text(
                json.dumps({"targets": [{"id": "a", "path": str(home / "nope" / "a.md")}]}),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                code = ps.main(["check", "--config", str(config), "--source", str(source)])
            self.assertEqual(code, 0)  # host not installed → skip, not failure

            (home / "a.md").write_text("plain\n", encoding="utf-8")
            with open(config, "w", encoding="utf-8") as handle:
                json.dump({"targets": [{"id": "a", "path": str(home / "a.md")}]}, handle)
            with contextlib.redirect_stdout(io.StringIO()):
                code = ps.main(["check", "--config", str(config), "--source", str(source)])
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
