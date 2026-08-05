"""Compatibility facade for the PySide6 desktop work-overlay helper."""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .. import overlay_ipc
from .work_overlay import constants as _constants
from .work_overlay import geometry as _geometry
from .work_overlay import model as _model
from .work_overlay import theme as _theme
from .work_overlay.constants import *  # noqa: F401,F403
from .work_overlay.geometry import *  # noqa: F401,F403
from .work_overlay.model import *  # noqa: F401,F403
from .work_overlay.theme import *  # noqa: F401,F403


def _multiline_elided_text(
    value: object,
    *,
    font: object,
    width: int,
    max_lines: int = WORK_OVERLAY_BODY_MAX_LINES,
) -> str:
    from .work_overlay.qt_window import _multiline_elided_text as owner

    return owner(value, font=font, width=width, max_lines=max_lines)


def _allow_foreground_process(
    process_id: int | None,
    *,
    user32: object | None = None,
) -> bool:
    from .work_overlay.qt_window import _allow_foreground_process as owner

    return owner(process_id, user32=user32, platform=sys.platform)


def _work_overlay_command_path(state_path: Path) -> Path:
    return overlay_ipc.command_path(state_path)


def _work_overlay_heartbeat_path(state_path: Path) -> Path:
    return overlay_ipc.heartbeat_path(state_path)


def _refresh_overlay_state_from_event(
    *,
    read_state: Callable[[], bool],
    retry_active: Callable[[], bool],
    start_retry: Callable[[int], None],
    stop_retry: Callable[[], None],
    heartbeat_active: Callable[[], bool],
    start_heartbeat: Callable[[], None],
    schedule_stale: Callable[[], None],
) -> bool:
    from .work_overlay.qt_runtime import _refresh_overlay_state_from_event as owner

    return owner(
        read_state=read_state,
        retry_active=retry_active,
        start_retry=start_retry,
        stop_retry=stop_retry,
        heartbeat_active=heartbeat_active,
        start_heartbeat=start_heartbeat,
        schedule_stale=schedule_stale,
    )


def run_work_overlay_helper_qt(*args: Any, **kwargs: Any) -> int:
    from .work_overlay.qt_runtime import run_work_overlay_helper_qt as owner

    return owner(*args, **kwargs)


__all__ = sorted(
    {
        *_constants.__all__,
        *_geometry.__all__,
        *_model.__all__,
        *_theme.__all__,
        "_allow_foreground_process",
        "_multiline_elided_text",
        "_refresh_overlay_state_from_event",
        "_work_overlay_command_path",
        "_work_overlay_heartbeat_path",
        "run_work_overlay_helper_qt",
    }
)
