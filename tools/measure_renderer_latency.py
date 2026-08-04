#!/usr/bin/env python3
"""Measure local renderer-mode latency baselines.

This harness intentionally avoids CDP and the live Codex renderer. It measures
the local work that currently dominates renderer refresh latency: JSONL parsing,
payload construction, budget aggregation, and filesystem fallback scans.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.cli import current_budget_windows  # noqa: E402
from codex_usage_hud.usage_cache import UsageSummaryCache  # noqa: E402
from codex_usage_hud.config import UserConfig  # noqa: E402
from codex_usage_hud.core.parser import JsonlSessionParser  # noqa: E402
from codex_usage_hud.platforms.file_watcher import (  # noqa: E402
    FileWatchSpec,
    _poll_signature,
)
from codex_usage_hud.renderer_payload_builder import payload_from_snapshot  # noqa: E402


DEFAULT_REGRESSION_BUDGETS_MS = {
    "current_session_parse_full": 50.0,
    "renderer_payload_build": 25.0,
    "usage_summary_full_scan": 250.0,
    "usage_summary_refresh_current_file": 25.0,
    "file_watcher_poll_signature": 250.0,
    "append_then_incremental_parse_and_payload": 250.0,
}


def _default_sessions_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def _jsonl_files(sessions_root: Path) -> list[Path]:
    roots = [sessions_root]
    if sessions_root.name == "sessions":
        roots.append(sessions_root.parent / "archived_sessions")
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            files.extend(path for path in root.rglob("*.jsonl") if path.is_file())
        except OSError:
            continue
    return files


def _newest_jsonl(sessions_root: Path) -> Path | None:
    files = _jsonl_files(sessions_root)
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def _record(timestamp: datetime, record_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "type": record_type,
        "payload": payload,
    }


def _token_count(timestamp: datetime, total: int) -> dict[str, Any]:
    input_tokens = max(1, total // 2)
    cached_tokens = max(0, input_tokens // 3)
    output_tokens = max(1, total // 3)
    reasoning_tokens = max(0, total - input_tokens - output_tokens)
    return _record(
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
                    "total_tokens": total,
                },
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning_tokens,
                    "total_tokens": total,
                },
            },
        },
    )


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")


def _write_synthetic_session(path: Path, *, rows: int = 240) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    start = datetime.now(timezone.utc) - timedelta(minutes=rows)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for index in range(rows):
            moment = start + timedelta(seconds=index * 15)
            if index % 6 == 0:
                payload = {"type": "task_started", "prompt": f"Synthetic task {index // 6 + 1}"}
                record = _record(moment, "event_msg", payload)
            elif index % 6 == 5:
                record = _token_count(moment, 800 + index * 37)
            elif index % 3 == 0:
                payload = {"type": "agent_message", "message": "Synthetic assistant output."}
                record = _record(moment, "event_msg", payload)
            else:
                payload = {"type": "user_message", "message": "Synthetic user prompt."}
                record = _record(moment, "event_msg", payload)
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return ordered[max(0, min(index, len(ordered) - 1))]


def _timed_runs(
    name: str,
    func: Callable[[], object],
    *,
    iterations: int,
    warmups: int,
) -> dict[str, object]:
    for _ in range(max(0, warmups)):
        func()
    samples: list[float] = []
    for _ in range(max(1, iterations)):
        started = time.perf_counter_ns()
        func()
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        samples.append(elapsed_ms)
    return {
        "name": name,
        "iterations": len(samples),
        "min_ms": min(samples),
        "median_ms": statistics.median(samples),
        "p90_ms": _percentile(samples, 0.90),
        "max_ms": max(samples),
        "mean_ms": statistics.fmean(samples),
    }


def _round_metrics(value: object) -> object:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {key: _round_metrics(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_metrics(item) for item in value]
    return value


def regression_budget_rows(
    report: dict[str, object],
    budgets_ms: dict[str, float] | None = None,
) -> list[dict[str, object]]:
    budgets = dict(DEFAULT_REGRESSION_BUDGETS_MS if budgets_ms is None else budgets_ms)
    rows: list[dict[str, object]] = []
    for item in report.get("operations", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name not in budgets:
            continue
        p90_ms = float(item.get("p90_ms") or 0.0)
        budget_ms = float(budgets[name])
        rows.append(
            {
                "name": name,
                "p90_ms": p90_ms,
                "budget_ms": budget_ms,
                "status": "PASS" if p90_ms <= budget_ms else "FAIL",
            }
        )
    return rows


def _append_measurement_session(source: Path, temp_root: Path) -> Path:
    sessions_root = temp_root / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)
    target = sessions_root / source.name
    target.write_bytes(source.read_bytes())
    return target


def measure_baseline(
    *,
    sessions_root: Path,
    session_file: Path | None,
    iterations: int,
    warmups: int,
) -> dict[str, object]:
    temp_dir = tempfile.TemporaryDirectory(prefix="codex-hud-latency-")
    temp_root = Path(temp_dir.name)
    try:
        selected = Path(session_file).expanduser() if session_file else _newest_jsonl(sessions_root)
        used_synthetic = False
        if selected is None:
            sessions_root = temp_root / "sessions"
            selected = sessions_root / "synthetic-session.jsonl"
            _write_synthetic_session(selected)
            used_synthetic = True
        else:
            selected = selected.resolve(strict=False)
            sessions_root = sessions_root.resolve(strict=False)

        parse_temp_root = temp_root / "parse"
        parse_path = _append_measurement_session(selected, parse_temp_root)

        parser = JsonlSessionParser()
        config = UserConfig()
        day_start, week_start = current_budget_windows(config)
        snapshot = parser.parse_file(parse_path)
        payload = payload_from_snapshot(snapshot).to_json()

        cache = UsageSummaryCache(parser, min_rescan_seconds=0)
        cache.summarize(sessions_root, day_start, week_start, force_rescan=True)
        poll_specs = [
            FileWatchSpec.tree(sessions_root, "sessions-root", suffixes=(".jsonl",)),
            FileWatchSpec.file(selected, "session"),
        ]

        append_temp_root = temp_root / "append"
        append_path = _append_measurement_session(selected, append_temp_root)
        append_sessions_root = append_path.parent

        append_counter = {"value": 0}
        _append_snapshot, append_tail_state = parser.parse_file_incremental(append_path)

        def append_parse_payload() -> int:
            nonlocal append_tail_state
            append_counter["value"] += 1
            _append_jsonl(
                append_path,
                _token_count(datetime.now(timezone.utc), 10_000 + append_counter["value"]),
            )
            append_snapshot, append_tail_state = parser.parse_file_incremental(
                append_path,
                append_tail_state,
            )
            return len(payload_from_snapshot(append_snapshot).to_json())

        append_cache = UsageSummaryCache(parser, min_rescan_seconds=0)
        append_day, append_week = current_budget_windows(config)
        append_cache.summarize(append_sessions_root, append_day, append_week, force_rescan=True)

        operations = [
            _timed_runs(
                "current_session_parse_full",
                lambda: parser.parse_file(parse_path),
                iterations=iterations,
                warmups=warmups,
            ),
            _timed_runs(
                "renderer_payload_build",
                lambda: payload_from_snapshot(snapshot).to_json(),
                iterations=iterations,
                warmups=warmups,
            ),
            _timed_runs(
                "usage_summary_full_scan",
                lambda: cache.summarize(
                    sessions_root,
                    day_start,
                    week_start,
                    force_rescan=True,
                ),
                iterations=iterations,
                warmups=warmups,
            ),
            _timed_runs(
                "usage_summary_refresh_current_file",
                lambda: append_cache.summarize(
                    append_sessions_root,
                    append_day,
                    append_week,
                    allow_stale=True,
                    refresh_paths=(append_path,),
                ),
                iterations=iterations,
                warmups=warmups,
            ),
            _timed_runs(
                "file_watcher_poll_signature",
                lambda: _poll_signature(tuple(poll_specs)),
                iterations=iterations,
                warmups=warmups,
            ),
            _timed_runs(
                "append_then_incremental_parse_and_payload",
                append_parse_payload,
                iterations=iterations,
                warmups=warmups,
            ),
        ]

        try:
            selected_stat = selected.stat()
            selected_size = selected_stat.st_size
        except OSError:
            selected_size = 0
        return {
            "schema": "codex-usage-hud.renderer-latency-baseline.v1",
            "measured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "project_root": str(PROJECT_ROOT),
            "sessions_root": str(sessions_root),
            "session_file": str(selected),
            "used_synthetic_session": used_synthetic,
            "session_bytes": selected_size,
            "session_lines": int(getattr(snapshot, "line_count", 0) or 0),
            "payload_keys": len(payload),
            "operations": operations,
            "regression_budgets_ms": dict(DEFAULT_REGRESSION_BUDGETS_MS),
            "regression_budget_results": regression_budget_rows({"operations": operations}),
            "notes": [
                "This local harness does not measure live CDP transport, renderer DOM paint, or user-visible end-to-end latency.",
                "current_session_parse_full and usage_summary_refresh_current_file run against temporary copies of the selected session file to avoid concurrent writes from the live Codex session skewing local parser timings.",
                "append_then_incremental_parse_and_payload writes only to a temporary copy of the selected session file.",
                "file_watcher_poll_signature represents the polling fallback scan cost, not native watcher delivery latency.",
            ],
        }
    finally:
        temp_dir.cleanup()


def format_markdown(report: dict[str, object]) -> str:
    lines = [
        "# Renderer Latency Baseline",
        "",
        f"- Measured at: `{report.get('measured_at')}`",
        f"- Sessions root: `{report.get('sessions_root')}`",
        f"- Session file: `{report.get('session_file')}`",
        f"- Session size: `{report.get('session_bytes')}` bytes, `{report.get('session_lines')}` lines",
        f"- Synthetic session: `{report.get('used_synthetic_session')}`",
        "",
        "| Operation | Median ms | P90 ms | Max ms | Iterations |",
        "|-----------|-----------|--------|--------|------------|",
    ]
    for item in report.get("operations", []):
        if not isinstance(item, dict):
            continue
        lines.append(
            "| {name} | {median:.3f} | {p90:.3f} | {max_ms:.3f} | {iterations} |".format(
                name=item.get("name", ""),
                median=float(item.get("median_ms") or 0.0),
                p90=float(item.get("p90_ms") or 0.0),
                max_ms=float(item.get("max_ms") or 0.0),
                iterations=item.get("iterations", ""),
            )
        )
    budget_rows = regression_budget_rows(report)
    if budget_rows:
        lines.extend(
            [
                "",
                "## Regression Budgets",
                "",
                "| Operation | P90 ms | Budget ms | Status |",
                "|-----------|--------|-----------|--------|",
            ]
        )
        for item in budget_rows:
            lines.append(
                "| {name} | {p90:.3f} | {budget:.3f} | {status} |".format(
                    name=item["name"],
                    p90=float(item["p90_ms"]),
                    budget=float(item["budget_ms"]),
                    status=item["status"],
                )
            )
    notes = report.get("notes") or []
    if notes:
        lines.extend(["", "## Notes", ""])
        for note in notes:
            lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions-root",
        type=Path,
        default=_default_sessions_root(),
        help="Codex sessions directory. Defaults to ~/.codex/sessions.",
    )
    parser.add_argument(
        "--session-file",
        type=Path,
        default=None,
        help="Specific JSONL session file to measure. Defaults to newest under sessions-root.",
    )
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = measure_baseline(
        sessions_root=args.sessions_root.expanduser(),
        session_file=args.session_file,
        iterations=max(1, args.iterations),
        warmups=max(0, args.warmups),
    )
    rounded = _round_metrics(report)
    text = json.dumps(rounded, ensure_ascii=False, indent=2)
    print(text)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        with args.json_output.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.write("\n")
    if args.markdown_output is not None:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        with args.markdown_output.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(format_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
