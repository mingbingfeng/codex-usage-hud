from types import SimpleNamespace
from unittest.mock import patch

from codex_usage_hud import desktop_overlay, overlay_supervision
from codex_usage_hud.desktop_overlay import DesktopWorkOverlay


def test_clean_helper_exit_is_immediately_restartable() -> None:
    decision = overlay_supervision.evaluate_helper_health(
        process_exit_code=0,
        user_object_count=10_000,
        helper_started_at=10.0,
        last_heartbeat_at=10.0,
        now_monotonic=50.0,
        now_wall=50.0,
        heartbeat_timeout_seconds=35.0,
        max_user_objects=2_000,
        restart_backoff_seconds=60.0,
    )

    assert decision == overlay_supervision.HelperHealthDecision(
        action=overlay_supervision.EXITED,
        exit_code=0,
    )


def test_failed_helper_exit_uses_bounded_backoff_and_reason() -> None:
    decision = overlay_supervision.evaluate_helper_health(
        process_exit_code=3,
        user_object_count=None,
        helper_started_at=10.0,
        last_heartbeat_at=10.0,
        now_monotonic=50.0,
        now_wall=50.0,
        heartbeat_timeout_seconds=35.0,
        max_user_objects=2_000,
        restart_backoff_seconds=60.0,
    )

    assert decision.action == overlay_supervision.EXITED
    assert decision.exit_code == 3
    assert decision.restart_blocked_until == 110.0
    assert decision.reason.endswith("code 3")


def test_resource_and_heartbeat_failures_request_restart() -> None:
    resource = overlay_supervision.evaluate_helper_health(
        process_exit_code=None,
        user_object_count=2_000,
        helper_started_at=10.0,
        last_heartbeat_at=10.0,
        now_monotonic=20.0,
        now_wall=20.0,
        heartbeat_timeout_seconds=35.0,
        max_user_objects=2_000,
        restart_backoff_seconds=60.0,
    )
    stale = overlay_supervision.evaluate_helper_health(
        process_exit_code=None,
        user_object_count=None,
        helper_started_at=10.0,
        last_heartbeat_at=10.0,
        now_monotonic=50.0,
        now_wall=45.0,
        heartbeat_timeout_seconds=35.0,
        max_user_objects=2_000,
        restart_backoff_seconds=60.0,
    )

    assert resource == overlay_supervision.HelperHealthDecision(
        action=overlay_supervision.RESTART,
        reason="user_objects=2000",
    )
    assert stale == overlay_supervision.HelperHealthDecision(
        action=overlay_supervision.RESTART,
        reason="heartbeat_age_seconds=35.0",
    )


def test_recent_heartbeat_and_missing_start_are_healthy() -> None:
    for started_at, heartbeat_at in ((0.0, 0.0), (10.0, 12.0)):
        decision = overlay_supervision.evaluate_helper_health(
            process_exit_code=None,
            user_object_count=None,
            helper_started_at=started_at,
            last_heartbeat_at=heartbeat_at,
            now_monotonic=20.0,
            now_wall=20.0,
            heartbeat_timeout_seconds=35.0,
            max_user_objects=2_000,
            restart_backoff_seconds=60.0,
        )
        assert decision == overlay_supervision.HelperHealthDecision(
            action=overlay_supervision.HEALTHY,
        )


def test_availability_probe_caches_and_normalizes_failure_reason() -> None:
    probe_calls = 0

    def probe() -> bool:
        nonlocal probe_calls
        probe_calls += 1
        raise ImportError("PySide6 missing")

    failed = overlay_supervision.probe_runtime_availability(
        cached=None,
        probe=probe,
    )
    cached = overlay_supervision.probe_runtime_availability(
        cached=False,
        probe=lambda: True,
        unavailable_reason=failed.reason,
    )

    assert failed == overlay_supervision.RuntimeAvailabilityDecision(
        available=False,
        reason="PySide6 missing",
    )
    assert cached == failed
    assert probe_calls == 1


def test_keep_alive_policy_uses_minimum_delay_and_disabled_gates() -> None:
    assert overlay_supervision.next_keep_alive_seconds(
        closed=False,
        enabled=True,
        has_payload=True,
        has_rest_reminder=False,
        now_monotonic=20.0,
        last_state_write_at=0.0,
        keepalive_seconds=15.0,
    ) == 0.1
    assert overlay_supervision.next_keep_alive_seconds(
        closed=False,
        enabled=False,
        has_payload=True,
        has_rest_reminder=False,
        now_monotonic=20.0,
        last_state_write_at=0.0,
        keepalive_seconds=15.0,
    ) is None


def test_system_action_router_keeps_deferred_rows_and_first_match() -> None:
    matched, deferred, runtime_error = overlay_supervision.route_system_action_commands(
        [
            {"action": "activateSession", "sessionId": "thread-1"},
            {"action": "runtimeError", "message": "state read failed"},
            {
                "action": "restartCodex",
                "actionId": "restart-1",
            },
            {
                "action": "restartCodex",
                "actionId": "restart-1",
            },
            {
                "action": "restartCodex",
                "actionId": "stale",
            },
        ],
        accepted_actions={"restartCodex"},
        expected_action_id="restart-1",
    )

    assert matched == {"action": "restartCodex", "actionId": "restart-1"}
    assert deferred == [{"action": "activateSession", "sessionId": "thread-1"}]
    assert runtime_error == "state read failed"


def test_desktop_overlay_adapts_health_decision_from_pure_owner() -> None:
    overlay = DesktopWorkOverlay(item_limit=2)
    process = SimpleNamespace(poll=lambda: None)
    overlay._process = process
    decision = overlay_supervision.HelperHealthDecision(
        action=overlay_supervision.HEALTHY,
    )

    with (
        patch.object(overlay, "_refresh_helper_heartbeat"),
        patch.object(desktop_overlay, "_windows_user_object_count", return_value=None),
        patch.object(
            overlay_supervision,
            "evaluate_helper_health",
            return_value=decision,
        ) as evaluate,
    ):
        overlay._ensure_helper_healthy(20.0)

    evaluate.assert_called_once()
    assert evaluate.call_args.kwargs["heartbeat_timeout_seconds"] == (
        desktop_overlay.WORK_OVERLAY_HELPER_HEARTBEAT_TIMEOUT_SECONDS
    )
    assert overlay._process is process
