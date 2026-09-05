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
import time
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
        key_text, separator, _ = before_comment.partition("=")
        if separator and key_text.strip() == "memories":
            indent = raw[: len(raw) - len(raw.lstrip())]
            comment = raw[raw.find("#") :].rstrip("\r\n") if "#" in raw else ""
            newline = "\r\n" if raw.endswith("\r\n") else "\n"
            lines[index] = f"{indent}memories = {value}{(' ' + comment) if comment else ''}{newline}"
            return "".join(lines)
    lines.insert(section_end, f"memories = {value}\n")
    return "".join(lines)


# Feature keys whose value may legitimately be a nested TOML table in some Codex
# versions (e.g. `memories` historically carried generate_memories / use_memories
# sub-keys, and our own writer preserves them verbatim). We must never
# auto-flatten these.
_FEATURE_SUBTABLE_PRESERVE = frozenset({"memories"})


def _parse_features(text: str) -> dict[str, object]:
    """Parse a config *text* into a dict using the shared lenient path.

    ``read_codex_config`` takes a path, so we round-trip through a temp file to
    reuse its full-parser-first / subset-fallback compatibility behaviour.
    """
    import tempfile as _tf
    with _tf.NamedTemporaryFile("w", encoding="utf-8", suffix=".toml", delete=False) as handle:
        handle.write(text)
        tmp = handle.name
    try:
        payload = read_codex_config(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return payload if isinstance(payload, dict) else {}


def _features_block_issues(text: str) -> tuple[list[str], list[str]]:
    """Inspect a Codex config's ``[features]`` block.

    Returns ``(issues, flattenable_keys)``:
    - ``issues``: shapes the *running* Codex is likely to reject (nested
      sub-tables that are not in the preserve set, or a top-level
      ``features = bool`` master flag).
    - ``flattenable_keys``: nested sub-tables whose leaves are all booleans and
      that we can safely collapse to ``<key> = <bool>`` without losing tuning.

    This is a heuristic, not a schema oracle: Codex's exact ``features`` schema
    varies by version, so we only auto-flatten the safe, unambiguous case and
    leave anything richer alone (with a warning) rather than risk clobbering
    user intent.
    """
    features = _parse_features(text).get("features")
    if features is None:
        return [], []
    if not isinstance(features, dict):
        return [f"features 被写成布尔值（{features!r}），当前 Codex 版本期望 [features] 表"], []
    issues: list[str] = []
    flattenable: list[str] = []
    for key, value in features.items():
        if not isinstance(value, dict):
            continue
        if key in _FEATURE_SUBTABLE_PRESERVE:
            continue
        leaves = list(value.values())
        if all(isinstance(v, bool) for v in leaves):
            flattenable.append(key)
        else:
            issues.append(
                f"features.{key} 是带非布尔参数的子表，当前 Codex 版本可能拒绝"
                f"（已原样保留，请按当前 Codex 版本手动修正）"
            )
    return issues, flattenable


def _drop_features_subtable(text: str, key: str) -> str:
    """Remove a ``[features.<key>]`` sub-table block (header + body)."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    dropping = False
    for line in lines:
        stripped = line.strip()
        if dropping:
            if stripped.startswith("["):
                dropping = False
                out.append(line)
            continue
        if stripped == f"[features.{key}]":
            dropping = True
            continue
        out.append(line)
    return "".join(out)


def _set_features_bool_key(text: str, key: str, enabled: bool) -> str:
    """Set ``<key> = <bool>`` inside ``[features]`` (mirrors ``_set_features_memories``)."""
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
        return f"{text}{suffix}\n[features]\n{key} = {value}\n"
    for index in range(section_start + 1, section_end):
        raw = lines[index]
        before_comment = raw.split("#", 1)[0]
        key_text, separator, _ = before_comment.partition("=")
        if separator and key_text.strip() == key:
            indent = raw[: len(raw) - len(raw.lstrip())]
            comment = raw[raw.find("#") :].rstrip("\r\n") if "#" in raw else ""
            newline = "\r\n" if raw.endswith("\r\n") else "\n"
            lines[index] = f"{indent}{key} = {value}{(' ' + comment) if comment else ''}{newline}"
            return "".join(lines)
    lines.insert(section_end, f"{key} = {value}\n")
    return "".join(lines)


def _replace_top_level_features_bool(text: str, value: bool) -> str:
    """Replace a top-level ``features = <bool>`` master flag with a ``[features]`` table."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        before_comment = line.split("#", 1)[0]
        key_text, separator, rest = before_comment.partition("=")
        if separator and key_text.strip() == "features" and rest.strip().lower() in ("true", "false"):
            out.append("[features]\n")
            out.append(f"memories = {rest.strip().lower()}\n")
            continue
        out.append(line)
    return "".join(out)


def _normalize_features_block(text: str) -> tuple[str, list[str]]:
    """Collapse safe ``[features.<key>]`` sub-tables into ``<key> = <bool>``.

    Idempotent and conservative: only bool-leaf sub-tables outside the preserve
    set are flattened, and a top-level ``features = bool`` master flag is
    rewritten as a ``[features]`` table. Returns ``(new_text, changes)``.
    """
    changes: list[str] = []
    working = text
    features = _parse_features(working).get("features")
    if isinstance(features, bool):
        working = _replace_top_level_features_bool(working, features)
        changes.append(f"features = {str(features).lower()} -> [features] 表（memories = {str(features).lower()}）")
    issues, flattenable = _features_block_issues(working)
    for key in flattenable:
        sub = _parse_features(working).get("features", {}).get(key, {})
        enabled = any(bool(v) for v in sub.values()) if isinstance(sub, dict) else False
        working = _drop_features_subtable(working, key)
        working = _set_features_bool_key(working, key, enabled)
        changes.append(f"features.{key} 子表折叠为 {key} = {str(enabled).lower()}")
    return working, changes


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

    def __init__(
        self,
        storage_dir: str | Path,
        *,
        codex_config_path: str | Path | None = None,
        open_settings: Callable[[], bool] | None = None,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.policy_path = self.storage_dir / "background-policies.json"
        self.audit_path = self.storage_dir / "background-policy-audit.jsonl"
        self.codex_config_path = Path(codex_config_path) if codex_config_path else default_codex_config_path()
        self._open_settings = open_settings or (lambda: bool(webbrowser.open("codex://settings")))

    def query(self, feature_key: object, event_id: object = "") -> dict[str, object]:
        key = self._key(feature_key)
        policies = self._load()
        policy = policies.get(key) or self._default(key)
        policy = self._refresh_loaded_policy(policies, key, policy, event_id)
        return self._response(policy, "policyQuery", event_id)

    def set(self, feature_key: object, desired_state: object, expected_revision: object = None,
            event_id: object = "", source: object = "usage_detail") -> dict[str, object]:
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
            return self._apply_memories(policies, current, desired, event_id, source)
        return self._apply_native_guidance(policies, current, desired, event_id, source, linked_key)

    def _apply_memories(self, policies: dict[str, BackgroundPolicy], current: BackgroundPolicy, desired: str, event_id: object, source: object) -> dict[str, object]:
        try:
            before = self.codex_config_path.read_text(encoding="utf-8") if self.codex_config_path.exists() else ""
            changes: list[str] = []
            issues, flattenable = _features_block_issues(before)
            parsed_before = _parse_features(before)
            features_before = parsed_before.get("features") if isinstance(parsed_before, dict) else None
            if flattenable or isinstance(features_before, bool):
                backup_name = f"{self.codex_config_path.name}.bak-{int(time.time())}"
                backup_path = self.codex_config_path.with_name(backup_name)
                try:
                    backup_path.write_text(before, encoding="utf-8")
                except OSError:
                    backup_path = None
                working, changes = _normalize_features_block(before)
                _atomic_write(self.codex_config_path, working)
            else:
                working = before
            candidate = _set_features_memories(working, desired == "enabled")
            _atomic_write(self.codex_config_path, candidate)
            # Success gate: even after our write, the running Codex must still be
            # able to load the [features] block. If an incompatible shape remains
            # (e.g. a structured sub-table we deliberately refused to flatten), do
            # not report false success.
            remaining_issues, _ = _features_block_issues(candidate)
            if remaining_issues:
                return self._failed(
                    current.feature_key,
                    "config_write_failed",
                    f"memories 已写入（{desired}），但配置仍含当前 Codex 版本拒绝的 features 形态："
                    f"{'; '.join(remaining_issues)}。原配置已备份，请按当前 Codex 版本手动修正后再重启。",
                    current,
                )
            features = read_codex_config(self.codex_config_path).get("features", {})
            actual = features.get("memories") if isinstance(features, Mapping) else None
            if actual != (desired == "enabled"):
                raise RuntimeError("配置读回值与请求状态不一致")
        except Exception as exc:
            action = "禁用" if desired == "disabled" else "启用"
            return self._failed(current.feature_key, "config_write_failed", f"{action}失败，现有配置未改变。{exc}", current)
        effective_at = _utc_now()
        next_policy = BackgroundPolicy(current.feature_key, desired, current.capability, effective_at, current.policy_revision + 1,
            _utc_now(), _utc_now(), "verified", "memories_toml", "1", f"features.memories={str(actual).lower()}", "", "", str(source or "usage_detail"), False, False, desired)
        policies[next_policy.feature_key] = next_policy
        self._save(policies)
        self._audit(next_policy, event_id, "config_readback")
        result = self._response(next_policy, "policyApply", event_id)
        action = "已禁用" if desired == "disabled" else "已启用"
        message = (
            f"Memories {action}。设置已写入，HUD 已立即按目标状态更新；"
            "部分 Codex 版本可能需要重启后才会完全采用新配置。"
        )
        if changes:
            message += f"\n（已自动归一化 features：{'；'.join(changes)}；原配置已备份）"
        result.update({"kind": "policyApply", "evidence": "config_readback", "error": "", "message": message})
        return result

    def _refresh_loaded_policy(
        self,
        policies: dict[str, BackgroundPolicy],
        key: str,
        policy: BackgroundPolicy,
        event_id: object = "",
    ) -> BackgroundPolicy:
        refreshed = policy
        if (
            policy.feature_key == "memory_consolidation"
            and policy.verification_state == "configured_unverified"
            and policy.desired_state in {"enabled", "disabled"}
            and self._memories_config_matches(policy.desired_state)
        ):
            refreshed = BackgroundPolicy(
                policy.feature_key,
                policy.desired_state,
                policy.capability,
                policy.effective_at,
                policy.policy_revision,
                policy.last_attempt_at,
                policy.last_verified_at or _utc_now(),
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
        if key not in policies or refreshed == policy:
            return refreshed
        policies[key] = refreshed
        self._save(policies)
        if policy.verification_state != "verified" and refreshed.verification_state == "verified":
            self._audit(refreshed, event_id, "config_readback_migration")
        return refreshed

    def _memories_config_matches(self, desired_state: str) -> bool:
        try:
            features = read_codex_config(self.codex_config_path).get("features", {})
        except Exception:
            return False
        actual = features.get("memories") if isinstance(features, Mapping) else None
        return actual == (str(desired_state or "") == "enabled")

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
]
