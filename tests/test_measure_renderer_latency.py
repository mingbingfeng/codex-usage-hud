"""Unit tests for the renderer latency baseline helper."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = PROJECT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import measure_renderer_latency  # noqa: E402


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
        self.assertIn("append_then_parse_and_payload", operation_names)

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
                    "name": "current_session_parse_full",
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
        self.assertIn("| current_session_parse_full | 1.200 | 1.300 | 1.400 | 1 |", text)
        self.assertIn("local only", text)


if __name__ == "__main__":
    unittest.main()
