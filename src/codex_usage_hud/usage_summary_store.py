"""Persistent per-session usage contributions for fast daemon restarts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from .core import UsageSummary
from .usage_contributions import (
    DailyUsageContribution,
    FileUsageContribution,
    UsageInsightAggregate,
    canonical_usage_path,
    path_under_usage_roots,
)


USAGE_SUMMARY_STORE_SCHEMA_VERSION = 2


def _datetime_text(value: datetime | None) -> str:
    return value.isoformat() if isinstance(value, datetime) else ""


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _summary_payload(summary: UsageSummary) -> dict[str, object]:
    return asdict(summary)


def _summary_from_payload(value: object) -> UsageSummary:
    raw = value if isinstance(value, Mapping) else {}
    allowed = {item.name for item in fields(UsageSummary)}
    return UsageSummary(**{key: raw[key] for key in allowed if key in raw})


def _models_payload(
    models: Mapping[str, UsageInsightAggregate],
) -> dict[str, object]:
    return {
        str(model): {
            "summary": _summary_payload(aggregate.summary),
            "priced_event_count": aggregate.priced_event_count,
            "total_event_count": aggregate.total_event_count,
            "latest_event_at": _datetime_text(aggregate.latest_event_at),
        }
        for model, aggregate in models.items()
    }


def _models_from_payload(value: object) -> dict[str, UsageInsightAggregate]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, UsageInsightAggregate] = {}
    for model, raw_aggregate in value.items():
        if not isinstance(raw_aggregate, Mapping):
            continue
        result[str(model)] = UsageInsightAggregate(
            summary=_summary_from_payload(raw_aggregate.get("summary")),
            priced_event_count=max(
                0, int(raw_aggregate.get("priced_event_count") or 0)
            ),
            total_event_count=max(0, int(raw_aggregate.get("total_event_count") or 0)),
            latest_event_at=_parse_datetime(raw_aggregate.get("latest_event_at")),
        )
    return result


def _daily_usage_payload(
    daily_usage: Mapping[str, DailyUsageContribution],
) -> dict[str, object]:
    return {
        date_key: {
            "summary": _summary_payload(contribution.summary),
            "models": _models_payload(contribution.models),
            "priced_event_count": contribution.priced_event_count,
            "total_event_count": contribution.total_event_count,
            "latest_event_at": _datetime_text(contribution.latest_event_at),
        }
        for date_key, contribution in daily_usage.items()
    }


def _daily_usage_from_payload(value: object) -> dict[str, DailyUsageContribution]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, DailyUsageContribution] = {}
    for date_key, raw_contribution in value.items():
        if not isinstance(raw_contribution, Mapping):
            continue
        result[str(date_key)] = DailyUsageContribution(
            summary=_summary_from_payload(raw_contribution.get("summary")),
            models=_models_from_payload(raw_contribution.get("models")),
            priced_event_count=max(
                0, int(raw_contribution.get("priced_event_count") or 0)
            ),
            total_event_count=max(
                0, int(raw_contribution.get("total_event_count") or 0)
            ),
            latest_event_at=_parse_datetime(raw_contribution.get("latest_event_at")),
        )
    return result


def _entry_payload(entry: FileUsageContribution) -> str:
    payload = {
        "month_start": _datetime_text(entry.month_start),
        "model_provider": entry.model_provider,
        "summary_day": _summary_payload(entry.summary_day),
        "summary_week": _summary_payload(entry.summary_week),
        "summary_month": _summary_payload(entry.summary_month),
        "summary_all": _summary_payload(entry.summary_all),
        "session_id": entry.session_id,
        "parent_session_id": entry.parent_session_id,
        "session_key": entry.session_key,
        "session_title": entry.session_title,
        "workdir_name": entry.workdir_name,
        "archived": entry.archived,
        "can_activate": entry.can_activate,
        "models_day": _models_payload(entry.models_day),
        "models_week": _models_payload(entry.models_week),
        "models_month": _models_payload(entry.models_month),
        "day_priced_event_count": entry.day_priced_event_count,
        "day_total_event_count": entry.day_total_event_count,
        "week_priced_event_count": entry.week_priced_event_count,
        "week_total_event_count": entry.week_total_event_count,
        "month_priced_event_count": entry.month_priced_event_count,
        "month_total_event_count": entry.month_total_event_count,
        "day_latest_event_at": _datetime_text(entry.day_latest_event_at),
        "week_latest_event_at": _datetime_text(entry.week_latest_event_at),
        "month_latest_event_at": _datetime_text(entry.month_latest_event_at),
        "daily_usage": _daily_usage_payload(entry.daily_usage),
        "daily_usage_complete": entry.daily_usage_complete,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _entry_from_row(row: sqlite3.Row) -> FileUsageContribution | None:
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    day_start = _parse_datetime(row["day_start"])
    week_start = _parse_datetime(row["week_start"])
    month_start = _parse_datetime(payload.get("month_start"))
    if day_start is None or week_start is None or month_start is None:
        return None
    return FileUsageContribution(
        mtime=float(row["mtime"]),
        file_size=int(row["file_size"]),
        day_start=day_start,
        week_start=week_start,
        month_start=month_start,
        model_provider=str(payload.get("model_provider") or "unknown"),
        summary_day=_summary_from_payload(payload.get("summary_day")),
        summary_week=_summary_from_payload(payload.get("summary_week")),
        summary_month=_summary_from_payload(payload.get("summary_month")),
        summary_all=_summary_from_payload(payload.get("summary_all")),
        session_id=str(payload.get("session_id") or ""),
        parent_session_id=str(payload.get("parent_session_id") or ""),
        session_key=str(payload.get("session_key") or ""),
        session_title=str(payload.get("session_title") or ""),
        workdir_name=str(payload.get("workdir_name") or ""),
        archived=bool(payload.get("archived")),
        can_activate=bool(payload.get("can_activate")),
        models_day=_models_from_payload(payload.get("models_day")),
        models_week=_models_from_payload(payload.get("models_week")),
        models_month=_models_from_payload(payload.get("models_month")),
        day_priced_event_count=max(0, int(payload.get("day_priced_event_count") or 0)),
        day_total_event_count=max(0, int(payload.get("day_total_event_count") or 0)),
        week_priced_event_count=max(
            0, int(payload.get("week_priced_event_count") or 0)
        ),
        week_total_event_count=max(0, int(payload.get("week_total_event_count") or 0)),
        month_priced_event_count=max(
            0, int(payload.get("month_priced_event_count") or 0)
        ),
        month_total_event_count=max(
            0, int(payload.get("month_total_event_count") or 0)
        ),
        day_latest_event_at=_parse_datetime(payload.get("day_latest_event_at")),
        week_latest_event_at=_parse_datetime(payload.get("week_latest_event_at")),
        month_latest_event_at=_parse_datetime(payload.get("month_latest_event_at")),
        parser_version=str(row["parser_version"] or ""),
        mtime_ns=int(row["mtime_ns"]),
        daily_usage=_daily_usage_from_payload(payload.get("daily_usage")),
        daily_usage_complete=bool(payload.get("daily_usage_complete")),
    )


def _merge_summary(target: UsageSummary, source: UsageSummary) -> None:
    for name in (
        "tokens",
        "input_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "output_tokens",
        "reasoning_tokens",
        "priced_event_count",
        "total_event_count",
    ):
        setattr(target, name, int(getattr(target, name)) + int(getattr(source, name)))
    target.cost_usd = round(float(target.cost_usd) + float(source.cost_usd), 6)


def _merge_models(
    target: dict[str, UsageInsightAggregate],
    source: Mapping[str, UsageInsightAggregate],
) -> None:
    for model, incoming in source.items():
        aggregate = target.setdefault(model, UsageInsightAggregate())
        _merge_summary(aggregate.summary, incoming.summary)
        aggregate.priced_event_count += incoming.priced_event_count
        aggregate.total_event_count += incoming.total_event_count
        if incoming.latest_event_at is not None and (
            aggregate.latest_event_at is None
            or incoming.latest_event_at > aggregate.latest_event_at
        ):
            aggregate.latest_event_at = incoming.latest_event_at


def _project_window(
    daily_usage: Mapping[str, DailyUsageContribution],
    start_at: datetime,
) -> tuple[
    UsageSummary,
    dict[str, UsageInsightAggregate],
    int,
    int,
    datetime | None,
]:
    summary = UsageSummary()
    models: dict[str, UsageInsightAggregate] = {}
    priced_count = 0
    total_count = 0
    latest_at: datetime | None = None
    start_key = start_at.date().isoformat()
    for date_key, contribution in daily_usage.items():
        if date_key < start_key:
            continue
        _merge_summary(summary, contribution.summary)
        _merge_models(models, contribution.models)
        priced_count += contribution.priced_event_count
        total_count += contribution.total_event_count
        if contribution.latest_event_at is not None and (
            latest_at is None or contribution.latest_event_at > latest_at
        ):
            latest_at = contribution.latest_event_at
    return summary, models, priced_count, total_count, latest_at


def _project_entry(
    entry: FileUsageContribution,
    day_start: datetime,
    week_start: datetime,
) -> FileUsageContribution:
    month_start = day_start - timedelta(days=29)
    day = _project_window(entry.daily_usage, day_start)
    week = _project_window(entry.daily_usage, week_start)
    month = _project_window(entry.daily_usage, month_start)
    return replace(
        entry,
        day_start=day_start,
        week_start=week_start,
        month_start=month_start,
        summary_day=day[0],
        summary_week=week[0],
        summary_month=month[0],
        models_day=day[1],
        models_week=week[1],
        models_month=month[1],
        day_priced_event_count=day[2],
        day_total_event_count=day[3],
        week_priced_event_count=week[2],
        week_total_event_count=week[3],
        month_priced_event_count=month[2],
        month_total_event_count=month[3],
        day_latest_event_at=day[4],
        week_latest_event_at=week[4],
        month_latest_event_at=month[4],
    )


class UsageSummaryStore:
    """SQLite owner for compact, restart-safe per-file contributions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=2.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 2000")
        return connection

    def initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS file_usage_contributions (
                    path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    file_size INTEGER NOT NULL,
                    day_start TEXT NOT NULL,
                    week_start TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_file_usage_contributions_window
                    ON file_usage_contributions(day_start, week_start, parser_version);
                CREATE INDEX IF NOT EXISTS idx_file_usage_contributions_parser
                    ON file_usage_contributions(parser_version);
                PRAGMA user_version=2;
                """
            )

    def load(
        self,
        scan_roots: Iterable[Path],
        day_start: datetime,
        week_start: datetime,
        parser_version: str,
    ) -> dict[Path, FileUsageContribution]:
        roots = tuple(scan_roots)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM file_usage_contributions
                WHERE parser_version=?
                """,
                (parser_version,),
            ).fetchall()
        entries: dict[Path, FileUsageContribution] = {}
        for row in rows:
            path = canonical_usage_path(Path(str(row["path"])))
            if not path_under_usage_roots(path, roots):
                continue
            entry = _entry_from_row(row)
            if entry is None:
                continue
            if entry.daily_usage_complete:
                entries[path] = _project_entry(entry, day_start, week_start)
            elif entry.day_start == day_start and entry.week_start == week_start:
                entries[path] = entry
        return entries

    def save_many(
        self,
        entries: Iterable[tuple[Path, FileUsageContribution]],
    ) -> None:
        values = [
            (
                str(canonical_usage_path(path)),
                float(entry.mtime or 0.0),
                int(entry.mtime_ns or 0),
                int(entry.file_size or 0),
                _datetime_text(entry.day_start),
                _datetime_text(entry.week_start),
                entry.parser_version,
                _entry_payload(entry),
                _datetime_text(datetime.now().astimezone()),
            )
            for path, entry in entries
        ]
        if not values:
            return
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                """
                INSERT INTO file_usage_contributions(
                    path, mtime, mtime_ns, file_size, day_start, week_start,
                    parser_version, payload_json, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    mtime=excluded.mtime,
                    mtime_ns=excluded.mtime_ns,
                    file_size=excluded.file_size,
                    day_start=excluded.day_start,
                    week_start=excluded.week_start,
                    parser_version=excluded.parser_version,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                values,
            )

    def delete_many(self, paths: Iterable[Path]) -> None:
        values = [(str(canonical_usage_path(path)),) for path in paths]
        if not values:
            return
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                "DELETE FROM file_usage_contributions WHERE path=?",
                values,
            )


__all__ = ["USAGE_SUMMARY_STORE_SCHEMA_VERSION", "UsageSummaryStore"]
