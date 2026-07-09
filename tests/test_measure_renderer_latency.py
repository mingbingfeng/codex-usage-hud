"""Unit tests for the renderer latency baseline helper."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = PROJECT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import measure_renderer_latency  # noqa: E402


def _fake_usage_summary() -> SimpleNamespace:
    return SimpleNamespace(
        tokens=0,
        input_tokens=0,
        cached_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_usd=0.0,
    )


class RendererLatencyToolTests(unittest.TestCase):
    def test_measure_baseline_uses_synthetic_session_when_no_sessions_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = measure_renderer_latency.measure_baseline(
                sessions_root=Path(temp_dir) / "missing-sessions",
                session_file=None,
                iterations=1,
                warmups=0,
            )

        self.assertEqual(report["schema"], "codex-usage-hud.renderer-latency-baseline.v1")
        self.assertTrue(report["used_synthetic_session"])
        operation_names = {
            str(item["name"])
            for item in report["operations"]
            if isinstance(item, dict)
        }
        self.assertIn("current_session_parse_full", operation_names)
        self.assertIn("append_then_incremental_parse_and_payload", operation_names)

    def test_format_markdown_includes_operation_table(self) -> None:
        report = {
            "measured_at": "2026-07-03T00:00:00Z",
            "sessions_root": "sessions",
            "session_file": "sessions/current.jsonl",
            "session_bytes": 10,
            "session_lines": 1,
            "used_synthetic_session": False,
            "operations": [
                {
                    "name": "append_then_incremental_parse_and_payload",
                    "iterations": 1,
                    "median_ms": 1.2,
                    "p90_ms": 1.3,
                    "max_ms": 1.4,
                }
            ],
            "notes": ["local only"],
        }

        text = measure_renderer_latency.format_markdown(report)

        self.assertIn("# Renderer Latency Baseline", text)
        self.assertIn(
            "| append_then_incremental_parse_and_payload | 1.200 | 1.300 | 1.400 | 1 |",
            text,
        )
        self.assertIn("local only", text)

    def test_format_markdown_includes_regression_budget_table(self) -> None:
        report = {
            "measured_at": "2026-07-03T00:00:00Z",
            "sessions_root": "sessions",
            "session_file": "sessions/current.jsonl",
            "session_bytes": 10,
            "session_lines": 1,
            "used_synthetic_session": False,
            "operations": [
                {
                    "name": "renderer_payload_build",
                    "iterations": 1,
                    "median_ms": 2.0,
                    "p90_ms": 3.0,
                    "max_ms": 4.0,
                },
                {
                    "name": "append_then_incremental_parse_and_payload",
                    "iterations": 1,
                    "median_ms": 300.0,
                    "p90_ms": 320.0,
                    "max_ms": 350.0,
                },
            ],
            "notes": [],
        }

        text = measure_renderer_latency.format_markdown(report)

        self.assertIn("## Regression Budgets", text)
        self.assertIn("| renderer_payload_build | 3.000 | 25.000 | PASS |", text)
        self.assertIn(
            "| append_then_incremental_parse_and_payload | 320.000 | 250.000 | FAIL |",
            text,
        )

    def test_main_writes_lf_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            json_output = Path(temp_dir) / "baseline.json"
            markdown_output = Path(temp_dir) / "baseline.md"

            exit_code = measure_renderer_latency.main(
                [
                    "--sessions-root",
                    str(Path(temp_dir) / "missing-sessions"),
                    "--iterations",
                    "1",
                    "--warmups",
                    "0",
                    "--json-output",
                    str(json_output),
                    "--markdown-output",
                    str(markdown_output),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertNotIn(b"\r\n", json_output.read_bytes())
            self.assertNotIn(b"\r\n", markdown_output.read_bytes())

    def test_measure_baseline_uses_temp_copy_for_full_parse_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_root = Path(temp_dir) / "sessions"
            sessions_root.mkdir()
            session_file = Path(temp_dir) / "source-session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            parse_paths: list[Path] = []
            cache_calls: list[tuple[str, Path, dict[str, object]]] = []
            cache_index = {"value": 0}

            class FakeParser:
                def parse_file(self, path):
                    parse_paths.append(Path(path))
                    return SimpleNamespace(line_count=1)

                def parse_file_incremental(self, path, state=None, **kwargs):
                    del kwargs
                    return SimpleNamespace(line_count=1), object()

            class FakeCache:
                def __init__(self, parser, min_rescan_seconds=0):
                    del parser, min_rescan_seconds
                    self.label = f"cache-{cache_index['value']}"
                    cache_index["value"] += 1

                def summarize(self, root, day_start, week_start, **kwargs):
                    del day_start, week_start
                    cache_calls.append((self.label, Path(root), dict(kwargs)))
                    return (
                        _fake_usage_summary(),
                        _fake_usage_summary(),
                    )

            def fake_timed_runs(name, func, *, iterations, warmups):
                del iterations, warmups
                func()
                return {"name": name, "iterations": 1, "median_ms": 0.0, "p90_ms": 0.0, "max_ms": 0.0}

            with (
                patch.object(measure_renderer_latency, "JsonlSessionParser", return_value=FakeParser()),
                patch.object(measure_renderer_latency, "UsageSummaryCache", FakeCache),
                patch.object(
                    measure_renderer_latency,
                    "payload_from_snapshot",
                    return_value=SimpleNamespace(to_json=lambda: {"topLine": "ok"}),
                ),
                patch.object(measure_renderer_latency, "_poll_signature", return_value={}),
                patch.object(
                    measure_renderer_latency,
                    "_timed_runs",
                    side_effect=fake_timed_runs,
                ),
            ):
                measure_renderer_latency.measure_baseline(
                    sessions_root=sessions_root,
                    session_file=session_file,
                    iterations=1,
                    warmups=0,
                )

        self.assertGreaterEqual(len(parse_paths), 2)
        self.assertNotEqual(parse_paths[0], session_file)
        self.assertNotEqual(parse_paths[1], session_file)

    def test_measure_baseline_uses_allow_stale_for_current_file_refresh_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            sessions_root = Path(temp_dir) / "sessions"
            sessions_root.mkdir()
            session_file = sessions_root / "session.jsonl"
            session_file.write_text("{}\n", encoding="utf-8")
            cache_calls: list[tuple[str, Path, dict[str, object]]] = []
            cache_index = {"value": 0}

            class FakeParser:
                def parse_file(self, path):
                    del path
                    return SimpleNamespace(line_count=1)

                def parse_file_incremental(self, path, state=None, **kwargs):
                    del path, state, kwargs
                    return SimpleNamespace(line_count=1), object()

            class FakeCache:
                def __init__(self, parser, min_rescan_seconds=0):
                    del parser, min_rescan_seconds
                    self.label = f"cache-{cache_index['value']}"
                    cache_index["value"] += 1

                def summarize(self, root, day_start, week_start, **kwargs):
                    del day_start, week_start
                    cache_calls.append((self.label, Path(root), dict(kwargs)))
                    return (
                        _fake_usage_summary(),
                        _fake_usage_summary(),
                    )

            def fake_timed_runs(name, func, *, iterations, warmups):
                del iterations, warmups
                func()
                return {"name": name, "iterations": 1, "median_ms": 0.0, "p90_ms": 0.0, "max_ms": 0.0}

            with (
                patch.object(measure_renderer_latency, "JsonlSessionParser", return_value=FakeParser()),
                patch.object(measure_renderer_latency, "UsageSummaryCache", FakeCache),
                patch.object(
                    measure_renderer_latency,
                    "payload_from_snapshot",
                    return_value=SimpleNamespace(to_json=lambda: {"topLine": "ok"}),
                ),
                patch.object(measure_renderer_latency, "_poll_signature", return_value={}),
                patch.object(
                    measure_renderer_latency,
                    "_timed_runs",
                    side_effect=fake_timed_runs,
                ),
            ):
                measure_renderer_latency.measure_baseline(
                    sessions_root=sessions_root,
                    session_file=session_file,
                    iterations=1,
                    warmups=0,
                )

        refresh_calls = [
            kwargs
            for label, root, kwargs in cache_calls
            if label == "cache-1" and "refresh_paths" in kwargs
        ]
        self.assertTrue(refresh_calls)
        self.assertTrue(refresh_calls[-1]["allow_stale"])


if __name__ == "__main__":
    unittest.main()
