"""Verified controls for audited Codex background features.

Only public, read-backable configuration is changed here.  A local policy is
never presented as proof that Codex stopped issuing requests.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping
import webbrowser

from .core.background_usage import BACKGROUND_FEATURE_LABELS, UNKNOWN_FEATURE_KEY
from .platforms.codex_theme import read_codex_config


CAPABILITIES = {
    "memory_consolidation": "hard_supported",
    "context_suggestions": "requires_user_action",
    "suggestion_safety": "linked_supported",
    "title_description": "unsupported",
    "description_refresh": "unsupported",
    UNKNOWN_FEATURE_KEY: "unknown",
}
_DISABLEABLE = frozenset({"memory_consolidation", "context_suggestions", "suggestion_safety"})


def _parse_utc_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stale_processes(processes: object, effective_at: object) -> list[object]:
    boundary = _parse_utc_timestamp(effective_at)
    values = list(processes) if isinstance(processes, (list, tuple)) else []
    if boundary is None:
        return values
    stale: list[object] = []
    for process in values:
        started_at = _parse_utc_timestamp(getattr(process, "started_at", ""))
        if started_at is None or started_at <= boundary:
            stale.append(process)
    return stale


def _process_pids(processes: object) -> list[int]:
    values = list(processes) if isinstance(processes, (list, tuple)) else []
    result: set[int] = set()
    for process in values:
        try:
            pid = int(getattr(process, "pid", 0) or 0)
        except (TypeError, ValueError):
            continue
        if pid > 0:
            result.add(pid)
    return sorted(result)


def default_memory_restart_probe(effective_at: object = "") -> dict[str, object]:
    """Describe whether a Codex restart is needed and safe to automate.

    The Memories setting is read by the Codex process at startup.  A config
    read-back therefore cannot prove that an already-running process changed
    behaviour.  Desktop can be restarted through the existing audited
    lifecycle helper, but standalone CLI processes are intentionally never
    terminated by the HUD.  The probe fails closed when process discovery is
    unavailable.
    """
    try:
        from .codex_app_runtime import (
            audited_running_codex_desktop_processes,
            running_standalone_codex_cli_processes,
        )

        cli_processes = tuple(running_standalone_codex_cli_processes())
        desktop_processes = tuple(audited_running_codex_desktop_processes())
        if str(effective_at or "").strip():
            stale_cli = tuple(_stale_processes(cli_processes, effective_at))
            stale_desktop = tuple(_stale_processes(desktop_processes, effective_at))
            if stale_cli:
                cli_pids = _process_pids(stale_cli)
                return {
                    "required": True,
                    "available": False,
                    "code": "standalone_cli_running",
                    "message": (
                        "检测到设置写入前仍在运行的 Codex CLI 进程（"
                        + ", ".join(str(pid) for pid in cli_pids)
                        + "）。HUD 不会强制终止 CLI 会话，请手动退出并重新启动。"
                    ),
                    "cliPids": cli_pids,
                }
            if stale_desktop:
                return {
                    "required": True,
                    "available": True,
                    "code": "desktop_restart_available",
                    "message": "检测到设置写入前仍在运行的 Codex Desktop 进程，需要重启后才能读取新的 Memories 设置。",
                    "desktopPids": _process_pids(stale_desktop),
                }
            if cli_processes or desktop_processes:
                return {
                    "required": False,
                    "available": False,
                    "code": "process_restart_verified",
                    "message": "已检测到设置写入后启动的 Codex 进程；新的 Memories 配置已被读取。",
                    "cliPids": _process_pids(cli_processes),
                    "desktopPids": _process_pids(desktop_processes),
                }
            return {
                "required": False,
                "available": False,
                "code": "no_codex_process_running",
                "message": "没有检测到运行中的 Codex 进程；新的进程启动时会读取该设置。",
            }

        cli_pids = _process_pids(cli_processes)
        if cli_processes:
            return {
                "required": True,
                "available": False,
                "code": "standalone_cli_running",
                "message": (
                    "检测到仍在运行的 Codex CLI 进程（"
                    + ", ".join(str(pid) for pid in cli_pids)
                    + "）。HUD 不会强制终止 CLI 会话，请手动退出并重新启动。"
                ),
                "cliPids": list(cli_pids),
            }
    except Exception as exc:
        return {
            "required": True,
            "available": False,
            "code": "process_audit_unavailable",
            "message": f"无法确认运行中的 Codex 进程，不能保证新的 Memories 设置已生效：{exc}",
        }

    if desktop_processes:
        return {
            "required": True,
            "available": True,
            "code": "desktop_restart_available",
            "message": "当前 Codex Desktop 进程需要重启才能读取新的 Memories 设置。",
        }
    return {
        "required": False,
        "available": False,
        "code": "no_codex_process_running",
        "message": "没有检测到运行中的 Codex 进程；新的进程启动时会读取该设置。",
    }


def default_memory_restart(effective_at: object = "") -> dict[str, object]:
    """Restart Codex Desktop only when the process audit says it is safe."""
    status = default_memory_restart_probe(effective_at=effective_at)
    if not bool(status.get("required")):
        return {"ok": True, "verified": True, "code": str(status.get("code") or "")}
    if not bool(status.get("available")):
        return {
            "ok": False,
            "verified": False,
            "code": str(status.get("code") or "restart_unavailable"),
            "message": str(status.get("message") or "当前没有可安全执行的 Codex 重启通道。"),
        }
    try:
        from .codex_app_runtime import restart_codex_app

        restarted = bool(restart_codex_app(debugger=False))
    except Exception as exc:
        return {
            "ok": False,
            "verified": False,
            "code": "restart_failed",
            "message": f"Codex 重启失败：{exc}",
        }
    if not restarted:
        return {
            "ok": False,
            "verified": False,
            "code": "restart_failed",
            "message": "Codex 重启未完成，不能确认新的 Memories 设置已经生效。",
        }
    return {
        "ok": True,
        "verified": True,
        "code": "desktop_restart_verified",
        "message": "Codex 已重启并重新读取 Memories 设置。",
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_codex_config_path() -> Path:
    home = str(os.environ.get("CODEX_HOME") or "").strip()
    return (Path(home).expanduser() if home else Path.home() / ".codex") / "config.toml"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _set_features_memories(text: str, enabled: bool) -> str:
    """Replace only ``[features].memories``, preserving other TOML verbatim."""
    lines = text.splitlines(keepends=True)
    section_start = None
    section_end = len(lines)
    for index, line in enumerate(lines):
        if line.strip() == "[features]":
            section_start = index
            for tail in range(index + 1, len(lines)):
                if lines[tail].lstrip().startswith("["):
                    section_end = tail
                    break
            break
    value = "true" if enabled else "false"
    if section_start is None:
        suffix = "" if not text or text.endswith(("\n", "\r")) else "\n"
        return f"{text}{suffix}\n[features]\nmemories = {value}\n"
    for index in range(section_start + 1, section_end):
        raw = lines[index]
        before_comment = raw.split("#", 1)[0]
        key_text, separator, _ = before_comment.partition("=")
        if separator and key_text.strip() == "memories":
            indent = raw[: len(raw) - len(raw.lstrip())]
            comment = raw[raw.find("#") :].rstrip("\r\n") if "#" in raw else ""
            newline = "\r\n" if raw.endswith("\r\n") else "\n"
            lines[index] = f"{indent}memories = {value}{(' ' + comment) if comment else ''}{newline}"
            return "".join(lines)
    lines.insert(section_end, f"memories = {value}\n")
    return "".join(lines)


@dataclass(frozen=True)
class BackgroundPolicy:
    feature_key: str
    desired_state: str = "enabled"
    capability: str = "unknown"
    effective_at: str = ""
    policy_revision: int = 0
    last_attempt_at: str = ""
    last_verified_at: str = ""
    verification_state: str = ""
    adapter_id: str = ""
    adapter_version: str = "1"
    external_state_fingerprint: str = ""
    last_error_code: str = ""
    last_error_message: str = ""
    source: str = "usage_detail"
    requires_restart: bool = False
    restart_available: bool = False
    effective_state: str = ""

    def response(self) -> dict[str, object]:
        message = self.last_error_message
        if not message and self.requires_restart:
            message = (
                "Memories 设置已写入；当前 Codex 进程仍在使用旧配置，需要重启后才能生效。"
            )
        effective_state = self.effective_state
        if effective_state not in {"enabled", "disabled"}:
            if self.verification_state == "verified":
                effective_state = self.desired_state
            elif self.requires_restart and self.desired_state in {"enabled", "disabled"}:
                effective_state = "enabled" if self.desired_state == "disabled" else "disabled"
            else:
                effective_state = "enabled"
        return {
            "featureKey": self.feature_key, "capability": self.capability,
            "desiredState": self.desired_state,
            "effectiveState": effective_state,
            "verificationState": self.verification_state or "pending",
            "policyRevision": self.policy_revision,
            "adapterId": self.adapter_id,
            "externalState": self.external_state_fingerprint,
            "message": message,
            "requiresRestart": bool(self.requires_restart),
            "restartAvailable": bool(self.restart_available),
        }


class BackgroundControlService:
    """Policy persistence and adapter dispatch owned by the Python runtime."""

    def __init__(self, storage_dir: str | Path, *, codex_config_path: str | Path | None = None,
                 open_settings: Callable[[], bool] | None = None,
                 restart_probe: Callable[[], object] | None = None,
                 restart_codex: Callable[[], object] | None = None) -> None:
        self.storage_dir = Path(storage_dir)
        self.policy_path = self.storage_dir / "background-policies.json"
        self.audit_path = self.storage_dir / "background-policy-audit.jsonl"
        self.codex_config_path = Path(codex_config_path) if codex_config_path else default_codex_config_path()
        self._open_settings = open_settings or (lambda: bool(webbrowser.open("codex://settings")))
        self._restart_probe = restart_probe
        self._restart_codex = restart_codex

    def query(self, feature_key: object, event_id: object = "") -> dict[str, object]:
        key = self._key(feature_key)
        policies = self._load()
        policy = policies.get(key) or self._default(key)
        policy = self._refresh_loaded_policy(policies, key, policy, event_id)
        return self._response(policy, "policyQuery", event_id)

    def set(self, feature_key: object, desired_state: object, expected_revision: object = None,
            event_id: object = "", source: object = "usage_detail", restart_now: object = False) -> dict[str, object]:
        key, desired = self._key(feature_key), str(desired_state or "").strip()
        if desired not in {"enabled", "disabled"}:
            return self._failed(key, "invalid_state", "desiredState 必须是 enabled 或 disabled。")
        policies = self._load()
        current = policies.get(key) or self._default(key)
        current = self._refresh_loaded_policy(policies, key, current, event_id)
        if expected_revision not in (None, "") and int(expected_revision) != current.policy_revision:
            return self._failed(key, "revision_conflict", "控制状态已变化，请刷新后重试。", current)
        if key not in _DISABLEABLE:
            return self._unsupported(current)
        linked_key = "context_suggestions" if key == "suggestion_safety" else key
        if linked_key == "memory_consolidation":
            if bool(restart_now):
                return self.restart(key, current, expected_revision, event_id, source)
            return self._apply_memories(policies, current, desired, event_id, source)
        return self._apply_native_guidance(policies, current, desired, event_id, source, linked_key)

    def restart(
        self,
        feature_key: object,
        current: BackgroundPolicy | None = None,
        expected_revision: object = None,
        event_id: object = "",
        source: object = "usage_detail",
    ) -> dict[str, object]:
        """Restart a pending Memories policy, then verify the transition.

        This is deliberately separate from writing the config.  A refused or
        unavailable restart leaves the policy configured-but-unverified and
        never reports the requested state as effective before verification.
        """
        key = self._key(feature_key)
        policies = self._load()
        policy = current or policies.get(key) or self._default(key)
        policy = self._refresh_loaded_policy(policies, key, policy, event_id)
        if expected_revision not in (None, "") and int(expected_revision) != policy.policy_revision:
            return self._failed(key, "revision_conflict", "控制状态已变化，请刷新后重试。", policy)
        if key != "memory_consolidation" or policy.desired_state not in {"disabled", "enabled"}:
            return self._failed(key, "restart_not_required", "当前后台任务没有等待中的 Memories 重启。", policy)
        if not policy.requires_restart:
            result = self._response(policy, "policyApply", event_id)
            result.update({"kind": "policyApply", "evidence": "no_restart_required", "error": "", "restartAttempted": False})
            return result
        if not policy.restart_available or self._restart_codex is None:
            message = policy.last_error_message or "设置已写入，但当前没有可安全执行的 Codex 重启通道；请手动退出并重新启动 Codex。"
            result = self._response(policy, "policyApply", event_id)
            result.update({
                "kind": "policyApply",
                "evidence": "config_readback",
                "error": {"code": "restart_unavailable", "message": message},
                "message": message,
                "restartAttempted": True,
            })
            return result
        try:
            raw_restart = self._restart_codex(effective_at=policy.effective_at)
        except TypeError:
            raw_restart = self._restart_codex()
        outcome = self._restart_outcome(raw_restart)
        if not outcome["ok"] or not outcome["verified"]:
            message = str(outcome["message"] or "Codex 重启未完成，不能确认新的 Memories 设置已经生效。")
            failed_policy = BackgroundPolicy(
                policy.feature_key,
                policy.desired_state,
                policy.capability,
                policy.effective_at,
                policy.policy_revision + 1,
                _utc_now(),
                policy.last_verified_at,
                "configured_unverified",
                policy.adapter_id,
                policy.adapter_version,
                policy.external_state_fingerprint,
                str(outcome["code"] or "restart_failed"),
                message,
                policy.source,
                True,
                bool(policy.restart_available),
                policy.response()["effectiveState"],
            )
            policies[key] = failed_policy
            self._save(policies)
            self._audit(failed_policy, event_id, "restart_failed")
            result = self._response(failed_policy, "policyApply", event_id)
            result.update({
                "kind": "policyApply",
                "evidence": "config_readback",
                "error": {"code": failed_policy.last_error_code, "message": message},
                "message": message,
                "restartAttempted": True,
            })
            return result
        verified_policy = BackgroundPolicy(
            policy.feature_key,
            policy.desired_state,
            policy.capability,
            policy.effective_at,
            policy.policy_revision + 1,
            _utc_now(),
            _utc_now(),
            "verified",
            policy.adapter_id,
            policy.adapter_version,
            policy.external_state_fingerprint,
            "",
            "",
            policy.source,
            False,
            False,
            policy.desired_state,
        )
        policies[key] = verified_policy
        self._save(policies)
        self._audit(verified_policy, event_id, "config_readback_process_restart")
        result = self._response(verified_policy, "policyApply", event_id)
        result.update({
            "kind": "policyApply",
            "evidence": "config_readback_process_restart",
            "error": "",
            "message": (
                "已重启 Codex 并验证 Memories 设置，后续“记忆整理”请求将被停止。"
                if verified_policy.desired_state == "disabled"
                else "已重启 Codex 并验证 Memories 设置，后续记忆功能已恢复。"
            ),
            "restartAttempted": True,
        })
        return result

    def _apply_memories(self, policies: dict[str, BackgroundPolicy], current: BackgroundPolicy, desired: str, event_id: object, source: object) -> dict[str, object]:
        try:
            before = self.codex_config_path.read_text(encoding="utf-8") if self.codex_config_path.exists() else ""
            candidate = _set_features_memories(before, desired == "enabled")
            _atomic_write(self.codex_config_path, candidate)
            features = read_codex_config(self.codex_config_path).get("features", {})
            actual = features.get("memories") if isinstance(features, Mapping) else None
            if actual != (desired == "enabled"):
                raise RuntimeError("配置读回值与请求状态不一致")
        except Exception as exc:
            action = "禁用" if desired == "disabled" else "启用"
            return self._failed(current.feature_key, "config_write_failed", f"{action}失败，现有配置未改变。{exc}", current)
        # Mark the boundary after the atomic write/read-back. A process created
        # while the file was being replaced must still be treated as old.
        effective_at = _utc_now()
        restart_status = self._memory_restart_status(effective_at)
        requires_restart = bool(restart_status["required"])
        restart_available = bool(restart_status["available"])
        restart_message = str(restart_status["message"] or "")
        next_policy = BackgroundPolicy(current.feature_key, desired, current.capability, effective_at, current.policy_revision + 1,
            _utc_now(), "", "configured_unverified", "memories_toml", "1", f"features.memories={str(actual).lower()}", "", restart_message if requires_restart else "", str(source or "usage_detail"), requires_restart, restart_available, str(current.response()["effectiveState"]))
        policies[next_policy.feature_key] = next_policy
        self._save(policies)
        self._audit(next_policy, event_id, "config_readback")
        result = self._response(next_policy, "policyApply", event_id)
        if next_policy.requires_restart:
            message = restart_message or (
                "Memories 设置已成功写入；当前 Codex 进程仍在使用旧配置，需要重启后才能生效。"
            )
        else:
            message = "设置已写入，但尚未获得足够证据验证实际生效状态。"
        result.update({"kind": "policyApply", "evidence": "config_readback", "error": "", "message": message, "restartAttempted": False})
        return result

    def _memory_restart_status(self, effective_at: object = "") -> dict[str, object]:
        if self._restart_probe is None:
            return {"required": False, "available": False, "code": "restart_probe_unconfigured", "message": ""}
        try:
            try:
                raw = self._restart_probe(effective_at=effective_at)
            except TypeError:
                raw = self._restart_probe()
        except Exception as exc:
            return {"required": True, "available": False, "code": "process_audit_unavailable", "message": f"无法确认运行中的 Codex 进程，不能保证新的 Memories 设置已生效：{exc}"}
        if isinstance(raw, Mapping):
            return {
                "required": bool(raw.get("required")),
                "available": bool(raw.get("available")),
                "code": str(raw.get("code") or ""),
                "message": str(raw.get("message") or ""),
            }
        required = bool(raw)
        return {"required": required, "available": required and self._restart_codex is not None, "code": "restart_required" if required else "", "message": "当前 Codex 进程需要重启才能读取新的 Memories 设置。" if required else ""}

    def _refresh_restart_requirement(self, policy: BackgroundPolicy) -> BackgroundPolicy:
        """Migrate old configured policies when a live process needs restart."""
        if (
            policy.feature_key != "memory_consolidation"
            or policy.desired_state not in {"disabled", "enabled"}
            or policy.verification_state != "configured_unverified"
            or self._restart_probe is None
        ):
            return policy
        status = self._memory_restart_status(policy.effective_at)
        required = bool(status["required"])
        available = bool(status["available"])
        message = str(status["message"] or "")
        if (
            not required
            and status["code"] == "process_restart_verified"
            and self._memories_config_matches(policy.desired_state)
        ):
            return replace(
                policy,
                last_verified_at=policy.last_verified_at or _utc_now(),
                verification_state="verified",
                last_error_code="",
                last_error_message="",
                requires_restart=False,
                restart_available=False,
                effective_state=policy.desired_state,
            )
        if (
            bool(policy.requires_restart) == required
            and bool(policy.restart_available) == available
            and (not required or policy.last_error_message == message)
        ):
            return policy
        return replace(
            policy,
            requires_restart=required,
            restart_available=available,
            last_error_message=message if required else policy.last_error_message,
        )

    def _refresh_loaded_policy(
        self,
        policies: dict[str, BackgroundPolicy],
        key: str,
        policy: BackgroundPolicy,
        event_id: object = "",
    ) -> BackgroundPolicy:
        refreshed = self._refresh_restart_requirement(policy)
        if key not in policies or refreshed == policy:
            return refreshed
        policies[key] = refreshed
        self._save(policies)
        if (
            policy.verification_state != "verified"
            and refreshed.verification_state == "verified"
        ):
            self._audit(refreshed, event_id, "process_restart_detected")
        return refreshed

    def _memories_config_matches(self, desired_state: str) -> bool:
        try:
            features = read_codex_config(self.codex_config_path).get("features", {})
        except Exception:
            return False
        actual = features.get("memories") if isinstance(features, Mapping) else None
        return actual == (str(desired_state or "") == "enabled")

    @staticmethod
    def _restart_outcome(raw: object) -> dict[str, object]:
        if isinstance(raw, Mapping):
            ok = bool(raw.get("ok"))
            return {"ok": ok, "verified": bool(raw.get("verified", ok)), "code": str(raw.get("code") or ""), "message": str(raw.get("message") or "")}
        ok = bool(raw)
        return {"ok": ok, "verified": ok, "code": "desktop_restart_verified" if ok else "restart_failed", "message": ""}

    def _apply_native_guidance(self, policies: dict[str, BackgroundPolicy], current: BackgroundPolicy, desired: str, event_id: object, source: object, linked_key: str) -> dict[str, object]:
        opened = False
        try:
            opened = self._open_settings()
        except Exception:
            opened = False
        next_policy = BackgroundPolicy(current.feature_key, desired, current.capability, _utc_now(), current.policy_revision + 1,
            _utc_now(), "", "requires_user_action", "desktop_suggested_prompts", "1", "settings_opened" if opened else "settings_unavailable", "", "", str(source or "usage_detail"), False, False, str(current.response()["effectiveState"]))
        policies[next_policy.feature_key] = next_policy
        if current.feature_key == "suggestion_safety":
            policies[linked_key] = BackgroundPolicy(linked_key, desired, CAPABILITIES[linked_key], next_policy.effective_at, next_policy.policy_revision, next_policy.last_attempt_at, "", "requires_user_action", next_policy.adapter_id, "1", next_policy.external_state_fingerprint, "", "", next_policy.source)
        self._save(policies)
        self._audit(next_policy, event_id, "native_settings_guidance")
        result = self._response(next_policy, "policyApply", event_id)
        action = "关闭" if desired == "disabled" else "启用"
        if opened:
            message = f"Codex 设置已打开。请在其中{action} Suggested prompts；HUD 当前无法自动读取该开关状态。"
            error: str | dict[str, str] = ""
        else:
            message = f"未能打开 Codex 设置。请手动进入设置并{action} Suggested prompts。"
            error = {"code": "settings_open_failed", "message": message}
        result.update({"kind": "policyApply", "evidence": "native_settings_guidance", "error": error, "message": message})
        return result

    def _default(self, key: str) -> BackgroundPolicy:
        return BackgroundPolicy(key, capability=CAPABILITIES.get(key, "unknown"), verification_state="unsupported" if CAPABILITIES.get(key, "unknown") in {"unsupported", "unknown"} else "pending")

    def _response(
        self,
        policy: BackgroundPolicy,
        kind: str,
        event_id: object = "",
    ) -> dict[str, object]:
        key = policy.feature_key
        payload = policy.response()
        payload.update({
            "kind": kind,
            "canDisable": key in _DISABLEABLE and policy.capability != "unknown",
            "canEnable": policy.desired_state == "disabled" and key in _DISABLEABLE,
            "requiresUserAction": policy.capability == "requires_user_action",
            "eventId": str(event_id or ""),
        })
        if policy.capability in {"unsupported", "unknown"}:
            payload["message"] = "当前 Codex 版本未发现可验证的官方关闭接口。HUD 仍会记录用量并在产生新请求时告警。"
        return payload

    def _key(self, feature_key: object) -> str:
        key = str(feature_key or "").strip()
        return key if key in BACKGROUND_FEATURE_LABELS else UNKNOWN_FEATURE_KEY

    def _load(self) -> dict[str, BackgroundPolicy]:
        try:
            raw = json.loads(self.policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        result = {}
        for key, value in (raw.items() if isinstance(raw, Mapping) else []):
            if isinstance(value, Mapping):
                allowed = {
                    field: value[field]
                    for field in BackgroundPolicy.__dataclass_fields__
                    if field in value
                }
                result[str(key)] = BackgroundPolicy(**allowed)
        return result

    def _save(self, policies: Mapping[str, BackgroundPolicy]) -> None:
        payload = {key: vars(value) for key, value in policies.items()}
        _atomic_write(self.policy_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def _audit(self, policy: BackgroundPolicy, event_id: object, evidence: str) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        entry = {"action_id": f"{policy.feature_key}:{policy.policy_revision}", "feature_key": policy.feature_key, "event_id": str(event_id or ""), "requested_state": policy.desired_state, "capability": policy.capability, "started_at": policy.last_attempt_at, "completed_at": _utc_now(), "verification_state": policy.verification_state, "evidence_kind": evidence, "error_code": policy.last_error_code}
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _failed(self, key: str, code: str, message: str, current: BackgroundPolicy | None = None) -> dict[str, object]:
        payload = self._response(current or self._default(key), "policyApply")
        payload.update({"verificationState": "failed", "error": {"code": code, "message": message}, "message": message})
        return payload

    def _unsupported(self, policy: BackgroundPolicy) -> dict[str, object]:
        payload = self._response(policy, "policyApply")
        payload.update({"verificationState": "unsupported", "error": "", "message": "当前 Codex 版本未发现可验证的官方关闭接口。HUD 仍会记录用量并在产生新请求时告警。"})
        return payload


__all__ = [
    "BackgroundControlService",
    "BackgroundPolicy",
    "CAPABILITIES",
    "default_codex_config_path",
    "default_memory_restart",
    "default_memory_restart_probe",
]
