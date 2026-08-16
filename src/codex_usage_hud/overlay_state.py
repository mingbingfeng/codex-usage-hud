"""Desktop-overlay state envelope, signature, and writer contracts."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from . import overlay_ipc
from .config import DEFAULT_WORK_OVERLAY_SIDE, normalize_work_overlay_side

StateWriter = Callable[[Path, Mapping[str, object]], None]


def state_signature(
    *,
    item_limit: int,
    side: str = DEFAULT_WORK_OVERLAY_SIDE,
    command_path: Path,
    items: Sequence[Mapping[str, object]],
    system_action: Mapping[str, object] | None = None,
    system_notice: Mapping[str, object] | None = None,
    rest_reminder: Mapping[str, object] | None = None,
    theme: Mapping[str, object] | None = None,
    close: bool,
) -> str:
    """Return the canonical state signature used to suppress unchanged writes."""
    return json.dumps(
        {
            "itemLimit": int(item_limit),
            "side": normalize_work_overlay_side(side),
            "commandPath": str(command_path),
            "items": list(items),
            "systemAction": dict(system_action or {}) if not close else {},
            "systemNotice": dict(system_notice or {}) if not close else {},
            "restReminder": dict(rest_reminder or {}) if not close else {},
            "theme": dict(theme or {}),
            "close": bool(close),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def build_state_message(
    *,
    owner_pid: int,
    item_limit: int,
    side: str = DEFAULT_WORK_OVERLAY_SIDE,
    command_path: Path,
    state_path: Path,
    revision: int,
    producer_instance_id: str,
    items: Sequence[Mapping[str, object]],
    system_action: Mapping[str, object] | None = None,
    system_notice: Mapping[str, object] | None = None,
    rest_reminder: Mapping[str, object] | None = None,
    theme: Mapping[str, object] | None = None,
    updated_at: float,
    close: bool,
) -> dict[str, object]:
    """Build the existing flat-plus-v1 state envelope without writing it."""
    return overlay_ipc.state_message(
        ownerPid=owner_pid,
        itemLimit=int(item_limit),
        side=normalize_work_overlay_side(side),
        commandPath=str(command_path),
        ackPath=str(overlay_ipc.ack_path(state_path)),
        revision=revision,
        producerInstanceId=producer_instance_id,
        items=list(items),
        systemAction=dict(system_action or {}) if not close else {},
        systemNotice=dict(system_notice or {}) if not close else {},
        restReminder=dict(rest_reminder or {}) if not close else {},
        theme=dict(theme or {}),
        updatedAt=updated_at,
        close=bool(close),
    )


def write_state(
    path: Path,
    payload: Mapping[str, object],
    *,
    writer: StateWriter,
) -> None:
    """Write one state payload through the injected atomic writer."""
    writer(path, payload)


__all__ = ["StateWriter", "build_state_message", "state_signature", "write_state"]
