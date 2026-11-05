"""Offline tests of vault_tidy.py: nothing is dropped, old log entries split
into monthly parts with links, a second run changes nothing."""

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import vault_tidy as vt  # noqa: E402

TODAY = dt.date(2026, 11, 12)


def vault(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


class Tidy(unittest.TestCase):
    def test_a_log_keeps_recent_entries_within_its_budget(self):
        # The session hook's entries: "## <date> <time> · session `<id>`".
        entries = [f"## 2026-11-{d:02d} 10:00 · session `s{d}`\nwork of the {d}th\n" for d in range(12, 0, -1)]
        entries += [f"## 2026-10-{d:02d} 10:00 · session `t{d}`\nolder {d}\n" for d in (20, 5)]
        entries += ["## 2026-09-30 09:00 · session `u`\n```\n## 2026-01-01 not an entry\n```\n"]
        text = "# p: session log\nOne entry per session.\n\n" + "\n".join(entries)
        v = vault({"log/p.md": text})
        vt.run(v, TODAY)
        page = (v / "log/p.md").read_text()
        self.assertIn("work of the 12th", page)
        self.assertIn("older 20", page, "within 30 days")
        self.assertNotIn("older 5", page)
        self.assertIn("Older entries, one file per month: [[log/archive/p/2026-10]] · [[log/archive/p/2026-09]]", page)
        self.assertTrue(page.startswith("# p: session log\nOne entry per session.\n"))
        self.assertIn("older 5", (v / "log/archive/p/2026-10.md").read_text())
        self.assertIn("[[log/p]]", (v / "log/archive/p/2026-10.md").read_text())
        self.assertIn("not an entry", (v / "log/archive/p/2026-09.md").read_text(), "a fenced heading stays in its entry")
        self.assertEqual(vt.run(v, TODAY)["changed"], [], "a second run changes nothing")
        # Over budget: the oldest leave first, and everything older than the first to leave goes too.
        old = vt.LOG_BUDGET
        vt.LOG_BUDGET = 200
        try:
            vt.run(v, TODAY)
        finally:
            vt.LOG_BUDGET = old
        page = (v / "log/p.md").read_text()
        self.assertIn("work of the 12th", page)
        self.assertNotIn("work of the 1th", page)
        found = [e for p in [v / "log/p.md", *v.glob("log/archive/p/*.md")] for e in vt.log_entries(p.read_text())[1]]
        self.assertEqual(sorted(found), sorted(e.rstrip("\n") + "\n" for e in entries), "every entry is somewhere, once")

    def test_empty_inbox_wrappers_fold_into_their_entries(self):
        text = ("# p: session log\n\n## 2026-11-12 09:02 UTC (from the inbox, restricted session)\n\n"
                "## 2026-11-09 01:08 · cut control\nDid it.\n\n## 2026-11-08 10:00 · x\nwork\n")
        v = vault({"log/p.md": text})
        vt.run(v, TODAY)
        page = (v / "log/p.md").read_text()
        self.assertIn("## 2026-11-09 01:08 · cut control (from the inbox, restricted session)\nDid it.\n", page)
        self.assertNotIn("09:02 UTC", page)
        self.assertEqual(len(vt.log_entries(page)[1]), 2)
        self.assertEqual(vt.run(v, TODAY)["changed"], [])

    def test_a_quiet_log_and_the_readme_are_left_alone(self):
        text = "# p: session log\n\n\n## 2026-11-11 10:00 · x\nwork\n\n\n"
        readme = "# Logs\n\nOne file per project, newest entry first.\n"
        v = vault({"log/p.md": text, "log/README.md": readme})
        self.assertEqual(vt.run(v, TODAY)["changed"], [])
        self.assertEqual((v / "log/p.md").read_text(), text)
        self.assertFalse((v / "log/archive").exists())

    def test_dry_run_writes_nothing(self):
        text = "# p: session log\n\n## 2026-01-10 10:00 · x\nold\n"
        v = vault({"log/p.md": text})
        s = vt.run(v, TODAY, dry_run=True)
        self.assertIn("log/p.md", s["changed"])
        self.assertEqual((v / "log/p.md").read_text(), text)
        self.assertFalse((v / "log/archive").exists())


if __name__ == "__main__":
    unittest.main(verbosity=1)
