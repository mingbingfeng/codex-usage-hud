"""Flatten Codex paginated rollout lineage into a standalone rollout.

Codex ``thread/fork`` creates a small child rollout whose first record points
at the source rollout through ``history_base``.  That is useful while the
source remains available, but it is not sufficient for a destructive
cross-provider migration.  This module resolves the local lineage and writes
one self-contained JSONL rollout before the source is removed.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import secrets
from typing import Any
import uuid


class SessionMaterializationError(RuntimeError):
    """Raised when a paginated rollout cannot be flattened safely."""


_THREAD_ID_KEYS = frozenset(
    {
        "thread_id",
        "threadId",
        "session_id",
        "sessionId",
        "parent_thread_id",
        "parentThreadId",
        "source_thread_id",
        "sourceThreadId",
        "forked_from_id",
        "forkedFromId",
    }
)


def _canonical_uuid(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        canonical = str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""
    return canonical if candidate.casefold() == canonical else ""


def _record_lines(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SessionMaterializationError(
            f"Rollout could not be read: {path.name}."
        ) from exc
    if limit is not None:
        if limit < 0 or limit > len(raw):
            raise SessionMaterializationError(
                f"Rollout history boundary is invalid: {path.name}."
            )
        if limit == 0 or (limit > 0 and raw[limit - 1 : limit] != b"\n"):
            raise SessionMaterializationError(
                f"Rollout history boundary is not a complete JSONL line: {path.name}."
            )
        raw = raw[:limit]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SessionMaterializationError(
            f"Rollout is not valid UTF-8: {path.name}."
        ) from exc
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SessionMaterializationError(
                f"Rollout contains invalid JSON: {path.name}."
            ) from exc
        if not isinstance(value, dict):
            raise SessionMaterializationError(
                f"Rollout record is not an object: {path.name}."
            )
        records.append(value)
    if not records or records[0].get("type") != "session_meta":
        raise SessionMaterializationError(
            f"Rollout has no leading session metadata: {path.name}."
        )
    return records


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        text = value.get("text")
        if isinstance(text, str):
            return text
        for key in ("content", "parts", "items"):
            if key in value:
                nested = _content_text(value.get(key))
                if nested:
                    return nested
        return ""
    if isinstance(value, list):
        return "".join(_content_text(item) for item in value)
    return ""


def _first_user_message_text(path: Path) -> str:
    """Read the first user message needed to seed Codex thread metadata."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, Mapping):
                    continue
                payload = record.get("payload")
                if not isinstance(payload, Mapping):
                    continue
                item = payload.get("item")
                if not isinstance(item, Mapping):
                    continue
                item_type = str(item.get("type") or "").strip().casefold()
                if item_type not in {"usermessage", "user_message"}:
                    continue
                text = " ".join(_content_text(item.get("content")).split())
                if text:
                    return text
    except (OSError, UnicodeError):
        return ""
    return ""


def _history_base(meta: Mapping[str, Any]) -> tuple[str, int] | None:
    payload = meta.get("payload")
    if not isinstance(payload, Mapping):
        return None
    if str(payload.get("history_mode") or "").strip().casefold() != "paginated":
        return None
    base = payload.get("history_base")
    if base is None:
        return None
    if not isinstance(base, Mapping):
        raise SessionMaterializationError("Paginated rollout history base is invalid.")
    base_id = _canonical_uuid(base.get("thread_id"))
    try:
        end_byte_offset = int(base.get("end_byte_offset"))
    except (TypeError, ValueError) as exc:
        raise SessionMaterializationError(
            "Paginated rollout history boundary is missing."
        ) from exc
    if not base_id or end_byte_offset < 0:
        raise SessionMaterializationError("Paginated rollout history base is invalid.")
    return base_id, end_byte_offset


def _flatten_segment(
    session_id: str,
    path: Path,
    paths: Mapping[str, Path],
    *,
    limit: int | None = None,
    visiting: set[str] | None = None,
) -> list[dict[str, Any]]:
    canonical_id = _canonical_uuid(session_id)
    if not canonical_id:
        raise SessionMaterializationError("Rollout session id is invalid.")
    active = set(visiting or ())
    if canonical_id in active:
        raise SessionMaterializationError("Rollout history lineage contains a cycle.")
    active.add(canonical_id)
    records = _record_lines(path, limit)
    base = _history_base(records[0])
    flattened: list[dict[str, Any]] = []
    if base is not None:
        base_id, end_byte_offset = base
        base_path = paths.get(base_id)
        if base_path is None or not base_path.is_file():
            raise SessionMaterializationError(
                f"Rollout history source is unavailable: {base_id}."
            )
        flattened.extend(
            _flatten_segment(
                base_id,
                base_path,
                paths,
                limit=end_byte_offset,
                visiting=active,
            )
        )
    flattened.extend(
        record
        for record in records[1:]
        if record.get("type") != "session_meta"
    )
    return flattened


def _standalone_metadata(record: Mapping[str, Any], target_id: str) -> dict[str, Any]:
    result = dict(record)
    payload = result.get("payload")
    if not isinstance(payload, Mapping):
        raise SessionMaterializationError("Target rollout metadata is invalid.")
    metadata = dict(payload)
    metadata["id"] = target_id
    metadata["session_id"] = target_id
    metadata.pop("history_base", None)
    metadata.pop("forked_from_id", None)
    result["payload"] = metadata
    result["ordinal"] = 0
    return result


def _rewrite_thread_identities(
    value: Any,
    source_id: str,
    target_id: str,
) -> Any:
    """Rebind persisted event identities from the fork source to its target."""
    if isinstance(value, Mapping):
        rewritten: dict[Any, Any] = {}
        for key, nested in value.items():
            if (
                str(key) in _THREAD_ID_KEYS
                and _canonical_uuid(nested) == source_id
            ):
                rewritten[key] = target_id
            else:
                rewritten[key] = _rewrite_thread_identities(
                    nested,
                    source_id,
                    target_id,
                )
        return rewritten
    if isinstance(value, list):
        return [
            _rewrite_thread_identities(item, source_id, target_id)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _rewrite_thread_identities(item, source_id, target_id)
            for item in value
        )
    return value


def materialize_forked_rollout(
    *,
    target_id: str,
    source_id: str,
    target_path: Path,
    rollout_paths: Mapping[str, Path],
) -> None:
    """Rewrite a forked rollout so it no longer depends on its source.

    The target file is replaced atomically.  The source and the target are
    untouched when validation fails, so callers can safely keep the source for
    a copy operation or abort a migration before deletion.
    """
    canonical_target = _canonical_uuid(target_id)
    canonical_source = _canonical_uuid(source_id)
    if not canonical_target or not canonical_source:
        raise SessionMaterializationError("源或目标会话标识无效。")
    target = Path(target_path)
    target_records = _record_lines(target)
    target_meta = target_records[0]
    payload = target_meta.get("payload")
    if not isinstance(payload, Mapping):
        raise SessionMaterializationError("目标 rollout 元数据无效。")
    base = _history_base(target_meta)
    if base is None:
        # A provider fork may already be standalone.  Still normalize its
        # identity and remove fork lineage before it is advertised as migrated.
        logical_records = [
            record
            for record in target_records[1:]
            if record.get("type") != "session_meta"
        ]
    else:
        base_id, end_byte_offset = base
        if base_id != canonical_source:
            raise SessionMaterializationError(
                "目标 rollout 的历史源与所选源会话不一致。"
            )
        source_path = rollout_paths.get(canonical_source)
        if source_path is None or not source_path.is_file():
            raise SessionMaterializationError(
                f"源 rollout 不存在，无法物化：{canonical_source}。"
            )
        logical_records = _flatten_segment(
            canonical_source,
            source_path,
            rollout_paths,
            limit=end_byte_offset,
        )
        logical_records.extend(
            record
            for record in target_records[1:]
            if record.get("type") != "session_meta"
        )
    output_records = [_standalone_metadata(target_meta, canonical_target)]
    output_records.extend(
        _rewrite_thread_identities(record, canonical_source, canonical_target)
        for record in logical_records
    )
    for ordinal, record in enumerate(output_records):
        record["ordinal"] = ordinal
    encoded = "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        for record in output_records
    )
    temporary = target.with_name(
        f".{target.name}.hud-materialize-{secrets.token_hex(10)}"
    )
    try:
        # Byte offsets in ``history_base`` are measured against the JSONL byte
        # stream.  Write bytes directly so Windows newline translation cannot
        # invalidate those offsets or silently change the stored format.
        temporary.write_bytes(encoded.encode("utf-8"))
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except (OSError, UnicodeError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        detail = " ".join(str(exc).split())
        suffix = (
            f"（{type(exc).__name__}: {detail[:180]}）"
            if detail
            else f"（{type(exc).__name__}）"
        )
        raise SessionMaterializationError(
            f"目标 rollout 物化失败{suffix}。"
        ) from exc


__all__ = ["SessionMaterializationError", "materialize_forked_rollout"]
