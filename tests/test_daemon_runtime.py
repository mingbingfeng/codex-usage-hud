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


def test_launched_codex_retries_initial_renderer_attach_before_restart() -> None:
    manager = SimpleNamespace(
        wait_for_codex=MagicMock(),
        poll_seconds=0.0,
    )
    loading = MagicMock()
    loading.start.return_value = loading
    renderer = MagicMock(
        side_effect=[daemon_runtime.RENDERER_HUD_UNAVAILABLE, 0]
    )
    launch = MagicMock(return_value=True)
    restart = MagicMock(return_value=True)
    services = daemon_runtime.DaemonServices(
        manager_factory=lambda **_kwargs: manager,
        lock_factory=MagicMock(return_value=MagicMock()),
        configure_logging=MagicMock(),
        attach_logger=MagicMock(),
        hide_console=MagicMock(),
        startup_decision=MagicMock(
            return_value=daemon_runtime.DaemonStartupDecision(
                daemon_runtime.DEFAULT_DAEMON_STARTUP_RENDERER,
                launch_codex=True,
            )
        ),
        create_loading=MagicMock(return_value=loading),
        launch=launch,
        run_renderer=renderer,
        restart_codex=restart,
    )

    result = daemon_runtime.run_daemon(
        SimpleNamespace(daemon_poll_ms=500),
        services=services,
    )

    assert result == 0
    assert renderer.call_count == 2
    assert manager.wait_for_codex.call_count == 2
    restart.assert_not_called()


def test_launched_codex_waits_for_replacement_after_recovery_restart(
    monkeypatch,
) -> None:
    manager = SimpleNamespace(
        last_snapshot=SimpleNamespace(primary_pid=27172),
        wait_for_codex=MagicMock(),
        wait_for_codex_replacement=MagicMock(return_value=True),
        poll_seconds=0.0,
    )
    loading = MagicMock()
    loading.start.return_value = loading
    renderer = MagicMock(
        side_effect=[
            daemon_runtime.RENDERER_HUD_UNAVAILABLE,
            daemon_runtime.RENDERER_HUD_UNAVAILABLE,
            daemon_runtime.RENDERER_HUD_UNAVAILABLE,
            0,
        ]
    )
    restart = MagicMock(return_value=True)
    retry = MagicMock(return_value=True)
    monkeypatch.setattr(
        daemon_runtime,
        "_wait_for_renderer_recovery_retry",
        retry,
    )
    services = daemon_runtime.DaemonServices(
        manager_factory=lambda **_kwargs: manager,
        lock_factory=MagicMock(return_value=MagicMock()),
        configure_logging=MagicMock(),
        attach_logger=MagicMock(),
        hide_console=MagicMock(),
        startup_decision=MagicMock(
            return_value=daemon_runtime.DaemonStartupDecision(
                daemon_runtime.DEFAULT_DAEMON_STARTUP_RENDERER,
                launch_codex=True,
            )
        ),
        create_loading=MagicMock(return_value=loading),
        launch=MagicMock(return_value=True),
        run_renderer=renderer,
        restart_codex=restart,
    )

    result = daemon_runtime.run_daemon(
        SimpleNamespace(daemon_poll_ms=500),
        services=services,
    )

    assert result == 0
    retry.assert_called_once_with(SimpleNamespace(daemon_poll_ms=500), services, None)
    restart.assert_called_once_with()
    manager.wait_for_codex_replacement.assert_called_once_with(27172)
    assert renderer.call_count == 4


def test_existing_codex_replacement_after_hud_started_relaunches_automatically() -> None:
    manager = SimpleNamespace(
        wait_for_codex=MagicMock(),
        poll_seconds=0.1,
    )
    renderer = MagicMock(
        side_effect=[
            daemon_runtime.DAEMON_RESTART_REQUESTED,
            daemon_runtime.HUD_AUTO_RESTART_CODEX,
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
    assert renderer.call_args_list[1].kwargs["observed_codex_launch"] is True
    assert renderer.call_args_list[2].kwargs["launched_codex"] is True
    assert renderer.call_args_list[2].kwargs["observed_codex_launch"] is False
    restart_codex.assert_called_once_with()


def test_session_lock_exit_keeps_daemon_and_restarts_renderer_after_unlock(
    monkeypatch,
) -> None:
    manager = SimpleNamespace(
        wait_for_codex=MagicMock(),
        poll_seconds=0.1,
    )
    renderer = MagicMock(
        side_effect=[daemon_runtime.HUD_SUSPEND_FOR_SESSION_LOCK, 0]
    )
    loading = MagicMock()
    loading.start.return_value = loading
    wait_for_unlock = MagicMock()
    monkeypatch.setattr(
        daemon_runtime,
        "_wait_for_session_unlock",
        wait_for_unlock,
    )
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
        create_loading=MagicMock(return_value=loading),
        run_renderer=renderer,
    )

    result = daemon_runtime.run_daemon(
        SimpleNamespace(daemon_poll_ms=500),
        services=services,
    )

    assert result == 0
    assert renderer.call_count == 2
    assert manager.wait_for_codex.call_count == 2
    wait_for_unlock.assert_called_once_with()


def test_wait_for_session_unlock_uses_native_unlock_event(monkeypatch) -> None:
    events: list[str] = []

    class FakeMonitor:
        def __init__(self, *, on_lock, on_unlock) -> None:
            del on_lock
            self._on_unlock = on_unlock

        def start(self, *, initial_locked: bool) -> None:
            assert initial_locked
            events.append("start")
            self._on_unlock()

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(daemon_runtime.sys, "platform", "win32")
    monkeypatch.setattr(daemon_runtime, "WindowsSessionLockMonitor", FakeMonitor)

    daemon_runtime._wait_for_session_unlock()

    assert events == ["start", "close"]


def test_removed_window_sessions_close_context_and_report_unavailable() -> None:
    context = SimpleNamespace(close=MagicMock())

    result = daemon_runtime.run_qt_window_session(context, SimpleNamespace())

    assert result == daemon_runtime.RENDERER_HUD_UNAVAILABLE
    context.close.assert_called_once_with()
