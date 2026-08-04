"""Tests for event-driven CDP connection health."""

from __future__ import annotations

import unittest
from pathlib import Path

from codex_usage_hud.core.connection_health import (
    ConnectionHealth,
    FAIL_THRESHOLD,
    HEALTH_FAILED,
    HEALTH_OK,
    HEALTH_RECOVERING,
    PROBE_FAIL_SECONDS,
    PROBE_IDLE_SECONDS,
    STUCK_FOLLOW_SECONDS,
)


class ConnectionHealthTests(unittest.TestCase):
    def test_success_resets_failures_and_schedules_idle_probe(self) -> None:
        health = ConnectionHealth()
        now = 100.0
        health.note_failure("update-failed", now=now)
        health.note_success("update-ok", now=now + 1.0)
        self.assertEqual(health.state, HEALTH_OK)
        self.assertEqual(health.consecutive_failures, 0)
        self.assertAlmostEqual(health.next_probe_at, now + 1.0 + PROBE_IDLE_SECONDS)

    def test_two_failures_turn_light_red(self) -> None:
        health = ConnectionHealth()
        health.note_failure("update-failed", now=10.0)
        self.assertEqual(health.state, HEALTH_RECOVERING)
        self.assertEqual(health.consecutive_failures, 1)
        for _ in range(FAIL_THRESHOLD - 1):
            health.note_failure("update-failed", now=11.0)
        self.assertEqual(health.state, HEALTH_FAILED)
        self.assertGreaterEqual(health.consecutive_failures, FAIL_THRESHOLD)
        self.assertAlmostEqual(health.next_probe_at, 11.0 + PROBE_FAIL_SECONDS)

    def test_channel_unavailable_marks_failed_and_allows_heal(self) -> None:
        health = ConnectionHealth()
        health.note_success("ok", now=50.0)
        health.note_channel_unavailable("channel-unavailable", now=60.0)
        self.assertFalse(health.channel_available)
        self.assertEqual(health.state, HEALTH_FAILED)
        should, reason = health.should_heal(
            now=60.0,
            follow_state="pending",
            follow_reason="renderer-channel-unavailable",
            follow_elapsed_ms=100,
        )
        self.assertTrue(should)
        self.assertEqual(reason, "channel-unavailable")

    def test_probe_skipped_while_healthy_and_busy(self) -> None:
        health = ConnectionHealth()
        now = 200.0
        health.note_success("update-ok", now=now)
        self.assertFalse(
            health.should_probe(
                now=now + 1.0,
                follow_state="confirmed",
                follow_elapsed_ms=0,
                update_failures=0,
            )
        )
        self.assertTrue(
            health.should_probe(
                now=now + PROBE_IDLE_SECONDS + 0.1,
                follow_state="confirmed",
                follow_elapsed_ms=0,
                update_failures=0,
            )
        )

    def test_stuck_new_session_triggers_heal_after_grace(self) -> None:
        health = ConnectionHealth()
        health.note_success("ok", now=0.0)
        should, reason = health.should_heal(
            now=10.0,
            follow_state="new-session",
            follow_reason="new-session",
            follow_elapsed_ms=int(STUCK_FOLLOW_SECONDS * 1000) - 100,
        )
        self.assertFalse(should)
        should, reason = health.should_heal(
            now=10.0,
            follow_state="new-session",
            follow_reason="new-session",
            follow_elapsed_ms=int(STUCK_FOLLOW_SECONDS * 1000) + 100,
        )
        self.assertTrue(should)
        self.assertEqual(reason, "stuck-new-session")

    def test_transport_ok_with_stuck_follow_turns_light_yellow(self) -> None:
        health = ConnectionHealth()
        health.note_success("update-ok", now=1.0)
        health.observe_follow(
            follow_state="new-session",
            follow_reason="new-session",
            follow_stuck_elapsed_ms=int(STUCK_FOLLOW_SECONDS * 1000) + 50,
        )
        self.assertEqual(health.transport_state, HEALTH_OK)
        self.assertEqual(health.state, HEALTH_RECOVERING)
        self.assertEqual(health.reason, "stuck-new-session")
        payload = health.to_payload()
        self.assertEqual(payload["state"], HEALTH_RECOVERING)
        self.assertIn("卡住", str(payload["detail"]))

    def test_heal_no_progress_keeps_recovering(self) -> None:
        health = ConnectionHealth()
        health.note_success("update-ok", now=1.0)
        health.observe_follow(
            follow_state="new-session",
            follow_reason="new-session",
            follow_stuck_elapsed_ms=10_000,
        )
        health.note_healing("stuck-new-session", now=2.0)
        health.note_heal_no_progress("heal-no-progress", now=2.1)
        self.assertEqual(health.state, HEALTH_RECOVERING)
        self.assertEqual(health.reason, "heal-no-progress")

    def test_heal_backoff_blocks_immediate_retry(self) -> None:
        health = ConnectionHealth()
        health.note_success("ok", now=0.0)
        health.note_healing("stuck-new-session", now=5.0)
        should, _ = health.should_heal(
            now=5.1,
            follow_state="new-session",
            follow_reason="new-session",
            follow_elapsed_ms=10_000,
        )
        self.assertFalse(should)
        self.assertGreater(health.next_heal_at, 5.0)

    def test_payload_shape(self) -> None:
        health = ConnectionHealth()
        health.note_success("update-ok", now=1.0)
        payload = health.to_payload()
        self.assertEqual(payload["state"], HEALTH_OK)
        self.assertIn("detail", payload)
        self.assertTrue(payload["channelAvailable"])
        self.assertIn("followState", payload)


class ConnectionHealthPayloadTests(unittest.TestCase):
    def test_payload_from_snapshot_includes_connection_health(self) -> None:
        from codex_usage_hud.core.parser import ParsedSession
        from codex_usage_hud.renderer_payload_builder import payload_from_snapshot

        health = ConnectionHealth()
        health.note_failure("update-failed", now=1.0)
        health.note_failure("update-failed", now=2.0)
        payload = payload_from_snapshot(
            ParsedSession(status="waiting"),
            connection_health=health,
        ).to_json()
        self.assertIn("connectionHealth", payload)
        self.assertEqual(payload["connectionHealth"]["state"], HEALTH_FAILED)
        domains = payload["payloadDomains"]
        self.assertIn("connectionHealth", domains["currentSession"])
        self.assertIn("connectionHealth", domains["sessionSwitch"])
        self.assertIn("connectionHealth", domains["diagnostics"])

    def test_renderer_script_includes_connection_dot(self) -> None:
        from codex_usage_hud import renderer_client as renderer_hud

        script = renderer_hud.RENDERER_HUD_SCRIPT
        self.assertIn("codex-usage-hud-connection-dot", script)
        self.assertIn('data-field="connectionDot"', script)
        self.assertIn("applyConnectionHealth", script)
        self.assertIn("codex-usage-hud-connection-breathe", script)
        self.assertIn(
            "diagnosticsDomain.apply(root",
            script,
        )
        self.assertIn("header-title", script)
        self.assertIn("5600", script)  # extended composer follow-up
        expanded_start = script.index("function requestExpandedMarkup()")
        expanded_end = script.index("function escapeHtml", expanded_start)
        expanded = script[expanded_start:expanded_end]
        self.assertIn('class="codex-usage-hud-left-controls"', expanded)
        self.assertLess(
            expanded.index('class="codex-usage-hud-left-controls"'),
            expanded.index('data-field="requestLineExpanded"'),
        )


class StickyNewSessionChannelTests(unittest.TestCase):
    def test_new_session_preserves_channel_unavailable_reason(self) -> None:
        from codex_usage_hud.platforms.active_session import ActiveSessionTracker
        from tests.test_active_session import FakePlatform

        tracker = ActiveSessionTracker(
            platform=FakePlatform(),
            state_db=Path("state_5.sqlite"),
            sessions_root=Path("sessions"),
            session_index_path=Path("session_index.jsonl"),
            poll_ms=250,
            enabled=True,
            start_background_watcher=False,
        )
        tracker.observe_conversation_ref(new_session=True, source="renderer")
        self.assertTrue(tracker.mark_renderer_channel_unavailable("binding closed"))
        path = tracker.current_path()
        self.assertIsNone(path)
        self.assertEqual(tracker.follow_reason, "renderer-channel-unavailable")
        self.assertEqual(tracker.follow_state, "pending")
        self.assertTrue(
            str(tracker.latest_source).startswith("renderer-new-session")
            or tracker.latest_source == "renderer-new-session"
        )

    def test_repeated_new_session_does_not_reset_stuck_clock(self) -> None:
        from codex_usage_hud.platforms.active_session import ActiveSessionTracker
        from tests.test_active_session import FakePlatform

        tracker = ActiveSessionTracker(
            platform=FakePlatform(),
            state_db=Path("state_5.sqlite"),
            sessions_root=Path("sessions"),
            session_index_path=Path("session_index.jsonl"),
            poll_ms=250,
            enabled=True,
            start_background_watcher=False,
        )
        first_at = 1_700_000_000_000
        tracker.observe_conversation_ref(
            new_session=True,
            source="renderer",
            observed_at_ms=first_at,
            selection_seq=1,
        )
        self.assertEqual(tracker.follow_stuck_since_ms, first_at)
        self.assertEqual(tracker.selection_observed_at_ms, first_at)
        tracker.observe_conversation_ref(
            new_session=True,
            source="renderer",
            observed_at_ms=first_at + 5_000,
            selection_seq=2,
        )
        self.assertEqual(tracker.follow_stuck_since_ms, first_at)
        self.assertEqual(tracker.selection_observed_at_ms, first_at)
        self.assertGreaterEqual(tracker.follow_stuck_elapsed_ms, 0)

    def test_follow_progressed_detects_leaving_new_session(self) -> None:
        from codex_usage_hud.platforms.active_session import ActiveSessionTracker

        before = {
            "followState": "new-session",
            "newSession": True,
            "sessionId": "",
            "path": "",
        }
        after = {
            "followState": "pending",
            "newSession": False,
            "sessionId": "thread-1",
            "path": "",
        }
        self.assertTrue(ActiveSessionTracker.follow_progressed(before, after))
        self.assertFalse(ActiveSessionTracker.follow_progressed(before, before))

    def test_pending_map_repeated_observe_keeps_stuck_clock(self) -> None:
        import tempfile

        from codex_usage_hud.platforms.active_session import ActiveSessionTracker
        from tests.test_active_session import FakePlatform

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "state_5.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )
            first_at = 1_700_000_000_000
            tracker.observe_conversation_ref(
                "local:unmapped-thread",
                "Unmapped",
                source="renderer",
                observed_at_ms=first_at,
                selection_seq=1,
            )
            self.assertEqual(tracker.follow_state, "pending")
            self.assertEqual(tracker.follow_reason, "awaiting-exact-mapping")
            self.assertEqual(tracker.follow_stuck_since_ms, first_at)
            tracker.observe_conversation_ref(
                "local:unmapped-thread",
                "Unmapped",
                source="renderer",
                observed_at_ms=first_at + 8_000,
                selection_seq=2,
            )
            self.assertEqual(tracker.follow_stuck_since_ms, first_at)
            self.assertEqual(tracker.selection_observed_at_ms, first_at)

    def test_rematerialize_uses_filesystem_fallback_for_known_id(self) -> None:
        import tempfile

        from codex_usage_hud.platforms.active_session import ActiveSessionTracker
        from tests.test_active_session import FakePlatform, _write_thread_mapping

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-mapped-thread-abc.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            # No state_db row — only the filename carries the id.
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=root / "missing_state.sqlite",
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )
            tracker.observe_conversation_ref(
                "mapped-thread-abc",
                "Mapped Thread",
                source="renderer",
                selection_seq=1,
                observed_at_ms=1_700_000_000_000,
            )
            self.assertEqual(tracker.follow_reason, "awaiting-exact-mapping")
            self.assertIsNone(tracker.current_path())
            self.assertTrue(tracker.rematerialize_renderer_mapping(force=True))
            self.assertEqual(tracker.follow_state, "confirmed")
            self.assertEqual(tracker.current_path(), session_path)

    def test_rematerialize_title_only_pending_map(self) -> None:
        import tempfile

        from codex_usage_hud.platforms.active_session import ActiveSessionTracker
        from tests.test_active_session import FakePlatform, _write_thread_mapping

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            sessions_root = root / "sessions"
            sessions_root.mkdir()
            session_path = sessions_root / "rollout-title-only.jsonl"
            session_path.write_text("{}\n", encoding="utf-8")
            state_db = root / "state_5.sqlite"
            _write_thread_mapping(
                state_db,
                "title-only-id",
                session_path,
                title="修复视频时长传递保存",
            )
            tracker = ActiveSessionTracker(
                platform=FakePlatform(),
                state_db=state_db,
                sessions_root=sessions_root,
                session_index_path=root / "session_index.jsonl",
                poll_ms=250,
                enabled=True,
                start_background_watcher=False,
            )
            # Live bug: title present, no session id, pending-map forever.
            tracker.observe_conversation_ref(
                "",
                "修复视频时长传递保存",
                source="renderer",
                selection_seq=1,
                observed_at_ms=1_700_000_000_000,
            )
            self.assertEqual(tracker.follow_state, "confirmed")
            self.assertEqual(tracker.current_path(), session_path)
            self.assertEqual(tracker.latest_session_id, "title-only-id")


if __name__ == "__main__":
    unittest.main()
