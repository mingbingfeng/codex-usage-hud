"""Track legacy facade patches until owner-based tests replace them."""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "tests/contracts/facade_patch_inventory.json"
FACADE_PREFIXES = (
    "codex_usage_hud.cli.",
    "codex_usage_hud.ui.renderer_hud.",
)

PHASE_OWNERS = {
    "P1": {
        "_recent_session_files": "active_work",
        "active_work_items_for_snapshot": "active_work",
        "fetch_model_prices": "usage_pricing",
    },
    "P2": {
        "_build_session_switch_controller": "overlay_commands",
        "_create_loading_feedback": "loading_feedback",
        "_desktop_overlay_dependency_status": "desktop_overlay",
        "_loading_feedback_enabled": "loading_feedback",
        "_prepare_codex_window_for_work_overlay_switch": "overlay_commands",
        "_primary_screen_height": "overlay_projection",
        "_refocus_codex_window_after_current_session_click": "overlay_commands",
        "_refocus_codex_window_after_work_overlay_switch": "overlay_commands",
        "_select_runtime_work_overlay_items": "overlay_projection",
        "_start_desktop_overlay_install": "desktop_overlay",
        "_windows_user_object_count": "desktop_overlay",
        "_work_overlay_helper_qt": "desktop_overlay",
        "_work_overlay_screen_max_items": "overlay_projection",
        "_WorkOverlayCommandPump": "overlay_commands",
        "CdpSessionSwitchBackend": "overlay_commands",
        "DesktopWorkOverlay": "desktop_overlay",
        "WindowsSearchSessionSwitchBackend": "overlay_commands",
    },
    "P3": {
        "_save_renderer_user_config": "settings_service",
        "BACKGROUND_USAGE_RESPONSE_RETRY_DELAYS_SECONDS": "settings_service",
        "build_runtime_context": "runtime_context",
        "build_snapshot": "snapshot_builder",
        "SettingsBridgeServer": "settings_bridge",
        "UserConfigStore": "runtime_config",
    },
    "P4": {
        "_record_cdp_update_failure": "renderer_event_loop",
        "_renderer_refresh_delay_seconds": "renderer_event_loop",
        "_renderer_runtime_signature": "renderer_event_loop",
        "_renderer_update_failure_limit": "renderer_event_loop",
        "_RendererFileEventSource": "file_events",
        "_wait_for_renderer_restart_request": "renderer_event_loop",
        "ActiveSessionTracker": "active_session_bridge",
        "FileChangeWatcher": "file_events",
        "RENDERER_ACTIVE_WORK_AFTER_SESSION_DELAY_SECONDS": "renderer_event_loop",
    },
    "P5": {
        "cdp_version_info": "renderer_cdp",
        "list_targets": "renderer_cdp",
        "remove_renderer_hud_from_pages": "renderer_client",
        "RendererHudClient": "renderer_client",
        "wait_for_renderer": "renderer_client",
    },
    "P7": {
        "_activate_running_codex_app": "codex_app_runtime",
        "_allocate_fresh_renderer_cdp_port": "renderer_startup",
        "_append_renderer_diagnostic": "runtime_diagnostics",
        "_assign_fresh_renderer_cdp_port": "renderer_startup",
        "_attach_cli_logger_to_daemon_log": "daemon_runtime",
        "_audited_running_codex_desktop_processes": "codex_app_runtime",
        "_codex_processes_running": "codex_app_runtime",
        "_daemon_startup_decision": "daemon_runtime",
        "_find_existing_renderer_cdp_candidate": "renderer_startup",
        "_localhost_cdp_port_available": "renderer_startup",
        "_localhost_cdp_port_is_listening": "renderer_startup",
        "_prepare_codex_window_for_renderer": "codex_app_runtime",
        "_prepare_codex_window_for_standalone": "codex_app_runtime",
        "_process_exists": "codex_app_runtime",
        "_read_persisted_renderer_cdp_port": "renderer_startup",
        "_remember_requested_renderer_cdp_port": "renderer_startup",
        "_renderer_startup_plan": "renderer_startup",
        "_restart_codex_for_renderer": "codex_app_runtime",
        "_running_codex_desktop_processes": "codex_app_runtime",
        "_select_initial_renderer_cdp_port": "renderer_startup",
        "_select_launch_renderer_cdp_port": "renderer_startup",
        "_stop_codex_processes": "codex_app_runtime",
        "_validate_renderer_cdp_candidate": "renderer_startup",
        "_wait_for_visible_codex_window": "codex_app_runtime",
        "AutoUpdateManager": "daemon_runtime",
        "cdp_port_from_env": "renderer_startup",
        "CodexDaemonManager": "daemon_runtime",
        "CodexWindowTracker": "codex_app_runtime",
        "configure_daemon_logging": "daemon_runtime",
        "get_current_platform": "codex_app_runtime",
        "hide_console_window": "cli_app",
        "hud_runtime_dir": "runtime_paths",
        "HudInstanceLock": "instance_lock",
        "launch_codex_app": "codex_app_runtime",
        "run_daemon": "daemon_runtime",
        "run_hud_session": "daemon_runtime",
        "run_qt_hud_session": "daemon_runtime",
        "run_renderer_hud_session": "renderer_runtime",
        "run_tk_hud_session": "daemon_runtime",
    },
}

CROSS_CUTTING = {
    "importlib.util.find_spec": "dependency_probe",
    "subprocess.Popen": "process_port",
    "subprocess.run": "process_port",
    "time.monotonic": "clock_port",
    "time.sleep": "clock_port",
    "write_json_object": "storage_port",
}

RENDERER_FACADE_OWNERS = {
    "send_cdp_command": ("P5", "renderer_cdp", "migrate-to-owner"),
}


def _metadata(prefix: str, symbol: str) -> tuple[str, str, str]:
    if prefix == "codex_usage_hud.ui.renderer_hud.":
        try:
            return RENDERER_FACADE_OWNERS[symbol]
        except KeyError as exc:
            raise ValueError(
                f"unclassified renderer facade patch target: {symbol}"
            ) from exc
    if symbol in CROSS_CUTTING:
        return "P0", CROSS_CUTTING[symbol], "inject-port"
    for phase, owners in PHASE_OWNERS.items():
        if symbol in owners:
            return phase, owners[symbol], "migrate-to-owner"
    raise ValueError(f"unclassified facade patch target: {symbol}")


def extract(root: Path = ROOT) -> tuple[list[dict[str, Any]], list[str]]:
    sites: dict[str, list[dict[str, object]]] = defaultdict(list)
    errors: list[str] = []
    for path in sorted((root / "tests").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        facade_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for module, prefix in (
                        ("codex_usage_hud.cli", FACADE_PREFIXES[0]),
                        ("codex_usage_hud.ui.renderer_hud", FACADE_PREFIXES[1]),
                    ):
                        if alias.name == module:
                            facade_aliases[alias.asname or module.rsplit(".", 1)[-1]] = prefix
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and function.attr == "object"
                and isinstance(function.value, ast.Name)
                and function.value.id == "patch"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in facade_aliases
            ):
                name_node = node.args[1]
                if not isinstance(name_node, ast.Constant) or not isinstance(
                    name_node.value, str
                ):
                    errors.append(
                        f"dynamic facade patch.object target: "
                        f"{path.relative_to(root).as_posix()}:{node.lineno}"
                    )
                    continue
                target = facade_aliases[node.args[0].id] + name_node.value
                sites[target].append(
                    {
                        "file": path.relative_to(root).as_posix(),
                        "line": node.lineno,
                        "kind": "patch-object",
                    }
                )
                continue
            if not node.args:
                continue
            is_patch = (isinstance(function, ast.Name) and function.id == "patch") or (
                isinstance(function, ast.Attribute) and function.attr == "patch"
            )
            target_node = node.args[0]
            if not is_patch:
                continue
            if not isinstance(target_node, ast.Constant):
                segment = ast.get_source_segment(source, target_node) or ""
                if any(token in segment for token in ("cli", "renderer_hud")):
                    errors.append(
                        f"dynamic facade patch target: "
                        f"{path.relative_to(root).as_posix()}:{node.lineno}"
                    )
                continue
            target = target_node.value
            if not isinstance(target, str) or not target.startswith(FACADE_PREFIXES):
                continue
            sites[target].append(
                {
                    "file": path.relative_to(root).as_posix(),
                    "line": node.lineno,
                    "kind": "patch-string",
                }
            )
    entries: list[dict[str, Any]] = []
    for target, target_sites in sorted(sites.items()):
        prefix = next(prefix for prefix in FACADE_PREFIXES if target.startswith(prefix))
        symbol = target.removeprefix(prefix)
        phase, owner, classification = _metadata(prefix, symbol)
        entries.append(
            {
                "path": target,
                "referenceCount": len(target_sites),
                "sites": target_sites,
                "terminalOwner": owner,
                "targetPhase": phase,
                "classification": classification,
                "removalCondition": (
                    f"Tests inject {owner} instead of patching the CLI facade"
                    if classification == "inject-port"
                    else f"Tests import or patch the {owner} owner directly"
                ),
            }
        )
    return entries, errors


def compare(inventory: dict[str, Any], actual: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if inventory.get("schemaVersion") != 1:
        errors.append("facade inventory schemaVersion must be 1")
    expected_entries = inventory.get("entries")
    if not isinstance(expected_entries, list):
        return [*errors, "facade inventory entries must be a list"]
    expected = {entry.get("path"): entry for entry in expected_entries}
    observed = {entry["path"]: entry for entry in actual}
    if len(expected) != len(expected_entries):
        errors.append("facade inventory contains duplicate or empty paths")
    for path in sorted(set(observed) - set(expected)):
        errors.append(f"new facade patch path: {path}")
    for path, entry in observed.items():
        baseline = expected.get(path)
        if baseline is None:
            continue
        if entry["referenceCount"] > baseline.get("referenceCount", -1):
            errors.append(
                f"facade patch count increased: {path} "
                f"{baseline.get('referenceCount')} -> {entry['referenceCount']}"
            )
        for key in ("terminalOwner", "targetPhase", "classification", "removalCondition"):
            if entry.get(key) != baseline.get(key):
                errors.append(
                    f"facade patch metadata changed: {path} {key} "
                    f"{baseline.get(key)!r} -> {entry.get(key)!r}"
                )
    for entry in expected_entries:
        for key in ("terminalOwner", "targetPhase", "classification", "removalCondition"):
            if not str(entry.get(key) or "").strip():
                errors.append(f"{entry.get('path')}: {key} is required")
    if sum(item["referenceCount"] for item in actual) > inventory.get("totalReferences", -1):
        errors.append("total facade patch count increased")
    return errors


def payload() -> dict[str, Any]:
    entries, extraction_errors = extract()
    counts = Counter(entry["targetPhase"] for entry in entries for _ in range(entry["referenceCount"]))
    return {
        "schemaVersion": 1,
        "sourceGlob": "tests/**/*.py",
        "facadePrefixes": list(FACADE_PREFIXES),
        "totalPaths": len(entries),
        "totalReferences": sum(entry["referenceCount"] for entry in entries),
        "referencesByPhase": dict(sorted(counts.items())),
        "entries": entries,
        "extractionErrors": extraction_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    current = payload()
    if current["extractionErrors"]:
        print("\n".join(current["extractionErrors"]))
        return 1
    if args.write:
        args.inventory.parent.mkdir(parents=True, exist_ok=True)
        args.inventory.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        print(args.inventory)
        return 0
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    errors = compare(inventory, current["entries"])
    if errors:
        print("\n".join(errors))
        return 1
    print(
        f"facade patch inventory monotonic: {current['totalPaths']} paths, "
        f"{current['totalReferences']} references"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
