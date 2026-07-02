"""Unit tests for the pre-send base estimator and reading-activity monitor."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.core.activity_monitor import (
    ReadingActivity,
    _extract_file_from_arguments,
    detect_reading_activity,
)
from codex_usage_hud.core.pre_send_estimator import BaseEstimate, PreSendEstimator


class BaseEstimateTests(unittest.TestCase):
    def test_short_label_cache_friendly(self) -> None:
        self.assertIn("Cache友好", BaseEstimate(total_tokens=12_500).short_label())

    def test_short_label_large_context(self) -> None:
        self.assertIn("大量上下文", BaseEstimate(total_tokens=150_000).short_label())

    def test_short_label_reports_error(self) -> None:
        self.assertIn("估价异常", BaseEstimate(error="boom").short_label())

    def test_with_session_history_replaces_history_term(self) -> None:
        base = BaseEstimate(
            total_tokens=981,
            input_text_tokens=6,
            session_history_tokens=0,
            context_files_tokens=920,
            mcp_schema_tokens=5,
            padding_tokens=50,
            encoding_used="tiktoken",
        )
        merged = base.with_session_history(48_000)
        self.assertEqual(merged.session_history_tokens, 48_000)
        # 6 + 920 + 5 + 50 + 48000
        self.assertEqual(merged.total_tokens, 48_981)
        self.assertEqual(merged.encoding_used, "tiktoken")

    def test_with_session_history_clamps_negative(self) -> None:
        merged = BaseEstimate(padding_tokens=50).with_session_history(-5)
        self.assertEqual(merged.session_history_tokens, 0)
        self.assertEqual(merged.total_tokens, 50)

    def test_breakdown_rows_cover_all_components(self) -> None:
        est = BaseEstimate(
            input_text_tokens=6,
            session_history_tokens=48000,
            context_files_tokens=920,
            mcp_schema_tokens=5,
            padding_tokens=50,
        )
        rows = est.breakdown_rows()
        self.assertEqual([r["key"] for r in rows], ["A", "B", "C", "D", "F"])
        by_key = {r["key"]: r["tokens"] for r in rows}
        self.assertEqual(by_key["A"], 6)
        self.assertEqual(by_key["B"], 48000)

    def test_breakdown_rows_override_live_input(self) -> None:
        est = BaseEstimate(input_text_tokens=6, padding_tokens=50)
        rows = est.breakdown_rows(live_input_tokens=123)
        by_key = {r["key"]: r["tokens"] for r in rows}
        self.assertEqual(by_key["A"], 123)


class PreSendEstimatorTests(unittest.TestCase):
    def test_latest_is_non_blocking_before_start(self) -> None:
        estimator = PreSendEstimator()
        self.assertEqual(estimator.latest().total_tokens, 0)

    def test_recompute_sums_all_components(self) -> None:
        estimator = PreSendEstimator(
            input_text_getter=lambda: "hello world",
            session_history_getter=lambda: "history text",
            mcp_schema_getter=lambda: "{}",
        )
        estimator._recompute()
        est = estimator.latest()
        self.assertGreater(est.total_tokens, 0)
        self.assertEqual(est.padding_tokens, 50)
        self.assertIn(est.encoding_used, {"tiktoken", "heuristic"})

    def test_background_thread_produces_estimate(self) -> None:
        estimator = PreSendEstimator(
            input_text_getter=lambda: "refactor ScanClient please",
            debounce_seconds=0.05,
        )
        estimator.start()
        try:
            estimator.invalidate()
            deadline = time.time() + 3.0
            while time.time() < deadline and estimator.latest().total_tokens == 0:
                time.sleep(0.05)
        finally:
            estimator.close()
        self.assertGreater(estimator.latest().total_tokens, 0)

    def test_set_project_roots_only_changes_on_difference(self) -> None:
        estimator = PreSendEstimator(project_roots=["a"])
        estimator._context_cache = "cached"
        estimator.set_project_roots(["a"])  # no change
        self.assertEqual(estimator._context_cache, "cached")
        estimator.set_project_roots(["b"])  # changed -> cache cleared
        self.assertIsNone(estimator._context_cache)

    def test_recompute_captures_getter_error(self) -> None:
        def boom() -> str:
            raise RuntimeError("getter failed")

        estimator = PreSendEstimator(input_text_getter=boom)
        estimator._recompute()
        self.assertIn("getter failed", estimator.latest().error)


class ExtractFileFromArgumentsTests(unittest.TestCase):
    def test_json_path_key(self) -> None:
        path, _ = _extract_file_from_arguments("read_file", '{"path": "src/ScanClient.cs"}')
        self.assertEqual(path, "src/ScanClient.cs")

    def test_shell_cat_command(self) -> None:
        path, _ = _extract_file_from_arguments("shell", '{"command": "cat src/ScanClient.cs"}')
        self.assertEqual(path, "src/ScanClient.cs")

    def test_command_as_list(self) -> None:
        path, _ = _extract_file_from_arguments(
            "shell", {"command": ["cat", "src/ScanClient.cs"]}
        )
        self.assertEqual(path, "src/ScanClient.cs")

    def test_empty_arguments(self) -> None:
        self.assertEqual(_extract_file_from_arguments("read_file", None), ("", ""))


class DetectReadingActivityTests(unittest.TestCase):
    def _snapshot(self, **kwargs: object) -> SimpleNamespace:
        base = dict(
            task_completed_at=None,
            task_aborted_at=None,
            activity=None,
        )
        base.update(kwargs)
        return SimpleNamespace(**base)

    def test_detects_read_file_tool(self) -> None:
        snap = self._snapshot(
            activity=SimpleNamespace(
                kind="tool call",
                detail='read_file {"path": "src/ScanClient.cs"}',
            )
        )
        activity = detect_reading_activity(snap)
        self.assertTrue(activity.active)
        self.assertEqual(activity.file_name, "ScanClient.cs")
        self.assertIn("ScanClient.cs", activity.warning_label())
        self.assertTrue(activity.warning_label().startswith("⚡"))

    def test_non_read_tool_is_inactive(self) -> None:
        snap = self._snapshot(
            activity=SimpleNamespace(
                kind="tool call",
                detail='apply_patch {"path": "x"}',
            )
        )
        self.assertFalse(detect_reading_activity(snap).active)

    def test_completed_task_turns_light_off(self) -> None:
        snap = self._snapshot(
            task_completed_at=object(),
            activity=SimpleNamespace(
                kind="tool call",
                detail='read_file {"path": "src/ScanClient.cs"}',
            ),
        )
        self.assertFalse(detect_reading_activity(snap).active)

    def test_aborted_task_turns_light_off(self) -> None:
        snap = self._snapshot(
            task_aborted_at=object(),
            activity=SimpleNamespace(
                kind="tool call",
                detail='read_file {"path": "src/ScanClient.cs"}',
            ),
        )
        self.assertFalse(detect_reading_activity(snap).active)

    def test_mcp_namespaced_read_tool(self) -> None:
        snap = self._snapshot(
            activity=SimpleNamespace(
                kind="tool call",
                detail='filesystem.read_file {"path": "a/b/Config.cs"}',
            )
        )
        activity = detect_reading_activity(snap)
        self.assertTrue(activity.active)
        self.assertEqual(activity.file_name, "Config.cs")

    def test_idle_activity_is_inactive(self) -> None:
        snap = self._snapshot(activity=SimpleNamespace(kind="idle", detail=""))
        self.assertFalse(detect_reading_activity(snap).active)

    def test_warning_label_empty_when_inactive(self) -> None:
        self.assertEqual(ReadingActivity(active=False).warning_label(), "")


if __name__ == "__main__":
    unittest.main()