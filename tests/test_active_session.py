"""Unit tests for active-session tracking and session resolution."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms.active_session import (
    ActiveSessionTracker,
    SessionPathResolver,
)
from codex_usage_hud.platforms.base import BasePlatform


class FakePlatform(BasePlatform):
    def __init__(self, latest_session: Path | None = None) -> None:
        self.latest_session = latest_session

    def get_codex_data_dir(self) -> Path:
        return Path.home() / ".codex"

    def detect_active_session(self, sessions_root: Path) -> Path | None:
        del sessions_root
        return self.latest_session


class FakeInProcessTitlePlatform(FakePlatform):
    def __init__(self, titles: list[str]) -> None:
        super().__init__()
        self.titles = titles
        self.command_requested = False

    def supports_active_title_polling(self) -> bool:
        return True

    def get_active_conversation_title(self) -> str | None:
        if not self.titles:
            return None
        if len(self.titles) == 1:
            return self.titles[0]
        return self.titles.pop(0)

    def build_active_title_command(self, poll_ms: int) -> list[str] | None:
        del poll_ms
        self.command_requested = True
        return ["unexpected-powershell-fallback"]


class _TrackerStub:
    def __init__(
        self,
        path: Path | None,
        source: str = "ui:selected",
        enabled: bool = True,
    ) -> None:
        self._path = path
        self.latest_source = source
        self.enabled = enabled

    def current_path(self) -> Path | None:
        return self._path


class ActiveSessionTrackerTests(unittest.TestCase):
    def test_path_from_session_index_resolves_thread_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-abc-session-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")

            session_index = root / "session_index.jsonl"
            session_index.write_text(
                '{"id":"abc-session-123","thread_name":"Selected Thread","updated_at":"2026-05-28T00:00:00Z"}\n',
                encoding="utf-8",
            )

            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=500,
                enabled=False,
            )

            self.assertEqual(
                tracker.path_from_session_index("Selected Thread"),
                session_path,
            )
            self.assertEqual(
                tracker.path_from_session_index("Selected Thread extra"),
                session_path,
            )

    def test_path_for_title_falls_back_to_state_db_threads_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-from-state-db.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")

            state_db = root / "state_5.sqlite"
            con = sqlite3.connect(state_db)
            try:
                con.execute(
                    "create table threads ("
                    "id text primary key, "
                    "rollout_path text not null, "
                    "title text not null, "
                    "archived integer not null default 0, "
                    "updated_at_ms integer, "
                    "updated_at integer)"
                )
                con.execute(
                    "insert into threads (id, rollout_path, title, archived, updated_at_ms, updated_at) "
                    "values (?, ?, ?, 0, 10, 10)",
                    (
                        "thread-1",
                        "\\\\?\\" + str(session_path),
                        "Visible Conversation",
                    ),
                )
                con.commit()
            finally:
                con.close()

            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=500,
                enabled=False,
            )

            self.assertEqual(
                tracker.path_for_title("Visible Conversation"),
                session_path,
            )

    def test_start_uses_in_process_title_polling_without_command_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            platform = FakeInProcessTitlePlatform(["", "Selected Thread"])
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )

            try:
                tracker.start()
                deadline = time.time() + 1.0
                title = ""
                while time.time() < deadline:
                    with tracker._lock:
                        title = tracker.latest_title
                    if title:
                        break
                    time.sleep(0.02)
            finally:
                tracker.close()

            self.assertEqual(title, "Selected Thread")
            self.assertFalse(platform.command_requested)
            self.assertIsNone(tracker._proc)


class SessionPathResolverTests(unittest.TestCase):
    def test_resolver_prefers_tracker_selected_path_over_latest_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            selected = root / "selected.jsonl"
            latest = root / "latest.jsonl"
            selected.write_text("{}\n", encoding="utf-8")
            latest.write_text("{}\n", encoding="utf-8")

            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=latest),
                sessions_root=root,
                active_session_tracker=_TrackerStub(selected),
            )

            path, source = resolver.resolve()

            self.assertEqual(path, selected)
            self.assertEqual(source, "ui:selected")

    def test_resolver_waits_for_idle_before_switching_to_newer_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "current.jsonl"
            latest = root / "latest.jsonl"
            current.write_text("{}\n", encoding="utf-8")
            latest.write_text("{}\n", encoding="utf-8")

            now = time.time()
            os.utime(current, (now, now))
            os.utime(latest, (now + 5, now + 5))

            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=latest),
                sessions_root=root,
                auto_switch_idle_seconds=30.0,
            )
            resolver.auto_session_file = current

            path, source = resolver.resolve()

            self.assertEqual(path, current)
            self.assertEqual(source, "activity")


if __name__ == "__main__":
    unittest.main()
