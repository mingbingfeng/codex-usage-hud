"""Persistent daemon lifecycle and renderer-only compatibility sessions."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, replace
import logging
import time
from typing import Any

from . import loading_feedback
from .codex_app_runtime import launch_codex_app, restart_codex_app
from .daemon import (
    CodexDaemonManager,
    ProcessListenerError,
    configure_daemon_logging,
    hide_console_window,
)
from .instance_lock import HudAlreadyRunningError, HudInstanceLock
from .runtime_diagnostics import append_renderer_diagnostic, attach_cli_logger_to_daemon_log


DEFAULT_DAEMON_STARTUP_WAIT = "wait"
DEFAULT_DAEMON_STARTUP_RENDERER = "renderer"
DEFAULT_DAEMON_STARTUP_CANCEL = "cancel"
DAEMON_RESTART_REQUESTED = 10
RENDERER_HUD_UNAVAILABLE = 20
HUD_SWITCH_TO_RENDERER = 31
HUD_SWITCH_TO_RENDERER_RESTART_CODEX = 32
HUD_AUTO_RESTART_CODEX = 33
WORK_OVERLAY_RESTART_ACTION_ID = "restart-codex-for-renderer"

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DaemonStartupDecision:
    mode: str
    launch_codex: bool = False
    codex_was_running: bool = False


@dataclass(frozen=True)
class DaemonServices:
    manager_factory: Callable[..., object] = CodexDaemonManager
    lock_factory: Callable[[], object] = HudInstanceLock
    configure_logging: Callable[[], object] = configure_daemon_logging
    attach_logger: Callable[[], object] = attach_cli_logger_to_daemon_log
    hide_console: Callable[[], object] = hide_console_window
    startup_decision: Callable[[argparse.Namespace, object], DaemonStartupDecision] | None = None
    create_loading: Callable[..., object] = loading_feedback._create_loading_feedback
    run_renderer: Callable[..., int] | None = None
    run_hud: Callable[..., int] | None = None
    restart_codex: Callable[[], bool] | None = None
    select_launch_port: Callable[[], int] | None = None
    launch: Callable[..., bool] = launch_codex_app
    append_diagnostic: Callable[..., object] = append_renderer_diagnostic


def _clone_args(args: argparse.Namespace, **changes: object) -> argparse.Namespace:
    """Copy argparse state before a daemon changes renderer preferences."""
    try:
        return replace(args, **changes)
    except TypeError:
        namespace = argparse.Namespace(**vars(args))
        for key, value in changes.items():
            setattr(namespace, key, value)
        return namespace


def clone_args_with_renderer_preference(
    args: argparse.Namespace,
    enabled: bool = True,
) -> argparse.Namespace:
    return _clone_args(
        args,
        renderer_hud=bool(enabled),
        hud_mode="renderer",
        runtime_hud_mode="renderer",
    )


def clone_args_with_display_mode(
    args: argparse.Namespace,
    mode: str,
) -> argparse.Namespace:
    return _clone_args(
        args,
        hud_mode=mode,
        runtime_hud_mode=mode,
        renderer_hud=mode == "renderer",
    )


def daemon_startup_decision(
    args: argparse.Namespace,
    manager: object,
) -> DaemonStartupDecision:
    del args
    snapshot = manager.snapshot()
    if snapshot.found:
        return DaemonStartupDecision(
            DEFAULT_DAEMON_STARTUP_WAIT,
            codex_was_running=True,
        )
    return DaemonStartupDecision(
        DEFAULT_DAEMON_STARTUP_RENDERER,
        launch_codex=True,
    )


def _default_restart_codex() -> bool:
    return restart_codex_app(debugger=True)


def _default_services() -> DaemonServices:
    return DaemonServices(
        startup_decision=daemon_startup_decision,
        restart_codex=_default_restart_codex,
    )


def run_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = True,
    daemon_manager: object | None = None,
    loading_feedback_instance: object | None = None,
    launched_codex: bool = False,
    observed_codex_launch: bool = False,
    run_renderer: Callable[..., int] | None = None,
    restart_codex: Callable[[], bool] | None = None,
) -> int:
    """Run one renderer session and handle the bounded restart transition."""
    del hide_until_attached
    renderer = run_renderer
    if renderer is None:
        raise RuntimeError("daemon runtime requires a renderer session callback")
    restart = restart_codex or _default_restart_codex
    session_args = clone_args_with_display_mode(args, "renderer")
    renderer_exit = renderer(
        session_args,
        lock_already_held=lock_already_held,
        daemon_manager=daemon_manager,
        launched_codex=launched_codex,
        observed_codex_launch=observed_codex_launch,
        loading_feedback=loading_feedback_instance,
    )
    if renderer_exit == HUD_SWITCH_TO_RENDERER_RESTART_CODEX:
        if not restart():
            return RENDERER_HUD_UNAVAILABLE
        return renderer(
            clone_args_with_renderer_preference(args, True),
            lock_already_held=lock_already_held,
            daemon_manager=daemon_manager,
            launched_codex=True,
            observed_codex_launch=False,
            loading_feedback=loading_feedback_instance,
        )
    if renderer_exit == HUD_SWITCH_TO_RENDERER:
        _LOGGER.info("renderer_hud_legacy_switch_ignored code=%s", renderer_exit)
        return RENDERER_HUD_UNAVAILABLE
    return renderer_exit


def legacy_hud_session_unavailable(surface: str) -> int:
    _LOGGER.info("legacy_hud_session_unavailable surface=%s renderer_only=true", surface)
    return RENDERER_HUD_UNAVAILABLE


def run_tk_window_session(
    context: object,
    args: argparse.Namespace,
    *,
    daemon_manager: object | None = None,
    existing_window: object | None = None,
    close_context: bool = True,
    update_manager: object | None = None,
) -> int:
    del args, daemon_manager, existing_window, update_manager
    if close_context:
        context.close()
    return legacy_hud_session_unavailable("tk")


def run_qt_window_session(
    context: object,
    args: argparse.Namespace,
    *,
    daemon_manager: object | None = None,
    existing_window: object | None = None,
    close_context: bool = True,
    update_manager: object | None = None,
) -> int:
    del args, daemon_manager, existing_window, update_manager
    if close_context:
        context.close()
    return legacy_hud_session_unavailable("qt")


def run_qt_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = True,
    daemon_manager: object | None = None,
    loading_feedback_instance: object | None = None,
) -> int:
    del args, lock_already_held, hide_until_attached, daemon_manager
    if loading_feedback_instance is not None:
        loading_feedback_instance.close()
    return legacy_hud_session_unavailable("qt")


def run_tk_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = True,
    daemon_manager: object | None = None,
    loading_feedback_instance: object | None = None,
) -> int:
    del args, lock_already_held, hide_until_attached, daemon_manager
    if loading_feedback_instance is not None:
        loading_feedback_instance.close()
    return legacy_hud_session_unavailable("tk")


def run_daemon(
    args: argparse.Namespace,
    *,
    services: DaemonServices | None = None,
) -> int:
    """Run the hidden daemon until the renderer session exits."""
    services = services or _default_services()
    services.configure_logging()
    services.attach_logger()
    services.hide_console()
    manager = services.manager_factory(poll_ms=args.daemon_poll_ms)
    startup_decision = services.startup_decision or daemon_startup_decision
    renderer = services.run_renderer
    hud = services.run_hud
    if renderer is None:
        raise RuntimeError("daemon runtime requires a renderer session callback")
    if hud is None:
        def default_hud(current_args: argparse.Namespace, **kwargs: object) -> int:
            if "loading_feedback" in kwargs:
                kwargs["loading_feedback_instance"] = kwargs.pop("loading_feedback")
            return run_hud_session(
                current_args,
                run_renderer=renderer,
                restart_codex=services.restart_codex,
                **kwargs,
            )

        hud = default_hud
    restart = services.restart_codex or _default_restart_codex
    try:
        with services.lock_factory():
            try:
                startup = startup_decision(args, manager)
            except KeyboardInterrupt:
                return 130
            except ProcessListenerError as exc:
                _LOGGER.exception("daemon_listener_failed fallback=%s", exc)
                return hud(args, lock_already_held=True, hide_until_attached=True)

            if startup.mode == DEFAULT_DAEMON_STARTUP_CANCEL:
                return 0
            startup_loading: Any | None = None
            launched_codex_for_renderer = False
            observed_codex_launch = False
            if startup.mode == DEFAULT_DAEMON_STARTUP_WAIT:
                startup_loading = services.create_loading(
                    args,
                    title="正在启动 Renderer HUD",
                    message="正在检查 Codex 的 CDP 连接…",
                ).start()
            if (
                startup.mode == DEFAULT_DAEMON_STARTUP_RENDERER
                and startup.launch_codex
            ):
                startup_loading = services.create_loading(
                    args,
                    title="正在启动 Renderer HUD",
                    message="正在以调试模式启动 Codex App...",
                ).start()
                try:
                    launch_port = (
                        services.select_launch_port() if services.select_launch_port else None
                    )
                except (OSError, RuntimeError) as exc:
                    startup_loading.close()
                    services.append_diagnostic(
                        "renderer_cdp_launch_failed",
                        reason=str(exc),
                        source="daemon-startup",
                    )
                    return RENDERER_HUD_UNAVAILABLE
                if not services.launch(debugger=True):
                    startup_loading.close()
                    services.append_diagnostic(
                        "renderer_cdp_launch_failed",
                        **({"port": launch_port} if launch_port is not None else {}),
                        source="daemon-startup",
                    )
                    return RENDERER_HUD_UNAVAILABLE
                launched_codex_for_renderer = True

            force_renderer_retry = startup.mode == DEFAULT_DAEMON_STARTUP_RENDERER
            while True:
                try:
                    manager.wait_for_codex()
                except KeyboardInterrupt:
                    return 130
                except ProcessListenerError as exc:
                    _LOGGER.exception("daemon_listener_failed fallback=%s", exc)
                    return hud(args, lock_already_held=True, hide_until_attached=True)
                if force_renderer_retry:
                    exit_code = renderer(
                        clone_args_with_renderer_preference(args, True),
                        lock_already_held=True,
                        daemon_manager=manager,
                        loading_feedback=startup_loading,
                        launched_codex=launched_codex_for_renderer,
                        observed_codex_launch=observed_codex_launch,
                    )
                else:
                    exit_code = hud(
                        clone_args_with_display_mode(args, "renderer"),
                        lock_already_held=True,
                        hide_until_attached=True,
                        daemon_manager=manager,
                        loading_feedback=startup_loading,
                        observed_codex_launch=observed_codex_launch,
                    )
                session_loading = startup_loading
                startup_loading = None
                observed_codex_launch = False
                if exit_code == HUD_AUTO_RESTART_CODEX:
                    startup_loading = session_loading
                    if startup_loading is None:
                        startup_loading = services.create_loading(
                            args,
                            title="正在切换到 Renderer HUD",
                            message="检测到普通 Codex 启动，正在改用调试/CDP 模式…",
                        ).start()
                    else:
                        startup_loading.update(
                            title="正在切换到 Renderer HUD",
                            message="检测到普通 Codex 启动，正在改用调试/CDP 模式…",
                        )
                    if not restart():
                        startup_loading.close()
                        return RENDERER_HUD_UNAVAILABLE
                    launched_codex_for_renderer = True
                    force_renderer_retry = True
                    continue
                if exit_code == HUD_SWITCH_TO_RENDERER_RESTART_CODEX:
                    startup_loading = services.create_loading(
                        args,
                        title="正在重启 Codex",
                        message="正在以调试/CDP 模式重启 Codex App，并重新尝试注入 HUD...",
                    ).start()
                    if not restart():
                        startup_loading.close()
                        return RENDERER_HUD_UNAVAILABLE
                    launched_codex_for_renderer = True
                    force_renderer_retry = True
                    continue
                if exit_code == DAEMON_RESTART_REQUESTED:
                    launched_codex_for_renderer = False
                    # The renderer session has already been running when this
                    # watchdog signal is emitted. A replacement Codex must be
                    # classified as an observed launch so a plain (non-CDP)
                    # process is stopped and relaunched with CDP automatically.
                    # Initial startup of an already-running Codex never emits
                    # this signal and keeps its explicit confirmation flow.
                    observed_codex_launch = True
                    continue
                if force_renderer_retry and exit_code == RENDERER_HUD_UNAVAILABLE:
                    time.sleep(manager.poll_seconds)
                    continue
                return exit_code
    except HudAlreadyRunningError as exc:
        print(f"codex-usage-hud: {exc}")
        return 2


__all__ = [
    "DAEMON_RESTART_REQUESTED", "DEFAULT_DAEMON_STARTUP_CANCEL",
    "DEFAULT_DAEMON_STARTUP_RENDERER", "DEFAULT_DAEMON_STARTUP_WAIT",
    "HUD_AUTO_RESTART_CODEX", "HUD_SWITCH_TO_RENDERER",
    "HUD_SWITCH_TO_RENDERER_RESTART_CODEX", "RENDERER_HUD_UNAVAILABLE",
    "DaemonServices", "DaemonStartupDecision", "clone_args_with_display_mode",
    "clone_args_with_renderer_preference", "daemon_startup_decision",
    "legacy_hud_session_unavailable", "run_daemon", "run_hud_session",
    "run_qt_hud_session", "run_qt_window_session", "run_tk_hud_session",
    "run_tk_window_session",
]
