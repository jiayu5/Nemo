"""The one-time desktop history merge preserves both sides and is repeat-safe."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from nemo.adapters.persistence import SQLiteRepository
from nemo.adapters.persistence.merge_desktop import merge_desktop_history


class DesktopMergeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.main = self.root / "nemo.db"
        self.desktop = self.root / "desktop.db"
        for path, session_id, run_id in (
            (self.main, "web", "web-run"),
            (self.desktop, "desktop", "desktop-run"),
        ):
            repository = SQLiteRepository(path)
            repository.create_session(
                session_id=session_id, workspace=self.root, model=None,
                approval_mode="full", user_instructions=None, project_instructions=None,
            )
            repository.create_run(run_id=run_id, session_id=session_id, prompt="hi", max_steps=1)
            repository.interrupt_run(run_id)
            repository.close()

    def test_merge_preserves_both_histories_and_backups(self):
        backups = merge_desktop_history(self.main, self.desktop)
        self.assertIsNotNone(backups)
        self.assertTrue(all(path.is_file() for path in backups))
        with sqlite3.connect(self.main) as connection:
            self.assertEqual(
                {row[0] for row in connection.execute("SELECT session_id FROM sessions")},
                {"web", "desktop"},
            )
            self.assertEqual(connection.execute("SELECT count(*) FROM runs").fetchone()[0], 2)
        self.assertIsNone(merge_desktop_history(self.main, self.desktop))
        with sqlite3.connect(self.desktop) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)

    def test_active_run_blocks_merge(self):
        with sqlite3.connect(self.desktop) as connection:
            connection.execute("UPDATE runs SET status = 'running' WHERE run_id = 'desktop-run'")
        with self.assertRaisesRegex(RuntimeError, "active runs"):
            merge_desktop_history(self.main, self.desktop)
