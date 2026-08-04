"""Versioned filesystem contracts shared with the desktop overlay helper."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Final
from uuid import uuid4


SCHEMA_VERSION: Final = 1
STATE_MESSAGE_TYPE: Final = "overlay.state"
COMMAND_MESSAGE_TYPE: Final = "overlay.command"
ACK_MESSAGE_TYPE: Final = "overlay.ack"
TRANSITION_MESSAGE_TYPE: Final = "overlay.transition"
ACK_STATUSES: Final = frozenset({"accepted", "completed", "rejected", "error"})
TRANSITION_NAMES: Final = frozenset(
    {
        "card_to_completed",
        "completed_to_card",
        "accounting_finalized",
        "status_changed",
    }
)


class OverlayContractError(ValueError):
    """Raised when a versioned overlay message violates its wire contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _message_id() -> str:
    return str(uuid4())


def _version(payload: Mapping[str, object]) -> int:
    value = payload.get("schemaVersion")
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise OverlayContractError("schemaVersion must be an integer")
    if value not in {0, SCHEMA_VERSION}:
        raise OverlayContractError(f"unsupported overlay schemaVersion: {value}")
    return value


def _validate_message(
    payload: Mapping[str, object],
    *,
    message_type: str,
    required: tuple[str, ...],
) -> dict[str, object]:
    result = dict(payload)
    version = _version(result)
    if version == 0:
        return result
    if result.get("messageType") != message_type:
        raise OverlayContractError(f"messageType must be {message_type}")
    for key in ("messageId", "createdAt", *required):
        if key not in result or result[key] is None or result[key] == "":
            raise OverlayContractError(f"missing required overlay field: {key}")
    return result


def envelope(message_type: str, **fields: object) -> dict[str, object]:
    """Build an additive v1 envelope while retaining flat legacy fields."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "messageType": message_type,
        "messageId": _message_id(),
        "createdAt": _utc_now(),
        **fields,
    }


def command_path(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.stem}-commands.jsonl")


def ack_path(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.stem}-acks.jsonl")


def heartbeat_path(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.stem}-heartbeat")


def transition_audit_path(runtime_dir: Path) -> Path:
    return runtime_dir / "work-overlay-transitions.jsonl"


def state_message(**fields: object) -> dict[str, object]:
    return envelope(STATE_MESSAGE_TYPE, **fields)


def command_message(**fields: object) -> dict[str, object]:
    fields.setdefault("requestId", _message_id())
    fields.setdefault("requestedAt", _utc_now())
    return envelope(COMMAND_MESSAGE_TYPE, **fields)


def ack_message(**fields: object) -> dict[str, object]:
    status = str(fields.get("status") or "")
    if status not in ACK_STATUSES:
        raise OverlayContractError(f"invalid overlay ack status: {status}")
    fields.setdefault("acknowledgedAt", _utc_now())
    return envelope(ACK_MESSAGE_TYPE, **fields)


def transition_message(**fields: object) -> dict[str, object]:
    name = str(fields.get("transition") or "")
    if name not in TRANSITION_NAMES:
        raise OverlayContractError(f"invalid overlay transition: {name}")
    fields.setdefault("eventId", _message_id())
    return envelope(TRANSITION_MESSAGE_TYPE, **fields)


def parse_state(payload: Mapping[str, object]) -> dict[str, object]:
    return _validate_message(
        payload,
        message_type=STATE_MESSAGE_TYPE,
        required=("ownerPid", "items", "revision", "producerInstanceId"),
    )


def parse_command(payload: Mapping[str, object]) -> dict[str, object]:
    return _validate_message(
        payload,
        message_type=COMMAND_MESSAGE_TYPE,
        required=("action", "requestId", "requestedAt"),
    )


def parse_ack(payload: Mapping[str, object]) -> dict[str, object]:
    result = _validate_message(
        payload,
        message_type=ACK_MESSAGE_TYPE,
        required=("requestId", "action", "status", "acknowledgedAt"),
    )
    if _version(result) and str(result["status"]) not in ACK_STATUSES:
        raise OverlayContractError(f"invalid overlay ack status: {result['status']}")
    return result


def parse_transition(payload: Mapping[str, object]) -> dict[str, object]:
    result = _validate_message(
        payload,
        message_type=TRANSITION_MESSAGE_TYPE,
        required=("eventId", "transition", "stateRevision", "producerInstanceId"),
    )
    if _version(result) and str(result["transition"]) not in TRANSITION_NAMES:
        raise OverlayContractError(
            f"invalid overlay transition: {result['transition']}"
        )
    return result


def normalized_match_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip().casefold()


def command_matches_item(
    command: Mapping[str, object],
    item: Mapping[str, object],
) -> bool:
    command_session = str(command.get("sessionId") or "").strip()
    item_sessions = {
        str(item.get("sessionId") or "").strip(),
        str(item.get("id") or "").strip(),
    }
    if command_session and command_session in item_sessions:
        return True

    command_title = normalized_match_text(
        command.get("targetTitle") or command.get("title")
    )
    if not command_title:
        return False
    item_titles = {
        normalized_match_text(item.get("targetTitle")),
        normalized_match_text(item.get("title")),
    }
    if command_title not in item_titles:
        return False

    command_workdir = normalized_match_text(command.get("workdir"))
    item_workdir = normalized_match_text(item.get("workdir"))
    return not command_workdir or not item_workdir or command_workdir == item_workdir


def payload_status(item: Mapping[str, object]) -> str:
    return str(item.get("status") or "").strip()


def payload_kind(item: Mapping[str, object]) -> str:
    return "completed" if payload_status(item) == "recent" else "card"


def transition_name(
    old_item: Mapping[str, object],
    new_item: Mapping[str, object],
) -> str:
    old_kind = payload_kind(old_item)
    new_kind = payload_kind(new_item)
    if old_kind == "card" and new_kind == "completed":
        return "card_to_completed"
    if old_kind == "completed" and new_kind == "card":
        return "completed_to_card"
    if (
        old_kind == "completed"
        and new_kind == "completed"
        and bool(old_item.get("pendingAccounting"))
        and not bool(new_item.get("pendingAccounting"))
    ):
        return "accounting_finalized"
    return "status_changed"
