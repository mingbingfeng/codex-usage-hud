#!/usr/bin/env python3
"""Run mixed live-acceptance checks for the renderer HUD."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.cli import renderer_diagnostic_path  # noqa: E402
from codex_usage_hud.config import default_settings_path  # noqa: E402
from codex_usage_hud.daemon import daemon_log_path  # noqa: E402

import measure_renderer_latency  # noqa: E402


PHASE_GATE_TEST_COMMAND = [
    sys.executable,
    "-m",
    "pytest",
    "tests/test_renderer_hud.py",
    "tests/test_active_session.py",
    "tests/test_ui.py",
    "tests/test_file_watcher.py",
    "-q",
]
COMPILEALL_COMMAND = [sys.executable, "-m", "compileall", "-q", "src", "tests", "tools"]
GIT_DIFF_CHECK_COMMAND = ["git", "diff", "--check"]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return PROJECT_ROOT / "artifacts" / "live_acceptance" / stamp


def _python_module_env(*, debug: bool | None) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_ROOT) if not existing else f"{SRC_ROOT}{os.pathsep}{existing}"
    if debug is True:
        env["CODEX_USAGE_HUD_DEBUG"] = "1"
    elif debug is False:
        env.pop("CODEX_USAGE_HUD_DEBUG", None)
    return env


def _command_text(command: list[str]) -> str:
    return subprocess.list2cmdline([str(item) for item in command])


def _tail_lines(text: str, limit: int = 40) -> str:
    lines = str(text or "").splitlines()
    return "\n".join(lines[-limit:])


def _status_text(value: str) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def runtime_paths() -> dict[str, str]:
    return {
        "settings": str(default_settings_path()),
        "daemon_log": str(daemon_log_path()),
        "renderer_diagnostic": str(renderer_diagnostic_path()),
    }


def load_manual_observations(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    observations: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        observations[str(key)] = dict(value)
    return observations


def run_command(
    name: str,
    command: list[str],
    *,
    output_dir: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    log_path = output_dir / f"{name}.log"
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout_seconds,
        errors="replace",
    )
    combined = completed.stdout or ""
    if completed.stderr:
        combined = f"{combined}\n{completed.stderr}" if combined else completed.stderr
    log_path.write_text(combined, encoding="utf-8", newline="\n")
    return {
        "name": name,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "exit_code": completed.returncode,
        "command": _command_text(command),
        "log_path": str(log_path),
        "output_tail": _tail_lines(combined),
    }


def run_latency_harness(output_dir: Path) -> dict[str, Any]:
    json_path = output_dir / "renderer_latency_baseline.json"
    markdown_path = output_dir / "renderer_latency_baseline.md"
    report = measure_renderer_latency.measure_baseline(
        sessions_root=measure_renderer_latency._default_sessions_root(),
        session_file=None,
        iterations=7,
        warmups=1,
    )
    rounded = measure_renderer_latency._round_metrics(report)
    json_path.write_text(
        json.dumps(rounded, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(
        measure_renderer_latency.format_markdown(report),
        encoding="utf-8",
        newline="\n",
    )
    budget_rows = measure_renderer_latency.regression_budget_rows(report)
    failing = [row for row in budget_rows if row["status"] != "PASS"]
    summary = (
        "All regression budgets passed."
        if not failing
        else "Regression budget failures: "
        + ", ".join(f"{row['name']}={row['p90_ms']:.3f}ms" for row in failing)
    )
    return {
        "name": "latency_harness",
        "status": "PASS" if not failing else "FAIL",
        "artifact": str(markdown_path),
        "json_artifact": str(json_path),
        "summary": summary,
        "budget_results": budget_rows,
    }


def _hud_command() -> list[str]:
    return [sys.executable, "-m", "codex_usage_hud"]


def start_hud_prepare(*, prepare_mode: str, output_dir: Path) -> dict[str, Any]:
    command = _hud_command() + ["--daemon"]
    env = _python_module_env(debug=prepare_mode == "debug")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    time.sleep(2.0)
    return {
        "name": "start_hud",
        "status": "PASS" if process.poll() is None else "FAIL",
        "prepare_mode": prepare_mode,
        "pid": process.pid,
        "command": _command_text(command),
        "log_path": str(output_dir / "start_hud.log"),
    }


def _read_process_cpu_seconds(pid: int) -> float | None:
    if pid <= 0 or not sys.platform.startswith("win"):
        return None
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; if ($p) {{ $p.CPU }}",
    ]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10.0,
    )
    text = str(completed.stdout or "").strip()
    if completed.returncode != 0 or not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _sample_hud_cpu_percent(pid: int, sample_seconds: float) -> float | None:
    duration = max(0.5, float(sample_seconds))
    start_cpu = _read_process_cpu_seconds(pid)
    if start_cpu is None:
        return None
    started = time.perf_counter()
    time.sleep(duration)
    end_cpu = _read_process_cpu_seconds(pid)
    elapsed = time.perf_counter() - started
    if end_cpu is None or elapsed <= 0:
        return None
    cpu_count = max(1, os.cpu_count() or 1)
    return max(0.0, ((end_cpu - start_cpu) / elapsed) * 100.0 / cpu_count)


def manual_checks_for_mode(prepare_mode: str) -> list[dict[str, Any]]:
    checks = [
        {
            "id": "debug_ready_row",
            "title": "DEBUG HUD Ready Row",
            "modes": {"debug"},
            "instructions": [
                "Open Codex App with the HUD attached.",
                "Expand the runtime errors panel and confirm `debug.ready` and `DEBUG HUD active` are visible.",
                "Drag the panel, then refresh or redraw the HUD and confirm the position persists.",
            ],
        },
        {
            "id": "active_session_latency",
            "title": "Active Session Switch Latency",
            "modes": {"debug"},
            "instructions": [
                "Open two Codex threads with visibly different titles or totals.",
                "Record multiple thread switches.",
                "Confirm the HUD tracks the selected thread within about 9 frames at 60fps (~150ms).",
            ],
        },
        {
            "id": "current_session_latency",
            "title": "Current Session Append Latency",
            "modes": {"debug"},
            "instructions": [
                "Open a live Codex thread.",
                "Submit a short prompt that produces visible request activity.",
                "Confirm the HUD reacts within about 15 frames at 60fps (~250ms).",
            ],
        },
        {
            "id": "idle_cpu",
            "title": "Idle CPU / No Background Work",
            "modes": {"debug"},
            "instructions": [
                "Leave Codex App on a stable idle thread for at least 60 seconds.",
                "Do not type, switch sessions, resize panels, or trigger overlay commands.",
                "Confirm there is no sustained HUD CPU usage or obvious periodic bursts.",
            ],
        },
        {
            "id": "normal_mode_diagnostics",
            "title": "Normal-Mode Diagnostics",
            "modes": {"normal"},
            "instructions": [
                "Reproduce one known runtime error if practical.",
                "Inspect `renderer_fallback.log`.",
                "Confirm a new JSON line includes source, severity, code, message, context, firstSeenAt, and lastSeenAt.",
            ],
        },
    ]
    selected = []
    for item in checks:
        if prepare_mode == "none" or prepare_mode in item["modes"]:
            selected.append(
                {
                    "id": item["id"],
                    "title": item["title"],
                    "status": "pending",
                    "instructions": list(item["instructions"]),
                }
            )
    return selected


def apply_manual_observations(
    checks: list[dict[str, Any]],
    observations: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    observed = observations or {}
    for item in checks:
        next_item = dict(item)
        raw = observed.get(str(item.get("id") or ""))
        if isinstance(raw, dict):
            status = _status_text(str(raw.get("status") or ""))
            if status:
                next_item["status"] = status
            note = str(raw.get("note") or "").strip()
            if note:
                next_item["note"] = note
            observed_ms = raw.get("observed_ms")
            if observed_ms is not None:
                try:
                    next_item["observed_ms"] = float(observed_ms)
                except (TypeError, ValueError):
                    pass
        merged.append(next_item)
    return merged


def run_acceptance(
    *,
    prepare_mode: str,
    output_dir: Path,
    run_automated_checks: bool = True,
    manual_observations: dict[str, dict[str, Any]] | None = None,
    idle_cpu_sample_seconds: float = 0.0,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    automated_checks: list[dict[str, Any]] = []
    if run_automated_checks:
        automated_checks.append(
            run_command(
                "phase_gate_pytest",
                PHASE_GATE_TEST_COMMAND,
                output_dir=output_dir,
                env=_python_module_env(debug=None),
            )
        )
        automated_checks.append(
            run_command(
                "compileall",
                COMPILEALL_COMMAND,
                output_dir=output_dir,
                env=_python_module_env(debug=None),
            )
        )
        automated_checks.append(
            run_command(
                "git_diff_check",
                GIT_DIFF_CHECK_COMMAND,
                output_dir=output_dir,
            )
        )
        automated_checks.append(run_latency_harness(output_dir))

    hud_prepare: dict[str, Any] | None = None
    if prepare_mode != "none":
        automated_checks.append(
            run_command(
                "stop_hud",
                _hud_command() + ["--stop"],
                output_dir=output_dir,
                env=_python_module_env(debug=None),
                timeout_seconds=60.0,
            )
        )
        hud_prepare = start_hud_prepare(prepare_mode=prepare_mode, output_dir=output_dir)
        pid = int(hud_prepare.get("pid") or 0)
        if pid > 0 and idle_cpu_sample_seconds > 0:
            cpu_percent = _sample_hud_cpu_percent(pid, idle_cpu_sample_seconds)
            automated_checks.append(
                {
                    "name": "idle_cpu_sample",
                    "status": (
                        "PASS"
                        if cpu_percent is not None and cpu_percent < 1.0
                        else "FAIL" if cpu_percent is not None else "UNKNOWN"
                    ),
                    "pid": pid,
                    "sample_seconds": float(idle_cpu_sample_seconds),
                    "cpu_percent": cpu_percent,
                    "summary": (
                        f"HUD process average CPU during idle sample: {cpu_percent:.3f}%"
                        if cpu_percent is not None
                        else "HUD CPU sample could not be collected."
                    ),
                }
            )

    manual_checks = apply_manual_observations(
        manual_checks_for_mode(prepare_mode),
        manual_observations,
    )
    return {
        "schema": "codex-usage-hud.live-acceptance.v1",
        "generated_at": _timestamp(),
        "project_root": str(PROJECT_ROOT),
        "output_dir": str(output_dir),
        "prepare_mode": prepare_mode,
        "runtime_paths": runtime_paths(),
        "automated_checks": automated_checks,
        "hud_prepare": hud_prepare,
        "manual_checks": manual_checks,
    }


def format_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Live Acceptance Report",
        "",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Prepare mode: `{report.get('prepare_mode')}`",
        f"- Output dir: `{report.get('output_dir')}`",
        "",
        "## Runtime Paths",
        "",
    ]
    for name, path in sorted((report.get("runtime_paths") or {}).items()):
        lines.append(f"- {name}: `{path}`")
    lines.extend(["", "## Automated Checks", ""])
    checks = report.get("automated_checks") or []
    if checks:
        lines.extend(
            [
                "| Check | Status | Command / Artifact |",
                "|-------|--------|--------------------|",
            ]
        )
        for item in checks:
            summary = item.get("summary") or item.get("artifact") or item.get("command") or ""
            lines.append(f"| {item.get('name')} | {item.get('status')} | {summary} |")
    else:
        lines.append("- No automated checks were run.")

    hud_prepare = report.get("hud_prepare")
    if isinstance(hud_prepare, dict):
        lines.extend(
            [
                "",
                "## HUD Preparation",
                "",
                f"- status: `{hud_prepare.get('status')}`",
                f"- mode: `{hud_prepare.get('prepare_mode')}`",
                f"- pid: `{hud_prepare.get('pid')}`",
            ]
        )

    lines.extend(["", "## Manual Checks", ""])
    manual_checks = report.get("manual_checks") or []
    if not manual_checks:
        lines.append("- No manual checks selected for this run.")
    else:
        for item in manual_checks:
            lines.append(f"### {item.get('title')}")
            lines.append("")
            lines.append(f"- status: `{item.get('status')}`")
            observed_ms = item.get("observed_ms")
            if observed_ms is not None:
                lines.append(f"- observed_ms: `{observed_ms}`")
            note = str(item.get("note") or "").strip()
            if note:
                lines.append(f"- note: {note}")
            for step in item.get("instructions") or []:
                lines.append(f"- {step}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepare-mode",
        choices=("none", "debug", "normal"),
        default="none",
        help="Optionally stop/start the HUD in a mode that prepares manual live checks.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_default_output_dir(),
        help="Directory for generated logs and reports.",
    )
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument("--markdown-output", type=Path, default=None)
    parser.add_argument(
        "--manual-observations",
        type=Path,
        default=None,
        help="Optional JSON file with manual check results keyed by check id.",
    )
    parser.add_argument(
        "--idle-cpu-sample-seconds",
        type=float,
        default=0.0,
        help="Optional idle CPU sample duration for the launched HUD process.",
    )
    parser.add_argument(
        "--skip-automated-checks",
        action="store_true",
        help="Only prepare HUD/manual steps without rerunning pytest/compileall/git diff/latency.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output_dir.expanduser()
    observations = load_manual_observations(
        args.manual_observations.expanduser() if args.manual_observations is not None else None
    )
    report = run_acceptance(
        prepare_mode=args.prepare_mode,
        output_dir=output_dir,
        run_automated_checks=not args.skip_automated_checks,
        manual_observations=observations,
        idle_cpu_sample_seconds=max(0.0, float(args.idle_cpu_sample_seconds)),
    )
    rounded = report
    text = json.dumps(rounded, ensure_ascii=False, indent=2)
    print(text)
    json_output = args.json_output.expanduser() if args.json_output is not None else output_dir / "live_acceptance_report.json"
    markdown_output = args.markdown_output.expanduser() if args.markdown_output is not None else output_dir / "live_acceptance_report.md"
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(text + "\n", encoding="utf-8", newline="\n")
    markdown_output.write_text(format_markdown(report), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
