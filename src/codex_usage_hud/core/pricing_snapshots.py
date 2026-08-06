"""Immutable request-level price snapshots for JSONL usage events."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock
import uuid


PRICING_SNAPSHOT_SCHEMA_VERSION = 1
_INSERT_SNAPSHOT_SQL = """
    INSERT OR IGNORE INTO usage_price_snapshots(
        event_key, session_id, event_line, occurred_at, provider, model,
        base_url, input_tokens, cached_input_tokens, cache_write_tokens,
        output_tokens, reasoning_tokens, cost_usd, status,
        price_snapshot_json, original_cost_usd,
        original_price_snapshot_json, created_at
    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _utc_iso(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        current = value
    else:
        text = str(value or "").strip()
        if not text:
            current = datetime.now(timezone.utc)
        else:
            current = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class StoredPriceSnapshot:
    event_key: str
    session_id: str
    event_line: int
    occurred_at: str
    provider: str
    model: str
    base_url: str
    input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float | None
    status: str
    price_snapshot: dict[str, object] | None
    original_cost_usd: float | None
    original_price_snapshot: dict[str, object] | None
    recalculated_at: str


@dataclass(frozen=True, slots=True)
class PricingRecalculationPreview:
    preview_id: str
    provider: str
    model: str
    start_at: str
    end_at: str
    matched_count: int
    changed_count: int
    unavailable_count: int
    previous_total_usd: float
    next_total_usd: float
    changes: tuple[dict[str, object], ...]
    time_breakdown: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "previewId": self.preview_id,
            "provider": self.provider,
            "model": self.model,
            "startAt": self.start_at,
            "endAt": self.end_at,
            "matchedCount": self.matched_count,
            "changedCount": self.changed_count,
            "unavailableCount": self.unavailable_count,
            "previousTotalUsd": self.previous_total_usd,
            "nextTotalUsd": self.next_total_usd,
            "changes": [dict(change) for change in self.changes],
            "timeBreakdown": dict(self.time_breakdown or {}),
        }


class PricingSnapshotLedger:
    """Persist the first confirmed price used for each JSONL token event."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._cache_lock = RLock()
        self._snapshot_cache: dict[str, StoredPriceSnapshot] = {}
        self._snapshot_cache_loaded = False
        self._batch_connection: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"pricing_snapshot_batch_{id(self)}",
            default=None,
        )
        self._batch_pending: ContextVar[list[tuple[object, ...]] | None] = ContextVar(
            f"pricing_snapshot_pending_{id(self)}",
            default=None,
        )
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 2000")
        return connection

    def initialize(self) -> None:
        with self._cache_lock, closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_price_snapshots (
                    event_key TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_line INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    base_url TEXT NOT NULL DEFAULT '',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL,
                    status TEXT NOT NULL DEFAULT 'unavailable',
                    price_snapshot_json TEXT NOT NULL DEFAULT '',
                    original_cost_usd REAL,
                    original_price_snapshot_json TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    recalculated_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_usage_price_snapshots_scope
                    ON usage_price_snapshots(provider, model, occurred_at);
                CREATE TABLE IF NOT EXISTS pricing_recalculation_audit (
                    audit_id TEXT PRIMARY KEY,
                    executed_at TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    start_at TEXT NOT NULL DEFAULT '',
                    end_at TEXT NOT NULL DEFAULT '',
                    matched_count INTEGER NOT NULL,
                    changed_count INTEGER NOT NULL,
                    previous_total_usd REAL NOT NULL,
                    next_total_usd REAL NOT NULL
                );
                INSERT OR IGNORE INTO metadata(key, value)
                    VALUES('schema_version', '1');
                """
            )

    @contextmanager
    def batch(self):
        """Cache lookups and flush new snapshots once for a parser batch."""
        existing = self._batch_connection.get()
        if existing is not None:
            yield
            return
        with self._cache_lock, closing(self._connect()) as connection, connection:
            self._ensure_snapshot_cache(connection)
            pending: list[tuple[object, ...]] = []
            connection_token = self._batch_connection.set(connection)
            pending_token = self._batch_pending.set(pending)
            try:
                yield
                if pending:
                    connection.executemany(_INSERT_SNAPSHOT_SQL, pending)
            except Exception:
                for values in pending:
                    self._snapshot_cache.pop(str(values[0]), None)
                raise
            finally:
                self._batch_pending.reset(pending_token)
                self._batch_connection.reset(connection_token)

    @staticmethod
    def event_key(session_id: object, line: object, occurred_at: object) -> str:
        return ":".join(
            (
                str(session_id or "n/a").strip() or "n/a",
                str(max(0, int(line or 0))),
                _utc_iso(occurred_at if isinstance(occurred_at, (datetime, str)) else None),
            )
        )

    @staticmethod
    def _decode_snapshot(value: object) -> dict[str, object] | None:
        try:
            decoded = json.loads(str(value or ""))
        except json.JSONDecodeError:
            return None
        return dict(decoded) if isinstance(decoded, Mapping) else None

    @classmethod
    def _stored(cls, row: sqlite3.Row) -> StoredPriceSnapshot:
        return StoredPriceSnapshot(
            event_key=str(row["event_key"]),
            session_id=str(row["session_id"]),
            event_line=int(row["event_line"] or 0),
            occurred_at=str(row["occurred_at"]),
            provider=str(row["provider"]),
            model=str(row["model"]),
            base_url=str(row["base_url"]),
            input_tokens=int(row["input_tokens"] or 0),
            cached_input_tokens=int(row["cached_input_tokens"] or 0),
            cache_write_tokens=int(row["cache_write_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            reasoning_tokens=int(row["reasoning_tokens"] or 0),
            cost_usd=(None if row["cost_usd"] is None else float(row["cost_usd"])),
            status=str(row["status"] or "unavailable"),
            price_snapshot=cls._decode_snapshot(row["price_snapshot_json"]),
            original_cost_usd=(
                None
                if row["original_cost_usd"] is None
                else float(row["original_cost_usd"])
            ),
            original_price_snapshot=cls._decode_snapshot(
                row["original_price_snapshot_json"]
            ),
            recalculated_at=str(row["recalculated_at"] or ""),
        )

    def _ensure_snapshot_cache(self, connection: sqlite3.Connection) -> None:
        if self._snapshot_cache_loaded:
            return
        rows = connection.execute("SELECT * FROM usage_price_snapshots").fetchall()
        self._snapshot_cache = {
            str(row["event_key"]): self._stored(row)
            for row in rows
        }
        self._snapshot_cache_loaded = True

    def _invalidate_snapshot_cache(self) -> None:
        self._snapshot_cache.clear()
        self._snapshot_cache_loaded = False

    def get(self, event_key: object) -> StoredPriceSnapshot | None:
        key = str(event_key or "")
        with self._cache_lock:
            if not self._snapshot_cache_loaded:
                with closing(self._connect()) as connection:
                    self._ensure_snapshot_cache(connection)
            return self._snapshot_cache.get(key)

    @staticmethod
    def _record_row(
        connection: sqlite3.Connection,
        values: tuple[object, ...],
    ) -> sqlite3.Row | None:
        connection.execute(_INSERT_SNAPSHOT_SQL, values)
        return connection.execute(
            "SELECT * FROM usage_price_snapshots WHERE event_key=?",
            (values[0],),
        ).fetchone()

    @staticmethod
    def _stored_from_values(
        values: tuple[object, ...],
        price_snapshot: Mapping[str, object] | None,
    ) -> StoredPriceSnapshot:
        cost_usd = None if values[12] is None else float(values[12])
        normalized_snapshot = (
            dict(price_snapshot) if isinstance(price_snapshot, Mapping) else None
        )
        return StoredPriceSnapshot(
            event_key=str(values[0]),
            session_id=str(values[1]),
            event_line=int(values[2]),
            occurred_at=str(values[3]),
            provider=str(values[4]),
            model=str(values[5]),
            base_url=str(values[6]),
            input_tokens=int(values[7]),
            cached_input_tokens=int(values[8]),
            cache_write_tokens=int(values[9]),
            output_tokens=int(values[10]),
            reasoning_tokens=int(values[11]),
            cost_usd=cost_usd,
            status=str(values[13]),
            price_snapshot=normalized_snapshot,
            original_cost_usd=cost_usd,
            original_price_snapshot=normalized_snapshot,
            recalculated_at="",
        )

    def record_if_absent(
        self,
        *,
        event_key: str,
        session_id: str,
        event_line: int,
        occurred_at: datetime | str,
        provider: str,
        model: str,
        base_url: str,
        input_tokens: int,
        cached_input_tokens: int,
        cache_write_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        cost_usd: float | None,
        status: str,
        price_snapshot: Mapping[str, object] | None,
    ) -> StoredPriceSnapshot:
        snapshot_json = (
            json.dumps(dict(price_snapshot), ensure_ascii=False, sort_keys=True)
            if isinstance(price_snapshot, Mapping)
            else ""
        )
        created_at = _utc_iso(None)
        values: tuple[object, ...] = (
            event_key,
            str(session_id or ""),
            max(0, int(event_line or 0)),
            _utc_iso(occurred_at),
            str(provider or "").strip().lower(),
            str(model or "").strip(),
            str(base_url or "").strip(),
            max(0, int(input_tokens or 0)),
            max(0, int(cached_input_tokens or 0)),
            max(0, int(cache_write_tokens or 0)),
            max(0, int(output_tokens or 0)),
            max(0, int(reasoning_tokens or 0)),
            cost_usd,
            str(status or "unavailable"),
            snapshot_json,
            cost_usd,
            snapshot_json,
            created_at,
        )
        existing = self.get(event_key)
        if existing is not None:
            return existing
        stored = self._stored_from_values(values, price_snapshot)
        pending = self._batch_pending.get()
        if pending is not None:
            self._snapshot_cache[event_key] = stored
            pending.append(values)
            return stored
        with self._cache_lock:
            existing = self._snapshot_cache.get(event_key)
            if existing is not None:
                return existing
            with closing(self._connect()) as standalone, standalone:
                row = self._record_row(standalone, values)
            if row is None:
                raise RuntimeError("price snapshot could not be persisted")
            stored = self._stored(row)
            self._snapshot_cache[event_key] = stored
            return stored

    @staticmethod
    def _scope_where(
        *, provider: str = "", model: str = "", start_at: str = "", end_at: str = ""
    ) -> tuple[str, list[object]]:
        clauses: list[str] = []
        values: list[object] = []
        if provider:
            clauses.append("provider=?")
            values.append(str(provider).strip().lower())
        if model:
            clauses.append("model=?")
            values.append(str(model).strip())
        if start_at:
            clauses.append("occurred_at>=?")
            values.append(_utc_iso(start_at))
        if end_at:
            clauses.append("occurred_at<=?")
            values.append(_utc_iso(end_at))
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), values

    def preview_recalculation(
        self,
        resolver: Callable[[StoredPriceSnapshot], tuple[float | None, str, Mapping[str, object] | None]],
        *,
        provider: str = "",
        model: str = "",
        start_at: str = "",
        end_at: str = "",
        effective_at: str = "",
    ) -> PricingRecalculationPreview:
        where, values = self._scope_where(
            provider=provider, model=model, start_at=start_at, end_at=end_at
        )
        with self._cache_lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM usage_price_snapshots" + where + " ORDER BY occurred_at, event_key",
                values,
            ).fetchall()
        changes: list[dict[str, object]] = []
        previous_total = 0.0
        next_total = 0.0
        unavailable = 0
        effective_at_utc = _utc_iso(effective_at) if effective_at else ""
        time_breakdown: dict[str, object] = {}
        if effective_at_utc:
            time_breakdown = {
                "effectiveAt": effective_at_utc,
                "before": {
                    "recordCount": 0,
                    "pricedCount": 0,
                    "unavailableCount": 0,
                    "costUsd": 0.0,
                    "previousCostUsd": 0.0,
                    "nextCostUsd": 0.0,
                },
                "after": {
                    "recordCount": 0,
                    "pricedCount": 0,
                    "unavailableCount": 0,
                    "costUsd": 0.0,
                    "previousCostUsd": 0.0,
                    "nextCostUsd": 0.0,
                },
            }
        for row in rows:
            stored = self._stored(row)
            next_cost, next_status, next_snapshot = resolver(stored)
            previous_total += float(stored.cost_usd or 0.0)
            next_total += float(next_cost or 0.0)
            if next_cost is None:
                unavailable += 1
            if effective_at_utc:
                period = (
                    "before"
                    if stored.occurred_at < effective_at_utc
                    else "after"
                )
                bucket = time_breakdown[period]
                assert isinstance(bucket, dict)
                bucket["recordCount"] = int(bucket["recordCount"]) + 1
                bucket["previousCostUsd"] = float(
                    bucket["previousCostUsd"]
                ) + float(stored.cost_usd or 0.0)
                if next_cost is None:
                    bucket["unavailableCount"] = int(
                        bucket["unavailableCount"]
                    ) + 1
                else:
                    bucket["pricedCount"] = int(bucket["pricedCount"]) + 1
                    next_value = float(next_cost)
                    bucket["costUsd"] = float(bucket["costUsd"]) + next_value
                    bucket["nextCostUsd"] = float(
                        bucket["nextCostUsd"]
                    ) + next_value
            if (
                stored.cost_usd != next_cost
                or stored.status != str(next_status or "unavailable")
                or stored.price_snapshot != (
                    dict(next_snapshot) if isinstance(next_snapshot, Mapping) else None
                )
            ):
                changes.append(
                    {
                        "eventKey": stored.event_key,
                        "occurredAt": stored.occurred_at,
                        "provider": stored.provider,
                        "model": stored.model,
                        "previousCostUsd": stored.cost_usd,
                        "nextCostUsd": next_cost,
                        "nextStatus": str(next_status or "unavailable"),
                        "nextPriceSnapshot": (
                            dict(next_snapshot)
                            if isinstance(next_snapshot, Mapping)
                            else None
                        ),
                    }
                )
        if effective_at_utc:
            for period in ("before", "after"):
                bucket = time_breakdown[period]
                assert isinstance(bucket, dict)
                for field_name in ("costUsd", "previousCostUsd", "nextCostUsd"):
                    bucket[field_name] = round(float(bucket[field_name]), 6)
        return PricingRecalculationPreview(
            preview_id=str(uuid.uuid4()),
            provider=str(provider or "").strip().lower(),
            model=str(model or "").strip(),
            start_at=_utc_iso(start_at) if start_at else "",
            end_at=_utc_iso(end_at) if end_at else "",
            matched_count=len(rows),
            changed_count=len(changes),
            unavailable_count=unavailable,
            previous_total_usd=round(previous_total, 6),
            next_total_usd=round(next_total, 6),
            changes=tuple(changes),
            time_breakdown=time_breakdown,
        )

    def apply_recalculation(self, preview: PricingRecalculationPreview) -> dict[str, object]:
        executed_at = _utc_iso(None)
        with self._cache_lock, closing(self._connect()) as connection, connection:
            changed = 0
            for change in preview.changes:
                snapshot = change.get("nextPriceSnapshot")
                snapshot_json = (
                    json.dumps(dict(snapshot), ensure_ascii=False, sort_keys=True)
                    if isinstance(snapshot, Mapping)
                    else ""
                )
                cursor = connection.execute(
                    """
                    UPDATE usage_price_snapshots
                    SET cost_usd=?, status=?, price_snapshot_json=?, recalculated_at=?
                    WHERE event_key=?
                    """,
                    (
                        change.get("nextCostUsd"),
                        str(change.get("nextStatus") or "unavailable"),
                        snapshot_json,
                        executed_at,
                        str(change.get("eventKey") or ""),
                    ),
                )
                changed += max(0, int(cursor.rowcount or 0))
            connection.execute(
                """
                INSERT INTO pricing_recalculation_audit(
                    audit_id, executed_at, provider, model, start_at, end_at,
                    matched_count, changed_count, previous_total_usd, next_total_usd
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preview.preview_id,
                    executed_at,
                    preview.provider,
                    preview.model,
                    preview.start_at,
                    preview.end_at,
                    preview.matched_count,
                    changed,
                    preview.previous_total_usd,
                    preview.next_total_usd,
                ),
            )
            self._invalidate_snapshot_cache()
        return {
            "auditId": preview.preview_id,
            "executedAt": executed_at,
            "matchedCount": preview.matched_count,
            "changedCount": changed,
            "previousTotalUsd": preview.previous_total_usd,
            "nextTotalUsd": preview.next_total_usd,
        }


__all__ = [
    "PRICING_SNAPSHOT_SCHEMA_VERSION",
    "PricingRecalculationPreview",
    "PricingSnapshotLedger",
    "StoredPriceSnapshot",
]
