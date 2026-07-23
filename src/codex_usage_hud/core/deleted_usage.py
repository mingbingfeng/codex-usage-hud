"""Compact usage ledger for sessions removed through the official Codex CLI."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any
import uuid


DELETED_USAGE_FORMAT = "codex-usage-hud-deleted-usage-v1"
DELETED_USAGE_RETENTION_DAYS = 30


class DeletedUsageLedgerError(RuntimeError):
    """Raised when deleted-session usage cannot be preserved safely."""


@dataclass(frozen=True)
class DeletedUsageEvent:
    id: str
    timestamp: datetime
    provider: str
    model: str
    tokens: int
    input_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float | None

    @property
    def total_tokens(self) -> int:
        return self.tokens


@dataclass(frozen=True)
class DeletedUsageSession:
    session_id: str
    family_session_ids: tuple[str, ...]
    title: str
    workdir_name: str
    deleted_at: datetime
    events: tuple[DeletedUsageEvent, ...]


def _event_value(event: object, name: str, default: object = None) -> object:
    if isinstance(event, Mapping):
        return event.get(name, default)
    return getattr(event, name, default)


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _canonical_id(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        canonical = str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""
    return canonical if candidate.casefold() == canonical else ""


def _aware_local(value: datetime, timezone_source: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone_source.tzinfo)
    return value.astimezone(timezone_source.tzinfo)


class DeletedUsageLedger:
    """Persist only metering fields needed after a session tree is deleted."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    @staticmethod
    def retention_start(now: datetime | None = None) -> datetime:
        current = (now or datetime.now().astimezone()).astimezone()
        local_day = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return local_day - timedelta(days=DELETED_USAGE_RETENTION_DAYS - 1)

    @staticmethod
    def _empty_payload() -> dict[str, object]:
        return {
            "format": DELETED_USAGE_FORMAT,
            "committed": {},
            "pending": {},
        }

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return self._empty_payload()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeletedUsageLedgerError(
                "Deleted-session usage ledger could not be read."
            ) from exc
        if not isinstance(raw, dict) or raw.get("format") != DELETED_USAGE_FORMAT:
            raise DeletedUsageLedgerError(
                "Deleted-session usage ledger format is not recognized."
            )
        if not isinstance(raw.get("committed"), dict) or not isinstance(
            raw.get("pending"), dict
        ):
            raise DeletedUsageLedgerError(
                "Deleted-session usage ledger structure is not recognized."
            )
        return raw

    def _write(self, payload: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(
                    dict(payload),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            raise DeletedUsageLedgerError(
                "Deleted-session usage ledger could not be written."
            ) from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _normalize_timestamp(value: object, cutoff: datetime) -> datetime | None:
        if isinstance(value, datetime):
            timestamp = value
        else:
            try:
                timestamp = datetime.fromisoformat(str(value or ""))
            except ValueError:
                return None
        normalized = _aware_local(timestamp, cutoff)
        return normalized if normalized >= cutoff else None

    @classmethod
    def _event_payload(
        cls,
        event: object,
        *,
        provider: str,
        source_session_id: str,
        source_event_index: int,
        cutoff: datetime,
    ) -> dict[str, object] | None:
        timestamp = cls._normalize_timestamp(_event_value(event, "timestamp"), cutoff)
        if timestamp is None:
            return None
        input_tokens = _nonnegative_int(_event_value(event, "input_tokens"))
        cached_tokens = min(
            input_tokens,
            _nonnegative_int(_event_value(event, "cached_tokens")),
        )
        cache_write_tokens = min(
            input_tokens - cached_tokens,
            _nonnegative_int(_event_value(event, "cache_write_tokens")),
        )
        raw_cost = _event_value(event, "cost_usd")
        try:
            cost_usd = None if raw_cost is None else max(0.0, float(raw_cost))
        except (TypeError, ValueError):
            cost_usd = None
        values: dict[str, object] = {
            "timestamp": timestamp.isoformat(timespec="microseconds"),
            "provider": str(provider or "unknown").strip().lower() or "unknown",
            "model": str(_event_value(event, "model", "") or "").strip()
            or "unknown",
            "tokens": _nonnegative_int(_event_value(event, "total_tokens")),
            "inputTokens": input_tokens,
            "cachedTokens": cached_tokens,
            "cacheWriteTokens": cache_write_tokens,
            "outputTokens": _nonnegative_int(_event_value(event, "output_tokens")),
            "reasoningTokens": _nonnegative_int(
                _event_value(event, "reasoning_tokens")
            ),
            "costUsd": round(cost_usd, 6) if cost_usd is not None else None,
        }
        identity = json.dumps(
            [
                source_session_id,
                _nonnegative_int(_event_value(event, "line"))
                or max(1, int(source_event_index)),
                *values.values(),
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        values["id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return values

    def prepare(
        self,
        *,
        session_id: str,
        family_session_ids: Sequence[str],
        title: str,
        workdir_name: str,
        rollout_paths: Sequence[Path],
        parser: Any,
        now: datetime | None = None,
    ) -> str:
        """Write a pending compact snapshot before the destructive command."""
        root_id = _canonical_id(session_id)
        family_ids = tuple(
            dict.fromkeys(
                canonical
                for value in family_session_ids
                if (canonical := _canonical_id(value))
            )
        )
        if not root_id or root_id not in family_ids:
            raise DeletedUsageLedgerError(
                "Deleted-session usage identity could not be verified."
            )
        cutoff = self.retention_start(now)
        events: dict[str, dict[str, object]] = {}
        source_paths: list[str] = []
        for raw_path in rollout_paths:
            path = Path(raw_path).expanduser().resolve(strict=True)
            source_paths.append(str(path))
            try:
                records = parser.load_records_lenient(path)
                usage_events = parser.usage_events(records)
            except Exception as exc:
                raise DeletedUsageLedgerError(
                    "Session usage could not be captured before deletion."
                ) from exc
            provider_reader = getattr(parser, "session_model_provider", None)
            provider = (
                str(provider_reader(records) or "").strip().lower()
                if callable(provider_reader)
                else "unknown"
            ) or "unknown"
            meta_reader = getattr(parser, "session_meta_payload", None)
            meta = meta_reader(records) if callable(meta_reader) else {}
            source_id = (
                _canonical_id(meta.get("id"))
                if isinstance(meta, Mapping)
                else ""
            ) or path.stem
            for event_index, event in enumerate(usage_events, start=1):
                payload = self._event_payload(
                    event,
                    provider=provider,
                    source_session_id=source_id,
                    source_event_index=event_index,
                    cutoff=cutoff,
                )
                if payload is not None:
                    events[str(payload["id"])] = payload
        if not events:
            return ""
        transaction_id = uuid.uuid4().hex
        current = (now or datetime.now().astimezone()).astimezone()
        pending_row = {
            "transactionId": transaction_id,
            "sessionId": root_id,
            "familySessionIds": list(family_ids),
            "title": " ".join(str(title or "").split()),
            "workdirName": str(workdir_name or "").strip(),
            "preparedAt": current.isoformat(timespec="seconds"),
            "sourcePaths": source_paths,
            "events": list(events.values()),
        }
        with self._lock:
            payload = self._read()
            self._prune_payload(payload, current)
            pending = dict(payload["pending"])
            pending[transaction_id] = pending_row
            payload["pending"] = pending
            self._write(payload)
        return transaction_id

    @staticmethod
    def _merge_committed(
        committed: dict[str, object], pending_row: Mapping[str, object], now: datetime
    ) -> None:
        session_id = _canonical_id(pending_row.get("sessionId"))
        if not session_id:
            raise DeletedUsageLedgerError(
                "Pending deleted-session usage identity is invalid."
            )
        previous = committed.get(session_id)
        previous_row = dict(previous) if isinstance(previous, Mapping) else {}
        merged_events: dict[str, object] = {}
        for raw_event in previous_row.get("events", []):
            if isinstance(raw_event, Mapping) and str(raw_event.get("id") or ""):
                merged_events[str(raw_event["id"])] = dict(raw_event)
        for raw_event in pending_row.get("events", []):
            if isinstance(raw_event, Mapping) and str(raw_event.get("id") or ""):
                merged_events[str(raw_event["id"])] = dict(raw_event)
        committed[session_id] = {
            "sessionId": session_id,
            "familySessionIds": list(pending_row.get("familySessionIds") or []),
            "title": str(pending_row.get("title") or ""),
            "workdirName": str(pending_row.get("workdirName") or ""),
            "deletedAt": now.isoformat(timespec="seconds"),
            "events": list(merged_events.values()),
        }

    def commit(self, transaction_id: str, *, now: datetime | None = None) -> None:
        if not transaction_id:
            return
        current = (now or datetime.now().astimezone()).astimezone()
        with self._lock:
            payload = self._read()
            pending = dict(payload["pending"])
            raw_row = pending.pop(transaction_id, None)
            if not isinstance(raw_row, Mapping):
                raise DeletedUsageLedgerError(
                    "Pending deleted-session usage snapshot is unavailable."
                )
            committed = dict(payload["committed"])
            self._merge_committed(committed, raw_row, current)
            payload["committed"] = committed
            payload["pending"] = pending
            self._prune_payload(payload, current)
            self._write(payload)

    def discard(self, transaction_id: str) -> None:
        if not transaction_id:
            return
        with self._lock:
            payload = self._read()
            pending = dict(payload["pending"])
            if pending.pop(transaction_id, None) is None:
                return
            payload["pending"] = pending
            self._prune_payload(payload, datetime.now().astimezone())
            self._write(payload)

    @classmethod
    def _decode_event(
        cls, raw: object, cutoff: datetime
    ) -> DeletedUsageEvent | None:
        if not isinstance(raw, Mapping):
            return None
        timestamp = cls._normalize_timestamp(raw.get("timestamp"), cutoff)
        event_id = str(raw.get("id") or "").strip()
        if timestamp is None or not event_id:
            return None
        raw_cost = raw.get("costUsd")
        try:
            cost_usd = None if raw_cost is None else max(0.0, float(raw_cost))
        except (TypeError, ValueError):
            cost_usd = None
        input_tokens = _nonnegative_int(raw.get("inputTokens"))
        cached_tokens = min(input_tokens, _nonnegative_int(raw.get("cachedTokens")))
        return DeletedUsageEvent(
            id=event_id,
            timestamp=timestamp,
            provider=str(raw.get("provider") or "unknown").strip().lower()
            or "unknown",
            model=str(raw.get("model") or "unknown").strip() or "unknown",
            tokens=_nonnegative_int(raw.get("tokens")),
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            cache_write_tokens=min(
                input_tokens - cached_tokens,
                _nonnegative_int(raw.get("cacheWriteTokens")),
            ),
            output_tokens=_nonnegative_int(raw.get("outputTokens")),
            reasoning_tokens=_nonnegative_int(raw.get("reasoningTokens")),
            cost_usd=round(cost_usd, 6) if cost_usd is not None else None,
        )

    def _prune_payload(
        self, payload: dict[str, object], current: datetime
    ) -> bool:
        cutoff = self.retention_start(current)
        changed = False
        committed = dict(payload["committed"])
        pending = dict(payload["pending"])
        for transaction_id, raw_row in list(pending.items()):
            if not isinstance(raw_row, Mapping):
                pending.pop(transaction_id, None)
                changed = True
                continue
            raw_events = raw_row.get("events", [])
            events = [
                raw_event
                for raw_event in raw_events
                if self._decode_event(raw_event, cutoff) is not None
            ]
            if not events:
                pending.pop(transaction_id, None)
                changed = True
                continue
            source_paths = [
                Path(str(value))
                for value in raw_row.get("sourcePaths", [])
                if str(value or "").strip()
            ]
            normalized_row = {**dict(raw_row), "events": events}
            if source_paths and all(not path.exists() for path in source_paths):
                self._merge_committed(committed, normalized_row, current)
                pending.pop(transaction_id, None)
                changed = True
            elif events != raw_events:
                pending[transaction_id] = normalized_row
                changed = True
        for key, raw_row in list(committed.items()):
            if not isinstance(raw_row, Mapping):
                committed.pop(key, None)
                changed = True
                continue
            raw_events = raw_row.get("events", [])
            events = [
                raw_event
                for raw_event in raw_events
                if self._decode_event(raw_event, cutoff) is not None
            ]
            if not events:
                committed.pop(key, None)
                changed = True
            elif events != raw_events:
                committed[key] = {**dict(raw_row), "events": events}
                changed = True
        payload["committed"] = committed
        payload["pending"] = pending
        return changed

    def sessions(self, *, now: datetime | None = None) -> tuple[DeletedUsageSession, ...]:
        """Recover interrupted commits, prune old events, and return committed rows."""
        current = (now or datetime.now().astimezone()).astimezone()
        cutoff = self.retention_start(current)
        with self._lock:
            payload = self._read()
            changed = self._prune_payload(payload, current)
            committed = dict(payload["committed"])
            sessions: list[DeletedUsageSession] = []
            for key, raw_row in list(committed.items()):
                if not isinstance(raw_row, Mapping):
                    committed.pop(key, None)
                    continue
                events = tuple(
                    event
                    for raw_event in raw_row.get("events", [])
                    if (event := self._decode_event(raw_event, cutoff)) is not None
                )
                if not events:
                    continue
                session_id = _canonical_id(raw_row.get("sessionId"))
                family_ids = tuple(
                    dict.fromkeys(
                        canonical
                        for value in raw_row.get("familySessionIds", [])
                        if (canonical := _canonical_id(value))
                    )
                )
                try:
                    deleted_at = datetime.fromisoformat(
                        str(raw_row.get("deletedAt") or "")
                    )
                except ValueError:
                    deleted_at = current
                if not session_id or session_id not in family_ids:
                    committed.pop(key, None)
                    changed = True
                    continue
                sessions.append(
                    DeletedUsageSession(
                        session_id=session_id,
                        family_session_ids=family_ids,
                        title=str(raw_row.get("title") or ""),
                        workdir_name=str(raw_row.get("workdirName") or ""),
                        deleted_at=_aware_local(deleted_at, current),
                        events=events,
                    )
                )
            payload["committed"] = committed
            if changed:
                self._write(payload)
            return tuple(sessions)
