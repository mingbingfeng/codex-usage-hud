from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from codex_usage_hud.core import JsonlSessionParser
from codex_usage_hud.usage_cache import UsageSummaryCache


def _record(timestamp: str, record_type: str, payload: dict[str, object]) -> dict[str, object]:
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

    rebuilt, _ = UsageSummaryCache(JsonlSessionParser()).summarize(
        sessions, day, week
    )
    assert first.tokens == 10
    assert incremental == rebuilt
    assert incremental.tokens == 25


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
