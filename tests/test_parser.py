"""Unit tests for Codex JSONL and SQLite SSE parsing."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.core.calculator import estimate_tokens
from codex_usage_hud.core.parser import (
    JsonlSessionParser,
    RequestTokens,
    SseRequestStateMachine,
    extract_log_field,
    parse_timestamp,
)


def record(
    timestamp: str,
    record_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {"timestamp": timestamp, "type": record_type, "payload": payload}


def token_count(
    timestamp: str,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    total_tokens: int,
    cumulative_total: int,
) -> dict[str, Any]:
    return record(
        timestamp,
        "event_msg",
        {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning_tokens,
                    "total_tokens": total_tokens,
                },
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning_tokens,
                    "total_tokens": cumulative_total,
                },
            },
        },
    )


class TimestampAndFieldTests(unittest.TestCase):
    def test_parse_timestamp_accepts_z_suffix(self) -> None:
        parsed = parse_timestamp("2026-05-28T01:02:03Z")

        self.assertEqual(parsed, datetime(2026, 5, 28, 1, 2, 3, tzinfo=timezone.utc))

    def test_extract_log_field_handles_quoted_values(self) -> None:
        body = 'event.name="codex.sse_event" slug="gpt-5.5" model=gpt-5.4'

        self.assertEqual(extract_log_field(body, "slug"), "gpt-5.5")
        self.assertEqual(extract_log_field(body, "model"), "gpt-5.4")


class JsonlSessionParserTests(unittest.TestCase):
    def test_load_records_lenient_skips_partial_jsonl(self) -> None:
        parser = JsonlSessionParser()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_text(
                json.dumps(record("2026-05-28T00:00:00Z", "session_meta", {"id": "s1"}))
                + "\n"
                + '{"timestamp":',
                encoding="utf-8",
            )

            records = parser.load_records_lenient(path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["_line"], 1)
        self.assertIsNotNone(records[0]["_dt"])

    def test_parse_records_extracts_confirmed_and_pending_estimates(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "turn_context", {"model": "gpt-5.5"}),
            token_count("2026-05-28T00:00:02Z", 100, 40, 20, 3, 120, 120),
            record(
                "2026-05-28T00:00:03Z",
                "event_msg",
                {"type": "user_message", "message": "abcd"},
            ),
            record(
                "2026-05-28T00:00:04Z",
                "response_item",
                {"type": "message", "content": [{"text": "abcdefgh"}]},
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(snapshot.session_id, "session-a")
        self.assertEqual(snapshot.confirmed.last_input, 100)
        self.assertEqual(snapshot.confirmed.last_cached, 40)
        self.assertEqual(snapshot.confirmed.last_output, 20)
        self.assertEqual(snapshot.confirmed.last_reasoning, 3)
        self.assertEqual(snapshot.token_events, 1)
        self.assertEqual(snapshot.estimate.input_tokens, estimate_tokens("abcd"))
        self.assertEqual(snapshot.estimate.output_tokens, estimate_tokens("abcdefgh"))
        self.assertEqual(snapshot.request.status, "running")
        self.assertEqual(snapshot.request.source, "jsonl")
        self.assertEqual(snapshot.request.started_at, parse_timestamp("2026-05-28T00:00:03Z"))
        self.assertEqual(snapshot.request.updated_at, parse_timestamp("2026-05-28T00:00:04Z"))
        self.assertEqual(len(snapshot.request_history), 2)
        self.assertEqual(snapshot.request_history[0].status, "confirmed")
        self.assertEqual(snapshot.request_history[1].status, "running")
        self.assertEqual(
            snapshot.request_history[1].started_at,
            parse_timestamp("2026-05-28T00:00:03Z"),
        )

    def test_latest_output_ignores_later_token_count_events(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:02Z",
                "event_msg",
                {"type": "agent_message", "message": "最后一轮输出会保留"},
            ),
            token_count("2026-05-28T00:00:03Z", 100, 20, 5, 1, 105, 105),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(snapshot.activity.kind, "confirmed")
        self.assertEqual(snapshot.last_output.kind, "agent")
        self.assertEqual(snapshot.last_output.detail, "最后一轮输出会保留")

    def test_usage_events_can_be_summarized_for_time_windows(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-27T23:59:58Z", "turn_context", {"model": "gpt-5.5"}),
            token_count("2026-05-27T23:59:59Z", 50, 0, 5, 0, 55, 55),
            token_count("2026-05-28T00:00:01Z", 100, 0, 0, 0, 100, 155),
            token_count("2026-05-28T00:00:02Z", 200, 100, 10, 2, 210, 365),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        events = parser.usage_events(records)
        summary = parser.summarize_usage_events(
            events,
            parse_timestamp("2026-05-28T00:00:00Z"),
        )

        self.assertEqual(len(events), 3)
        self.assertEqual(summary.tokens, 310)
        self.assertEqual(summary.input_tokens, 300)
        self.assertEqual(summary.cached_tokens, 100)
        self.assertEqual(summary.output_tokens, 10)
        self.assertEqual(summary.reasoning_tokens, 2)
        self.assertEqual(summary.cost_usd, 0.00141)

    def test_token_rounds_since_latest_task_only_include_current_task(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "turn_context", {"model": "gpt-5.5"}),
            token_count("2026-05-28T00:00:01Z", 10, 0, 5, 0, 15, 15),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_started"}),
            token_count("2026-05-28T00:00:03Z", 20, 5, 6, 1, 26, 41),
            token_count("2026-05-28T00:00:04Z", 20, 5, 6, 1, 26, 41),
            token_count("2026-05-28T00:00:05Z", 30, 7, 9, 2, 39, 80),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        task_index, _ = parser.latest_task_started(records)
        rounds = parser.token_rounds_since_task(records, task_index)

        self.assertEqual(len(rounds), 2)
        self.assertEqual([item.input_tokens for item in rounds], [20, 30])
        self.assertEqual([item.index for item in rounds], [1, 2])

    def test_parse_records_falls_back_to_jsonl_when_sse_errors(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "turn_context", {"model": "gpt-5.5"}),
            token_count("2026-05-28T00:00:02Z", 100, 20, 5, 1, 105, 105),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        missing_db = Path(tempfile.gettempdir()) / "missing-codex-hud-sse.sqlite"
        machine = SseRequestStateMachine(db_path=missing_db)

        snapshot = parser.parse_records(records, sse_tracker=machine)

        self.assertEqual(snapshot.request.status, "confirmed")
        self.assertEqual(snapshot.request.source, "jsonl")
        self.assertEqual(snapshot.request.input_tokens, 100)
        self.assertIn("SSE log database not found", snapshot.error)

    def test_parse_records_extracts_slow_tool_and_request_wait_summaries(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "turn_context", {"model": "gpt-5.5"}),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:03Z",
                "response_item",
                {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": "{}"},
            ),
            record(
                "2026-05-28T00:00:09Z",
                "response_item",
                {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            ),
            record(
                "2026-05-28T00:00:10Z",
                "event_msg",
                {"type": "user_message", "message": "hello"},
            ),
            record(
                "2026-05-28T00:00:17Z",
                "response_item",
                {"type": "message", "content": [{"text": "hi"}]},
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(snapshot.slow.slowest_tool, "6.0s shell")
        self.assertEqual(snapshot.slow.slowest_user_wait, "无（本任务无用户确认）")
        self.assertEqual(snapshot.slow.longest_gap, "7.0s model_startup")
        self.assertNotEqual(snapshot.slow.current_gap, "无")

    def test_parse_records_marks_current_gap_as_finished_after_task_complete(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "event_msg", {"type": "task_started"}),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_complete"}),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(snapshot.slow.current_gap, "任务已结束")
        self.assertFalse(snapshot.slow.current_gap_active)
        self.assertEqual(
            snapshot.task_completed_at,
            parse_timestamp("2026-05-28T00:00:02Z"),
        )

    def test_parse_records_ignores_turn_aborted_marker_and_ends_task(self) -> None:
        parser = JsonlSessionParser()
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "turn_context", {"model": "gpt-5.5"}),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_started"}),
            record(
                "2026-05-28T00:00:03Z",
                "response_item",
                {"type": "message", "role": "assistant", "content": [{"text": "继续处理"}]},
            ),
            token_count("2026-05-28T00:00:04Z", 80, 20, 12, 2, 92, 92),
            record(
                "2026-05-28T00:00:05Z",
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "<turn_aborted>\n"
                                "The user interrupted the previous turn on purpose.\n"
                                "</turn_aborted>"
                            )
                        }
                    ],
                },
            ),
            record(
                "2026-05-28T00:00:06Z",
                "event_msg",
                {"type": "turn_aborted", "reason": "interrupted"},
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records)

        self.assertEqual(snapshot.request.status, "confirmed")
        self.assertEqual(snapshot.estimate.total_tokens, 0)
        self.assertFalse(snapshot.slow.current_gap_active)
        self.assertEqual(snapshot.last_output.detail, "继续处理")
        self.assertEqual(
            snapshot.task_aborted_at,
            parse_timestamp("2026-05-28T00:00:06Z"),
        )

    def test_parse_records_clears_running_sse_request_after_turn_aborted(self) -> None:
        parser = JsonlSessionParser()
        tracker = SseRequestStateMachine(db_path=None)
        tracker.current = RequestTokens(
            status="running",
            round_index=1,
            model="gpt-5.5",
            source="sse",
            started_at=parse_timestamp("2026-05-28T00:00:05Z"),
            updated_at=parse_timestamp("2026-05-28T00:00:05Z"),
            total_tokens=0,
            output_tokens=0,
            estimated=True,
        )
        records = [
            record("2026-05-28T00:00:00Z", "session_meta", {"id": "session-a"}),
            record("2026-05-28T00:00:01Z", "turn_context", {"model": "gpt-5.5"}),
            record("2026-05-28T00:00:02Z", "event_msg", {"type": "task_started"}),
            token_count("2026-05-28T00:00:04Z", 40, 10, 8, 1, 48, 48),
            record(
                "2026-05-28T00:00:05Z",
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "<turn_aborted>\n"
                                "The user interrupted the previous turn on purpose.\n"
                                "</turn_aborted>"
                            )
                        }
                    ],
                },
            ),
            record(
                "2026-05-28T00:00:06Z",
                "event_msg",
                {"type": "turn_aborted", "reason": "interrupted"},
            ),
        ]
        for index, item in enumerate(records, 1):
            item["_line"] = index
            item["_dt"] = parse_timestamp(item["timestamp"])

        snapshot = parser.parse_records(records, sse_tracker=tracker)

        self.assertEqual(snapshot.request.status, "confirmed")
        self.assertEqual(
            snapshot.request.completed_at,
            parse_timestamp("2026-05-28T00:00:06Z"),
        )
        self.assertTrue(snapshot.request_history)
        self.assertNotIn("running", [item.status for item in snapshot.request_history])


class SseRequestStateMachineTests(unittest.TestCase):
    def test_state_machine_estimates_delta_then_confirms_from_otel_counts(self) -> None:
        machine = SseRequestStateMachine()
        rows = [
            {
                "id": 1,
                "target": "codex_otel.log_only",
                "feedback_log_body": (
                    'event.name="codex.sse_event" event.kind=response.created '
                    'conversation.id=session-a slug=gpt-5.5 '
                    "event.timestamp=2026-05-28T00:00:00Z"
                ),
            },
            {
                "id": 2,
                "target": "codex_api::sse::responses",
                "feedback_log_body": (
                    'SSE event: {"type":"response.output_text.delta","delta":"abcd"}'
                ),
            },
            {
                "id": 3,
                "target": "codex_otel.log_only",
                "feedback_log_body": (
                    'event.name="codex.sse_event" '
                    "event.kind=response.output_text.delta "
                    "conversation.id=session-a"
                ),
            },
            {
                "id": 4,
                "target": "codex_otel.log_only",
                "feedback_log_body": (
                    'event.name="codex.sse_event" event.kind=response.completed '
                    "input_token_count=100 cached_token_count=20 "
                    "output_token_count=5 reasoning_token_count=1 "
                    'conversation.id=session-a slug=gpt-5.5 '
                    "event.timestamp=2026-05-28T00:00:02Z"
                ),
            },
        ]

        machine.consume_log_rows(rows, "session-a")

        self.assertEqual(machine.current.status, "confirmed")
        self.assertEqual(machine.current.input_tokens, 100)
        self.assertEqual(machine.current.cached_tokens, 20)
        self.assertEqual(machine.current.output_tokens, 5)
        self.assertEqual(machine.current.reasoning_tokens, 1)
        self.assertEqual(machine.current.total_tokens, 105)
        self.assertFalse(machine.current.estimated)
        self.assertEqual(machine.rounds()[0].status, "confirmed")

    def test_sse_completed_event_can_confirm_without_otel_counts(self) -> None:
        machine = SseRequestStateMachine()
        machine.start_round(parse_timestamp("2026-05-28T00:00:00Z"), "gpt-5.5")
        machine.consume_sse(
            "SSE event: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_1",
                        "model": "gpt-5.5",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 4,
                            "total_tokens": 14,
                            "input_tokens_details": {"cached_tokens": 3},
                            "output_tokens_details": {"reasoning_tokens": 1},
                        },
                    },
                }
            )
        )

        self.assertEqual(machine.current.status, "confirmed")
        self.assertEqual(machine.current.response_id, "resp_1")
        self.assertEqual(machine.current.cached_tokens, 3)
        self.assertEqual(machine.current.total_tokens, 14)

    def test_build_reads_sqlite_logs_table_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "logs.sqlite"
            con = sqlite3.connect(db_path)
            try:
                con.execute(
                    "create table logs ("
                    "id integer primary key, "
                    "ts integer, "
                    "target text, "
                    "feedback_log_body text)"
                )
                con.executemany(
                    "insert into logs (id, ts, target, feedback_log_body) values (?, ?, ?, ?)",
                    [
                        (
                            1,
                            1_779_926_400,
                            "codex_otel.log_only",
                            'event.name="codex.sse_event" '
                            "event.kind=response.created "
                            "conversation.id=session-a slug=gpt-5.5 "
                            "event.timestamp=2026-05-28T00:00:00Z",
                        ),
                        (
                            2,
                            1_779_926_401,
                            "codex_otel.log_only",
                            'event.name="codex.sse_event" '
                            "event.kind=response.completed "
                            "input_token_count=50 cached_token_count=10 "
                            "output_token_count=8 reasoning_token_count=2 "
                            "conversation.id=session-a slug=gpt-5.5 "
                            "event.timestamp=2026-05-28T00:00:01Z",
                        ),
                    ],
                )
                con.commit()
            finally:
                con.close()

            machine = SseRequestStateMachine(db_path=db_path)
            request, history = machine.build("session-a")

        self.assertEqual(request.status, "confirmed")
        self.assertEqual(request.input_tokens, 50)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].output_tokens, 8)


if __name__ == "__main__":
    unittest.main()
