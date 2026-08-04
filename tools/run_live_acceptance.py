#!/usr/bin/env python3
"""Run mixed live-acceptance checks for the renderer HUD."""

from __future__ import annotations

import argparse
import hashlib
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

REPORT_SCHEMA = "codex-usage-hud.live-acceptance.v1"
REPORT_SCHEMA_VALIDATION_SCHEMA = "codex-usage-hud.live-acceptance-schema.v1"
REPORT_SCHEMA_EXTENSIONS = [
    "codex-usage-hud.git-provenance.v1",
    "codex-usage-hud.p8-acceptance.v1",
]

P8_AUTOMATED_REQUIREMENTS = (
    (
        "full_automated_suites",
        "Full Automated Test Suites",
        "all",
        ("The complete UI and non-UI suites pass with no narrowed exclusions.",),
    ),
    (
        "failure_and_flaky_manifests",
        "Failure And Flaky Manifests",
        "all",
        ("The failure manifest and flaky allowlist are both empty.",),
    ),
    (
        "terminal_import_cycle_ownership_audit",
        "Terminal Import-Cycle And Ownership Audit",
        "all",
        ("The terminal module graph has no forbidden cycle or reverse dependency.",),
    ),
    (
        "latency_regression_budgets",
        "Latency Regression Budgets",
        "all",
        (
            "All regression budgets pass.",
            "Evidence distinguishes measured pipeline latency from visible end-to-end latency.",
        ),
    ),
    (
        "event_driven_idle_no_work",
        "Event-Driven Idle / No Recurring Work",
        "all",
        (
            "No page, session, config, or filesystem event means no recurring snapshot, scan, or CDP push work.",
        ),
    ),
    (
        "compatibility_facade_allowlist",
        "Compatibility Facade Allowlist",
        "all",
        ("Compatibility facades expose only the documented explicit allowlist.",),
    ),
    (
        "legacy_patch_reference_inventory",
        "Legacy Patch Reference Inventory",
        "all",
        (
            "Old CLI and Renderer-facade patch references are zero outside compatibility tests.",
        ),
    ),
    (
        "identity_alias_migration_cleanup",
        "Identity Alias And Migration Cleanup",
        "all",
        (
            "Identity aliases, obsolete adapters, stale baselines, and temporary fixtures are removed.",
        ),
    ),
    (
        "documentation_release_evidence",
        "Documentation And Release Evidence",
        "all",
        (
            "Strategy, audit, runbook, macOS, packaging, and release records match current evidence.",
        ),
    ),
)

P8_INTERACTION_REQUIREMENTS = (
    (
        "windows_renderer_startup",
        "Windows Renderer Startup",
        "windows",
        ("The real Codex App starts with the Renderer HUD attached and usable.",),
    ),
    (
        "windows_active_session",
        "Windows Active Session",
        "windows",
        ("The visible HUD follows real Codex thread selection correctly.",),
    ),
    (
        "windows_current_request",
        "Windows Current Request",
        "windows",
        ("A live request updates the current-session HUD state correctly.",),
    ),
    (
        "windows_all_settings_tabs",
        "Windows All Settings Tabs",
        "windows",
        (
            "Every current settings tab opens, reads, writes, and closes without a Renderer error.",
        ),
    ),
    (
        "windows_overlay_commands_artifacts",
        "Windows Overlay Commands And Artifacts",
        "windows",
        (
            "Overlay commands complete and their state, command, and transition artifacts agree.",
        ),
    ),
    (
        "windows_root_replacement",
        "Windows Root Replacement",
        "windows",
        (
            "Replacing the application root reattaches one functional HUD without leaked resources.",
        ),
    ),
    (
        "windows_reinject_remove",
        "Windows Reinject And Remove",
        "windows",
        (
            "Repeated reinject/remove cycles preserve state and do not grow lifecycle resource counts.",
        ),
    ),
    (
        "windows_theme",
        "Windows Theme",
        "windows",
        ("Theme changes update the HUD without duplicate listeners or stale styling.",),
    ),
    (
        "windows_drag_resize",
        "Windows Drag And Resize",
        "windows",
        ("HUD panels drag, resize, and restore their persisted layout correctly.",),
    ),
    (
        "windows_narrow_windows",
        "Windows Narrow Windows",
        "windows",
        ("The HUD remains usable and non-overlapping in narrow Codex windows.",),
    ),
    (
        "windows_idle_cpu",
        "Windows Idle CPU",
        "windows",
        (
            "A real idle Renderer session shows no sustained HUD CPU work or periodic bursts.",
        ),
    ),
    (
        "macos_renderer_interaction",
        "macOS Renderer Interaction",
        "macos",
        ("The packaged macOS app passes a real Renderer interaction smoke.",),
    ),
)

P8_PACKAGE_REQUIREMENTS = (
    (
        "windows_source_checkout_smoke",
        "Windows Source Checkout Smoke",
        "windows",
        (
            "Import, --help, --version, and isolated --once startup pass from the source checkout.",
        ),
    ),
    (
        "windows_installed_wheel_smoke",
        "Windows Installed Wheel Smoke",
        "windows",
        (
            "A freshly built and isolated installed wheel passes import and startup smoke.",
        ),
    ),
    (
        "windows_pyinstaller_executable_smoke",
        "Windows PyInstaller Executable Smoke",
        "windows",
        (
            "A freshly built executable passes --help, --version, and isolated startup smoke.",
        ),
    ),
    (
        "macos_source_checkout_import_startup_smoke",
        "macOS Source Checkout Import And Startup Smoke",
        "macos",
        ("The source checkout passes package-data import and startup smoke on macOS.",),
    ),
    (
        "macos_installed_package_import_startup_smoke",
        "macOS Installed Package Import And Startup Smoke",
        "macos",
        ("A freshly installed macOS package passes import and startup smoke.",),
    ),
)

P8_REQUIREMENT_GROUPS = {
    "automated_evidence": P8_AUTOMATED_REQUIREMENTS,
    "interaction_smoke": P8_INTERACTION_REQUIREMENTS,
    "package_smoke": P8_PACKAGE_REQUIREMENTS,
}
P8_REAL_APP_REQUIREMENT_IDS = frozenset(
    item[0] for item in P8_INTERACTION_REQUIREMENTS
)
P8_FORBIDDEN_EVIDENCE_SCOPE_MARKERS = (
    "daemon-only",
    "hud-daemon-process-only",
    "chromium",
    "smoke-host",
    "smoke_host",
)
P8_INELIGIBLE_STATUS_MARKERS = frozenset(
    {
        "invalidated",
        "ineligible",
        "not-eligible",
        "non-eligible",
        "discarded",
        "superseded",
    }
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return PROJECT_ROOT / "artifacts" / "live_acceptance" / stamp


def _python_module_env(*, debug: bool | None) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(SRC_ROOT) if not existing else f"{SRC_ROOT}{os.pathsep}{existing}"
    )
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


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _git_capture(project_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=project_root,
        env=env,
        capture_output=True,
        timeout=60.0,
        check=False,
    )


def _require_git_output(
    project_root: Path,
    *args: str,
) -> bytes:
    completed = _git_capture(project_root, *args)
    if completed.returncode == 0:
        return completed.stdout
    command = _command_text(["git", *args])
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    raise RuntimeError(f"{command} exited {completed.returncode}: {detail}")


def _untracked_file_records(
    project_root: Path,
    raw_paths: bytes,
) -> tuple[list[dict[str, Any]], list[str]]:
    root = project_root.resolve()
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    decoded_paths = [
        item.decode("utf-8", errors="replace")
        for item in raw_paths.split(b"\0")
        if item
    ]
    for relative_path in sorted(decoded_paths):
        candidate = project_root / Path(relative_path)
        try:
            lexical_path = Path(os.path.abspath(candidate))
            lexical_path.relative_to(root)
        except (OSError, ValueError) as exc:
            errors.append(f"Could not resolve untracked path {relative_path!r}: {exc}")
            records.append(
                {
                    "path": relative_path.replace("\\", "/"),
                    "status": "FAIL",
                    "error": str(exc),
                }
            )
            continue
        if candidate.is_symlink():
            try:
                link_target = os.readlink(candidate)
                payload = os.fsencode(link_target)
                mode = candidate.lstat().st_mode & 0o777
            except OSError as exc:
                errors.append(
                    f"Could not hash untracked symlink {relative_path!r}: {exc}"
                )
                records.append(
                    {
                        "path": relative_path.replace("\\", "/"),
                        "status": "FAIL",
                        "error": str(exc),
                    }
                )
                continue
            records.append(
                {
                    "path": relative_path.replace("\\", "/"),
                    "status": "PASS",
                    "type": "symlink",
                    "mode": f"{mode:04o}",
                    "bytes": len(payload),
                    "sha256": _sha256_bytes(payload),
                    "target": link_target,
                }
            )
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            errors.append(f"Could not resolve untracked path {relative_path!r}: {exc}")
            records.append(
                {
                    "path": relative_path.replace("\\", "/"),
                    "status": "FAIL",
                    "error": str(exc),
                }
            )
            continue
        if not resolved.is_file():
            error = "Untracked path is not a regular file."
            errors.append(f"{relative_path!r}: {error}")
            records.append(
                {
                    "path": relative_path.replace("\\", "/"),
                    "status": "FAIL",
                    "error": error,
                }
            )
            continue
        try:
            sha256, size = _sha256_file(resolved)
        except OSError as exc:
            errors.append(f"Could not hash untracked path {relative_path!r}: {exc}")
            records.append(
                {
                    "path": relative_path.replace("\\", "/"),
                    "status": "FAIL",
                    "error": str(exc),
                }
            )
            continue
        records.append(
            {
                "path": relative_path.replace("\\", "/"),
                "status": "PASS",
                "type": "file",
                "mode": f"{resolved.stat().st_mode & 0o777:04o}",
                "bytes": size,
                "sha256": sha256,
            }
        )
    return records, errors


def collect_git_provenance(
    output_dir: Path,
    *,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Capture the exact pre-run HEAD and worktree content provenance."""

    captured_at = _timestamp()
    status_artifact = output_dir / "git_status.txt"
    patch_artifact = output_dir / "git_tracked_worktree.patch"
    untracked_artifact = output_dir / "git_untracked_manifest.json"
    commands = {
        "head": "git rev-parse HEAD",
        "branch": "git symbolic-ref --quiet --short HEAD",
        "status": "git status --porcelain=v1 --untracked-files=all",
        "tracked_diff": "git diff --binary --full-index --no-ext-diff --no-textconv HEAD --",
        "staged_diff": "git diff --binary --full-index --no-ext-diff --no-textconv --cached --",
        "unstaged_diff": "git diff --binary --full-index --no-ext-diff --no-textconv --",
        "untracked": "git ls-files --others --exclude-standard -z",
    }
    try:
        repository_root = (
            _require_git_output(
                project_root,
                "rev-parse",
                "--show-toplevel",
            )
            .decode("utf-8", errors="replace")
            .strip()
        )
        head_before = (
            _require_git_output(project_root, "rev-parse", "HEAD")
            .decode(
                "ascii",
                errors="replace",
            )
            .strip()
        )
        branch_result = _git_capture(
            project_root, "symbolic-ref", "--quiet", "--short", "HEAD"
        )
        branch = (
            branch_result.stdout.decode("utf-8", errors="replace").strip()
            if branch_result.returncode == 0
            else None
        )
        status_before = _require_git_output(
            project_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        tracked_diff = _require_git_output(
            project_root,
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        )
        staged_diff = _require_git_output(
            project_root,
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "--cached",
            "--",
        )
        unstaged_diff = _require_git_output(
            project_root,
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "--",
        )
        untracked_paths = _require_git_output(
            project_root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
        untracked_records, errors = _untracked_file_records(
            project_root, untracked_paths
        )

        head_after = (
            _require_git_output(project_root, "rev-parse", "HEAD")
            .decode(
                "ascii",
                errors="replace",
            )
            .strip()
        )
        status_after = _require_git_output(
            project_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        tracked_diff_after = _require_git_output(
            project_root,
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        )
        stable = (
            head_before == head_after
            and status_before == status_after
            and tracked_diff == tracked_diff_after
        )
        if not stable:
            errors.append(
                "HEAD or tracked worktree changed while provenance was captured."
            )

        untracked_payload = (
            json.dumps(
                {"files": untracked_records},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        fingerprint_payload = json.dumps(
            {
                "head_sha": head_before,
                "worktree_status_sha256": _sha256_bytes(status_before),
                "tracked_diff_sha256": _sha256_bytes(tracked_diff),
                "staged_diff_sha256": _sha256_bytes(staged_diff),
                "unstaged_diff_sha256": _sha256_bytes(unstaged_diff),
                "untracked_manifest_sha256": _sha256_bytes(untracked_payload),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

        status_artifact.write_bytes(status_before)
        patch_artifact.write_bytes(tracked_diff)
        untracked_artifact.write_bytes(untracked_payload)
        return {
            "schema": "codex-usage-hud.git-provenance.v1",
            "status": "PASS" if not errors else "FAIL",
            "captured_at": captured_at,
            "capture_point": "pre-run",
            "repository_root": repository_root,
            "head_sha": head_before,
            "branch": branch,
            "detached_head": branch is None,
            "worktree_dirty": bool(status_before),
            "capture_stable": stable,
            "worktree_fingerprint_sha256": _sha256_bytes(fingerprint_payload),
            "worktree_status": {
                "sha256": _sha256_bytes(status_before),
                "lines": status_before.decode("utf-8", errors="replace").splitlines(),
                "artifact": str(status_artifact),
            },
            "tracked_diff": {
                "bytes": len(tracked_diff),
                "sha256": _sha256_bytes(tracked_diff),
                "artifact": str(patch_artifact),
            },
            "staged_diff": {
                "bytes": len(staged_diff),
                "sha256": _sha256_bytes(staged_diff),
            },
            "unstaged_diff": {
                "bytes": len(unstaged_diff),
                "sha256": _sha256_bytes(unstaged_diff),
            },
            "untracked_files": {
                "count": len(untracked_records),
                "sha256": _sha256_bytes(untracked_payload),
                "artifact": str(untracked_artifact),
                "files": untracked_records,
            },
            "generated_artifacts_excluded": True,
            "generated_artifacts_note": (
                "The snapshot is captured before acceptance commands and before these provenance artifacts are written."
            ),
            "commands": commands,
            "errors": errors,
        }
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {
            "schema": "codex-usage-hud.git-provenance.v1",
            "status": "FAIL",
            "captured_at": captured_at,
            "capture_point": "pre-run",
            "worktree_dirty": None,
            "capture_stable": False,
            "commands": commands,
            "errors": [str(exc)],
        }


def runtime_paths() -> dict[str, str]:
    return {
        "settings": str(default_settings_path()),
        "daemon_log": str(daemon_log_path()),
        "renderer_diagnostic": str(renderer_diagnostic_path()),
    }


def _manual_observation_bundle(
    path: Path | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if path is None:
        return {}, {
            "status": "NOT_PROVIDED",
            "path": None,
            "sha256": None,
            "observation_count": 0,
        }
    try:
        payload = path.read_bytes()
        raw = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, {
            "status": "FAIL",
            "path": str(path),
            "sha256": None,
            "observation_count": 0,
            "error": str(exc),
        }
    if not isinstance(raw, dict):
        return {}, {
            "status": "FAIL",
            "path": str(path),
            "sha256": _sha256_bytes(payload),
            "observation_count": 0,
            "error": "The evidence file root must be a JSON object.",
        }
    observations: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        observations[str(key)] = dict(value)
    return observations, {
        "status": "PASS",
        "path": str(path),
        "sha256": _sha256_bytes(payload),
        "observation_count": len(observations),
    }


def load_manual_observations(path: Path | None) -> dict[str, dict[str, Any]]:
    observations, _metadata = _manual_observation_bundle(path)
    return observations


def _normalize_p8_status(value: Any) -> tuple[str, str | None]:
    normalized = _status_text(str(value or ""))
    if not normalized:
        return "PENDING", None
    if normalized in {"pass", "passed", "ok", "success"}:
        return "PASS", None
    if normalized in {"fail", "failed", "failure", "error"}:
        return "FAIL", None
    if normalized in {"pending", "unknown", "not-run", "not-provided"}:
        return "PENDING", None
    return "FAIL", f"Unsupported evidence status: {value!r}."


def _observation_evidence(raw: dict[str, Any]) -> list[Any]:
    evidence: list[Any] = []
    supplied = raw.get("evidence")
    if isinstance(supplied, list):
        evidence.extend(item for item in supplied if item not in (None, "", {}))
    elif supplied not in (None, "", {}):
        evidence.append(supplied)

    for key in ("artifact", "json_artifact", "log_path", "report", "screenshot", "url"):
        value = raw.get(key)
        if value not in (None, ""):
            evidence.append({"kind": key, "value": value})
    artifacts = raw.get("artifacts")
    if isinstance(artifacts, list):
        evidence.extend(
            {"kind": "artifact", "value": value}
            for value in artifacts
            if value not in (None, "")
        )

    command = str(raw.get("command") or "").strip()
    exit_code = raw.get("exit_code")
    if command and exit_code is not None:
        evidence.append(
            {
                "kind": "command",
                "command": command,
                "exit_code": exit_code,
            }
        )
    for key in ("observed_ms", "cpu_percent", "sample_seconds"):
        value = raw.get(key)
        if value is not None:
            evidence.append({"kind": "measurement", "name": key, "value": value})
    return evidence


def _evidence_scope(raw: dict[str, Any]) -> str:
    """Return the normalized provenance scope declared by an observation."""

    value = raw.get("evidence_scope")
    if value in (None, ""):
        value = raw.get("scope")
    return _status_text(str(value or ""))


def _forbidden_evidence_scope_reason(scope: str) -> str | None:
    if not scope:
        return None
    if any(marker in scope for marker in P8_FORBIDDEN_EVIDENCE_SCOPE_MARKERS):
        return (
            f"Evidence scope {scope!r} is not an accepted live/package source; "
            "daemon-only and Chromium/smoke-host evidence cannot prove this gate."
        )
    return None


def _is_explicit_false(value: Any) -> bool:
    if value is False:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 0
    return _status_text(str(value or "")) in {"false", "no", "off", "0"}


def _ineligible_evidence_reason(raw: dict[str, Any]) -> str | None:
    """Reject evidence explicitly marked unusable for the P8 gate.

    A report can carry a real-App scope while the actual sample was invalidated
    by a watched input change. Keep those declarations visible in the generated
    check and refuse a hand-edited ``status=pass`` wrapper around them.
    """

    for key in ("p8_eligible", "eligible"):
        if key in raw and _is_explicit_false(raw.get(key)):
            return f"Evidence declares {key}=false; it is not P8-eligible."

    status_keys = (
        "evidence_status",
        "artifact_status",
        "measurement_status",
        "sample_status",
        "observation_status",
    )
    for key in status_keys:
        value = raw.get(key)
        if _status_text(str(value or "")) in P8_INELIGIBLE_STATUS_MARKERS:
            return (
                f"Evidence {key}={value!r} is invalidated or ineligible; "
                "it cannot satisfy this P8 gate."
            )

    supplied = raw.get("evidence")
    if isinstance(supplied, list):
        for item in supplied:
            if not isinstance(item, dict):
                continue
            for key in ("p8_eligible", "eligible"):
                if key in item and _is_explicit_false(item.get(key)):
                    return f"Evidence declares {key}=false; it is not P8-eligible."
            for key in ("status", *status_keys):
                value = item.get(key)
                if _status_text(str(value or "")) in P8_INELIGIBLE_STATUS_MARKERS:
                    return (
                        f"Evidence {key}={value!r} is invalidated or ineligible; "
                        "it cannot satisfy this P8 gate."
                    )
    return None


def _automatic_p8_observations(
    automated_checks: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    requirement_ids = {
        item[0]
        for group in (
            P8_AUTOMATED_REQUIREMENTS,
            P8_INTERACTION_REQUIREMENTS,
            P8_PACKAGE_REQUIREMENTS,
        )
        for item in group
    }
    observations: dict[str, dict[str, Any]] = {}
    for item in automated_checks:
        name = str(item.get("name") or "")
        target = "latency_regression_budgets" if name == "latency_harness" else name
        if target not in requirement_ids:
            continue
        automatic = dict(item)
        automatic["evidence"] = [
            {
                "kind": "automated_check",
                "name": name,
                "status": item.get("status"),
                "artifact": item.get("artifact") or item.get("json_artifact"),
                "command": item.get("command"),
                "exit_code": item.get("exit_code"),
                "budget_results": item.get("budget_results"),
            }
        ]
        observations[target] = automatic
    return observations


def _p8_check(
    requirement: tuple[str, str, str, tuple[str, ...]],
    *,
    automatic: dict[str, Any] | None,
    observed: dict[str, Any] | None,
) -> dict[str, Any]:
    check_id, title, platform, expected = requirement
    raw: dict[str, Any] = {}
    source = "missing"
    if automatic:
        raw.update(automatic)
        source = "automatic"
    if observed:
        raw.update(observed)
        source = "observation"

    status, invalid_reason = _normalize_p8_status(raw.get("status"))
    evidence = _observation_evidence(raw)
    reason = invalid_reason
    evidence_scope = _evidence_scope(raw)
    scope_reason = _forbidden_evidence_scope_reason(evidence_scope)
    measurement_scope = _status_text(str(raw.get("measurement_scope") or ""))
    measurement_scope_reason = _forbidden_evidence_scope_reason(measurement_scope)
    eligibility_reason = _ineligible_evidence_reason(raw)
    if status == "PASS" and not evidence:
        status = "FAIL"
        reason = "PASS was reported without auditable evidence."
    if status == "PASS" and eligibility_reason is not None:
        status = "FAIL"
        reason = eligibility_reason
    if status == "PASS" and raw.get("exit_code") not in (None, 0, "0"):
        status = "FAIL"
        reason = f"PASS was reported with non-zero exit_code={raw.get('exit_code')!r}."
    if status == "PASS" and scope_reason is not None:
        status = "FAIL"
        reason = scope_reason
    if status == "PASS" and measurement_scope_reason is not None:
        status = "FAIL"
        reason = measurement_scope_reason
    if status == "PASS" and check_id in P8_REAL_APP_REQUIREMENT_IDS:
        if not evidence_scope:
            status = "FAIL"
            reason = (
                "PASS for a real Codex App interaction requires "
                "evidence_scope='real-codex-app'."
            )
        elif evidence_scope != "real-codex-app":
            status = "FAIL"
            reason = (
                f"Evidence scope {evidence_scope!r} cannot prove a real Codex App "
                "Renderer interaction; use evidence_scope='real-codex-app'."
            )
    if status == "PENDING" and reason is None:
        reason = "Required evidence has not been supplied."
    if status == "FAIL" and reason is None:
        reason = (
            str(raw.get("reason") or "").strip()
            or "The supplied evidence reports failure."
        )

    check: dict[str, Any] = {
        "id": check_id,
        "title": title,
        "platform": platform,
        "required": True,
        "status": status,
        "status_reason": reason,
        "source": source,
        "expected": list(expected),
        "evidence": evidence,
    }
    if evidence_scope:
        check["evidence_scope"] = evidence_scope
    if measurement_scope:
        check["measurement_scope"] = measurement_scope
    for key in (
        "note",
        "observed_ms",
        "cpu_percent",
        "sample_seconds",
        "command",
        "exit_code",
        "p8_eligible",
        "evidence_status",
        "artifact_status",
        "measurement_status",
        "sample_status",
        "observation_status",
    ):
        if raw.get(key) not in (None, ""):
            check[key] = raw[key]
    return check


def _p8_group(checks: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: 0 for status in ("PASS", "FAIL", "PENDING")}
    for item in checks:
        counts[str(item["status"])] += 1
    incomplete = [
        str(item["id"])
        for item in checks
        if item.get("required") and item.get("status") != "PASS"
    ]
    return {
        "status": "PASS" if not incomplete else "FAIL",
        "counts": counts,
        "incomplete_required_ids": incomplete,
        "checks": checks,
    }


def build_p8_acceptance(
    automated_checks: list[dict[str, Any]],
    observations: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    automatic = _automatic_p8_observations(automated_checks)
    observed = observations or {}

    def build_group(
        requirements: tuple[tuple[str, str, str, tuple[str, ...]], ...],
    ) -> dict[str, Any]:
        return _p8_group(
            [
                _p8_check(
                    requirement,
                    automatic=automatic.get(requirement[0]),
                    observed=observed.get(requirement[0]),
                )
                for requirement in requirements
            ]
        )

    groups = {
        "automated_evidence": build_group(P8_AUTOMATED_REQUIREMENTS),
        "interaction_smoke": build_group(P8_INTERACTION_REQUIREMENTS),
        "package_smoke": build_group(P8_PACKAGE_REQUIREMENTS),
    }
    checks = [item for group in groups.values() for item in group["checks"]]
    summary = _p8_group(checks)
    summary.pop("checks")
    return {
        "schema": "codex-usage-hud.p8-acceptance.v1",
        "status": summary["status"],
        "complete": summary["status"] == "PASS",
        "counts": summary["counts"],
        "incomplete_required_ids": summary["incomplete_required_ids"],
        **groups,
    }


def validate_report_schema(report: Any) -> dict[str, Any]:
    """Validate the generated report shape without changing evidence status.

    This is deliberately a structural gate. It catches malformed or hand-edited
    reports before they are consumed as release evidence while leaving the
    distinction between PASS, FAIL, and PENDING to the P8 evidence gate.
    """

    errors: list[str] = []
    if not isinstance(report, dict):
        return {
            "schema": REPORT_SCHEMA_VALIDATION_SCHEMA,
            "status": "FAIL",
            "errors": ["Report root must be a JSON object."],
        }

    required_top_level = (
        "schema",
        "schema_extensions",
        "generated_at",
        "project_root",
        "output_dir",
        "prepare_mode",
        "provenance",
        "evidence_input",
        "runtime_paths",
        "automated_checks",
        "hud_prepare",
        "manual_checks",
        "p8_acceptance",
    )
    for key in required_top_level:
        if key not in report:
            errors.append(f"Missing top-level field: {key}.")

    if report.get("schema") != REPORT_SCHEMA:
        errors.append(
            f"Unsupported report schema: {report.get('schema')!r}; "
            f"expected {REPORT_SCHEMA!r}."
        )
    if report.get("prepare_mode") not in {"none", "debug", "normal"}:
        errors.append("prepare_mode must be one of: none, debug, normal.")
    if not isinstance(report.get("schema_extensions"), list):
        errors.append("schema_extensions must be a JSON array.")
    for key in ("provenance", "evidence_input", "runtime_paths"):
        if key in report and not isinstance(report[key], dict):
            errors.append(f"{key} must be a JSON object.")
    for key in ("automated_checks", "manual_checks"):
        if key in report and not isinstance(report[key], list):
            errors.append(f"{key} must be a JSON array.")
    if "hud_prepare" in report and report["hud_prepare"] is not None and not isinstance(
        report["hud_prepare"], dict
    ):
        errors.append("hud_prepare must be null or a JSON object.")

    p8 = report.get("p8_acceptance")
    if not isinstance(p8, dict):
        errors.append("p8_acceptance must be a JSON object.")
    else:
        if p8.get("schema") != "codex-usage-hud.p8-acceptance.v1":
            errors.append("p8_acceptance has an unsupported schema.")
        if p8.get("status") not in {"PASS", "FAIL"}:
            errors.append("p8_acceptance.status must be PASS or FAIL.")
        if not isinstance(p8.get("complete"), bool):
            errors.append("p8_acceptance.complete must be boolean.")
        elif p8.get("complete") != (p8.get("status") == "PASS"):
            errors.append("p8_acceptance.complete must match its status.")

        p8_counts = p8.get("counts")
        if not isinstance(p8_counts, dict):
            errors.append("p8_acceptance.counts must be a JSON object.")
            p8_counts = {}
        for status in ("PASS", "FAIL", "PENDING"):
            count = p8_counts.get(status)
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                errors.append(f"p8_acceptance.counts.{status} must be a non-negative integer.")

        all_checks: list[dict[str, Any]] = []
        expected_all_ids: list[str] = []
        for group_name, requirements in P8_REQUIREMENT_GROUPS.items():
            expected_ids = [item[0] for item in requirements]
            expected_all_ids.extend(expected_ids)
            group = p8.get(group_name)
            if not isinstance(group, dict):
                errors.append(f"p8_acceptance.{group_name} must be a JSON object.")
                continue
            if group.get("status") not in {"PASS", "FAIL"}:
                errors.append(f"p8_acceptance.{group_name}.status must be PASS or FAIL.")
            checks = group.get("checks")
            if not isinstance(checks, list):
                errors.append(f"p8_acceptance.{group_name}.checks must be a JSON array.")
                continue
            actual_ids: list[str] = []
            actual_counts = {status: 0 for status in ("PASS", "FAIL", "PENDING")}
            incomplete_ids: list[str] = []
            for check in checks:
                if not isinstance(check, dict):
                    errors.append(f"p8_acceptance.{group_name} contains a non-object check.")
                    continue
                check_id = str(check.get("id") or "")
                actual_ids.append(check_id)
                all_checks.append(check)
                if check.get("status") not in actual_counts:
                    errors.append(f"Unsupported P8 check status for {check_id!r}.")
                else:
                    actual_counts[str(check["status"])] += 1
                if not isinstance(check.get("required"), bool):
                    errors.append(f"P8 check {check_id!r}.required must be boolean.")
                if check.get("required") and check.get("status") != "PASS":
                    incomplete_ids.append(check_id)
                if not isinstance(check.get("evidence"), list):
                    errors.append(f"P8 check {check_id!r}.evidence must be a JSON array.")
                if check.get("status") == "PASS" and not check.get("evidence"):
                    errors.append(f"P8 check {check_id!r} passes without evidence.")
                if check.get("status") == "PASS":
                    eligibility_reason = _ineligible_evidence_reason(check)
                    if eligibility_reason is not None:
                        errors.append(
                            f"P8 check {check_id!r} passes with ineligible evidence: "
                            f"{eligibility_reason}"
                        )
                if check.get("status") in {"FAIL", "PENDING"} and not str(
                    check.get("status_reason") or ""
                ).strip():
                    errors.append(f"P8 check {check_id!r} needs status_reason.")
                if check_id in P8_REAL_APP_REQUIREMENT_IDS and check.get("status") == "PASS":
                    scope = _evidence_scope(check)
                    scope_reason = _forbidden_evidence_scope_reason(scope)
                    measurement_reason = _forbidden_evidence_scope_reason(
                        _status_text(str(check.get("measurement_scope") or ""))
                    )
                    if scope != "real-codex-app":
                        errors.append(
                            f"P8 check {check_id!r} PASS requires evidence_scope='real-codex-app'."
                        )
                    elif scope_reason is not None:
                        errors.append(scope_reason)
                    elif measurement_reason is not None:
                        errors.append(measurement_reason)
            if actual_ids != expected_ids:
                errors.append(
                    f"p8_acceptance.{group_name}.checks ids do not match the requirement list."
                )
            group_counts = group.get("counts")
            if group_counts != actual_counts:
                errors.append(f"p8_acceptance.{group_name}.counts do not match its checks.")
            if group.get("incomplete_required_ids") != incomplete_ids:
                errors.append(
                    f"p8_acceptance.{group_name}.incomplete_required_ids do not match its checks."
                )
            expected_group_status = "PASS" if not incomplete_ids else "FAIL"
            if group.get("status") != expected_group_status:
                errors.append(f"p8_acceptance.{group_name}.status does not match its checks.")

        summary_counts = {status: 0 for status in ("PASS", "FAIL", "PENDING")}
        summary_incomplete: list[str] = []
        for check in all_checks:
            status = check.get("status")
            if status in summary_counts:
                summary_counts[str(status)] += 1
            if check.get("required") and status != "PASS":
                summary_incomplete.append(str(check.get("id") or ""))
        if expected_all_ids and len(all_checks) == len(expected_all_ids):
            actual_all_ids = [str(item.get("id") or "") for item in all_checks]
            if actual_all_ids != expected_all_ids:
                errors.append("p8_acceptance group order or ids do not match the schema.")
        if p8.get("counts") != summary_counts:
            errors.append("p8_acceptance.counts do not match all grouped checks.")
        if p8.get("incomplete_required_ids") != summary_incomplete:
            errors.append("p8_acceptance.incomplete_required_ids do not match all grouped checks.")
        expected_status = "PASS" if not summary_incomplete else "FAIL"
        if p8.get("status") != expected_status:
            errors.append("p8_acceptance.status does not match all grouped checks.")

    return {
        "schema": REPORT_SCHEMA_VALIDATION_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
    }


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
    log_path = output_dir / "start_hud.log"
    with log_path.open("w", encoding="utf-8", newline="\n") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    time.sleep(2.0)
    return {
        "name": "start_hud",
        "status": "PASS" if process.poll() is None else "FAIL",
        "prepare_mode": prepare_mode,
        "pid": process.pid,
        "command": _command_text(command),
        "log_path": str(log_path),
        "log_recorded": log_path.exists(),
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
    manual_observation_source: dict[str, Any] | None = None,
    idle_cpu_sample_seconds: float = 0.0,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    provenance = collect_git_provenance(output_dir)
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
        hud_prepare = start_hud_prepare(
            prepare_mode=prepare_mode, output_dir=output_dir
        )
        pid = int(hud_prepare.get("pid") or 0)
        if pid > 0 and idle_cpu_sample_seconds > 0:
            cpu_percent = _sample_hud_cpu_percent(pid, idle_cpu_sample_seconds)
            automated_checks.append(
                {
                    "name": "idle_cpu_sample",
                    # The daemon supervises the injected HUD but does not host
                    # renderer JavaScript. A daemon-only average cannot prove
                    # real Codex idle CPU or rule out periodic renderer bursts.
                    "status": "UNKNOWN",
                    "pid": pid,
                    "measurement_scope": "hud-daemon-process-only",
                    "p8_eligible": False,
                    "sample_seconds": float(idle_cpu_sample_seconds),
                    "cpu_percent": cpu_percent,
                    "summary": (
                        f"HUD daemon average CPU during idle sample: {cpu_percent:.3f}%. "
                        "This does not verify real Codex renderer idle CPU or periodic bursts."
                        if cpu_percent is not None
                        else "HUD daemon CPU sample could not be collected; real Codex renderer CPU remains unverified."
                    ),
                }
            )

    manual_checks = apply_manual_observations(
        manual_checks_for_mode(prepare_mode),
        manual_observations,
    )
    p8_acceptance = build_p8_acceptance(automated_checks, manual_observations)
    report = {
        "schema": REPORT_SCHEMA,
        "schema_extensions": list(REPORT_SCHEMA_EXTENSIONS),
        "generated_at": _timestamp(),
        "project_root": str(PROJECT_ROOT),
        "output_dir": str(output_dir),
        "prepare_mode": prepare_mode,
        "provenance": provenance,
        "evidence_input": manual_observation_source
        or {
            "status": "NOT_PROVIDED",
            "path": None,
            "sha256": None,
            "observation_count": len(manual_observations or {}),
        },
        "runtime_paths": runtime_paths(),
        "automated_checks": automated_checks,
        "hud_prepare": hud_prepare,
        "manual_checks": manual_checks,
        "p8_acceptance": p8_acceptance,
    }
    report["schema_validation"] = validate_report_schema(report)
    return report


def _markdown_cell(value: Any) -> str:
    return (
        str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
    )


def _p8_evidence_summary(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") or []
    if not evidence:
        return str(item.get("status_reason") or "No evidence supplied.")
    first = evidence[0]
    if isinstance(first, dict):
        detail = (
            first.get("artifact")
            or first.get("path")
            or first.get("value")
            or first.get("url")
            or first.get("command")
            or first.get("name")
            or first.get("kind")
        )
    else:
        detail = first
    suffix = f" (+{len(evidence) - 1} more)" if len(evidence) > 1 else ""
    return f"{detail}{suffix}"


def format_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Live Acceptance Report",
        "",
        f"- Schema: `{report.get('schema')}`",
        f"- Generated at: `{report.get('generated_at')}`",
        f"- Prepare mode: `{report.get('prepare_mode')}`",
        f"- Output dir: `{report.get('output_dir')}`",
    ]

    provenance = report.get("provenance")
    if isinstance(provenance, dict):
        lines.extend(
            [
                "",
                "## Source Provenance",
                "",
                f"- capture status: `{provenance.get('status')}`",
                f"- capture point: `{provenance.get('capture_point')}`",
                f"- HEAD: `{provenance.get('head_sha')}`",
                f"- branch: `{provenance.get('branch') or '(detached)'}`",
                f"- worktree dirty: `{provenance.get('worktree_dirty')}`",
                f"- capture stable: `{provenance.get('capture_stable')}`",
                (
                    "- worktree fingerprint: "
                    f"`{provenance.get('worktree_fingerprint_sha256')}`"
                ),
            ]
        )
        tracked_diff = provenance.get("tracked_diff")
        if isinstance(tracked_diff, dict):
            lines.append(f"- tracked patch: `{tracked_diff.get('artifact')}`")
            lines.append(f"- tracked patch SHA-256: `{tracked_diff.get('sha256')}`")
        untracked = provenance.get("untracked_files")
        if isinstance(untracked, dict):
            lines.append(f"- untracked files: `{untracked.get('count')}`")
            lines.append(f"- untracked manifest: `{untracked.get('artifact')}`")
        for error in provenance.get("errors") or []:
            lines.append(f"- error: {_markdown_cell(error)}")

    schema_validation = report.get("schema_validation")
    if isinstance(schema_validation, dict):
        lines.extend(
            [
                "",
                "## Report Schema Validation",
                "",
                f"- status: `{schema_validation.get('status')}`",
                f"- schema: `{schema_validation.get('schema')}`",
            ]
        )
        for error in schema_validation.get("errors") or []:
            lines.append(f"- error: {_markdown_cell(error)}")

    p8_acceptance = report.get("p8_acceptance")
    if isinstance(p8_acceptance, dict):
        counts = p8_acceptance.get("counts") or {}
        lines.extend(
            [
                "",
                "## P8 Final Acceptance",
                "",
                f"- gate status: `{p8_acceptance.get('status')}`",
                f"- complete: `{p8_acceptance.get('complete')}`",
                (
                    "- evidence counts: "
                    f"PASS={counts.get('PASS', 0)}, "
                    f"FAIL={counts.get('FAIL', 0)}, "
                    f"PENDING={counts.get('PENDING', 0)}"
                ),
            ]
        )
        incomplete = p8_acceptance.get("incomplete_required_ids") or []
        if incomplete:
            lines.append(
                "- incomplete required checks: " + ", ".join(map(str, incomplete))
            )
        group_titles = (
            ("automated_evidence", "Automated Evidence"),
            ("interaction_smoke", "Renderer Interaction Smoke"),
            ("package_smoke", "Package Smoke"),
        )
        for key, title in group_titles:
            group = p8_acceptance.get(key)
            if not isinstance(group, dict):
                continue
            lines.extend(
                [
                    "",
                    f"### {title}",
                    "",
                    f"Gate: `{group.get('status')}`",
                    "",
                    "| ID | Platform | Status | Evidence / Reason |",
                    "|----|----------|--------|-------------------|",
                ]
            )
            for item in group.get("checks") or []:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            _markdown_cell(item.get("id")),
                            _markdown_cell(item.get("platform")),
                            _markdown_cell(item.get("status")),
                            _markdown_cell(_p8_evidence_summary(item)),
                        ]
                    )
                    + " |"
                )

    lines.extend(["", "## Runtime Paths", ""])
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
            summary = (
                item.get("summary") or item.get("artifact") or item.get("command") or ""
            )
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
        help=(
            "Optional JSON evidence file keyed by legacy manual-check or P8 requirement id. "
            "The existing flat object format remains supported."
        ),
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
    observation_path = (
        args.manual_observations.expanduser()
        if args.manual_observations is not None
        else None
    )
    observations, observation_source = _manual_observation_bundle(observation_path)
    report = run_acceptance(
        prepare_mode=args.prepare_mode,
        output_dir=output_dir,
        run_automated_checks=not args.skip_automated_checks,
        manual_observations=observations,
        manual_observation_source=observation_source,
        idle_cpu_sample_seconds=max(0.0, float(args.idle_cpu_sample_seconds)),
    )
    rounded = report
    text = json.dumps(rounded, ensure_ascii=False, indent=2)
    print(text)
    json_output = (
        args.json_output.expanduser()
        if args.json_output is not None
        else output_dir / "live_acceptance_report.json"
    )
    markdown_output = (
        args.markdown_output.expanduser()
        if args.markdown_output is not None
        else output_dir / "live_acceptance_report.md"
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(text + "\n", encoding="utf-8", newline="\n")
    markdown_output.write_text(format_markdown(report), encoding="utf-8", newline="\n")
    schema_validation = report.get("schema_validation")
    if isinstance(schema_validation, dict) and schema_validation.get("status") == "FAIL":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
