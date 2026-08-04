"""Unit tests for the mixed live-acceptance runner."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = PROJECT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_live_acceptance  # noqa: E402


def _git(project_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return completed.stdout.strip()


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

    def test_format_markdown_includes_provenance_and_incomplete_p8_gate(self) -> None:
        report = {
            "schema": run_live_acceptance.REPORT_SCHEMA,
            "generated_at": "2026-07-31T00:00:00Z",
            "prepare_mode": "none",
            "output_dir": "C:/tmp/acceptance",
            "runtime_paths": {},
            "automated_checks": [],
            "manual_checks": [],
            "provenance": {
                "status": "PASS",
                "capture_point": "pre-run",
                "head_sha": "a" * 40,
                "branch": "main",
                "worktree_dirty": True,
                "capture_stable": True,
                "worktree_fingerprint_sha256": "b" * 64,
                "tracked_diff": {
                    "artifact": "C:/tmp/acceptance/git_tracked_worktree.patch",
                    "sha256": "c" * 64,
                },
                "untracked_files": {
                    "count": 2,
                    "artifact": "C:/tmp/acceptance/git_untracked_manifest.json",
                },
                "errors": [],
            },
            "p8_acceptance": run_live_acceptance.build_p8_acceptance([], {}),
        }

        text = run_live_acceptance.format_markdown(report)

        self.assertIn("## Source Provenance", text)
        self.assertIn("a" * 40, text)
        self.assertIn("## P8 Final Acceptance", text)
        self.assertIn("windows_renderer_startup", text)
        self.assertIn("Required evidence has not been supplied.", text)
        self.assertIn("gate status: `FAIL`", text)


class GitProvenanceTests(unittest.TestCase):
    def test_collect_git_provenance_hashes_head_tracked_and_untracked_content(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "repo"
            project_root.mkdir()
            _git(project_root, "init")
            tracked = project_root / "tracked.txt"
            tracked.write_text("baseline\n", encoding="utf-8")
            _git(project_root, "add", "tracked.txt")
            _git(
                project_root,
                "-c",
                "user.name=Codex Test",
                "-c",
                "user.email=codex@example.invalid",
                "commit",
                "-m",
                "baseline",
            )
            head_sha = _git(project_root, "rev-parse", "HEAD")

            tracked.write_text("changed\n", encoding="utf-8")
            untracked = project_root / "new file.txt"
            untracked.write_text("untracked payload\n", encoding="utf-8")
            output_dir = project_root / "artifacts" / "acceptance"
            output_dir.mkdir(parents=True)

            provenance = run_live_acceptance.collect_git_provenance(
                output_dir,
                project_root=project_root,
            )

            self.assertEqual(provenance["status"], "PASS")
            self.assertEqual(provenance["head_sha"], head_sha)
            self.assertTrue(provenance["worktree_dirty"])
            self.assertTrue(provenance["capture_stable"])
            self.assertEqual(len(provenance["worktree_fingerprint_sha256"]), 64)

            patch_path = Path(provenance["tracked_diff"]["artifact"])
            patch_payload = patch_path.read_bytes()
            self.assertIn(b"changed", patch_payload)
            self.assertEqual(
                provenance["tracked_diff"]["sha256"],
                hashlib.sha256(patch_payload).hexdigest(),
            )

            records = provenance["untracked_files"]["files"]
            record = next(item for item in records if item["path"] == "new file.txt")
            self.assertEqual(record["status"], "PASS")
            self.assertEqual(
                record["sha256"],
                hashlib.sha256(untracked.read_bytes()).hexdigest(),
            )
            self.assertTrue(Path(provenance["worktree_status"]["artifact"]).exists())
            self.assertTrue(Path(provenance["untracked_files"]["artifact"]).exists())


class P8AcceptanceSchemaTests(unittest.TestCase):
    def test_missing_p8_evidence_is_pending_and_fails_final_gate(self) -> None:
        result = run_live_acceptance.build_p8_acceptance([], {})

        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["complete"])
        self.assertGreater(result["counts"]["PENDING"], 0)
        self.assertEqual(result["counts"]["FAIL"], 0)
        self.assertIn("windows_renderer_startup", result["incomplete_required_ids"])
        startup = next(
            item
            for item in result["interaction_smoke"]["checks"]
            if item["id"] == "windows_renderer_startup"
        )
        self.assertEqual(startup["status"], "PENDING")
        self.assertEqual(startup["source"], "missing")

    def test_reported_pass_without_evidence_is_explicit_failure(self) -> None:
        result = run_live_acceptance.build_p8_acceptance(
            [],
            {
                "windows_installed_wheel_smoke": {
                    "status": "pass",
                    "note": "Ran locally.",
                }
            },
        )

        wheel = next(
            item
            for item in result["package_smoke"]["checks"]
            if item["id"] == "windows_installed_wheel_smoke"
        )
        self.assertEqual(wheel["status"], "FAIL")
        self.assertIn("without auditable evidence", wheel["status_reason"])

    def test_all_required_evidence_can_complete_the_p8_gate(self) -> None:
        requirements = (
            run_live_acceptance.P8_AUTOMATED_REQUIREMENTS
            + run_live_acceptance.P8_INTERACTION_REQUIREMENTS
            + run_live_acceptance.P8_PACKAGE_REQUIREMENTS
        )
        observations = {
            item[0]: {
                "status": "pass",
                "evidence": [{"artifact": f"artifacts/{item[0]}.json"}],
                **(
                    {"evidence_scope": "real-codex-app"}
                    if item in run_live_acceptance.P8_INTERACTION_REQUIREMENTS
                    else {}
                ),
            }
            for item in requirements
        }

        result = run_live_acceptance.build_p8_acceptance([], observations)

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["complete"])
        self.assertEqual(result["counts"]["PENDING"], 0)
        self.assertEqual(result["counts"]["FAIL"], 0)
        self.assertEqual(result["incomplete_required_ids"], [])

    def test_latency_harness_result_populates_matching_p8_evidence(self) -> None:
        result = run_live_acceptance.build_p8_acceptance(
            [
                {
                    "name": "latency_harness",
                    "status": "PASS",
                    "artifact": "C:/tmp/latency.md",
                    "budget_results": [{"name": "refresh", "status": "PASS"}],
                }
            ],
            {},
        )

        latency = next(
            item
            for item in result["automated_evidence"]["checks"]
            if item["id"] == "latency_regression_budgets"
        )
        self.assertEqual(latency["status"], "PASS")
        self.assertEqual(latency["source"], "automatic")
        self.assertTrue(latency["evidence"])

    def test_real_app_interaction_pass_requires_real_app_scope(self) -> None:
        result = run_live_acceptance.build_p8_acceptance(
            [],
            {
                "windows_renderer_startup": {
                    "status": "pass",
                    "evidence": [{"artifact": "startup.png"}],
                }
            },
        )

        startup = next(
            item
            for item in result["interaction_smoke"]["checks"]
            if item["id"] == "windows_renderer_startup"
        )
        self.assertEqual(startup["status"], "FAIL")
        self.assertIn("evidence_scope='real-codex-app'", startup["status_reason"])

    def test_daemon_and_chromium_scopes_cannot_pass_real_app_gate(self) -> None:
        for scope in ("hud-daemon-process-only", "chromium-smoke-host"):
            with self.subTest(scope=scope):
                result = run_live_acceptance.build_p8_acceptance(
                    [],
                    {
                        "windows_active_session": {
                            "status": "pass",
                            "evidence_scope": scope,
                            "evidence": [{"artifact": "evidence.json"}],
                        }
                    },
                )
                active = next(
                    item
                    for item in result["interaction_smoke"]["checks"]
                    if item["id"] == "windows_active_session"
                )
                self.assertEqual(active["status"], "FAIL")
                self.assertIn("cannot prove", active["status_reason"])

    def test_daemon_measurement_scope_cannot_be_hidden_by_real_app_scope(self) -> None:
        result = run_live_acceptance.build_p8_acceptance(
            [],
            {
                "windows_idle_cpu": {
                    "status": "pass",
                    "evidence_scope": "real-codex-app",
                    "measurement_scope": "hud-daemon-process-only",
                    "cpu_percent": 0.2,
                    "sample_seconds": 60,
                    "evidence": [{"artifact": "idle.json"}],
                }
            },
        )

        idle = next(
            item
            for item in result["interaction_smoke"]["checks"]
            if item["id"] == "windows_idle_cpu"
        )
        self.assertEqual(idle["status"], "FAIL")
        self.assertIn("daemon-only", idle["status_reason"])

    def test_explicit_p8_ineligible_idle_evidence_cannot_pass(self) -> None:
        result = run_live_acceptance.build_p8_acceptance(
            [],
            {
                "windows_idle_cpu": {
                    "status": "pass",
                    "evidence_scope": "real-codex-app",
                    "measurement_scope": "real-codex-app-process-and-renderer-cdp",
                    "p8_eligible": False,
                    "evidence": [
                        {
                            "artifact": "real-codex-app-idle.json",
                            "status": "INVALIDATED",
                        }
                    ],
                }
            },
        )

        idle = next(
            item
            for item in result["interaction_smoke"]["checks"]
            if item["id"] == "windows_idle_cpu"
        )
        self.assertEqual(idle["status"], "FAIL")
        self.assertIn("p8_eligible=false", idle["status_reason"])
        self.assertFalse(idle["p8_eligible"])

    def test_invalidated_nested_evidence_cannot_pass(self) -> None:
        result = run_live_acceptance.build_p8_acceptance(
            [],
            {
                "windows_idle_cpu": {
                    "status": "pass",
                    "evidence_scope": "real-codex-app",
                    "measurement_scope": "real-codex-app-process-and-renderer-cdp",
                    "evidence": [
                        {
                            "artifact": "real-codex-app-idle.json",
                            "status": "INVALIDATED",
                            "p8_eligible": True,
                        }
                    ],
                }
            },
        )

        idle = next(
            item
            for item in result["interaction_smoke"]["checks"]
            if item["id"] == "windows_idle_cpu"
        )
        self.assertEqual(idle["status"], "FAIL")
        self.assertIn("invalidated", idle["status_reason"])

    def test_validate_report_schema_rejects_ineligible_pass_marker(self) -> None:
        requirements = (
            run_live_acceptance.P8_AUTOMATED_REQUIREMENTS
            + run_live_acceptance.P8_INTERACTION_REQUIREMENTS
            + run_live_acceptance.P8_PACKAGE_REQUIREMENTS
        )
        observations = {
            item[0]: {
                "status": "pass",
                "evidence": [{"artifact": f"artifacts/{item[0]}.json"}],
                **(
                    {"evidence_scope": "real-codex-app"}
                    if item in run_live_acceptance.P8_INTERACTION_REQUIREMENTS
                    else {}
                ),
            }
            for item in requirements
        }
        p8 = run_live_acceptance.build_p8_acceptance([], observations)
        idle = next(
            item
            for item in p8["interaction_smoke"]["checks"]
            if item["id"] == "windows_idle_cpu"
        )
        idle["p8_eligible"] = False

        report = {
            "schema": run_live_acceptance.REPORT_SCHEMA,
            "schema_extensions": list(run_live_acceptance.REPORT_SCHEMA_EXTENSIONS),
            "generated_at": "2026-08-03T00:00:00Z",
            "project_root": "C:/tmp/project",
            "output_dir": "C:/tmp/output",
            "prepare_mode": "none",
            "provenance": {},
            "evidence_input": {},
            "runtime_paths": {},
            "automated_checks": [],
            "hud_prepare": None,
            "manual_checks": [],
            "p8_acceptance": p8,
        }

        validation = run_live_acceptance.validate_report_schema(report)

        self.assertEqual(validation["status"], "FAIL")
        self.assertTrue(any("ineligible evidence" in error for error in validation["errors"]))

    def test_validate_report_schema_rejects_tampered_p8_counts(self) -> None:
        report = {
            "schema": run_live_acceptance.REPORT_SCHEMA,
            "schema_extensions": list(run_live_acceptance.REPORT_SCHEMA_EXTENSIONS),
            "generated_at": "2026-08-03T00:00:00Z",
            "project_root": "C:/tmp/project",
            "output_dir": "C:/tmp/output",
            "prepare_mode": "none",
            "provenance": {},
            "evidence_input": {},
            "runtime_paths": {},
            "automated_checks": [],
            "hud_prepare": None,
            "manual_checks": [],
            "p8_acceptance": run_live_acceptance.build_p8_acceptance([], {}),
        }
        report["p8_acceptance"]["counts"]["PENDING"] += 1

        validation = run_live_acceptance.validate_report_schema(report)

        self.assertEqual(validation["status"], "FAIL")
        self.assertTrue(
            any("counts do not match" in error for error in validation["errors"])
        )


class LiveAcceptanceMainTests(unittest.TestCase):
    def test_existing_cli_arguments_remain_compatible(self) -> None:
        args = run_live_acceptance.build_parser().parse_args(
            [
                "--prepare-mode",
                "normal",
                "--output-dir",
                "C:/tmp/out",
                "--json-output",
                "C:/tmp/report.json",
                "--markdown-output",
                "C:/tmp/report.md",
                "--manual-observations",
                "C:/tmp/evidence.json",
                "--idle-cpu-sample-seconds",
                "60",
                "--skip-automated-checks",
            ]
        )

        self.assertEqual(args.prepare_mode, "normal")
        self.assertEqual(args.output_dir, Path("C:/tmp/out"))
        self.assertEqual(args.manual_observations, Path("C:/tmp/evidence.json"))
        self.assertEqual(args.idle_cpu_sample_seconds, 60.0)
        self.assertTrue(args.skip_automated_checks)

    def test_manual_evidence_file_has_auditable_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "evidence.json"
            payload = json.dumps(
                {
                    "windows_renderer_startup": {
                        "status": "pass",
                        "evidence": ["startup-screen-recording.mp4"],
                    }
                }
            ).encode("utf-8")
            path.write_bytes(payload)

            observations, source = run_live_acceptance._manual_observation_bundle(path)

        self.assertIn("windows_renderer_startup", observations)
        self.assertEqual(source["status"], "PASS")
        self.assertEqual(source["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(source["observation_count"], 1)

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
            with patch.object(
                run_live_acceptance, "run_acceptance", return_value=report
            ):
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
            self.assertEqual(
                json.loads(json_output.read_text(encoding="utf-8")), report
            )
            self.assertIn(
                "# Live Acceptance Report", markdown_output.read_text(encoding="utf-8")
            )

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
            patch.object(
                run_live_acceptance, "run_command", side_effect=fake_run_command
            ),
            patch.object(
                run_live_acceptance,
                "run_latency_harness",
                return_value={"name": "latency_harness", "status": "PASS"},
            ),
            patch.object(
                run_live_acceptance, "start_hud_prepare", side_effect=fake_start_hud
            ),
            patch.object(
                run_live_acceptance,
                "collect_git_provenance",
                return_value={"status": "PASS", "head_sha": "a" * 40},
            ),
            patch.object(
                run_live_acceptance,
                "runtime_paths",
                return_value={"daemon_log": "C:/tmp/daemon.log"},
            ),
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
        self.assertEqual(report["schema"], run_live_acceptance.REPORT_SCHEMA)
        self.assertIn("codex-usage-hud.p8-acceptance.v1", report["schema_extensions"])
        self.assertEqual(report["p8_acceptance"]["status"], "FAIL")
        self.assertEqual(report["schema_validation"]["status"], "PASS")

    def test_run_acceptance_applies_manual_observations(self) -> None:
        observations = {
            "active_session_latency": {
                "status": "fail",
                "observed_ms": 2500,
                "note": "Visible switch took about 2-3 seconds.",
            }
        }

        with (
            patch.object(
                run_live_acceptance,
                "run_command",
                return_value={"name": "noop", "status": "PASS"},
            ),
            patch.object(
                run_live_acceptance,
                "run_latency_harness",
                return_value={"name": "latency_harness", "status": "PASS"},
            ),
            patch.object(
                run_live_acceptance,
                "collect_git_provenance",
                return_value={"status": "PASS", "head_sha": "a" * 40},
            ),
            patch.object(run_live_acceptance, "runtime_paths", return_value={}),
        ):
            report = run_live_acceptance.run_acceptance(
                prepare_mode="debug",
                output_dir=PROJECT_ROOT / "tmp-live-acceptance",
                run_automated_checks=False,
                manual_observations=observations,
            )

        check = next(
            item
            for item in report["manual_checks"]
            if item["id"] == "active_session_latency"
        )
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["observed_ms"], 2500)
        self.assertIn("2-3 seconds", check["note"])

    def test_run_acceptance_appends_idle_cpu_sample_when_requested(self) -> None:
        with (
            patch.object(
                run_live_acceptance,
                "run_command",
                return_value={"name": "stop_hud", "status": "PASS"},
            ),
            patch.object(
                run_live_acceptance,
                "run_latency_harness",
                return_value={"name": "latency_harness", "status": "PASS"},
            ),
            patch.object(
                run_live_acceptance,
                "start_hud_prepare",
                return_value={
                    "name": "start_hud",
                    "status": "PASS",
                    "prepare_mode": "debug",
                    "pid": 4321,
                },
            ),
            patch.object(
                run_live_acceptance, "_sample_hud_cpu_percent", return_value=0.4
            ),
            patch.object(
                run_live_acceptance,
                "collect_git_provenance",
                return_value={"status": "PASS", "head_sha": "a" * 40},
            ),
            patch.object(run_live_acceptance, "runtime_paths", return_value={}),
        ):
            report = run_live_acceptance.run_acceptance(
                prepare_mode="debug",
                output_dir=PROJECT_ROOT / "tmp-live-acceptance",
                run_automated_checks=False,
                idle_cpu_sample_seconds=60.0,
            )

        sample = next(
            item
            for item in report["automated_checks"]
            if item["name"] == "idle_cpu_sample"
        )
        self.assertEqual(sample["status"], "UNKNOWN")
        self.assertEqual(sample["sample_seconds"], 60.0)
        self.assertEqual(sample["cpu_percent"], 0.4)
        self.assertEqual(sample["measurement_scope"], "hud-daemon-process-only")
        self.assertFalse(sample["p8_eligible"])


if __name__ == "__main__":
    unittest.main()
