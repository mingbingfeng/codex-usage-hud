from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from codex_usage_hud.core.connection_health import ConnectionHealth
from codex_usage_hud.renderer_connection import RendererConnectionManager


class _Tracker:
    def __init__(self) -> None:
        self.follow_state = "new-session"
        self.follow_reason = "renderer-channel-unavailable"
        self.follow_stuck_elapsed_ms = 6_000
        self.renderer_new_session = True
        self.rematerialize_renderer_mapping = MagicMock(return_value=False)

    def follow_snapshot(self) -> dict[str, object]:
        return {
            "followState": self.follow_state,
            "followReason": self.follow_reason,
            "newSession": self.renderer_new_session,
        }

    @staticmethod
    def follow_progressed(
        before: dict[str, object],
        after: dict[str, object],
    ) -> bool:
        return before.get("followState") != after.get("followState")


def _manager(
    *,
    client: object | None = None,
    tracker: object | None = None,
    health: ConnectionHealth | None = None,
    wake: MagicMock | None = None,
    schedule: MagicMock | None = None,
) -> tuple[RendererConnectionManager, object, MagicMock, MagicMock]:
    client = client or SimpleNamespace(update_payload=MagicMock(return_value=True))
    wake = wake or MagicMock()
    schedule = schedule or MagicMock()
    manager = RendererConnectionManager(
        client=client,
        tracker_provider=lambda: tracker,
        wake=wake,
        schedule_soft_reinstall=schedule,
        debug_enabled=lambda: True,
        runtime_errors=lambda: [{"code": "sample"}],
        health=health,
        wall_time=lambda: 20.0,
    )
    return manager, client, wake, schedule


def test_connection_light_wakes_before_enable_then_pushes_lightweight_payload() -> None:
    manager, client, wake, _ = _manager()

    manager.request_light()
    manager.enable_light_push()
    manager.request_light()

    wake.assert_called_once_with()
    payload = client.update_payload.call_args.args[0]
    assert set(payload) == {
        "connectionHealth",
        "debug",
        "runtimeErrors",
        "payloadDomains",
    }
    assert payload["payloadDomains"]["diagnostics"] == {
        "connectionHealth": payload["connectionHealth"],
        "debug": True,
        "runtimeErrors": [{"code": "sample"}],
    }


def test_connection_probe_is_bounded_by_health_deadline() -> None:
    health = ConnectionHealth(next_probe_at=float("inf"), last_ok_at=10.0)
    client = SimpleNamespace(
        probe_connection=MagicMock(return_value=True),
        update_payload=MagicMock(return_value=True),
        last_status="ok",
        last_error="",
    )
    manager, _, _, _ = _manager(client=client, health=health)
    manager.enable_light_push()

    assert not manager.maybe_probe(None, update_failures=0)
    client.probe_connection.assert_not_called()

    health.next_probe_at = 0.0
    health.last_ok_at = 0.0
    assert manager.maybe_probe(None, update_failures=0)
    client.probe_connection.assert_called_once_with()
    assert health.reason == "probe-ok"


def test_connection_heal_l1_requires_follow_progress() -> None:
    tracker = _Tracker()
    health = ConnectionHealth()
    health.note_channel_unavailable(now=0.0)

    def report(reason: str) -> bool:
        tracker.follow_state = "confirmed"
        tracker.follow_reason = "confirmed"
        tracker.renderer_new_session = False
        return True

    client = SimpleNamespace(
        report_active_session=MagicMock(side_effect=report),
        rebind_active_session_channel=MagicMock(return_value=False),
        update_payload=MagicMock(return_value=True),
    )
    manager, _, wake, schedule = _manager(
        client=client,
        tracker=tracker,
        health=health,
    )
    manager.enable_light_push()

    assert manager.maybe_heal(SimpleNamespace(follow_state="new-session"))
    client.report_active_session.assert_called_once_with("self-heal:channel-unavailable")
    assert health.state == "ok"
    wake.assert_called_once_with()
    schedule.assert_not_called()


def test_connection_heal_l3_schedules_soft_reinstall_once() -> None:
    tracker = _Tracker()
    health = ConnectionHealth()
    health.note_channel_unavailable(now=0.0)
    client = SimpleNamespace(
        report_active_session=MagicMock(return_value=False),
        rebind_active_session_channel=MagicMock(return_value=False),
        _clear_target_cache=MagicMock(),
        update_payload=MagicMock(return_value=True),
    )
    manager, _, wake, schedule = _manager(
        client=client,
        tracker=tracker,
        health=health,
    )
    manager.enable_light_push()

    assert manager.maybe_heal(SimpleNamespace(follow_state="new-session"))
    client._clear_target_cache.assert_called_once_with(clear_script=True)
    schedule.assert_called_once_with()
    wake.assert_called_once_with()


def test_activity_wake_rematerializes_pending_mapping_before_dom_report() -> None:
    tracker = _Tracker()
    tracker.follow_state = "pending"
    tracker.follow_reason = "awaiting-exact-mapping"

    def rematerialize(*, force: bool) -> bool:
        assert force
        tracker.follow_state = "confirmed"
        tracker.follow_reason = "confirmed"
        tracker.renderer_new_session = False
        return True

    tracker.rematerialize_renderer_mapping.side_effect = rematerialize
    client = SimpleNamespace(
        report_active_session=MagicMock(return_value=True),
        update_payload=MagicMock(return_value=True),
    )
    manager, _, wake, _ = _manager(client=client, tracker=tracker)
    manager.enable_light_push()

    assert manager.activity_wake(
        SimpleNamespace(follow_state="pending", follow_reason="awaiting-exact-mapping"),
        reason="session-map",
    )
    client.report_active_session.assert_not_called()
    wake.assert_called_once_with()
