"""Unit tests for the mixed live-acceptance runner."""

from __future__ import annotations

import json
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

import run_live_acceptance  # noqa: E402


class LiveAcceptanceFormattingTests(unittest.TestCase):
    def test_format_markdown_includes_gate_results_and_manual_checks(self) -> None:
        report = {
            "generated_at": "2026-07-09T00:00:00Z",
            "prepare_mode": "debug",
            "runtime_paths": {
                "settings": "C:/tmp/hud_settings.json",
                "daemon_log": "C:/tmp/daemon.log",
                "renderer_diagnostic": "C:/tmp/renderer_fallback.log",
            },
            "automated_checks": [
                {
                    "name": "phase_gate_pytest",
                    "status": "PASS",
                    "command": "python -m pytest tests/test_renderer_hud.py -q",
                    "log_path": "C:/tmp/pytest.log",
                },
                {
                    "name": "latency_harness",
                    "status": "PASS",
                    "summary": "All regression budgets passed.",
                    "artifact": "C:/tmp/renderer_latency_baseline.md",
                },
            ],
            "manual_checks": [
                {
                    "id": "active_session_latency",
                    "title": "Active Session Switch Latency",
                    "status": "fail",
                    "observed_ms": 2500,
                    "note": "Visible HUD switch lagged behind by 2-3 seconds.",
                    "instructions": [
                        "Open two Codex threads.",
                        "Record the thread switch and HUD update.",
                    ],
                }
            ],
        }

        text = run_live_acceptance.format_markdown(report)

        self.assertIn("# Live Acceptance Report", text)
        self.assertIn("`debug`", text)
        self.assertIn("phase_gate_pytest", text)
        self.assertIn("All regression budgets passed.", text)
        self.assertIn("Active Session Switch Latency", text)
        self.assertIn("Open two Codex threads.", text)
        self.assertIn("2500", text)
        self.assertIn("2-3 seconds", text)


class LiveAcceptanceMainTests(unittest.TestCase):
    def test_main_writes_json_and_markdown_reports(self) -> None:
        report = {
            "generated_at": "2026-07-09T00:00:00Z",
            "prepare_mode": "none",
            "runtime_paths": {},
            "automated_checks": [],
            "manual_checks": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "acceptance"
            json_output = output_dir / "report.json"
            markdown_output = output_dir / "report.md"
            with patch.object(run_live_acceptance, "run_acceptance", return_value=report):
                exit_code = run_live_acceptance.main(
                    [
                        "--output-dir",
                        str(output_dir),
                        "--json-output",
                        str(json_output),
                        "--markdown-output",
                        str(markdown_output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(json_output.read_text(encoding="utf-8")), report)
            self.assertIn("# Live Acceptance Report", markdown_output.read_text(encoding="utf-8"))

    def test_run_acceptance_uses_debug_prepare_mode_for_hud_bootstrap(self) -> None:
        start_calls: list[str] = []
        stop_calls: list[str] = []

        def fake_run_command(name: str, command: list[str], **kwargs):
            del command, kwargs
            if name == "stop_hud":
                stop_calls.append(name)
            return {
                "name": name,
                "status": "PASS",
                "exit_code": 0,
                "command": name,
            }

        def fake_start_hud(*, prepare_mode: str, **kwargs):
            del kwargs
            start_calls.append(prepare_mode)
            return {
                "name": "start_hud",
                "status": "PASS",
                "prepare_mode": prepare_mode,
                "pid": 1234,
            }

        with (
            patch.object(run_live_acceptance, "run_command", side_effect=fake_run_command),
            patch.object(run_live_acceptance, "run_latency_harness", return_value={"name": "latency_harness", "status": "PASS"}),
            patch.object(run_live_acceptance, "start_hud_prepare", side_effect=fake_start_hud),
            patch.object(run_live_acceptance, "runtime_paths", return_value={"daemon_log": "C:/tmp/daemon.log"}),
        ):
            report = run_live_acceptance.run_acceptance(
                prepare_mode="debug",
                output_dir=PROJECT_ROOT / "tmp-live-acceptance",
                run_automated_checks=False,
            )

        self.assertEqual(start_calls, ["debug"])
        self.assertEqual(stop_calls, ["stop_hud"])
        self.assertEqual(report["prepare_mode"], "debug")
        self.assertTrue(report["manual_checks"])

    def test_run_acceptance_applies_manual_observations(self) -> None:
        observations = {
            "active_session_latency": {
                "status": "fail",
                "observed_ms": 2500,
                "note": "Visible switch took about 2-3 seconds.",
            }
        }

        with (
            patch.object(run_live_acceptance, "run_command", return_value={"name": "noop", "status": "PASS"}),
            patch.object(run_live_acceptance, "run_latency_harness", return_value={"name": "latency_harness", "status": "PASS"}),
            patch.object(run_live_acceptance, "runtime_paths", return_value={}),
        ):
            report = run_live_acceptance.run_acceptance(
                prepare_mode="debug",
                output_dir=PROJECT_ROOT / "tmp-live-acceptance",
                run_automated_checks=False,
                manual_observations=observations,
            )

        check = next(item for item in report["manual_checks"] if item["id"] == "active_session_latency")
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["observed_ms"], 2500)
        self.assertIn("2-3 seconds", check["note"])

    def test_run_acceptance_appends_idle_cpu_sample_when_requested(self) -> None:
        with (
            patch.object(run_live_acceptance, "run_command", return_value={"name": "stop_hud", "status": "PASS"}),
            patch.object(run_live_acceptance, "run_latency_harness", return_value={"name": "latency_harness", "status": "PASS"}),
            patch.object(
                run_live_acceptance,
                "start_hud_prepare",
                return_value={"name": "start_hud", "status": "PASS", "prepare_mode": "debug", "pid": 4321},
            ),
            patch.object(run_live_acceptance, "_sample_hud_cpu_percent", return_value=0.4),
            patch.object(run_live_acceptance, "runtime_paths", return_value={}),
        ):
            report = run_live_acceptance.run_acceptance(
                prepare_mode="debug",
                output_dir=PROJECT_ROOT / "tmp-live-acceptance",
                run_automated_checks=False,
                idle_cpu_sample_seconds=60.0,
            )

        sample = next(item for item in report["automated_checks"] if item["name"] == "idle_cpu_sample")
        self.assertEqual(sample["status"], "PASS")
        self.assertEqual(sample["sample_seconds"], 60.0)
        self.assertEqual(sample["cpu_percent"], 0.4)


if __name__ == "__main__":
    unittest.main()
