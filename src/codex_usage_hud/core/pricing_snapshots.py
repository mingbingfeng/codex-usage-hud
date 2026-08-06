"""Immutable request-level price snapshots for JSONL usage events."""

from __future__ import annotations

from collections import OrderedDict
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


PRICING_SNAPSHOT_SCHEMA_VERSION = 2
DEFAULT_PRICING_SNAPSHOT_CACHE_SIZE = 4096
_INSERT_SNAPSHOT_SQL = """
    INSERT OR IGNORE INTO usage_price_snapshots(
        event_key, session_id, event_line, occurred_at, provider, model,
        base_url, input_tokens, cached_input_tokens, cache_write_tokens,
        output_tokens, reasoning_tokens, cost_usd, status,
        version_id, created_at
    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def __init__(
        self,
        path: str | Path,
        *,
        max_cache_entries: int = DEFAULT_PRICING_SNAPSHOT_CACHE_SIZE,
    ) -> None:
        self.path = Path(path)
        self._cache_lock = RLock()
        self._max_cache_entries = max(0, int(max_cache_entries))
        self._snapshot_cache: OrderedDict[str, StoredPriceSnapshot] = OrderedDict()
        self._revision = 0
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
                    VALUES('pricing_revision', '0');
                """
            )
            self._ensure_snapshot_schema(connection)

    @staticmethod
    def _create_snapshot_table(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE usage_price_snapshots (
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
                version_id TEXT NOT NULL DEFAULT '',
                original_cost_usd REAL,
                original_version_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                recalculated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX idx_usage_price_snapshots_scope
                ON usage_price_snapshots(provider, model, occurred_at);
            """
        )

    @staticmethod
    def _snapshot_version_id(value: object) -> str:
        if not isinstance(value, Mapping):
            return ""
        return str(value.get("version_id") or value.get("versionId") or "").strip()

    @classmethod
    def _version_id_from_json(cls, value: object) -> str:
        return cls._snapshot_version_id(cls._decode_snapshot(value))

    def _ensure_snapshot_schema(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(usage_price_snapshots)"
            ).fetchall()
        }
        if not columns:
            self._create_snapshot_table(connection)
        elif "version_id" not in columns:
            connection.execute(
                "ALTER TABLE usage_price_snapshots RENAME TO usage_price_snapshots_v1"
            )
            connection.execute("DROP INDEX IF EXISTS idx_usage_price_snapshots_scope")
            self._create_snapshot_table(connection)
            cursor = connection.execute(
                "SELECT * FROM usage_price_snapshots_v1 ORDER BY event_key"
            )
            while True:
                rows = cursor.fetchmany(512)
                if not rows:
                    break
                values = []
                for row in rows:
                    recalculated_at = str(row["recalculated_at"] or "")
                    values.append(
                        (
                            row["event_key"],
                            row["session_id"],
                            row["event_line"],
                            row["occurred_at"],
                            row["provider"],
                            row["model"],
                            row["base_url"],
                            row["input_tokens"],
                            row["cached_input_tokens"],
                            row["cache_write_tokens"],
                            row["output_tokens"],
                            row["reasoning_tokens"],
                            row["cost_usd"],
                            row["status"],
                            self._version_id_from_json(row["price_snapshot_json"]),
                            row["original_cost_usd"] if recalculated_at else None,
                            (
                                self._version_id_from_json(
                                    row["original_price_snapshot_json"]
                                )
                                if recalculated_at
                                else ""
                            ),
                            row["created_at"],
                            recalculated_at,
                        )
                    )
                connection.executemany(
                    """
                    INSERT INTO usage_price_snapshots VALUES(
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    values,
                )
            connection.execute("DROP TABLE usage_price_snapshots_v1")
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(PRICING_SNAPSHOT_SCHEMA_VERSION),),
        )
        revision_row = connection.execute(
            "SELECT value FROM metadata WHERE key='pricing_revision'"
        ).fetchone()
        self._revision = max(0, int(revision_row[0] if revision_row is not None else 0))

    @property
    def revision(self) -> int:
        return self._revision

    @contextmanager
    def batch(self):
        """Reuse one connection and flush new snapshots once for a parser batch."""
        existing = self._batch_connection.get()
        if existing is not None:
            yield
            return
        with self._cache_lock, closing(self._connect()) as connection, connection:
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
                _utc_iso(
                    occurred_at if isinstance(occurred_at, (datetime, str)) else None
                ),
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
        version_id = str(row["version_id"] or "")
        original_version_id = str(row["original_version_id"] or "")
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
            price_snapshot=({"version_id": version_id} if version_id else None),
            original_cost_usd=(
                None
                if row["original_cost_usd"] is None
                else float(row["original_cost_usd"])
            ),
            original_price_snapshot=(
                {"version_id": original_version_id} if original_version_id else None
            ),
            recalculated_at=str(row["recalculated_at"] or ""),
        )

    def _cache_put(self, stored: StoredPriceSnapshot) -> None:
        if self._max_cache_entries <= 0:
            return
        self._snapshot_cache[stored.event_key] = stored
        self._snapshot_cache.move_to_end(stored.event_key)
        while len(self._snapshot_cache) > self._max_cache_entries:
            self._snapshot_cache.popitem(last=False)

    def _invalidate_snapshot_cache(self) -> None:
        self._snapshot_cache.clear()

    def get(self, event_key: object) -> StoredPriceSnapshot | None:
        key = str(event_key or "")
        if not key:
            return None
        with self._cache_lock:
            cached = self._snapshot_cache.get(key)
            if cached is not None:
                self._snapshot_cache.move_to_end(key)
                return cached
            connection = self._batch_connection.get()
            if connection is not None:
                row = connection.execute(
                    "SELECT * FROM usage_price_snapshots WHERE event_key=?",
                    (key,),
                ).fetchone()
            else:
                with closing(self._connect()) as standalone:
                    row = standalone.execute(
                        "SELECT * FROM usage_price_snapshots WHERE event_key=?",
                        (key,),
                    ).fetchone()
            if row is None:
                return None
            stored = self._stored(row)
            self._cache_put(stored)
            return stored

    def get_many(
        self,
        event_keys: list[str] | tuple[str, ...],
    ) -> dict[str, StoredPriceSnapshot]:
        """Fetch a bounded set in one query without ever hydrating the full ledger."""
        keys = tuple(dict.fromkeys(str(key or "") for key in event_keys if key))
        if not keys:
            return {}
        found: dict[str, StoredPriceSnapshot] = {}
        missing: list[str] = []
        with self._cache_lock:
            for key in keys:
                cached = self._snapshot_cache.get(key)
                if cached is None:
                    missing.append(key)
                    continue
                self._snapshot_cache.move_to_end(key)
                found[key] = cached
            if missing:
                connection = self._batch_connection.get()
                standalone = None
                if connection is None:
                    standalone = self._connect()
                    connection = standalone
                try:
                    for offset in range(0, len(missing), 400):
                        chunk = missing[offset : offset + 400]
                        placeholders = ",".join("?" for _key in chunk)
                        rows = connection.execute(
                            "SELECT * FROM usage_price_snapshots "
                            f"WHERE event_key IN ({placeholders})",
                            chunk,
                        ).fetchall()
                        for row in rows:
                            stored = self._stored(row)
                            found[stored.event_key] = stored
                            self._cache_put(stored)
                finally:
                    if standalone is not None:
                        standalone.close()
        return found

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
        del price_snapshot
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
            price_snapshot=(
                {"version_id": str(values[14])} if str(values[14] or "") else None
            ),
            original_cost_usd=None,
            original_price_snapshot=None,
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
        version_id = self._snapshot_version_id(price_snapshot)
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
            version_id,
            created_at,
        )
        existing = self.get(event_key)
        if existing is not None:
            return existing
        stored = self._stored_from_values(values, price_snapshot)
        pending = self._batch_pending.get()
        if pending is not None:
            self._cache_put(stored)
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
            self._cache_put(stored)
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
        resolver: Callable[
            [StoredPriceSnapshot], tuple[float | None, str, Mapping[str, object] | None]
        ],
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
                "SELECT * FROM usage_price_snapshots"
                + where
                + " ORDER BY occurred_at, event_key",
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
                period = "before" if stored.occurred_at < effective_at_utc else "after"
                bucket = time_breakdown[period]
                assert isinstance(bucket, dict)
                bucket["recordCount"] = int(bucket["recordCount"]) + 1
                bucket["previousCostUsd"] = float(bucket["previousCostUsd"]) + float(
                    stored.cost_usd or 0.0
                )
                if next_cost is None:
                    bucket["unavailableCount"] = int(bucket["unavailableCount"]) + 1
                else:
                    bucket["pricedCount"] = int(bucket["pricedCount"]) + 1
                    next_value = float(next_cost)
                    bucket["costUsd"] = float(bucket["costUsd"]) + next_value
                    bucket["nextCostUsd"] = float(bucket["nextCostUsd"]) + next_value
            if (
                stored.cost_usd != next_cost
                or stored.status != str(next_status or "unavailable")
                or self._snapshot_version_id(stored.price_snapshot)
                != self._snapshot_version_id(next_snapshot)
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
                        "nextVersionId": self._snapshot_version_id(next_snapshot),
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

    def apply_recalculation(
        self, preview: PricingRecalculationPreview
    ) -> dict[str, object]:
        executed_at = _utc_iso(None)
        with self._cache_lock, closing(self._connect()) as connection, connection:
            changed = 0
            for change in preview.changes:
                cursor = connection.execute(
                    """
                    UPDATE usage_price_snapshots
                    SET original_cost_usd=CASE
                            WHEN recalculated_at='' THEN cost_usd
                            ELSE original_cost_usd
                        END,
                        original_version_id=CASE
                            WHEN recalculated_at='' THEN version_id
                            ELSE original_version_id
                        END,
                        cost_usd=?, status=?, version_id=?, recalculated_at=?
                    WHERE event_key=?
                    """,
                    (
                        change.get("nextCostUsd"),
                        str(change.get("nextStatus") or "unavailable"),
                        str(change.get("nextVersionId") or ""),
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
            connection.execute(
                """
                UPDATE metadata
                SET value=CAST(CAST(value AS INTEGER) + 1 AS TEXT)
                WHERE key='pricing_revision'
                """
            )
            self._revision += 1
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
    "DEFAULT_PRICING_SNAPSHOT_CACHE_SIZE",
    "PRICING_SNAPSHOT_SCHEMA_VERSION",
    "PricingRecalculationPreview",
    "PricingSnapshotLedger",
    "StoredPriceSnapshot",
]
