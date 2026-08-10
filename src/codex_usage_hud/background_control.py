"""Verified controls for audited Codex background features.

Only public, read-backable configuration is changed here.  A local policy is
never presented as proof that Codex stopped issuing requests.
"""

from __future__ import annotations

from dataclasses import dataclass
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
        if before_comment.strip().startswith("memories") and "=" in before_comment:
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

    def response(self) -> dict[str, object]:
        return {
            "featureKey": self.feature_key, "capability": self.capability,
            "desiredState": self.desired_state,
            "effectiveState": "disabled" if self.verification_state == "verified" and self.desired_state == "disabled" else "enabled",
            "verificationState": self.verification_state or "pending",
            "policyRevision": self.policy_revision,
            "adapterId": self.adapter_id,
            "externalState": self.external_state_fingerprint,
            "message": self.last_error_message,
        }


class BackgroundControlService:
    """Policy persistence and adapter dispatch owned by the Python runtime."""

    def __init__(self, storage_dir: str | Path, *, codex_config_path: str | Path | None = None,
                 open_settings: Callable[[], bool] | None = None) -> None:
        self.storage_dir = Path(storage_dir)
        self.policy_path = self.storage_dir / "background-policies.json"
        self.audit_path = self.storage_dir / "background-policy-audit.jsonl"
        self.codex_config_path = Path(codex_config_path) if codex_config_path else default_codex_config_path()
        self._open_settings = open_settings or (lambda: bool(webbrowser.open("codex://settings")))

    def query(self, feature_key: object, event_id: object = "") -> dict[str, object]:
        key = self._key(feature_key)
        policy = self._load().get(key) or self._default(key)
        return self._response(policy, "policyQuery", event_id)

    def set(self, feature_key: object, desired_state: object, expected_revision: object = None,
            event_id: object = "", source: object = "usage_detail") -> dict[str, object]:
        key, desired = self._key(feature_key), str(desired_state or "").strip()
        if desired not in {"enabled", "disabled"}:
            return self._failed(key, "invalid_state", "desiredState 必须是 enabled 或 disabled。")
        policies = self._load()
        current = policies.get(key) or self._default(key)
        if expected_revision not in (None, "") and int(expected_revision) != current.policy_revision:
            return self._failed(key, "revision_conflict", "控制状态已变化，请刷新后重试。", current)
        if key not in _DISABLEABLE:
            return self._unsupported(current)
        linked_key = "context_suggestions" if key == "suggestion_safety" else key
        if linked_key == "memory_consolidation":
            return self._apply_memories(policies, current, desired, event_id, source)
        return self._apply_native_guidance(policies, current, desired, event_id, source, linked_key)

    def _apply_memories(self, policies: dict[str, BackgroundPolicy], current: BackgroundPolicy, desired: str, event_id: object, source: object) -> dict[str, object]:
        try:
            before = self.codex_config_path.read_text(encoding="utf-8") if self.codex_config_path.exists() else ""
            candidate = _set_features_memories(before, desired == "enabled")
            _atomic_write(self.codex_config_path, candidate)
            features = read_codex_config(self.codex_config_path).get("features", {})
            actual = features.get("memories") if isinstance(features, Mapping) else None
            if actual is not (desired == "enabled"):
                raise RuntimeError("配置读回值与请求状态不一致")
        except Exception as exc:
            return self._failed(current.feature_key, "config_write_failed", f"禁止失败，现有配置未改变。{exc}", current)
        next_policy = BackgroundPolicy(current.feature_key, desired, current.capability, _utc_now(), current.policy_revision + 1,
            _utc_now(), "", "configured_unverified", "memories_toml", "1", f"features.memories={str(actual).lower()}", "", "", str(source or "usage_detail"))
        policies[next_policy.feature_key] = next_policy
        self._save(policies)
        self._audit(next_policy, event_id, "config_readback")
        result = self._response(next_policy, "policyApply", event_id)
        result.update({"kind": "policyApply", "evidence": "config_readback", "error": "", "message": "设置已写入，尚未获得足够的后台日志证据；不会把它标记为已禁止。"})
        return result

    def _apply_native_guidance(self, policies: dict[str, BackgroundPolicy], current: BackgroundPolicy, desired: str, event_id: object, source: object, linked_key: str) -> dict[str, object]:
        opened = False
        try:
            opened = self._open_settings()
        except Exception:
            opened = False
        next_policy = BackgroundPolicy(current.feature_key, desired, current.capability, _utc_now(), current.policy_revision + 1,
            _utc_now(), "", "requires_user_action", "desktop_suggested_prompts", "1", "settings_opened" if opened else "settings_unavailable", "", "", str(source or "usage_detail"))
        policies[next_policy.feature_key] = next_policy
        if current.feature_key == "suggestion_safety":
            policies[linked_key] = BackgroundPolicy(linked_key, desired, CAPABILITIES[linked_key], next_policy.effective_at, next_policy.policy_revision, next_policy.last_attempt_at, "", "requires_user_action", next_policy.adapter_id, "1", next_policy.external_state_fingerprint, "", "", next_policy.source)
        self._save(policies)
        self._audit(next_policy, event_id, "native_settings_guidance")
        result = self._response(next_policy, "policyApply", event_id)
        result.update({"kind": "policyApply", "evidence": "native_settings_guidance", "error": "", "message": "请在 Codex 设置中关闭 Suggested prompts，完成后返回此处验证。"})
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
                allowed = {field: value.get(field) for field in BackgroundPolicy.__dataclass_fields__}
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


__all__ = ["BackgroundControlService", "BackgroundPolicy", "CAPABILITIES", "default_codex_config_path"]
