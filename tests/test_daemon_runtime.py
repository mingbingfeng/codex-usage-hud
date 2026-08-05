from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from codex_usage_hud import daemon_runtime


def test_daemon_startup_attaches_to_existing_desktop() -> None:
    manager = SimpleNamespace(snapshot=lambda: SimpleNamespace(found=True))

    decision = daemon_runtime.daemon_startup_decision(SimpleNamespace(), manager)

    assert decision == daemon_runtime.DaemonStartupDecision(
        daemon_runtime.DEFAULT_DAEMON_STARTUP_WAIT,
        codex_was_running=True,
    )


def test_daemon_startup_launches_when_desktop_is_absent() -> None:
    manager = SimpleNamespace(snapshot=lambda: SimpleNamespace(found=False))

    decision = daemon_runtime.daemon_startup_decision(SimpleNamespace(), manager)

    assert decision == daemon_runtime.DaemonStartupDecision(
        daemon_runtime.DEFAULT_DAEMON_STARTUP_RENDERER,
        launch_codex=True,
    )


def test_hud_session_restarts_once_then_attaches() -> None:
    renderer = MagicMock(
        side_effect=[daemon_runtime.HUD_SWITCH_TO_RENDERER_RESTART_CODEX, 0]
    )
    restart = MagicMock(return_value=True)

    result = daemon_runtime.run_hud_session(
        SimpleNamespace(),
        run_renderer=renderer,
        restart_codex=restart,
    )

    assert result == 0
    restart.assert_called_once_with()
    assert renderer.call_count == 2
    assert renderer.call_args_list[1].kwargs["launched_codex"] is True


def test_existing_codex_replacement_still_waits_for_user_restart() -> None:
    manager = SimpleNamespace(
        wait_for_codex=MagicMock(),
        poll_seconds=0.1,
    )
    renderer = MagicMock(
        side_effect=[
            daemon_runtime.DAEMON_RESTART_REQUESTED,
            daemon_runtime.HUD_SWITCH_TO_RENDERER_RESTART_CODEX,
            0,
        ]
    )
    restart_codex = MagicMock(return_value=True)
    services = daemon_runtime.DaemonServices(
        manager_factory=lambda **_kwargs: manager,
        lock_factory=MagicMock(return_value=MagicMock()),
        configure_logging=MagicMock(),
        attach_logger=MagicMock(),
        hide_console=MagicMock(),
        startup_decision=MagicMock(
            return_value=daemon_runtime.DaemonStartupDecision(
                daemon_runtime.DEFAULT_DAEMON_STARTUP_WAIT,
                codex_was_running=True,
            )
        ),
        create_loading=MagicMock(),
        run_renderer=renderer,
        restart_codex=restart_codex,
    )

    result = daemon_runtime.run_daemon(
        SimpleNamespace(daemon_poll_ms=500),
        services=services,
    )

    assert result == 0
    assert renderer.call_count == 3
    assert renderer.call_args_list[0].kwargs["observed_codex_launch"] is False
    assert renderer.call_args_list[1].kwargs["observed_codex_launch"] is False
    assert renderer.call_args_list[2].kwargs["launched_codex"] is True
    assert renderer.call_args_list[2].kwargs["observed_codex_launch"] is False
    restart_codex.assert_called_once_with()


def test_removed_window_sessions_close_context_and_report_unavailable() -> None:
    context = SimpleNamespace(close=MagicMock())

    result = daemon_runtime.run_qt_window_session(context, SimpleNamespace())

    assert result == daemon_runtime.RENDERER_HUD_UNAVAILABLE
    context.close.assert_called_once_with()
