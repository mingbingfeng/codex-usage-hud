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
    RealtimeSessionWatcher,
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


class FakeEventTitlePlatform(FakeInProcessTitlePlatform):
    def __init__(self, event_titles: list[str], poll_titles: list[str] | None = None) -> None:
        super().__init__(poll_titles or [])
        self.event_titles = event_titles
        self.events_requested = False

    def supports_active_title_events(self) -> bool:
        return True

    def watch_active_conversation_title(self, stop_event, on_title) -> bool:
        self.events_requested = True
        for title in self.event_titles:
            if stop_event.is_set():
                break
            on_title(title)
        return True


class FakeFailingEventTitlePlatform(FakeInProcessTitlePlatform):
    def __init__(self, poll_titles: list[str]) -> None:
        super().__init__(poll_titles)
        self.events_requested = False

    def supports_active_title_events(self) -> bool:
        return True

    def watch_active_conversation_title(self, stop_event, on_title) -> bool:
        del stop_event, on_title
        self.events_requested = True
        return False


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


class _TrackerSequenceStub:
    def __init__(
        self,
        paths: list[Path | None],
        source: str = "ui:selected",
        enabled: bool = True,
    ) -> None:
        self._paths = paths
        self.latest_source = source
        self.enabled = enabled

    def current_path(self) -> Path | None:
        if not self._paths:
            return None
        if len(self._paths) == 1:
            return self._paths[0]
        return self._paths.pop(0)


class ActiveSessionTrackerTests(unittest.TestCase):
    def test_realtime_watcher_falls_back_to_polling_when_events_fail(self) -> None:
        platform = FakeFailingEventTitlePlatform(["Selected Thread"])
        received: list[tuple[str, str, float]] = []
        watcher = RealtimeSessionWatcher(
            platform,
            250,
            lambda title, source, detected_at: received.append(
                (title, source, detected_at)
            ),
        )

        try:
            self.assertTrue(watcher.start())
            deadline = time.time() + 1.0
            while time.time() < deadline and not received:
                time.sleep(0.02)
        finally:
            watcher.close()

        self.assertTrue(platform.events_requested)
        self.assertEqual(received[0][0], "Selected Thread")
        self.assertEqual(received[0][1], "poll")
        self.assertGreater(received[0][2], 0)

    def test_realtime_watcher_does_not_poll_over_realtime_event(self) -> None:
        platform = FakeEventTitlePlatform(
            ["Event Selected Thread"],
            poll_titles=["Poll Should Not Override"],
        )
        received: list[tuple[str, str, float]] = []
        watcher = RealtimeSessionWatcher(
            platform,
            250,
            lambda title, source, detected_at: received.append(
                (title, source, detected_at)
            ),
        )

        try:
            self.assertTrue(watcher.start())
            time.sleep(1.2)
        finally:
            watcher.close()

        self.assertEqual([item[0] for item in received], ["Event Selected Thread"])
        self.assertEqual(received[0][1], "event")

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

    def test_start_prefers_event_stream_over_polling(self) -> None:
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
            platform = FakeEventTitlePlatform(
                ["Selected Thread"],
                poll_titles=["Poll Fallback Title"],
            )
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
            )

            try:
                tracker.start()
                deadline = time.time() + 1.0
                path = None
                while time.time() < deadline:
                    path = tracker.current_path()
                    if path is not None:
                        break
                    time.sleep(0.02)
            finally:
                tracker.close()

            self.assertTrue(platform.events_requested)
            self.assertFalse(platform.command_requested)
            self.assertEqual(path, session_path)
            self.assertEqual(tracker.latest_title, "Selected Thread")

    def test_event_stream_logs_switch_response_latency(self) -> None:
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
                platform=FakeEventTitlePlatform(["Selected Thread"]),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
            )

            try:
                with self.assertLogs(
                    "codex_usage_hud.active_session",
                    level="INFO",
                ) as logs:
                    tracker.start()
                    deadline = time.time() + 1.0
                    while time.time() < deadline:
                        if tracker.current_path() is not None:
                            break
                        time.sleep(0.02)
            finally:
                tracker.close()

            combined = "\n".join(logs.output)
            self.assertIn("ACTIVE_SESSION_SWITCH", combined)
            self.assertIn("matched=True", combined)
            self.assertRegex(combined, r"response_ms=\d+\.\d")
            self.assertGreaterEqual(tracker.latest_response_ms, 0.0)

    def test_title_for_session_prefers_live_title_for_active_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "selected.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )

            tracker.latest_title = "Live Selected Thread"
            tracker.latest_path = session_path

            self.assertEqual(
                tracker.title_for_session(session_path, "session-1"),
                "Live Selected Thread",
            )


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

    def test_resolver_switches_immediately_when_tracker_selects_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.jsonl"
            second = root / "second.jsonl"
            first.write_text("{}\n", encoding="utf-8")
            second.write_text("{}\n", encoding="utf-8")

            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=second),
                sessions_root=root,
                active_session_tracker=_TrackerSequenceStub([first, second]),
            )

            first_path, first_source = resolver.resolve()
            second_path, second_source = resolver.resolve()

            self.assertEqual(first_path, first)
            self.assertEqual(first_source, "ui:selected")
            self.assertEqual(second_path, second)
            self.assertEqual(second_source, "ui:selected")


if __name__ == "__main__":
    unittest.main()
