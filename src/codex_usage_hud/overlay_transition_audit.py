"""Versioned transition-audit projection and JSONL persistence for overlays."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from . import overlay_ipc, overlay_projection


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def append_transition_audit(
    items: Sequence[Mapping[str, object]],
    *,
    previous_items: Sequence[Mapping[str, object]] | None,
    close: bool,
    state_revision: int,
    audit_path: Path,
    state_path: Path,
    producer_instance_id: str,
    owner_pid: int,
    timestamp_factory: Callable[[], str] = _timestamp,
) -> None:
    """Append state transitions while preserving the existing v1 envelope."""
    if close or previous_items is None:
        return
    old_by_id = {
        str(item.get("id") or item.get("sessionId") or "").strip(): item
        for item in previous_items
        if str(item.get("id") or item.get("sessionId") or "").strip()
    }
    if not old_by_id:
        return
    now = timestamp_factory()
    events: list[dict[str, object]] = []
    for item in items:
        item_id = str(item.get("id") or item.get("sessionId") or "").strip()
        if not item_id or item_id not in old_by_id:
            continue
        old_item = old_by_id[item_id]
        old_status = overlay_projection.payload_status(old_item)
        new_status = overlay_projection.payload_status(item)
        old_pending = overlay_projection.payload_pending_accounting(old_item)
        new_pending = overlay_projection.payload_pending_accounting(item)
        old_kind = overlay_projection.payload_kind(old_item)
        new_kind = overlay_projection.payload_kind(item)
        if (
            old_status == new_status
            and old_pending == new_pending
            and old_kind == new_kind
        ):
            continue
        events.append(
            overlay_ipc.transition_message(
                time=now,
                ownerPid=owner_pid,
                stateFile=str(state_path),
                stateRevision=state_revision,
                producerInstanceId=producer_instance_id,
                id=item_id,
                sessionId=str(item.get("sessionId") or old_item.get("sessionId") or ""),
                title=str(
                    item.get("targetTitle")
                    or item.get("title")
                    or old_item.get("targetTitle")
                    or old_item.get("title")
                    or ""
                ),
                transition=overlay_projection.transition_name(old_item, item),
                oldKind=old_kind,
                newKind=new_kind,
                oldStatus=old_status,
                newStatus=new_status,
                oldPendingAccounting=old_pending,
                newPendingAccounting=new_pending,
                oldUpdatedAt=str(old_item.get("updatedAt") or ""),
                newUpdatedAt=str(item.get("updatedAt") or ""),
            )
        )
    if not events:
        return
    try:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
    except OSError:
        return


__all__ = ["append_transition_audit"]
