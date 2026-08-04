from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from codex_usage_hud import daemon_runtime


def test_daemon_startup_attaches_to_existing_desktop() -> None:
    manager = SimpleNamespace(snapshot=lambda: SimpleNamespace(found=True))

    decision = daemon_runtime.daemon_startup_decision(SimpleNamespace(), manager)

    assert decision == daemon_runtime.DaemonStartupDecision(
        daemon_runtime.DEFAULT_DAEMON_STARTUP_WAIT
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


def test_removed_window_sessions_close_context_and_report_unavailable() -> None:
    context = SimpleNamespace(close=MagicMock())

    result = daemon_runtime.run_qt_window_session(context, SimpleNamespace())

    assert result == daemon_runtime.RENDERER_HUD_UNAVAILABLE
    context.close.assert_called_once_with()
