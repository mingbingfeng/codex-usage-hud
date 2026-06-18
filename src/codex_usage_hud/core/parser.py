"""Local Codex JSONL and SQLite SSE parsing primitives."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .calculator import UsageCalculator, estimate_tokens

DEFAULT_MODEL = "gpt-5.5"
MAX_REQUEST_HISTORY = 500


def parse_timestamp(value: Any) -> datetime | None:
    """Parse Codex ISO timestamps without raising for partial log lines."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def short_session_id(path: Path) -> str:
    """Return a stable session id derived from a JSONL filename."""
    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        path.stem,
        re.IGNORECASE,
    )
    return match.group(1) if match else path.stem


def compact_text(value: Any, limit: int = 140) -> str:
    """Collapse whitespace and trim text for local display metadata."""
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def message_text(payload: Mapping[str, Any]) -> str:
    """Extract assistant text from a Codex response_item message payload."""
    direct = payload.get("text") or payload.get("message")
    if direct:
        return str(direct)

    parts: list[str] = []
    for item in payload.get("content") or []:
        if isinstance(item, Mapping):
            text = item.get("text")
            if text is None:
                text = item.get("content")
            if text is not None:
                parts.append(str(text))
        elif item is not None:
            parts.append(str(item))
    return " ".join(part for part in parts if part)


def reasoning_text(payload: Mapping[str, Any]) -> str:
    """Extract readable text from a reasoning response item when available."""
    direct = payload.get("text") or payload.get("reasoning") or payload.get("summary_text")
    if direct:
        return str(direct)

    parts: list[str] = []
    for key in ("summary", "content"):
        items = payload.get(key) or []
        if not isinstance(items, Sequence):
            continue
        for item in items:
            if isinstance(item, Mapping):
                text = item.get("text")
                if text is None:
                    text = item.get("content")
                if text is not None:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
    return " ".join(part for part in parts if part)


def response_message_role(payload: Mapping[str, Any]) -> str:
    """Return a normalized role for response_item message payloads."""
    return str(payload.get("role") or "").strip().lower()


def is_turn_aborted_message(payload: Mapping[str, Any]) -> bool:
    """Return whether a response_item message is Codex's synthetic turn_aborted marker."""
    if payload.get("type") != "message":
        return False
    if response_message_role(payload) != "user":
        return False
    text = " ".join(message_text(payload).split()).lower()
    return text.startswith("<turn_aborted>") or "<turn_aborted>" in text


def extract_log_field(body: str, name: str) -> str:
    """Extract a key=value field from an OTel feedback log line."""
    match = re.search(rf"\b{re.escape(name)}=(\"[^\"]*\"|[^\s]+)", body)
    if not match:
        return ""
    value = match.group(1)
    return value[1:-1] if value.startswith('"') and value.endswith('"') else value


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _row_value(row: Mapping[str, Any] | sqlite3.Row, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    try:
        return row[name]
    except (IndexError, KeyError, TypeError):
        return default


def seconds_between(start: datetime, end: datetime) -> float:
    """Return elapsed seconds between two timestamps."""
    return (end - start).total_seconds()


def event_label(record: Mapping[str, Any]) -> str:
    """Return a compact label for one JSONL event."""
    payload = record.get("payload") or {}
    record_type = record.get("type")
    payload_type = payload.get("type") if isinstance(payload, Mapping) else None

    if record_type == "response_item":
        if payload_type == "function_call":
            return "call:" + str(payload.get("name") or "?")
        if payload_type == "function_call_output":
            return "output:" + str(payload.get("call_id") or "?")
        if payload_type == "message":
            if is_turn_aborted_message(payload):
                return "turn_aborted"
            return "assistant:" + compact_text(message_text(payload), 70)
        if payload_type == "reasoning":
            detail = compact_text(reasoning_text(payload), 70)
            return "reasoning:" + detail if detail else "reasoning"

    if record_type == "event_msg":
        if payload_type == "user_message":
            return "user:" + compact_text(payload.get("message"), 70)
        if payload_type == "agent_message":
            return "agent:" + compact_text(payload.get("message"), 70)
        if payload_type == "token_count":
            return "token_count"
        if payload_type == "task_started":
            return "task_started"
        if payload_type == "task_complete":
            return "task_complete"

    return f"{record_type}:{payload_type}"


def classify_gap(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> str:
    """Classify a wait gap between adjacent JSONL records."""
    previous_payload = previous.get("payload") or {}
    current_payload = current.get("payload") or {}

    if (
        previous.get("type") == "response_item"
        and isinstance(previous_payload, Mapping)
        and previous_payload.get("type") == "function_call"
        and current.get("type") == "response_item"
        and isinstance(current_payload, Mapping)
        and current_payload.get("type") == "function_call_output"
    ):
        if previous_payload.get("call_id") == current_payload.get("call_id"):
            if previous_payload.get("name") == "request_user_input":
                return "user_wait"
            return "tool_wait"

    if (
        previous.get("type") == "event_msg"
        and isinstance(previous_payload, Mapping)
        and previous_payload.get("type") == "task_complete"
        and current.get("type") == "event_msg"
        and isinstance(current_payload, Mapping)
        and current_payload.get("type") == "task_started"
    ):
        return "idle_between_tasks"

    if (
        previous.get("type") == "event_msg"
        and isinstance(previous_payload, Mapping)
        and previous_payload.get("type") in {"user_message", "task_started"}
    ):
        return "model_startup"

    if (
        previous.get("type") == "event_msg"
        and isinstance(previous_payload, Mapping)
        and previous_payload.get("type") == "token_count"
        and current.get("type") == "response_item"
        and isinstance(current_payload, Mapping)
        and current_payload.get("type") == "reasoning"
    ):
        return "model_or_idle"

    return "other_gap"


@dataclass
class ConfirmedTokens:
    """Confirmed token counters from JSONL token_count events."""

    last_total: int = 0
    last_input: int = 0
    last_cached: int = 0
    last_output: int = 0
    last_reasoning: int = 0
    cumulative_total: int = 0
    cumulative_input: int = 0
    cumulative_cached: int = 0
    cumulative_output: int = 0
    cumulative_reasoning: int = 0
    cumulative_cost_usd: float | None = None
    timestamp: datetime | None = None
    line: int = 0


@dataclass
class EstimateTokens:
    """Unconfirmed local estimates after the latest token_count event."""

    input_tokens: int = 0
    output_tokens: int = 0
    tool_tokens: int = 0
    total_tokens: int = 0
    source: str = ""
    started_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class RequestTokens:
    """Current request state, either confirmed or still being estimated."""

    status: str = "waiting"
    round_index: int = 0
    model: str = ""
    input_tokens: int | None = None
    cached_tokens: int | None = None
    output_tokens: int | None = 0
    reasoning_tokens: int | None = None
    total_tokens: int | None = 0
    estimated: bool = True
    source: str = "jsonl"
    response_id: str = ""
    updated_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    cost_usd: float | None = None
    error: str = ""


@dataclass
class RequestRound:
    """One model request round within the current Codex task."""

    index: int
    status: str
    model: str
    input_tokens: int | None
    cached_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    estimated: bool
    cost_usd: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class UsageEvent:
    """One confirmed token_count event with computed cost metadata."""

    timestamp: datetime | None
    model: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    cost_usd: float | None = None


@dataclass
class UsageSummary:
    """Aggregated usage totals for a rolling time window."""

    tokens: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class Activity:
    """Latest user-visible activity discovered in a session log."""

    kind: str = "idle"
    detail: str = ""
    timestamp: datetime | None = None


@dataclass
class WorkStatusItem:
    """One active Codex work item shown in the HUD activity stack."""

    id: str
    title: str
    status: str
    status_label: str
    detail: str
    session_id: str = ""
    target_title: str = ""
    round_index: int = 0
    model_name: str = ""
    status_text: str = ""
    last_text: str = ""
    elapsed_text: str = ""
    progress: str = ""
    tokens_text: str = ""
    cost_text: str = ""
    cache_hit_text: str = ""
    workdir_name: str = ""
    source: str = ""
    workdir: str = ""
    session_started_at: datetime | None = None
    task_started_at: datetime | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    current: bool = False


@dataclass
class ToolCallTiming:
    """One completed tool or user-wait round-trip."""

    call_id: str
    name: str
    args: str
    start: datetime
    start_line: int
    end: datetime | None = None
    end_line: int | None = None
    output: str = ""

    @property
    def duration_seconds(self) -> float | None:
        if self.end is None:
            return None
        return seconds_between(self.start, self.end)

    @property
    def category(self) -> str:
        return "user_wait" if self.name == "request_user_input" else "tool"


@dataclass
class GapTiming:
    """One classified wait gap between adjacent JSONL records."""

    start: datetime
    end: datetime
    duration_seconds: float
    category: str
    from_event: str
    to_event: str
    start_line: int
    end_line: int


@dataclass
class SlowSummary:
    """Slow wait summaries for the currently visible task."""

    slowest_user_wait: str = "无（本任务无用户确认）"
    slowest_user_wait_call: ToolCallTiming | None = None
    slowest_tool: str = "无（本任务无工具调用）"
    slowest_tool_call: ToolCallTiming | None = None
    longest_gap: str = "无（本任务无长响应等待）"
    longest_gap_detail: GapTiming | None = None
    current_gap: str = "任务已结束"
    current_gap_active: bool = False


@dataclass
class ParsedSession:
    """Parsed view of a Codex session JSONL file."""

    session_path: Path | None = None
    session_id: str = "n/a"
    session_title: str = ""
    cwd: str = ""
    status: str = "starting"
    error: str = ""
    refreshed_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    session_started_at: datetime | None = None
    last_event_time: datetime | None = None
    last_file_mtime: float | None = None
    confirmed: ConfirmedTokens = field(default_factory=ConfirmedTokens)
    estimate: EstimateTokens = field(default_factory=EstimateTokens)
    request: RequestTokens = field(default_factory=RequestTokens)
    request_history: list[RequestRound] = field(default_factory=list)
    activity: Activity = field(default_factory=Activity)
    last_output: Activity = field(default_factory=Activity)
    slow: SlowSummary = field(default_factory=SlowSummary)
    line_count: int = 0
    token_events: int = 0
    task_started_at: datetime | None = None
    task_completed_at: datetime | None = None
    task_aborted_at: datetime | None = None
    selection_source: str = "activity"
    today_tokens: int = 0
    today_cost_usd: float = 0.0
    week_tokens: int = 0
    week_cost_usd: float = 0.0
    week_before_today_tokens: int = 0
    week_before_today_cost_usd: float = 0.0
    week_adjustment_usd: float = 0.0
    daily_limit_usd: float = 100.0
    weekly_limit_usd: float = 400.0
    day_start: datetime | None = None
    week_start: datetime | None = None
    budget_warnings: list[str] = field(default_factory=list)
    budget_error: str = ""
    active_work_items: list[WorkStatusItem] = field(default_factory=list)


class CostEstimator:
    """Small adapter that keeps parsing resilient to unknown model names."""

    def __init__(
        self,
        calculator: UsageCalculator | None = None,
        default_model: str = DEFAULT_MODEL,
    ) -> None:
        self._calculator = calculator or UsageCalculator()
        self._default_model = default_model

    def calculate(
        self,
        model: str,
        input_tokens: int | None,
        cached_tokens: int | None,
        output_tokens: int | None,
        reasoning_tokens: int | None = 0,
    ) -> float | None:
        if input_tokens is None or output_tokens is None:
            return None
        try:
            return self._calculator.calculate_cost_usd(
                model_name=model or self._default_model,
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens or 0,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens or 0,
            )
        except ValueError:
            return None


class JsonlSessionParser:
    """Parse Codex session JSONL records into token and request state."""

    def __init__(
        self,
        estimate_enabled: bool = True,
        cost_estimator: CostEstimator | None = None,
    ) -> None:
        self.estimate_enabled = estimate_enabled
        self.cost_estimator = cost_estimator or CostEstimator()

    def parse_file(
        self,
        path: Path,
        session_id: str | None = None,
        sse_tracker: SseRequestStateMachine | None = None,
    ) -> ParsedSession:
        """Parse a session file, skipping incomplete trailing JSONL rows."""
        snapshot = ParsedSession(session_path=path)
        if not path.exists():
            snapshot.status = "missing"
            snapshot.error = f"Session file not found: {path}"
            return snapshot

        records = self.load_records_lenient(path)
        try:
            snapshot.last_file_mtime = path.stat().st_mtime
        except OSError:
            snapshot.last_file_mtime = None
        return self.parse_records(records, path, session_id, sse_tracker, snapshot)

    def parse_records(
        self,
        records: Sequence[dict[str, Any]],
        path: Path | None = None,
        session_id: str | None = None,
        sse_tracker: SseRequestStateMachine | None = None,
        snapshot: ParsedSession | None = None,
    ) -> ParsedSession:
        """Parse already loaded Codex JSONL records."""
        parsed = snapshot or ParsedSession(session_path=path)
        parsed.line_count = len(records)
        if not records:
            parsed.status = "waiting"
            parsed.error = "Session file has no complete JSONL records"
            if path is not None:
                parsed.session_id = session_id or short_session_id(path)
            return parsed

        parsed.session_id = session_id or self.session_id_from_records(records, path)
        parsed.session_started_at = self.session_started_at(records)
        parsed.cwd = self.session_cwd(records)
        parsed.last_event_time = records[-1].get("_dt")
        parsed.activity = self.latest_activity(records)
        parsed.last_output = self.latest_output(records)
        parsed.slow = self.slow_summary(records, parsed.last_event_time)
        token_index = self.apply_confirmed_tokens(parsed, records)
        if self.estimate_enabled:
            parsed.estimate = self.estimate_since_last_token(records, token_index)

        task_started_index, task_started_at = self.latest_task_started(records)
        parsed.task_started_at = task_started_at
        parsed.task_completed_at = self.latest_task_completed_after(
            records,
            task_started_index,
        )
        parsed.task_aborted_at = self.latest_task_aborted_after(
            records,
            task_started_index,
        )
        jsonl_rounds = self.token_rounds_since_task(records, task_started_index)
        parsed.request = self.build_request_tokens(
            parsed,
            task_started_at,
            self.latest_model(records),
            jsonl_rounds,
            sse_tracker,
        )
        parsed.status = "parsed"
        return parsed

    def load_records_lenient(self, path: Path) -> list[dict[str, Any]]:
        """Read JSONL records while tolerating partially written lines."""
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                text = line.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                record["_line"] = line_number
                record["_dt"] = parse_timestamp(record.get("timestamp"))
                records.append(record)
        return records

    def session_id_from_records(
        self, records: Sequence[Mapping[str, Any]], path: Path | None = None
    ) -> str:
        for record in records:
            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload") or {}
            if isinstance(payload, Mapping) and payload.get("id"):
                return str(payload["id"])
        return short_session_id(path) if path is not None else "n/a"

    def session_started_at(
        self, records: Sequence[Mapping[str, Any]]
    ) -> datetime | None:
        for record in records:
            if record.get("type") != "session_meta":
                continue
            timestamp = record.get("_dt")
            if isinstance(timestamp, datetime):
                return timestamp
        first_timestamp = records[0].get("_dt") if records else None
        return first_timestamp if isinstance(first_timestamp, datetime) else None

    def session_cwd(self, records: Sequence[Mapping[str, Any]]) -> str:
        for record in records:
            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload") or {}
            if isinstance(payload, Mapping) and payload.get("cwd"):
                return str(payload.get("cwd") or "").strip()
        return ""

    def latest_task_started(
        self, records: Sequence[Mapping[str, Any]]
    ) -> tuple[int | None, datetime | None]:
        for index in range(len(records) - 1, -1, -1):
            record = records[index]
            payload = record.get("payload") or {}
            if (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") == "task_started"
            ):
                return index, record.get("_dt")
        return None, None

    def latest_task_completed_after(
        self,
        records: Sequence[Mapping[str, Any]],
        task_started_index: int | None,
    ) -> datetime | None:
        start_index = 0 if task_started_index is None else task_started_index + 1
        for record in reversed(records[start_index:]):
            payload = record.get("payload") or {}
            if (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") == "task_complete"
            ):
                return record.get("_dt")
        return None

    def latest_task_aborted_after(
        self,
        records: Sequence[Mapping[str, Any]],
        task_started_index: int | None,
    ) -> datetime | None:
        start_index = 0 if task_started_index is None else task_started_index + 1
        for record in reversed(records[start_index:]):
            payload = record.get("payload") or {}
            if (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") == "turn_aborted"
            ):
                return record.get("_dt")
        return None

    def latest_model(self, records: Sequence[Mapping[str, Any]]) -> str:
        for record in reversed(records):
            payload = record.get("payload") or {}
            if (
                record.get("type") == "turn_context"
                and isinstance(payload, Mapping)
                and payload.get("model")
            ):
                return str(payload.get("model") or "")
        return ""

    def apply_confirmed_tokens(
        self, snapshot: ParsedSession, records: Sequence[Mapping[str, Any]]
    ) -> int:
        """Apply confirmed token_count events and return the last event index."""
        last_token_index = -1
        current_model = ""
        session_cost = 0.0
        has_session_cost = False
        last_cumulative_seen: int | None = None
        seen_usage_keys: set[tuple[Any, ...]] = set()

        for index, record in enumerate(records):
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            if record.get("type") == "turn_context":
                current_model = str(payload.get("model") or current_model)
                continue
            if record.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue

            info = payload.get("info") or {}
            if not isinstance(info, Mapping):
                continue
            last_usage = info.get("last_token_usage") or {}
            cumulative = info.get("total_token_usage") or {}
            if not isinstance(last_usage, Mapping) or not isinstance(cumulative, Mapping):
                continue

            last_input = _as_int(last_usage.get("input_tokens"))
            last_cached = _as_int(last_usage.get("cached_input_tokens"))
            last_output = _as_int(last_usage.get("output_tokens"))
            last_reasoning = _as_int(last_usage.get("reasoning_output_tokens"))
            last_total = _as_int(
                last_usage.get("total_tokens"), last_input + last_output
            )
            cumulative_total = _as_int(cumulative.get("total_tokens"))
            if not (last_input or last_output or last_reasoning):
                continue
            if cumulative_total:
                if (
                    last_cumulative_seen is not None
                    and cumulative_total <= last_cumulative_seen
                ):
                    continue
                last_cumulative_seen = cumulative_total
            else:
                usage_key = (
                    current_model,
                    last_input,
                    last_cached,
                    last_output,
                    last_reasoning,
                    last_total,
                )
                if usage_key in seen_usage_keys:
                    continue
                seen_usage_keys.add(usage_key)

            last_cost = self.cost_estimator.calculate(
                current_model,
                last_input,
                last_cached,
                last_output,
                last_reasoning,
            )
            if last_cost is not None:
                session_cost += last_cost
                has_session_cost = True

            last_token_index = index
            snapshot.token_events += 1
            snapshot.confirmed = ConfirmedTokens(
                last_total=last_total,
                last_input=last_input,
                last_cached=last_cached,
                last_output=last_output,
                last_reasoning=last_reasoning,
                cumulative_total=cumulative_total,
                cumulative_input=_as_int(cumulative.get("input_tokens")),
                cumulative_cached=_as_int(cumulative.get("cached_input_tokens")),
                cumulative_output=_as_int(cumulative.get("output_tokens")),
                cumulative_reasoning=_as_int(
                    cumulative.get("reasoning_output_tokens")
                ),
                cumulative_cost_usd=session_cost if has_session_cost else None,
                timestamp=record.get("_dt"),
                line=_as_int(record.get("_line")),
            )
        return last_token_index

    def usage_events(self, records: Sequence[Mapping[str, Any]]) -> list[UsageEvent]:
        """Return deduplicated confirmed token usage events across a session."""
        events: list[UsageEvent] = []
        current_model = ""
        last_cumulative_seen: int | None = None
        seen_usage_keys: set[tuple[Any, ...]] = set()

        for record in records:
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            if record.get("type") == "turn_context":
                current_model = str(payload.get("model") or current_model)
                continue
            if record.get("type") != "event_msg" or payload.get("type") != "token_count":
                continue

            info = payload.get("info") or {}
            if not isinstance(info, Mapping):
                continue
            usage = info.get("last_token_usage") or {}
            cumulative = info.get("total_token_usage") or {}
            if not isinstance(usage, Mapping) or not isinstance(cumulative, Mapping):
                continue

            input_tokens = _as_int(usage.get("input_tokens"))
            cached_tokens = _as_int(usage.get("cached_input_tokens"))
            output_tokens = _as_int(usage.get("output_tokens"))
            reasoning_tokens = _as_int(usage.get("reasoning_output_tokens"))
            total_tokens = _as_int(
                usage.get("total_tokens"), input_tokens + output_tokens
            )
            cumulative_total = _as_int(cumulative.get("total_tokens"))
            if not (input_tokens or output_tokens or reasoning_tokens):
                continue

            if cumulative_total:
                if (
                    last_cumulative_seen is not None
                    and cumulative_total <= last_cumulative_seen
                ):
                    continue
                last_cumulative_seen = cumulative_total
            else:
                usage_key = (
                    current_model,
                    input_tokens,
                    cached_tokens,
                    output_tokens,
                    reasoning_tokens,
                    total_tokens,
                    record.get("_dt"),
                )
                if usage_key in seen_usage_keys:
                    continue
                seen_usage_keys.add(usage_key)

            events.append(
                UsageEvent(
                    timestamp=record.get("_dt"),
                    model=current_model,
                    input_tokens=input_tokens,
                    cached_tokens=cached_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=total_tokens,
                    cost_usd=self.cost_estimator.calculate(
                        current_model,
                        input_tokens,
                        cached_tokens,
                        output_tokens,
                        reasoning_tokens,
                    ),
                )
            )
        return events

    def summarize_usage_events(
        self,
        events: Sequence[UsageEvent],
        start_at: datetime,
    ) -> UsageSummary:
        """Aggregate confirmed usage events from ``start_at`` onward."""
        summary = UsageSummary()
        for event in events:
            if event.timestamp is None:
                continue
            event_time = event.timestamp.astimezone(start_at.tzinfo)
            if event_time < start_at:
                continue
            summary.tokens += event.total_tokens
            summary.input_tokens += event.input_tokens
            summary.cached_tokens += event.cached_tokens
            summary.output_tokens += event.output_tokens
            summary.reasoning_tokens += event.reasoning_tokens
            summary.cost_usd += float(event.cost_usd or 0.0)
        summary.cost_usd = round(summary.cost_usd, 6)
        return summary

    def estimate_since_last_token(
        self, records: Sequence[Mapping[str, Any]], token_index: int
    ) -> EstimateTokens:
        estimate = EstimateTokens()
        sources: list[str] = []
        for record in records[token_index + 1 :]:
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            record_type = record.get("type")
            payload_type = payload.get("type")
            timestamp = record.get("_dt")
            contributed = False

            if record_type == "event_msg" and payload_type == "user_message":
                value = estimate_tokens(payload.get("message"))
                estimate.input_tokens += value
                if value:
                    sources.append(f"user~{value}")
                    contributed = True
            elif record_type == "response_item" and payload_type == "message":
                if is_turn_aborted_message(payload):
                    continue
                role = response_message_role(payload)
                if role and role != "assistant":
                    continue
                value = estimate_tokens(message_text(payload))
                estimate.output_tokens += value
                if value:
                    sources.append(f"assistant~{value}")
                    contributed = True
            elif record_type == "response_item" and payload_type == "function_call":
                value = estimate_tokens(payload.get("arguments"))
                estimate.output_tokens += value
                if value:
                    sources.append(f"tool-call~{value}")
                    contributed = True
            elif record_type == "response_item" and payload_type == "function_call_output":
                value = estimate_tokens(payload.get("output"))
                estimate.tool_tokens += value
                if value:
                    sources.append(f"tool-output~{value}")
                    contributed = True

            if contributed and isinstance(timestamp, datetime):
                if estimate.started_at is None:
                    estimate.started_at = timestamp
                estimate.updated_at = timestamp

        estimate.total_tokens = (
            estimate.input_tokens + estimate.output_tokens + estimate.tool_tokens
        )
        estimate.source = ", ".join(sources[-4:]) if sources else "no pending estimate"
        return estimate

    def token_rounds_since_task(
        self, records: Sequence[Mapping[str, Any]], task_started_index: int | None
    ) -> list[RequestRound]:
        rounds: list[RequestRound] = []
        current_model = ""
        start_index = 0 if task_started_index is None else task_started_index + 1
        for record in records[:start_index]:
            payload = record.get("payload") or {}
            if (
                record.get("type") == "turn_context"
                and isinstance(payload, Mapping)
                and payload.get("model")
            ):
                current_model = str(payload.get("model") or current_model)

        last_cumulative_total: int | None = None
        seen_usage_keys: set[tuple[Any, ...]] = set()
        round_started_at = (
            records[task_started_index].get("_dt")
            if task_started_index is not None
            else None
        )
        for record in records[start_index:]:
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            record_type = record.get("type")
            if record_type == "turn_context":
                current_model = str(payload.get("model") or current_model)
                continue

            timestamp = record.get("_dt")
            if round_started_at is None:
                round_started_at = timestamp
            if record_type != "event_msg" or payload.get("type") != "token_count":
                continue

            info = payload.get("info") or {}
            if not isinstance(info, Mapping):
                continue
            usage = info.get("last_token_usage") or {}
            cumulative = info.get("total_token_usage") or {}
            if not isinstance(usage, Mapping) or not isinstance(cumulative, Mapping):
                continue

            input_tokens = _as_int(usage.get("input_tokens"))
            cached_tokens = _as_int(usage.get("cached_input_tokens"))
            output_tokens = _as_int(usage.get("output_tokens"))
            reasoning_tokens = _as_int(usage.get("reasoning_output_tokens"))
            total_tokens = _as_int(
                usage.get("total_tokens"), input_tokens + output_tokens
            )
            cumulative_total = _as_int(cumulative.get("total_tokens"))
            if not (input_tokens or output_tokens or reasoning_tokens):
                continue
            if cumulative_total:
                if (
                    last_cumulative_total is not None
                    and cumulative_total <= last_cumulative_total
                ):
                    continue
                last_cumulative_total = cumulative_total
            else:
                usage_key = (
                    current_model,
                    input_tokens,
                    cached_tokens,
                    output_tokens,
                    reasoning_tokens,
                    total_tokens,
                    timestamp,
                )
                if usage_key in seen_usage_keys:
                    continue
                seen_usage_keys.add(usage_key)

            rounds.append(
                RequestRound(
                    index=len(rounds) + 1,
                    status="confirmed",
                    model=current_model,
                    input_tokens=input_tokens,
                    cached_tokens=cached_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=total_tokens,
                    estimated=False,
                    cost_usd=self.cost_estimator.calculate(
                        current_model,
                        input_tokens,
                        cached_tokens,
                        output_tokens,
                        reasoning_tokens,
                    ),
                    started_at=round_started_at,
                    completed_at=timestamp,
                )
            )
            round_started_at = timestamp
        return rounds

    def latest_activity(self, records: Sequence[Mapping[str, Any]]) -> Activity:
        for record in reversed(records):
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            record_type = record.get("type")
            payload_type = payload.get("type")
            timestamp = record.get("_dt")

            if record_type == "event_msg" and payload_type == "user_message":
                return Activity("user", compact_text(payload.get("message"), 160), timestamp)
            if record_type == "event_msg" and payload_type == "agent_message":
                return Activity("agent", compact_text(payload.get("message"), 160), timestamp)
            if record_type == "response_item" and payload_type == "function_call":
                return Activity(
                    "tool call",
                    f"{payload.get('name')} {compact_text(payload.get('arguments'), 140)}",
                    timestamp,
                )
            if record_type == "response_item" and payload_type == "function_call_output":
                return Activity(
                    "tool output",
                    f"{payload.get('call_id')} {compact_text(payload.get('output'), 140)}",
                    timestamp,
                )
            if record_type == "response_item" and payload_type == "message":
                if is_turn_aborted_message(payload):
                    continue
                role = response_message_role(payload)
                if role and role != "assistant":
                    continue
                return Activity("assistant", compact_text(message_text(payload), 160), timestamp)
            if record_type == "event_msg" and payload_type == "token_count":
                return Activity("confirmed", "received token_count", timestamp)
        return Activity("idle", "no activity", None)

    def latest_output(self, records: Sequence[Mapping[str, Any]]) -> Activity:
        """Return the latest assistant-visible text, ignoring bookkeeping events."""
        for record in reversed(records):
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            record_type = record.get("type")
            payload_type = payload.get("type")
            timestamp = record.get("_dt")

            if record_type == "event_msg" and payload_type == "agent_message":
                text = compact_text(payload.get("message"), 220)
                if text:
                    return Activity("agent", text, timestamp)
            if record_type == "response_item" and payload_type == "message":
                if is_turn_aborted_message(payload):
                    continue
                role = response_message_role(payload)
                if role and role != "assistant":
                    continue
                text = compact_text(message_text(payload), 220)
                if text:
                    return Activity("assistant", text, timestamp)
        return Activity("idle", "", None)

    def slow_summary(
        self,
        records: Sequence[Mapping[str, Any]],
        last_event_time: datetime | None,
    ) -> SlowSummary:
        """Summarize tool waits and model-response gaps for the latest task."""
        calls_by_id: dict[str, ToolCallTiming] = {}
        completed_calls: list[ToolCallTiming] = []
        gaps: list[GapTiming] = []
        active_after_record: list[bool] = []
        task_active = False
        latest_task_start_index = 0

        for record in records:
            payload = record.get("payload") or {}
            if (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") == "task_started"
            ):
                task_active = True
                latest_task_start_index = len(active_after_record)
            is_task_terminal = (
                record.get("type") == "event_msg"
                and isinstance(payload, Mapping)
                and payload.get("type") in {"task_complete", "turn_aborted"}
            )
            active_after_record.append(
                task_active
                and not is_task_terminal
            )
            if is_task_terminal:
                task_active = False

        active_gap_categories = {
            "model_startup",
            "model_or_idle",
            "tool_wait",
            "other_gap",
        }
        for index, (previous, current) in enumerate(zip(records, records[1:])):
            previous_dt = previous.get("_dt")
            current_dt = current.get("_dt")
            if not isinstance(previous_dt, datetime) or not isinstance(current_dt, datetime):
                continue
            duration = seconds_between(previous_dt, current_dt)
            category = classify_gap(previous, current)
            previous_is_active = (
                active_after_record[index] if index < len(active_after_record) else False
            )
            if (
                duration >= 5.0
                and index >= latest_task_start_index
                and previous_is_active
                and category in active_gap_categories
            ):
                gaps.append(
                    GapTiming(
                        start=previous_dt,
                        end=current_dt,
                        duration_seconds=duration,
                        category=category,
                        from_event=event_label(previous),
                        to_event=event_label(current),
                        start_line=_as_int(previous.get("_line")),
                        end_line=_as_int(current.get("_line")),
                    )
                )

        for record in records:
            payload = record.get("payload") or {}
            if not isinstance(payload, Mapping):
                continue
            record_type = record.get("type")
            payload_type = payload.get("type")
            timestamp = record.get("_dt")
            if not isinstance(timestamp, datetime):
                continue

            if record_type == "response_item" and payload_type == "function_call":
                call_id = str(payload.get("call_id") or "")
                calls_by_id[call_id] = ToolCallTiming(
                    call_id=call_id,
                    name=str(payload.get("name") or "?"),
                    args=str(payload.get("arguments") or ""),
                    start=timestamp,
                    start_line=_as_int(record.get("_line")),
                )
            elif record_type == "response_item" and payload_type == "function_call_output":
                call_id = str(payload.get("call_id") or "")
                call = calls_by_id.get(call_id)
                if call is None:
                    continue
                call.end = timestamp
                call.end_line = _as_int(record.get("_line"))
                call.output = str(payload.get("output") or "")
                completed_calls.append(call)

        waits = [
            call
            for call in completed_calls
            if call.category == "user_wait" and call.duration_seconds is not None
        ]
        tools = [
            call
            for call in completed_calls
            if call.category == "tool" and call.duration_seconds is not None
        ]
        waits.sort(key=lambda call: call.duration_seconds or 0.0, reverse=True)
        tools.sort(key=lambda call: call.duration_seconds or 0.0, reverse=True)
        gaps.sort(key=lambda item: item.duration_seconds, reverse=True)

        current_gap = "任务已结束"
        currently_active = bool(active_after_record[-1]) if active_after_record else False
        if last_event_time is not None and currently_active:
            current_gap = f"{seconds_between(last_event_time, datetime.now(last_event_time.tzinfo)):.1f}s"

        return SlowSummary(
            slowest_user_wait=(
                self.format_call(waits[0]) if waits else "无（本任务无用户确认）"
            ),
            slowest_user_wait_call=waits[0] if waits else None,
            slowest_tool=(
                self.format_call(tools[0]) if tools else "无（本任务无工具调用）"
            ),
            slowest_tool_call=tools[0] if tools else None,
            longest_gap=(
                self.format_gap(gaps[0]) if gaps else "无（本任务无长响应等待）"
            ),
            longest_gap_detail=gaps[0] if gaps else None,
            current_gap=current_gap,
            current_gap_active=currently_active,
        )

    def format_call(self, call: ToolCallTiming) -> str:
        """Format one tool or user-wait duration."""
        duration = call.duration_seconds or 0.0
        name = "用户确认" if call.name == "request_user_input" else call.name
        return f"{duration:.1f}s {name}"

    def format_gap(self, gap: GapTiming) -> str:
        """Format one model / response wait gap."""
        return f"{gap.duration_seconds:.1f}s {gap.category}"

    def build_request_tokens(
        self,
        snapshot: ParsedSession,
        task_started_at: datetime | None,
        latest_model: str,
        jsonl_rounds: list[RequestRound],
        sse_tracker: SseRequestStateMachine | None = None,
    ) -> RequestTokens:
        history = list(jsonl_rounds)
        if sse_tracker is not None:
            request, sse_history = sse_tracker.build(snapshot.session_id, task_started_at)
            if self.running_request_already_confirmed(request, history):
                request = RequestTokens(status="waiting", source="sse")
            if not history:
                history = list(sse_history)
                if request.status == "running" and request.round_index and history:
                    history[-1] = self.round_from_request(
                        request, snapshot, latest_model
                    )
            elif request.status == "running" and request.round_index:
                history.append(self.round_from_request(request, snapshot, latest_model))
            elif request.status == "confirmed" and request.input_tokens is not None:
                latest = history[-1] if history else None
                same_as_latest = (
                    latest is not None
                    and latest.input_tokens == request.input_tokens
                    and latest.cached_tokens == request.cached_tokens
                    and latest.output_tokens == request.output_tokens
                    and latest.reasoning_tokens == request.reasoning_tokens
                    and latest.total_tokens == request.total_tokens
                )
                if not same_as_latest:
                    history.append(self.round_from_request(request, snapshot, latest_model))
            history = self._history_after_task_abort(history, snapshot.task_aborted_at)
            snapshot.request_history = self.reindex_rounds(history)
            request = self.request_after_task_abort(snapshot, request, latest_model)
            if request.error:
                if not snapshot.error:
                    snapshot.error = request.error
            elif request.status != "waiting":
                return request

        request = self.fallback_request_tokens(snapshot, latest_model)
        if history:
            if request.status == "running":
                history.append(self.round_from_request(request, snapshot, latest_model))
            history = self._history_after_task_abort(history, snapshot.task_aborted_at)
            snapshot.request_history = self.reindex_rounds(history)
        elif request.status != "waiting":
            snapshot.request_history = self.reindex_rounds(
                [self.round_from_request(request, snapshot, latest_model)]
            )
        return request

    def running_request_already_confirmed(
        self, request: RequestTokens, history: Sequence[RequestRound]
    ) -> bool:
        if request.status != "running" or request.started_at is None:
            return False
        for item in reversed(history):
            if item.estimated or item.completed_at is None:
                continue
            return (item.completed_at - request.started_at).total_seconds() >= -1.0
        return False

    def fallback_request_tokens(
        self, snapshot: ParsedSession, latest_model: str
    ) -> RequestTokens:
        if (
            snapshot.task_completed_at is None
            and snapshot.task_aborted_at is None
            and snapshot.estimate.total_tokens > 0
        ):
            return RequestTokens(
                status="running",
                model=latest_model,
                input_tokens=snapshot.estimate.input_tokens or None,
                output_tokens=snapshot.estimate.output_tokens
                + snapshot.estimate.tool_tokens,
                reasoning_tokens=None,
                total_tokens=snapshot.estimate.total_tokens,
                estimated=True,
                source="jsonl",
                updated_at=snapshot.estimate.updated_at or snapshot.last_event_time,
                started_at=snapshot.estimate.started_at or snapshot.last_event_time,
            )
        confirmed = snapshot.confirmed
        cost = self.cost_estimator.calculate(
            latest_model,
            confirmed.last_input,
            confirmed.last_cached,
            confirmed.last_output,
            confirmed.last_reasoning,
        )
        return RequestTokens(
            status="confirmed" if confirmed.last_total else "waiting",
            model=latest_model,
            input_tokens=confirmed.last_input or None,
            cached_tokens=confirmed.last_cached or None,
            output_tokens=confirmed.last_output,
            reasoning_tokens=confirmed.last_reasoning,
            total_tokens=confirmed.last_total,
            estimated=False,
            source="jsonl",
            updated_at=snapshot.task_aborted_at or confirmed.timestamp,
            completed_at=snapshot.task_aborted_at,
            cost_usd=cost,
        )

    def _history_after_task_abort(
        self,
        history: Sequence[RequestRound],
        task_aborted_at: datetime | None,
    ) -> list[RequestRound]:
        items = list(history)
        if task_aborted_at is None:
            return items
        while items and items[-1].status == "running":
            items.pop()
        return items

    def request_after_task_abort(
        self,
        snapshot: ParsedSession,
        request: RequestTokens,
        latest_model: str,
    ) -> RequestTokens:
        if snapshot.task_aborted_at is None or request.status != "running":
            return request
        next_request = self.fallback_request_tokens(snapshot, request.model or latest_model)
        next_request.source = request.source or next_request.source
        if next_request.started_at is None:
            next_request.started_at = snapshot.task_started_at or request.started_at
        if next_request.updated_at is None:
            next_request.updated_at = snapshot.task_aborted_at
        if next_request.completed_at is None:
            next_request.completed_at = snapshot.task_aborted_at
        return next_request

    def round_from_request(
        self,
        request: RequestTokens,
        snapshot: ParsedSession | None = None,
        fallback_model: str = "",
    ) -> RequestRound:
        model = request.model or fallback_model
        input_tokens = request.input_tokens
        cached_tokens = request.cached_tokens
        output_tokens = request.output_tokens
        total_tokens = request.total_tokens
        cost_usd = request.cost_usd

        if snapshot is not None and request.status == "running" and request.estimated:
            if input_tokens is None:
                input_tokens = (
                    snapshot.confirmed.last_input
                    + snapshot.estimate.input_tokens
                    + snapshot.estimate.tool_tokens
                ) or None
            if input_tokens is not None and (
                total_tokens is None or total_tokens <= int(output_tokens or 0)
            ):
                total_tokens = int(input_tokens) + int(output_tokens or 0)
            if cached_tokens is None:
                cached_tokens = (
                    min(int(snapshot.confirmed.last_cached or 0), int(input_tokens))
                    if input_tokens is not None
                    else None
                )
            if cost_usd is None:
                cost_usd = self.cost_estimator.calculate(
                    model,
                    input_tokens,
                    cached_tokens,
                    output_tokens or 0,
                    request.reasoning_tokens or 0,
                )

        return RequestRound(
            index=request.round_index,
            status=request.status,
            model=model,
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=request.reasoning_tokens,
            total_tokens=total_tokens,
            estimated=request.estimated,
            cost_usd=cost_usd,
            started_at=request.started_at,
            completed_at=request.completed_at,
        )

    def reindex_rounds(self, rounds: Sequence[RequestRound]) -> list[RequestRound]:
        items = list(rounds)
        for index, item in enumerate(items, 1):
            item.index = index
        return items


class SseRequestStateMachine:
    """Track live request usage from Codex SQLite OTel and SSE log rows."""

    def __init__(
        self,
        db_path: Path | None = None,
        estimate_enabled: bool = True,
        cost_estimator: CostEstimator | None = None,
    ) -> None:
        self.db_path = db_path
        self.estimate_enabled = estimate_enabled
        self.cost_estimator = cost_estimator or CostEstimator()
        self.session_id: str | None = None
        self.task_started_at: datetime | None = None
        self.active_start_id: int | None = None
        self.last_seen_id = 0
        self.round_index = 0
        self.history: list[RequestRound] = []
        self.pending_sse_events: list[str] = []
        self.current = RequestTokens(status="waiting", source="sse")

    def reset_session(
        self, session_id: str, task_started_at: datetime | None = None
    ) -> None:
        self.session_id = session_id
        self.task_started_at = task_started_at
        self.active_start_id = None
        self.last_seen_id = 0
        self.round_index = 0
        self.history = []
        self.pending_sse_events = []
        self.current = RequestTokens(status="waiting", source="sse")

    def reset_task(self, task_started_at: datetime | None) -> None:
        self.archive_current()
        self.task_started_at = task_started_at
        self.active_start_id = None
        self.last_seen_id = 0
        self.round_index = 0
        self.history = []
        self.pending_sse_events = []
        self.current = RequestTokens(status="waiting", source="sse")

    def build(
        self, session_id: str, task_started_at: datetime | None = None
    ) -> tuple[RequestTokens, list[RequestRound]]:
        """Read SQLite rows and return the current request plus recent rounds."""
        if session_id == "n/a":
            return RequestTokens(status="waiting", source="sse"), []
        if self.db_path is None:
            return self.current, self.rounds()
        if not self.db_path.exists():
            return (
                RequestTokens(
                    status="error",
                    source="sse",
                    error=f"SSE log database not found: {self.db_path}",
                ),
                [],
            )
        if self.session_id != session_id:
            self.reset_session(session_id, task_started_at)
        elif self.task_started_at != task_started_at:
            self.reset_task(task_started_at)

        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                con.row_factory = sqlite3.Row
                if self.active_start_id is None:
                    self.bootstrap(con, session_id, task_started_at)
                if self.active_start_id is None:
                    return RequestTokens(status="waiting", source="sse"), []
                self.consume_new_rows(con, session_id)
                return self.current, self.recent_rounds(con, session_id)
            finally:
                con.close()
        except sqlite3.Error as exc:
            return (
                RequestTokens(status="error", source="sse", error=f"SQLite: {exc}"),
                self.rounds(),
            )

    def bootstrap(
        self,
        con: sqlite3.Connection,
        session_id: str,
        task_started_at: datetime | None,
    ) -> None:
        if task_started_at is None:
            row = con.execute(
                """
                select id
                from logs
                where target='codex_otel.log_only'
                  and feedback_log_body like ?
                  and feedback_log_body like '%event.name="codex.sse_event"%'
                  and feedback_log_body like '%event.kind=response.created%'
                order by id desc
                limit 1
                """,
                (f"%conversation.id={session_id}%",),
            ).fetchone()
        else:
            row = con.execute(
                """
                select id
                from logs
                where target='codex_otel.log_only'
                  and ts >= ?
                  and feedback_log_body like ?
                  and feedback_log_body like '%event.name="codex.sse_event"%'
                  and feedback_log_body like '%event.kind=response.created%'
                order by id asc
                limit 1
                """,
                (
                    int(task_started_at.timestamp()) - 2,
                    f"%conversation.id={session_id}%",
                ),
            ).fetchone()
        if row is None:
            return
        self.active_start_id = int(row["id"])
        self.last_seen_id = max(0, self.active_start_id - 1)
        self.current = RequestTokens(status="waiting", source="sse")

    def consume_new_rows(self, con: sqlite3.Connection, session_id: str) -> None:
        rows = con.execute(
            """
            select id, ts, target, feedback_log_body
            from logs
            where id > ?
              and (target='codex_api::sse::responses' or target='codex_otel.log_only')
            order by id
            """,
            (self.last_seen_id,),
        ).fetchall()
        self.consume_log_rows(rows, session_id)

    def consume_log_rows(
        self, rows: Iterable[Mapping[str, Any]], session_id: str
    ) -> None:
        """Consume already fetched SQLite log rows; useful for tests and adapters."""
        if self.session_id != session_id:
            self.reset_session(session_id)
        for row in rows:
            row_id = _as_int(_row_value(row, "id"))
            target = str(_row_value(row, "target", "") or "")
            body = str(_row_value(row, "feedback_log_body", "") or "")
            if target == "codex_otel.log_only":
                self.consume_otel(row_id, body, session_id)
            elif target == "codex_api::sse::responses":
                self.pending_sse_events.append(body)
                self.pending_sse_events = self.pending_sse_events[-200:]
            self.last_seen_id = max(self.last_seen_id, row_id)

    def consume_otel(self, row_id: int, body: str, session_id: str) -> None:
        del row_id
        if 'event.name="codex.sse_event"' not in body:
            return
        kind = self.extract_text(body, r"\bevent\.kind=([^\s]+)")
        timestamp = self.extract_timestamp(body)
        model = extract_log_field(body, "slug") or extract_log_field(body, "model")
        is_current_session = f"conversation.id={session_id}" in body

        if kind in {
            "response.output_text.delta",
            "response.function_call_arguments.delta",
        }:
            pending = self.pop_pending_sse_event(kind)
            if is_current_session and pending:
                self.consume_sse(pending)
            return

        if not is_current_session:
            return

        if kind == "response.created":
            self.start_round(timestamp, model)
            return

        if kind == "response.in_progress" and self.current.status == "waiting":
            self.current.status = "running"
            self.current.updated_at = timestamp
            return

        if kind == "response.completed":
            exact = self.request_from_otel_counts(body, timestamp, model)
            if exact is not None:
                self.finish_round(exact)

    def pop_pending_sse_event(self, event_type: str) -> str | None:
        for index, body in enumerate(self.pending_sse_events):
            event = self.parse_sse_event(body)
            if event and event.get("type") == event_type:
                item = self.pending_sse_events.pop(index)
                if index:
                    del self.pending_sse_events[:index]
                return item
        return None

    def start_round(self, timestamp: datetime | None, model: str) -> None:
        self.archive_current()
        self.round_index += 1
        self.current = RequestTokens(
            status="running",
            round_index=self.round_index,
            model=model,
            output_tokens=0,
            total_tokens=0,
            estimated=True,
            source="sse",
            updated_at=timestamp,
            started_at=timestamp,
        )

    def finish_round(self, exact: RequestTokens) -> None:
        exact.round_index = self.current.round_index or self.round_index
        exact.model = exact.model or self.current.model
        exact.started_at = self.current.started_at
        exact.completed_at = exact.updated_at
        exact.cost_usd = self.calculate_request_cost(exact)
        self.current = exact

    def archive_current(self) -> None:
        if self.current.status == "waiting" or not self.current.round_index:
            return
        self.history.append(self.to_round(self.current))
        self.history = self.history[-MAX_REQUEST_HISTORY:]

    def rounds(self) -> list[RequestRound]:
        items = list(self.history)
        if self.current.status != "waiting" and self.current.round_index:
            items.append(self.to_round(self.current))
        return items[-MAX_REQUEST_HISTORY:]

    def recent_rounds(
        self, con: sqlite3.Connection, session_id: str
    ) -> list[RequestRound]:
        rows = con.execute(
            """
            select id, feedback_log_body
            from logs
            where target='codex_otel.log_only'
              and id >= ?
              and feedback_log_body like ?
              and feedback_log_body like 'event.name="codex.sse_event" event.kind=response.completed input_token_count=%'
            order by id
            """,
            (int(self.active_start_id or 0), f"%conversation.id={session_id}%"),
        ).fetchall()
        items: list[RequestRound] = []
        seen_usage_keys: set[tuple[Any, ...]] = set()
        for index, row in enumerate(rows, 1):
            body = str(row["feedback_log_body"] or "")
            model = extract_log_field(body, "slug") or extract_log_field(body, "model")
            input_tokens = self.extract_int(body, "input_token_count")
            output_tokens = self.extract_int(body, "output_token_count")
            cached_tokens = self.extract_int(body, "cached_token_count")
            reasoning_tokens = self.extract_int(body, "reasoning_token_count")
            timestamp = self.extract_timestamp(body)
            usage_key = (
                model,
                input_tokens,
                cached_tokens,
                output_tokens,
                reasoning_tokens,
                timestamp,
            )
            if usage_key in seen_usage_keys:
                continue
            seen_usage_keys.add(usage_key)
            items.append(
                RequestRound(
                    index=index,
                    status="confirmed",
                    model=model,
                    input_tokens=input_tokens,
                    cached_tokens=cached_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    total_tokens=(
                        input_tokens + output_tokens
                        if input_tokens is not None and output_tokens is not None
                        else None
                    ),
                    estimated=False,
                    cost_usd=self.cost_estimator.calculate(
                        model,
                        input_tokens,
                        cached_tokens,
                        output_tokens,
                        reasoning_tokens,
                    ),
                    completed_at=timestamp,
                )
            )
        if self.current.status == "running" and self.current.round_index:
            items.append(self.to_round(self.current))
        items = items[-MAX_REQUEST_HISTORY:]
        for index, item in enumerate(items, 1):
            item.index = index
        return items

    def to_round(self, request: RequestTokens) -> RequestRound:
        return RequestRound(
            index=request.round_index,
            status=request.status,
            model=request.model,
            input_tokens=request.input_tokens,
            cached_tokens=request.cached_tokens,
            output_tokens=request.output_tokens,
            reasoning_tokens=request.reasoning_tokens,
            total_tokens=request.total_tokens,
            estimated=request.estimated,
            cost_usd=request.cost_usd,
            started_at=request.started_at,
            completed_at=request.completed_at,
        )

    def consume_sse(self, body: str) -> None:
        event = self.parse_sse_event(body)
        if not event:
            return
        event_type = str(event.get("type") or "")
        if event_type in {
            "response.output_text.delta",
            "response.function_call_arguments.delta",
        }:
            if self.current.status != "running" or not self.current.round_index:
                return
            if not self.estimate_enabled:
                return
            delta = str(event.get("delta") or "")
            if not delta:
                return
            output = (self.current.output_tokens or 0) + estimate_tokens(delta)
            self.current.status = "running"
            self.current.output_tokens = output
            self.current.total_tokens = output
            self.current.estimated = True
            self.current.source = "sse"
            return

        if event_type == "response.completed":
            exact = self.request_from_sse_completed(event)
            if exact is not None:
                self.finish_round(exact)

    def request_from_otel_counts(
        self, body: str, timestamp: datetime | None, model: str
    ) -> RequestTokens | None:
        input_tokens = self.extract_int(body, "input_token_count")
        output_tokens = self.extract_int(body, "output_token_count")
        cached_tokens = self.extract_int(body, "cached_token_count")
        reasoning_tokens = self.extract_int(body, "reasoning_token_count")
        if input_tokens is None or output_tokens is None:
            return None
        return RequestTokens(
            status="confirmed",
            model=model,
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens or 0,
            total_tokens=input_tokens + output_tokens,
            estimated=False,
            source="sse",
            updated_at=timestamp,
        )

    def request_from_sse_completed(self, event: Mapping[str, Any]) -> RequestTokens | None:
        response = event.get("response") or {}
        if not isinstance(response, Mapping):
            return None
        usage = response.get("usage") or {}
        if not isinstance(usage, Mapping) or not usage:
            return None
        details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        if not isinstance(details, Mapping):
            details = {}
        if not isinstance(output_details, Mapping):
            output_details = {}
        input_tokens = _as_int(usage.get("input_tokens"))
        output_tokens = _as_int(usage.get("output_tokens"))
        total_tokens = _as_int(usage.get("total_tokens"), input_tokens + output_tokens)
        return RequestTokens(
            status="confirmed",
            model=str(response.get("model") or ""),
            input_tokens=input_tokens,
            cached_tokens=_as_int(details.get("cached_tokens")),
            output_tokens=output_tokens,
            reasoning_tokens=_as_int(output_details.get("reasoning_tokens")),
            total_tokens=total_tokens,
            estimated=False,
            source="sse",
            response_id=str(response.get("id") or ""),
        )

    def calculate_request_cost(self, request: RequestTokens) -> float | None:
        return self.cost_estimator.calculate(
            request.model,
            request.input_tokens,
            request.cached_tokens,
            request.output_tokens,
            request.reasoning_tokens or 0,
        )

    def parse_sse_event(self, body: str) -> dict[str, Any] | None:
        prefix = "SSE event:"
        if not body.startswith(prefix):
            return None
        try:
            event = json.loads(body[len(prefix) :].strip())
        except json.JSONDecodeError:
            return None
        return event if isinstance(event, dict) else None

    def extract_int(self, body: str, name: str) -> int | None:
        match = re.search(rf"\b{re.escape(name)}=(\d+)", body)
        return int(match.group(1)) if match else None

    def extract_text(self, body: str, pattern: str) -> str:
        match = re.search(pattern, body)
        return match.group(1) if match else ""

    def extract_timestamp(self, body: str) -> datetime | None:
        return parse_timestamp(self.extract_text(body, r"\bevent\.timestamp=([^\s]+)"))
