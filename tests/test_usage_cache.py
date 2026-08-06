from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codex_usage_hud.core import JsonlSessionParser
from codex_usage_hud.core.parser import CostEstimator
from codex_usage_hud.core.pricing_snapshots import PricingSnapshotLedger
from codex_usage_hud.usage_cache import UsageSummaryCache
from codex_usage_hud.usage_summary_store import UsageSummaryStore


def _record(
    timestamp: str, record_type: str, payload: dict[str, object]
) -> dict[str, object]:
    return {"timestamp": timestamp, "type": record_type, "payload": payload}


def _token_count(timestamp: str, cumulative_input: int) -> dict[str, object]:
    return _record(
        timestamp,
        "event_msg",
        {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": cumulative_input,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                }
            },
        },
    )


def _append_record(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload) + "\n")


def test_usage_cache_reports_warm_only_after_matching_window_scan(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    cache = UsageSummaryCache(JsonlSessionParser())
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)

    assert not cache.is_warm_for(sessions, day, week)

    cache.summarize(sessions, day, week)

    assert cache.is_warm_for(sessions, day, week)
    assert not cache.is_warm_for(sessions, day + timedelta(days=1), week)


def test_usage_cache_bounds_retained_raw_tail_records(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    for index in range(3):
        (sessions / f"session-{index}.jsonl").write_text(
            json.dumps(
                _record(
                    "2026-07-30T00:00:00Z",
                    "session_meta",
                    {"id": f"s{index}", "model_provider": "custom"},
                )
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    cache = UsageSummaryCache(
        JsonlSessionParser(),
        max_tail_state_bytes=0,
    )
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)

    cache.summarize(sessions, day, week)

    assert all(entry.tail_state is None for entry in cache._entries.values())


def test_cold_usage_scan_batches_price_snapshot_transactions(tmp_path: Path) -> None:
    class CountingLedger(PricingSnapshotLedger):
        def __init__(self, path: Path) -> None:
            self.connection_count = 0
            super().__init__(path)

        def _connect(self):
            self.connection_count += 1
            return super()._connect()

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    for index in range(3):
        (sessions / f"session-{index}.jsonl").write_text(
            json.dumps(
                _record(
                    "2026-07-30T00:00:00Z",
                    "session_meta",
                    {"id": f"s{index}", "model_provider": "custom"},
                )
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    ledger = CountingLedger(tmp_path / "pricing.sqlite3")
    ledger.connection_count = 0
    parser = JsonlSessionParser(cost_estimator=CostEstimator(pricing_ledger=ledger))
    cache = UsageSummaryCache(parser)

    cache.summarize(
        sessions,
        datetime(2026, 7, 30, tzinfo=timezone.utc),
        datetime(2026, 7, 27, tzinfo=timezone.utc),
    )

    assert ledger.connection_count == 1


def test_usage_cache_append_reuses_tail_state_and_matches_full_rebuild(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    current = sessions / "current.jsonl"
    current.write_text(
        json.dumps(
            _record(
                "2026-07-30T00:00:00Z",
                "session_meta",
                {"id": "s1", "model_provider": "custom"},
            )
        )
        + "\n"
        + json.dumps(_token_count("2026-07-30T00:00:01Z", 10))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    parser = JsonlSessionParser()
    cache = UsageSummaryCache(parser, min_rescan_seconds=60)
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)

    first, _ = cache.summarize(sessions, day, week)
    first_state = cache._entries[current.resolve()].tail_state
    assert first_state is not None
    first_offset = first_state.offset

    _append_record(current, _token_count("2026-07-30T00:00:02Z", 25))
    incremental, _ = cache.summarize(
        sessions,
        day,
        week,
        allow_stale=True,
        refresh_paths=(current,),
    )
    second_state = cache._entries[current.resolve()].tail_state
    assert second_state is first_state
    assert second_state.offset > first_offset

    rebuilt, _ = UsageSummaryCache(JsonlSessionParser()).summarize(sessions, day, week)
    assert first.tokens == 10
    assert incremental == rebuilt
    assert incremental.tokens == 25


def test_usage_cache_does_not_build_full_session_snapshots_for_aggregate_scan(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    current = sessions / "current.jsonl"
    current.write_text(
        json.dumps(
            _record(
                "2026-07-30T00:00:00Z",
                "session_meta",
                {"id": "s1", "model_provider": "custom"},
            )
        )
        + "\n"
        + json.dumps(_token_count("2026-07-30T00:00:01Z", 10))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    class AggregateParser(JsonlSessionParser):
        def parse_file_incremental(self, *args: object, **kwargs: object):
            raise AssertionError("aggregate scans must not build ParsedSession")

    cache = UsageSummaryCache(AggregateParser(), min_rescan_seconds=60)
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)

    summary, _ = cache.summarize(sessions, day, week)

    assert summary.tokens == 10
    assert cache._entries[current.resolve()].tail_state is not None


def test_usage_cache_parser_version_change_resets_tail_state(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    path = sessions / "current.jsonl"
    path.write_text(
        json.dumps(_record("2026-07-30T00:00:00Z", "session_meta", {"id": "s1"}))
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    parser = JsonlSessionParser()
    parser.usage_contribution_version = "v1"
    cache = UsageSummaryCache(parser, min_rescan_seconds=60)
    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)

    cache.summarize(sessions, day, week)
    old_state = cache._entries[path.resolve()].tail_state
    parser.usage_contribution_version = "v2"
    _append_record(path, _token_count("2026-07-30T00:00:01Z", 5))
    cache.summarize(
        sessions,
        day,
        week,
        allow_stale=True,
        refresh_paths=(path,),
    )

    entry = cache._entries[path.resolve()]
    assert entry.parser_version == "v2"
    assert entry.tail_state is not old_state
    assert entry.summary_day.tokens == 5


def test_persisted_session_summaries_only_reparse_changed_jsonl(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    paths = [sessions / "first.jsonl", sessions / "second.jsonl"]
    for index, path in enumerate(paths, 1):
        path.write_text(
            json.dumps(
                _record(
                    "2026-07-30T00:00:00Z",
                    "session_meta",
                    {"id": f"s{index}", "model_provider": "custom"},
                )
            )
            + "\n"
            + json.dumps(_token_count("2026-07-30T00:00:01Z", index * 10))
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

    class CountingParser(JsonlSessionParser):
        def __init__(self) -> None:
            super().__init__()
            self.read_paths: list[Path] = []

        def load_records_incremental(self, path: Path, state=None):
            self.read_paths.append(path.resolve())
            return super().load_records_incremental(path, state)

    day = datetime(2026, 7, 30, tzinfo=timezone.utc)
    week = datetime(2026, 7, 27, tzinfo=timezone.utc)
    database = tmp_path / "usage-summary.sqlite3"
    first_parser = CountingParser()
    first_cache = UsageSummaryCache(
        first_parser,
        summary_store=UsageSummaryStore(database),
    )

    first_total, _ = first_cache.summarize(sessions, day, week)

    assert first_total.tokens == 30
    assert set(first_parser.read_paths) == {path.resolve() for path in paths}

    restarted_parser = CountingParser()
    restarted_cache = UsageSummaryCache(
        restarted_parser,
        summary_store=UsageSummaryStore(database),
    )
    restored_total, _ = restarted_cache.summarize(sessions, day, week)

    assert restored_total == first_total
    assert restarted_parser.read_paths == []
    assert all(entry.tail_state is None for entry in restarted_cache._entries.values())

    rollover_parser = CountingParser()
    rollover_cache = UsageSummaryCache(
        rollover_parser,
        summary_store=UsageSummaryStore(database),
    )
    rollover_day, rollover_week = rollover_cache.summarize(
        sessions,
        datetime(2026, 7, 31, tzinfo=timezone.utc),
        week,
    )

    assert rollover_day.tokens == 0
    assert rollover_week.tokens == first_total.tokens
    assert rollover_parser.read_paths == []

    _append_record(paths[0], _token_count("2026-07-30T00:00:02Z", 25))
    changed_parser = CountingParser()
    changed_cache = UsageSummaryCache(
        changed_parser,
        summary_store=UsageSummaryStore(database),
    )
    changed_total, _ = changed_cache.summarize(sessions, day, week)

    assert changed_total.tokens == 45
    assert changed_parser.read_paths == [paths[0].resolve()]
