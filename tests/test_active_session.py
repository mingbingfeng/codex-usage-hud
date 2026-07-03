"""Unit tests for active-session tracking and session resolution."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.detect_calls = 0

    def get_codex_data_dir(self) -> Path:
        return Path.home() / ".codex"

    def detect_active_session(self, sessions_root: Path) -> Path | None:
        del sessions_root
        self.detect_calls += 1
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


class FakeCdpRefPlatform(FakePlatform):
    def __init__(self, session_id: str, title: str) -> None:
        super().__init__()
        self.session_id = session_id
        self.title = title
        self.ref_calls = 0

    def get_active_conversation_ref(self) -> tuple[str, str] | None:
        self.ref_calls += 1
        return self.session_id, self.title


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
            time.sleep(0.6)
        finally:
            watcher.close()

        self.assertEqual([item[0] for item in received], ["Event Selected Thread"])
        self.assertEqual(received[0][1], "event")

    def test_realtime_watcher_backstop_polls_after_event_gap(self) -> None:
        platform = FakeEventTitlePlatform(
            ["Event Selected Thread"],
            poll_titles=["Polled New Thread"],
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
            deadline = time.time() + 2.0
            while time.time() < deadline and len(received) < 2:
                time.sleep(0.02)
        finally:
            watcher.close()

        self.assertEqual(
            [item[:2] for item in received[:2]],
            [
                ("Event Selected Thread", "event"),
                ("Polled New Thread", "poll-backstop"),
            ],
        )

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

    def test_path_from_session_index_resolves_elided_long_title_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-long-title-session.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            long_title = (
                "查看首扫页/批扫扫描时图片落盘（D:\\ScanSystemData\\2026-06-23）"
                "时文件的DPI参数，我实测了更多内容"
            )

            session_index = root / "session_index.jsonl"
            session_index.write_text(
                json.dumps(
                    {"id": "long-title-session", "thread_name": long_title},
                    ensure_ascii=False,
                )
                + "\n",
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
                tracker.path_from_session_index("查看首扫页/批扫扫描时图片落盘…"),
                session_path,
            )
            self.assertIsNone(tracker.path_from_session_index("初始…"))

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

    def test_current_path_prefers_cdp_session_id_over_title_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-cdp-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            session_index = root / "session_index.jsonl"
            session_index.write_text(
                "\n".join(
                    [
                        '{"id":"other-thread","thread_name":"Duplicate Title","updated_at":"2026-05-28T00:00:00Z"}',
                        '{"id":"cdp-thread-123","thread_name":"Resolved CDP Title","updated_at":"2026-05-29T00:00:00Z"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            tracker = ActiveSessionTracker(
                platform=FakeCdpRefPlatform("cdp-thread-123", "Duplicate Title"),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=500,
                enabled=True,
            )

            self.assertEqual(tracker.current_path(), session_path)
            self.assertEqual(tracker.latest_title, "Resolved CDP Title")
            self.assertEqual(tracker.latest_source, "cdp:Resolved CDP Title")

    def test_path_from_thread_id_reuses_cached_rollout_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "rollout-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=500,
                enabled=False,
            )

            with patch(
                "codex_usage_hud.platforms.active_session.find_session_file",
                return_value=session_path,
            ) as finder:
                self.assertEqual(tracker.path_from_thread_id("thread-123"), session_path)
                self.assertEqual(tracker.path_from_thread_id("thread-123"), session_path)

        finder.assert_called_once_with("thread-123", root)

    def test_path_from_thread_id_prefers_state_db_before_file_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")

            state_db = root / "state_5.sqlite"
            con = sqlite3.connect(state_db)
            try:
                con.execute(
                    "create table threads ("
                    "id text primary key, "
                    "rollout_path text not null, "
                    "archived integer not null default 0, "
                    "updated_at_ms integer, "
                    "updated_at integer)"
                )
                con.execute(
                    "insert into threads (id, rollout_path, archived, updated_at_ms, updated_at) "
                    "values (?, ?, 0, 10, 10)",
                    ("thread-123", "\\\\?\\" + str(session_path)),
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

            with patch(
                "codex_usage_hud.platforms.active_session.find_session_file",
                side_effect=AssertionError("recursive file scan should not run"),
            ) as finder:
                self.assertEqual(tracker.path_from_thread_id("thread-123"), session_path)
                tracker.invalidate_mapping_cache()
                self.assertEqual(tracker.path_from_thread_id("local:thread-123"), session_path)

        finder.assert_not_called()

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

    def test_start_can_skip_background_watcher_for_renderer_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_path = root / "rollout-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            platform = FakeInProcessTitlePlatform(["Selected Thread"])
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            tracker.start()

            self.assertIsNone(tracker._watcher)
            self.assertIsNone(tracker._thread)
            self.assertFalse(platform.command_requested)
            self.assertTrue(
                tracker.observe_conversation_ref(
                    "thread-123",
                    "Renderer Selected Thread",
                )
            )
            self.assertEqual(tracker.current_path(), session_path)
            self.assertEqual(tracker.latest_source, "renderer:Renderer Selected Thread")

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

    def test_invalidate_mapping_cache_clears_title_and_thread_path_cache(self) -> None:
        tracker = ActiveSessionTracker(
            platform=FakePlatform(),
            state_db=Path("state_5.sqlite"),
            sessions_root=Path("sessions"),
            session_index_path=Path("session_index.jsonl"),
            poll_ms=250,
            enabled=True,
        )
        tracker._title_cache_key = ("thread-1", "path")
        tracker._title_cache_value = "Cached Title"
        tracker._thread_path_cache["thread-1"] = (None, 1.0)

        tracker.invalidate_mapping_cache()

        self.assertIsNone(tracker._title_cache_key)
        self.assertEqual(tracker._title_cache_value, "")
        self.assertEqual(tracker._thread_path_cache, {})

    def test_current_path_resolves_archived_session_from_cdp_thread_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            archived_root = root / "archived_sessions" / "2026" / "06"
            sessions_root.mkdir()
            archived_root.mkdir(parents=True)
            archived_session = archived_root / "session-archived-123.jsonl"
            archived_session.write_text("{}\n", encoding="utf-8")

            tracker = ActiveSessionTracker(
                platform=FakeCdpRefPlatform("archived-123", "Archived Thread"),
                state_db=root / "missing.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )

            self.assertEqual(tracker.current_path(), archived_session)
            self.assertEqual(tracker.latest_source, "cdp:Archived Thread")

    def test_renderer_observed_ref_bypasses_cdp_probe_in_current_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-renderer-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            platform = FakeCdpRefPlatform("stale-cdp-thread", "Stale CDP Thread")
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )

            changed = tracker.observe_conversation_ref(
                "renderer-thread-123",
                "Renderer Selected Thread",
            )
            tracker.latest_event_source = "cdp"
            tracker.latest_source = "cdp:Stale CDP Thread"
            tracker.latest_title = "Stale CDP Thread"
            tracker.latest_session_id = "stale-cdp-thread"
            tracker.latest_path = None

            self.assertTrue(changed)
            self.assertEqual(tracker.current_path(), session_path)
            self.assertEqual(platform.ref_calls, 0)
            self.assertEqual(tracker.latest_source, "renderer:Renderer Selected Thread")

    def test_renderer_authoritative_tracker_skips_cdp_ref_without_renderer_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-cdp-thread-123.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            platform = FakeCdpRefPlatform("cdp-thread-123", "CDP Thread")
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )

            self.assertIsNone(tracker.current_path())
            self.assertEqual(platform.ref_calls, 0)
            self.assertEqual(tracker.latest_source, "renderer-waiting")

    def test_renderer_new_session_clears_previous_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            stale_path = sessions_root / "rollout-stale-thread.jsonl"
            stale_path.write_text("{}\n", encoding="utf-8")
            platform = FakeCdpRefPlatform("stale-thread", "Stale CDP Thread")
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )
            tracker.latest_title = "Stale Thread"
            tracker.latest_session_id = "stale-thread"
            tracker.latest_path = stale_path
            tracker._mapped_title = "Stale Thread"
            tracker._renderer_session_id = "stale-thread"
            tracker._renderer_title = "Stale Thread"
            tracker._renderer_path = stale_path

            changed = tracker.observe_conversation_ref(
                "",
                "",
                source="renderer",
                new_session=True,
            )

            self.assertTrue(changed)
            self.assertIsNone(tracker.current_path())
            self.assertEqual(platform.ref_calls, 0)
            self.assertEqual(tracker.latest_title, "")
            self.assertEqual(tracker.latest_session_id, "")
            self.assertIsNone(tracker.latest_path)
            self.assertEqual(tracker.latest_source, "renderer-new-session")

    def test_active_session_change_callback_runs_for_background_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-thread-1.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            session_index = root / "session_index.jsonl"
            session_index.write_text(
                '{"id":"thread-1","thread_name":"Selected Thread"}\n',
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
            changes: list[str] = []
            tracker.set_change_callback(lambda: changes.append("changed"))

            try:
                tracker.start()
                deadline = time.time() + 1.0
                while time.time() < deadline and not changes:
                    time.sleep(0.02)
            finally:
                tracker.close()

            self.assertEqual(changes, ["changed"])

    def test_current_path_direct_poll_overrides_stale_event_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            old_path = sessions_root / "rollout-old-thread.jsonl"
            new_path = sessions_root / "rollout-new-thread.jsonl"
            old_path.write_text("{}\n", encoding="utf-8")
            new_path.write_text("{}\n", encoding="utf-8")
            session_index = root / "session_index.jsonl"
            session_index.write_text(
                "\n".join(
                    [
                        '{"id":"old-thread","thread_name":"Old Thread"}',
                        '{"id":"new-thread","thread_name":"New Thread"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            tracker = ActiveSessionTracker(
                platform=FakeInProcessTitlePlatform(["New Thread"]),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=session_index,
                poll_ms=250,
                enabled=True,
            )
            tracker.latest_title = "Old Thread"
            tracker.latest_path = old_path
            tracker._mapped_title = "Old Thread"

            self.assertEqual(tracker.current_path(), new_path)
            self.assertEqual(tracker.latest_title, "New Thread")
            self.assertEqual(tracker.latest_source, "ui:New Thread")

    def test_current_path_clears_stale_event_title_when_direct_poll_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stale_path = root / "stale.jsonl"
            stale_path.write_text("{}\n", encoding="utf-8")
            tracker = ActiveSessionTracker(
                platform=FakeInProcessTitlePlatform([""]),
                state_db=root / "state_5.sqlite",
                sessions_root=root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
            )
            tracker.latest_title = "Stale Thread"
            tracker.latest_path = stale_path
            tracker._mapped_title = "Stale Thread"

            self.assertIsNone(tracker.current_path())
            self.assertEqual(tracker.latest_title, "")
            self.assertIsNone(tracker.latest_path)
            self.assertEqual(tracker.latest_source, "ui-unmatched")


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

    def test_resolver_does_not_fallback_to_activity_for_renderer_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            latest = root / "latest.jsonl"
            latest.write_text("{}\n", encoding="utf-8")
            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=latest),
                sessions_root=root,
                active_session_tracker=_TrackerStub(
                    None,
                    source="renderer-new-session",
                ),
            )
            resolver.auto_session_file = latest

            path, source = resolver.resolve()

            self.assertIsNone(path)
            self.assertEqual(source, "renderer-new-session")
            self.assertIsNone(resolver.auto_session_file)

    def test_resolver_does_not_fallback_to_activity_while_renderer_tracker_waits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            latest = sessions_root / "latest.jsonl"
            latest.write_text("{}\n", encoding="utf-8")
            platform = FakeCdpRefPlatform("cdp-thread-123", "CDP Thread")
            platform.latest_session = latest
            tracker = ActiveSessionTracker(
                platform=platform,
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )
            resolver = SessionPathResolver(
                platform=platform,
                sessions_root=sessions_root,
                active_session_tracker=tracker,
            )

            path, source = resolver.resolve()

            self.assertIsNone(path)
            self.assertEqual(source, "renderer-waiting")
            self.assertIsNone(resolver.auto_session_file)
            self.assertEqual(platform.ref_calls, 0)
            self.assertEqual(platform.detect_calls, 0)

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

    def test_resolver_bypasses_idle_gate_for_unresolved_tracker_switch(self) -> None:
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
                active_session_tracker=_TrackerStub(None, source="ui-unmatched"),
                auto_switch_idle_seconds=30.0,
            )
            resolver.auto_session_file = current

            path, source = resolver.resolve()

            self.assertEqual(path, latest)
            self.assertEqual(source, "ui-unmatched+activity")

    def test_resolver_does_not_fallback_to_activity_for_unresolved_renderer_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current = root / "current.jsonl"
            latest = root / "latest.jsonl"
            current.write_text("{}\n", encoding="utf-8")
            latest.write_text("{}\n", encoding="utf-8")

            now = time.time()
            os.utime(current, (now, now))
            os.utime(latest, (now + 5, now + 5))

            platform = FakePlatform(latest_session=latest)
            resolver = SessionPathResolver(
                platform=platform,
                sessions_root=root,
                active_session_tracker=_TrackerStub(None, source="renderer-unmatched"),
                auto_switch_idle_seconds=30.0,
            )
            resolver.auto_session_file = current

            path, source = resolver.resolve()

            self.assertIsNone(path)
            self.assertEqual(source, "renderer-unmatched")
            self.assertIsNone(resolver.auto_session_file)
            self.assertEqual(platform.detect_calls, 0)

    def test_resolver_switches_to_archived_latest_when_tracker_is_unmatched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            archived_root = root / "archived_sessions" / "2026" / "06"
            sessions_root.mkdir()
            archived_root.mkdir(parents=True)

            current = sessions_root / "current.jsonl"
            archived_latest = archived_root / "archived-latest.jsonl"
            current.write_text("{}\n", encoding="utf-8")
            archived_latest.write_text("{}\n", encoding="utf-8")

            now = time.time()
            os.utime(current, (now, now))
            os.utime(archived_latest, (now + 5, now + 5))

            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=archived_latest),
                sessions_root=sessions_root,
                active_session_tracker=_TrackerStub(None, source="ui-unmatched"),
                auto_switch_idle_seconds=30.0,
            )
            resolver.auto_session_file = current

            path, source = resolver.resolve()

            self.assertEqual(path, archived_latest)
            self.assertEqual(source, "ui-unmatched+activity")

    def test_resolver_clears_stale_missing_auto_session_when_no_latest_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing = root / "missing.jsonl"

            resolver = SessionPathResolver(
                platform=FakePlatform(latest_session=None),
                sessions_root=root,
                active_session_tracker=_TrackerStub(None, source="ui-unmatched"),
            )
            resolver.auto_session_file = missing

            path, source = resolver.resolve()

            self.assertIsNone(path)
            self.assertIsNone(resolver.auto_session_file)
            self.assertEqual(source, "ui-unmatched")


if __name__ == "__main__":
    unittest.main()
