"""Unit tests for renderer filesystem invalidation."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms import file_watcher as fw
from codex_usage_hud.platforms.file_watcher import FileChangeWatcher, FileWatchSpec


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for file watcher event")


class FileChangeWatcherTests(unittest.TestCase):
    def test_polling_fallback_emits_file_reason_on_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hud_settings.json"
            path.write_text("{}", encoding="utf-8")
            events: list[tuple[set[str], set[Path]]] = []
            watcher = FileChangeWatcher(
                lambda reasons, paths: events.append((set(reasons), set(paths))),
                fallback_poll_seconds=0.05,
                force_polling=True,
            )
            try:
                watcher.update([FileWatchSpec.file(path, "settings")])
                path.write_text('{"daily_budget_usd": 1}', encoding="utf-8")
                _wait_for(
                    lambda: any("settings" in reasons for reasons, _paths in events)
                )
            finally:
                watcher.close()
        self.assertTrue(any(path in paths for _reasons, paths in events))

    def test_polling_fallback_treats_sqlite_wal_as_mapping_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "state_5.sqlite"
            db_path.write_text("", encoding="utf-8")
            events: list[tuple[set[str], set[Path]]] = []
            watcher = FileChangeWatcher(
                lambda reasons, paths: events.append((set(reasons), set(paths))),
                fallback_poll_seconds=0.05,
                force_polling=True,
            )
            try:
                watcher.update([FileWatchSpec.file(db_path, "session-map")])
                wal_path = root / "state_5.sqlite-wal"
                wal_path.write_text("changed", encoding="utf-8")
                _wait_for(
                    lambda: any("session-map" in reasons for reasons, _paths in events)
                )
            finally:
                watcher.close()
        self.assertTrue(any(wal_path in paths for _reasons, paths in events))

    def test_polling_fallback_emits_tree_reason_for_nested_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sessions"
            nested = root / "2026" / "06"
            nested.mkdir(parents=True)
            events: list[tuple[set[str], set[Path]]] = []
            watcher = FileChangeWatcher(
                lambda reasons, paths: events.append((set(reasons), set(paths))),
                fallback_poll_seconds=0.05,
                force_polling=True,
            )
            try:
                watcher.update(
                    [FileWatchSpec.tree(root, "sessions-root", suffixes=(".jsonl",))]
                )
                session_path = nested / "session.jsonl"
                session_path.write_text("{}\n", encoding="utf-8")
                _wait_for(
                    lambda: any(
                        "sessions-root" in reasons for reasons, _paths in events
                    )
                )
            finally:
                watcher.close()
        self.assertTrue(any(session_path in paths for _reasons, paths in events))

    def test_macos_recursive_tree_uses_polling_even_when_kqueue_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sessions"
            root.mkdir()
            watcher = FileChangeWatcher(
                lambda _reasons, _paths: None,
                fallback_poll_seconds=0.05,
            )
            try:
                with (
                    patch.object(sys, "platform", "darwin"),
                    patch.object(fw.select, "kqueue", MagicMock(), create=True) as kqueue,
                    patch.object(fw.select, "kevent", MagicMock(), create=True),
                ):
                    watcher.update(
                        [FileWatchSpec.tree(root, "sessions-root", suffixes=(".jsonl",))]
                    )
                    self.assertFalse(watcher.event_driven)
                    kqueue.assert_not_called()
            finally:
                watcher.close()


if __name__ == "__main__":
    unittest.main()
