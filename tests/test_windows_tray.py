"""Focused tests for the Windows tray lifecycle and shutdown fan-out."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from codex_usage_hud import daemon_runtime
from codex_usage_hud import windows_tray


def test_shutdown_coordinator_notifies_bound_callback_once() -> None:
    coordinator = windows_tray.ShutdownCoordinator()
    callbacks: list[str] = []
    coordinator.bind(lambda: callbacks.append("exit"))

    assert coordinator.request() is True
    assert coordinator.request() is False
    assert coordinator.is_requested() is True
    assert callbacks == ["exit"]


def test_shutdown_coordinator_binds_after_request() -> None:
    coordinator = windows_tray.ShutdownCoordinator()
    assert coordinator.request() is True

    callbacks: list[str] = []
    coordinator.bind(lambda: callbacks.append("late-exit"))

    assert callbacks == ["late-exit"]


def test_windows_tray_is_a_noop_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(windows_tray.sys, "platform", "linux")
    callbacks: list[str] = []
    tray = windows_tray.WindowsTrayIcon(on_exit=lambda: callbacks.append("exit"))

    assert tray.start() is False
    tray.close()
    assert callbacks == []


def test_windows_tray_exit_callback_is_idempotent() -> None:
    callbacks: list[str] = []
    tray = windows_tray.WindowsTrayIcon(on_exit=lambda: callbacks.append("exit"))

    tray._request_exit()
    tray._request_exit()

    assert callbacks == ["exit"]


def test_resolve_tray_icon_path_uses_designed_ico_asset() -> None:
    path = windows_tray.resolve_tray_icon_path()

    assert path is not None
    assert path.name == "hud-app-icon.ico"
    assert Path(path).is_file()


def test_daemon_owns_tray_for_lifetime_and_forwards_shutdown() -> None:
    class FakeTray:
        def __init__(self, on_exit) -> None:
            self.on_exit = on_exit
            self.started = False
            self.closed = False

        def start(self) -> None:
            self.started = True

        def close(self) -> None:
            self.closed = True

    tray: FakeTray | None = None

    def create_tray(*, on_exit):
        nonlocal tray
        tray = FakeTray(on_exit)
        return tray

    manager = SimpleNamespace(wait_for_codex=MagicMock(), poll_seconds=0.0)
    loading = MagicMock()
    loading.start.return_value = loading

    def run_renderer(_args, **kwargs):
        kwargs["shutdown_coordinator"].request()
        return 0

    services = daemon_runtime.DaemonServices(
        manager_factory=lambda **_kwargs: manager,
        lock_factory=MagicMock(return_value=MagicMock()),
        configure_logging=MagicMock(),
        attach_logger=MagicMock(),
        hide_console=MagicMock(),
        startup_decision=MagicMock(
            return_value=daemon_runtime.DaemonStartupDecision(
                daemon_runtime.DEFAULT_DAEMON_STARTUP_WAIT,
            )
        ),
        create_loading=MagicMock(return_value=loading),
        run_renderer=run_renderer,
        create_tray=create_tray,
    )

    result = daemon_runtime.run_daemon(
        SimpleNamespace(daemon_poll_ms=500),
        services=services,
    )

    assert result == 0
    assert tray is not None and tray.started and tray.closed
