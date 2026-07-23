"""Conservative local cleanup inventory and offline maintenance primitives.

The module performs no work during construction.  Callers explicitly scan,
preview, and create a short-lived maintenance plan.  Cleanup payloads may show
the exact local target path for auditability, but mutation authority remains
bound to Python-owned inventory state, opaque IDs, and filesystem fingerprints.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Literal
from urllib.parse import quote

from .codex_file_manager import CodexCleanupCandidate, path_is_locked


CleanupTier = Literal["safe", "consent", "protected"]
CacheMode = Literal["root", "expired_children"]
SQLiteKind = Literal["logs", "background"]
ActionKind = Literal["delete_path", "sqlite"]
PathKind = Literal["file", "directory", "unknown"]

TIERS: tuple[CleanupTier, ...] = ("safe", "consent", "protected")
PLAN_VERSION = 1
RESULT_VERSION = 1
PLAN_FORMAT = "codex-usage-hud-cleanup-plan"
RESULT_FORMAT = "codex-usage-hud-cleanup-result"
DEFAULT_CONFIRMATION_TTL_SECONDS = 5 * 60
DEFAULT_PLAN_TTL_SECONDS = 10 * 60
DEFAULT_PROCESS_WAIT_SECONDS = 30.0
DEFAULT_TEMP_MIN_AGE_SECONDS = 24 * 60 * 60
DEFAULT_DIAGNOSTIC_MIN_AGE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_BACKUP_MIN_AGE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_LOG_RETENTION_HOURS = 24.0
DEFAULT_BACKGROUND_RETENTION_DAYS = 30.0
DEFAULT_VACUUM_MIN_RECLAIM_BYTES = 16 * 1024 * 1024
WINDOWS_REPARSE_ATTRIBUTE = 0x400
MAX_PLAN_BYTES = 2 * 1024 * 1024
MAX_RESTART_ARGUMENTS = 64
MAX_RESTART_ARGUMENT_LENGTH = 4096
MAX_RESTART_COMMAND_LENGTH = 32_768

_HUD_LOG_BASES = {
    "crash.log",
    "renderer_fallback.log",
    "daemon.log",
    "window_tracker.log",
    "hud_geometry.log",
}
_HUD_EXACT_AUDIT_FILES = {"work-overlay-transitions.jsonl"}
_HUD_PROTECTED_NAMES = {
    "hud_settings.json",
    "renderer_cdp_state.json",
    "background-usage.sqlite3",
    "background-usage.sqlite3-wal",
    "background-usage.sqlite3-shm",
}
_OVERLAY_COMMAND_RE = re.compile(
    r"^work-overlay-(?P<pid>[1-9][0-9]*)-[0-9]+-commands\.jsonl$",
    re.IGNORECASE,
)
_OLD_LOG_BACKUP_RE = re.compile(
    r"^(?:logs_2\.sqlite|background-usage\.sqlite3)\.pre-cleanup-"
    r"[A-Za-z0-9][A-Za-z0-9._-]*$",
    re.IGNORECASE,
)
_SAFE_PLAN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")

_LOGS_COLUMNS = {
    "id",
    "ts",
    "ts_nanos",
    "level",
    "target",
    "feedback_log_body",
    "module_path",
    "file",
    "line",
    "thread_id",
    "process_uuid",
    "estimated_bytes",
}
_LOGS_ALLOWED_TABLES = {"logs", "_sqlx_migrations"}
_LOGS_MIGRATION_COLUMNS = {
    "version",
    "description",
    "installed_on",
    "success",
    "checksum",
    "execution_time",
}
_LOGS_INDEXES = {
    "idx_logs_ts": (("ts", "ts_nanos", "id"), False),
    "idx_logs_thread_id": (("thread_id",), False),
    "idx_logs_thread_id_ts": (("thread_id", "ts", "ts_nanos", "id"), False),
    "idx_logs_process_uuid_threadless_ts": (
        ("process_uuid", "ts", "ts_nanos", "id"),
        True,
    ),
}
_SQLITE_RELATED_PROCESSES = ("codex", "codex-cli")
_BACKGROUND_REQUIRED_COLUMNS = {
    "metadata": {"key", "value"},
    "scan_state": {"source_key", "last_log_id", "initialized_at", "updated_at"},
    "process_evidence": {"process_uuid", "app_evidence", "last_seen_at"},
    "background_events": {"event_id", "last_seen_at"},
    "background_requests": {"request_id", "event_id", "occurred_at"},
}
_BACKGROUND_ALLOWED_TABLES = set(_BACKGROUND_REQUIRED_COLUMNS)
_BACKGROUND_SCHEMA_VERSIONS = frozenset({"1", "2"})

# Fixed-weight scan phases for progressive UI. Values sum to 100; live progress
# stays at most 99 until the final completed/preview snapshot.
SCAN_PHASE_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("hud", 12),
    ("codex", 16),
    ("processes", 8),
    ("caches", 40),
    ("backups", 12),
    ("sqlite", 12),
)
SCAN_PHASE_COUNT = len(SCAN_PHASE_WEIGHTS)
SCAN_PHASE_LABELS: dict[str, str] = {
    "hud": "HUD diagnostics",
    "codex": "Codex temporary items",
    "processes": "Related application state",
    "caches": "Application and developer caches",
    "backups": "Old cleanup backups",
    "sqlite": "Historical databases",
    "preview": "Default safe preview",
}


class SafeCleanupError(RuntimeError):
    """A cleanup request was rejected before touching unverified data."""


class CleanupPlanError(SafeCleanupError):
    """A versioned maintenance plan is malformed, stale, or unsupported."""


@dataclass(frozen=True)
class CacheDefinition:
    """One exact platform cache/diagnostic root or one expiring parent."""

    key: str
    category: str
    path: Path
    label: str
    impact: str
    related_processes: tuple[str, ...] = ()
    mode: CacheMode = "root"
    min_age_seconds: float = 0.0
    tier: CleanupTier = "safe"

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.category.strip() or not self.label.strip():
            raise ValueError("cache definitions require a key, category, and label")
        if self.tier not in {"safe", "consent"}:
            raise ValueError("platform cleanup definitions must be safe or consent")
        if self.mode not in {"root", "expired_children"}:
            raise ValueError("unsupported cache cleanup mode")
        if float(self.min_age_seconds) < 0:
            raise ValueError("cache retention must be nonnegative")


@dataclass(frozen=True)
class SQLiteTarget:
    """One recognized SQLite database eligible for row-level maintenance."""

    path: Path
    kind: SQLiteKind
    retention_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"logs", "background"}:
            raise ValueError("unsupported SQLite cleanup target")
        if self.retention_seconds is not None and float(self.retention_seconds) <= 0:
            raise ValueError("SQLite retention must be positive")


@dataclass(frozen=True)
class _MeasuredPath:
    size: int
    files: int
    mtime: float
    fingerprint: str
    contains_reparse: bool
    errors: int


@dataclass(frozen=True)
class _SQLiteAudit:
    kind: SQLiteKind
    cutoff: int
    retention_seconds: float
    deletable_rows: int
    total_rows: int
    estimated_bytes: int
    schema_signature: str
    group_fingerprint: str


@dataclass(frozen=True)
class CleanupItem:
    id: str
    category: str
    tier: CleanupTier
    label: str
    size: int
    file_count: int
    impact: str
    retention: str = ""
    requires_offline: bool = False
    requires_backup: bool = False
    requires_codex_close: bool = False
    blocked_reason: str = ""
    related_processes: tuple[str, ...] = ()
    action: str = ""
    modified_at: float = 0.0
    _path: Path | None = field(default=None, repr=False, compare=False)
    _approved_root: Path | None = field(default=None, repr=False, compare=False)
    _fingerprint: str = field(default="", repr=False, compare=False)
    _lstat: tuple[int, int, int, int, int] = field(
        default=(0, 0, 0, 0, 0), repr=False, compare=False
    )
    _sqlite: _SQLiteAudit | None = field(default=None, repr=False, compare=False)

    def to_payload(self) -> dict[str, object]:
        path_text = ""
        path_kind: PathKind = "unknown"
        if self._path is not None:
            path_text = os.fspath(_absolute_path(self._path))
            if self._lstat[2]:
                if stat.S_ISDIR(self._lstat[2]):
                    path_kind = "directory"
                elif stat.S_ISREG(self._lstat[2]):
                    path_kind = "file"
        return {
            "id": self.id,
            "category": self.category,
            "tier": self.tier,
            "label": self.label,
            "bytes": int(self.size),
            "items": 1,
            "files": int(self.file_count),
            "impact": self.impact,
            "retention": self.retention,
            "requiresOffline": bool(self.requires_offline),
            "requiresBackup": bool(self.requires_backup),
            "requiresCodexClose": bool(self.requires_codex_close),
            "blockedReason": self.blocked_reason,
            "relatedProcesses": list(self.related_processes),
            "path": path_text,
            "pathKind": path_kind,
            "modifiedAt": (
                _iso_timestamp(self.modified_at) if self.modified_at > 0 else ""
            ),
        }


@dataclass(frozen=True)
class CleanupInventory:
    revision: str
    generated_at: float
    platform: str
    items: tuple[CleanupItem, ...]

    def to_payload(
        self, operation: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        totals = {
            "reclaimableBytes": 0,
            "consentBytes": 0,
            "protectedBytes": 0,
            "backupBytes": 0,
            "items": 0,
        }
        for item in self.items:
            if item.tier == "safe":
                totals["reclaimableBytes"] += item.size
            elif item.tier == "consent":
                totals["consentBytes"] += item.size
            else:
                totals["protectedBytes"] += item.size
            totals["items"] += 1
        operation_payload = dict(operation or _idle_operation())
        totals["backupBytes"] = max(0, int(operation_payload.get("backupBytes") or 0))
        tier_order = {tier: index for index, tier in enumerate(TIERS)}
        groups = sorted(
            (item.to_payload() for item in self.items),
            key=lambda item: (
                tier_order.get(str(item["tier"]), len(TIERS)),
                -int(item["bytes"]),
                str(item["category"]),
            ),
        )
        return {
            "revision": self.revision,
            "generatedAt": _iso_timestamp(self.generated_at),
            "platform": self.platform,
            "totals": totals,
            "groups": groups,
            "defaultSelectedIds": [
                item.id
                for item in self.items
                if item.tier == "safe"
                and not item.blocked_reason
                and not item.requires_offline
            ],
            "operation": operation_payload,
        }


@dataclass(frozen=True)
class _Confirmation:
    revision: str
    item_ids: tuple[str, ...]
    consent: bool
    backup_directory: Path | None
    expires_at: float


@dataclass(frozen=True)
class MaintenanceAction:
    item_id: str
    kind: ActionKind
    category: str
    tier: CleanupTier
    path: str
    approved_root: str
    fingerprint: str
    lstat: tuple[int, int, int, int, int]
    estimated_bytes: int
    requires_offline: bool = False
    requires_backup: bool = False
    requires_codex_close: bool = False
    allows_growth: bool = False
    related_processes: tuple[str, ...] = ()
    sqlite_kind: str = ""
    cutoff: int = 0
    retention_seconds: float = 0.0
    schema_signature: str = ""
    expected_rows: int = 0
    vacuum_min_bytes: int = DEFAULT_VACUUM_MIN_RECLAIM_BYTES

    def to_dict(self) -> dict[str, object]:
        return {
            "itemId": self.item_id,
            "kind": self.kind,
            "category": self.category,
            "tier": self.tier,
            "path": self.path,
            "approvedRoot": self.approved_root,
            "fingerprint": self.fingerprint,
            "lstat": list(self.lstat),
            "estimatedBytes": int(self.estimated_bytes),
            "requiresOffline": bool(self.requires_offline),
            "requiresBackup": bool(self.requires_backup),
            "requiresCodexClose": bool(self.requires_codex_close),
            "allowsGrowth": bool(self.allows_growth),
            "relatedProcesses": list(self.related_processes),
            "sqliteKind": self.sqlite_kind,
            "cutoff": int(self.cutoff),
            "retentionSeconds": float(self.retention_seconds),
            "schemaSignature": self.schema_signature,
            "expectedRows": int(self.expected_rows),
            "vacuumMinBytes": int(self.vacuum_min_bytes),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "MaintenanceAction":
        value = _mapping(raw, "maintenance action")
        kind = _required_string(value, "kind")
        tier = _required_string(value, "tier")
        if kind not in {"delete_path", "sqlite"}:
            raise CleanupPlanError("unsupported maintenance action")
        if tier not in TIERS or tier == "protected":
            raise CleanupPlanError("maintenance action has an invalid tier")
        path = _absolute_string(value, "path")
        approved_root = _absolute_string(value, "approvedRoot")
        raw_lstat = value.get("lstat")
        if not isinstance(raw_lstat, list) or len(raw_lstat) != 5:
            raise CleanupPlanError("maintenance action has invalid lstat")
        try:
            lstat_value = tuple(int(part) for part in raw_lstat)
        except (TypeError, ValueError) as exc:
            raise CleanupPlanError("maintenance action has invalid lstat") from exc
        sqlite_kind = str(value.get("sqliteKind") or "")
        schema_signature = str(value.get("schemaSignature") or "")
        requires_offline = bool(value.get("requiresOffline"))
        requires_backup = bool(value.get("requiresBackup"))
        requires_codex_close = bool(value.get("requiresCodexClose"))
        allows_growth = bool(value.get("allowsGrowth"))
        raw_related_processes = value.get("relatedProcesses", [])
        if not isinstance(raw_related_processes, list) or any(
            not isinstance(name, str)
            or not name.strip()
            or any(character in name for character in ("\x00", "\r", "\n"))
            for name in raw_related_processes
        ):
            raise CleanupPlanError("maintenance action has invalid related processes")
        related_processes = tuple(dict.fromkeys(raw_related_processes))
        if kind == "sqlite":
            if sqlite_kind not in {"logs", "background"}:
                raise CleanupPlanError("SQLite action has an invalid database kind")
            if not re.fullmatch(r"[0-9a-f]{64}", schema_signature):
                raise CleanupPlanError("SQLite action has an invalid schema signature")
            if not requires_offline or not requires_backup:
                raise CleanupPlanError("SQLite action must require offline backup")
            if not requires_codex_close:
                raise CleanupPlanError("SQLite action must close Codex")
            if allows_growth:
                raise CleanupPlanError("SQLite action cannot allow file growth")
        elif sqlite_kind or schema_signature:
            raise CleanupPlanError("path action contains unexpected SQLite fields")
        elif requires_backup:
            raise CleanupPlanError("path action cannot require a SQLite backup")
        if requires_codex_close and not requires_offline:
            raise CleanupPlanError("Codex-close actions must run offline")
        if allows_growth and (
            str(value.get("category") or "") != "hud_diagnostics"
            or not requires_offline
            or _path_key(Path(path).parent) != _path_key(Path(approved_root))
            or not _matches_hud_log(Path(path).name)
        ):
            raise CleanupPlanError("file growth is restricted to HUD diagnostics")
        return cls(
            item_id=_required_string(value, "itemId"),
            kind=kind,  # type: ignore[arg-type]
            category=_required_string(value, "category"),
            tier=tier,  # type: ignore[arg-type]
            path=path,
            approved_root=approved_root,
            fingerprint=_required_string(value, "fingerprint"),
            lstat=lstat_value,  # type: ignore[arg-type]
            estimated_bytes=_nonnegative_int(value, "estimatedBytes"),
            requires_offline=requires_offline,
            requires_backup=requires_backup,
            requires_codex_close=requires_codex_close,
            allows_growth=allows_growth,
            related_processes=related_processes,
            sqlite_kind=sqlite_kind,
            cutoff=_nonnegative_int(value, "cutoff"),
            retention_seconds=_nonnegative_float(value, "retentionSeconds"),
            schema_signature=schema_signature,
            expected_rows=_nonnegative_int(value, "expectedRows"),
            vacuum_min_bytes=_nonnegative_int(value, "vacuumMinBytes"),
        )


@dataclass(frozen=True)
class MaintenancePlan:
    id: str
    created_at: float
    expires_at: float
    parent_pid: int
    wait_pids: tuple[int, ...]
    wait_timeout_seconds: float
    backup_directory: str
    actions: tuple[MaintenanceAction, ...]
    result_path: str = ""
    restart_command: tuple[str, ...] = ()
    version: int = PLAN_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "version": int(self.version),
            "id": self.id,
            "createdAt": float(self.created_at),
            "expiresAt": float(self.expires_at),
            "parentPid": int(self.parent_pid),
            "waitPids": list(self.wait_pids),
            "waitTimeoutSeconds": float(self.wait_timeout_seconds),
            "backupDirectory": self.backup_directory,
            "actions": [action.to_dict() for action in self.actions],
            "resultPath": self.result_path,
            "restartCommand": list(self.restart_command),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "MaintenancePlan":
        value = _mapping(raw, "maintenance plan")
        version = _nonnegative_int(value, "version")
        if version != PLAN_VERSION:
            raise CleanupPlanError("unsupported cleanup plan version")
        plan_id = _required_string(value, "id")
        if not _SAFE_PLAN_ID_RE.fullmatch(plan_id):
            raise CleanupPlanError("cleanup plan id is invalid")
        raw_pids = value.get("waitPids")
        if not isinstance(raw_pids, list):
            raise CleanupPlanError("cleanup plan waitPids must be a list")
        try:
            wait_pids = tuple(sorted({int(pid) for pid in raw_pids if int(pid) > 0}))
        except (TypeError, ValueError) as exc:
            raise CleanupPlanError("cleanup plan contains an invalid pid") from exc
        raw_actions = value.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            raise CleanupPlanError("cleanup plan has no actions")
        actions = tuple(MaintenanceAction.from_dict(item) for item in raw_actions)
        backup_directory = str(value.get("backupDirectory") or "")
        if any(action.kind == "sqlite" for action in actions):
            backup_directory = _absolute_text(backup_directory, "backupDirectory")
        elif backup_directory:
            backup_directory = _absolute_text(backup_directory, "backupDirectory")
        result_path = str(value.get("resultPath") or "")
        if result_path:
            result_path = _absolute_text(result_path, "resultPath")
        restart_command = _restart_command(value.get("restartCommand"))
        return cls(
            version=version,
            id=plan_id,
            created_at=_nonnegative_float(value, "createdAt"),
            expires_at=_nonnegative_float(value, "expiresAt"),
            parent_pid=_nonnegative_int(value, "parentPid"),
            wait_pids=wait_pids,
            wait_timeout_seconds=_nonnegative_float(value, "waitTimeoutSeconds"),
            backup_directory=backup_directory,
            actions=actions,
            result_path=result_path,
            restart_command=restart_command,
        )


@dataclass(frozen=True)
class MaintenanceActionResult:
    item_id: str
    category: str
    state: str
    estimated_bytes: int
    actual_bytes: int = 0
    deleted_rows: int = 0
    backup_path: str = ""
    backup_bytes: int = 0
    restored: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "itemId": self.item_id,
            "category": self.category,
            "state": self.state,
            "estimatedBytes": int(self.estimated_bytes),
            "actualBytes": int(self.actual_bytes),
            "deletedRows": int(self.deleted_rows),
            "backupPath": self.backup_path,
            "backupBytes": int(self.backup_bytes),
            "restored": bool(self.restored),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "MaintenanceActionResult":
        value = _mapping(raw, "maintenance result item")
        return cls(
            item_id=_required_string(value, "itemId"),
            category=_required_string(value, "category"),
            state=_required_string(value, "state"),
            estimated_bytes=_nonnegative_int(value, "estimatedBytes"),
            actual_bytes=_nonnegative_int(value, "actualBytes"),
            deleted_rows=_nonnegative_int(value, "deletedRows"),
            backup_path=str(value.get("backupPath") or ""),
            backup_bytes=_nonnegative_int(value, "backupBytes"),
            restored=bool(value.get("restored")),
            error=str(value.get("error") or ""),
        )


@dataclass(frozen=True)
class MaintenanceResult:
    plan_id: str
    state: str
    started_at: float
    completed_at: float
    actions: tuple[MaintenanceActionResult, ...]
    error: str = ""
    restart_requested: bool = False
    restart_state: str = ""
    restart_error: str = ""
    version: int = RESULT_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "version": int(self.version),
            "planId": self.plan_id,
            "state": self.state,
            "startedAt": float(self.started_at),
            "completedAt": float(self.completed_at),
            "actions": [action.to_dict() for action in self.actions],
            "error": self.error,
            "restartRequested": bool(self.restart_requested),
            "restartState": self.restart_state,
            "restartError": self.restart_error,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "MaintenanceResult":
        value = _mapping(raw, "maintenance result")
        version = _nonnegative_int(value, "version")
        if version != RESULT_VERSION:
            raise CleanupPlanError("unsupported cleanup result version")
        raw_actions = value.get("actions")
        if not isinstance(raw_actions, list):
            raise CleanupPlanError("cleanup result actions must be a list")
        restart_state = str(value.get("restartState") or "")
        if restart_state not in {"", "pending", "launched", "failed", "deferred"}:
            raise CleanupPlanError("cleanup result has an invalid restart state")
        return cls(
            version=version,
            plan_id=_required_string(value, "planId"),
            state=_required_string(value, "state"),
            started_at=_nonnegative_float(value, "startedAt"),
            completed_at=_nonnegative_float(value, "completedAt"),
            actions=tuple(
                MaintenanceActionResult.from_dict(item) for item in raw_actions
            ),
            error=str(value.get("error") or ""),
            restart_requested=bool(value.get("restartRequested")),
            restart_state=restart_state,
            restart_error=str(value.get("restartError") or ""),
        )


def _mapping(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise CleanupPlanError(f"{label} must be an object")
    return raw


def _required_string(value: Mapping[str, object], key: str) -> str:
    text = str(value.get(key) or "").strip()
    if not text or "\x00" in text:
        raise CleanupPlanError(f"{key} is required")
    return text


def _nonnegative_int(value: Mapping[str, object], key: str) -> int:
    try:
        result = int(value.get(key) or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CleanupPlanError(f"{key} must be an integer") from exc
    if result < 0:
        raise CleanupPlanError(f"{key} must be nonnegative")
    return result


def _nonnegative_float(value: Mapping[str, object], key: str) -> float:
    try:
        result = float(value.get(key) or 0.0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CleanupPlanError(f"{key} must be a number") from exc
    if result < 0 or result == float("inf") or result != result:
        raise CleanupPlanError(f"{key} must be a finite nonnegative number")
    return result


def _absolute_text(value: str, key: str) -> str:
    path = Path(value)
    if not value or "\x00" in value or not path.is_absolute():
        raise CleanupPlanError(f"{key} must be an absolute path")
    return os.path.abspath(os.fspath(path))


def _absolute_string(value: Mapping[str, object], key: str) -> str:
    return _absolute_text(_required_string(value, key), key)


def _restart_command(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise CleanupPlanError("restartCommand must be a list of strings")
    if len(raw) > MAX_RESTART_ARGUMENTS:
        raise CleanupPlanError("restartCommand has too many arguments")
    arguments: list[str] = []
    total_length = 0
    for argument in raw:
        if not isinstance(argument, str):
            raise CleanupPlanError("restartCommand must contain only strings")
        if len(argument) > MAX_RESTART_ARGUMENT_LENGTH:
            raise CleanupPlanError("restartCommand argument is too long")
        if any(character in argument for character in ("\x00", "\r", "\n")):
            raise CleanupPlanError("restartCommand contains an unsafe character")
        total_length += len(argument)
        arguments.append(argument)
    if total_length > MAX_RESTART_COMMAND_LENGTH:
        raise CleanupPlanError("restartCommand is too long")
    if arguments and (not arguments[0].strip() or not Path(arguments[0]).is_absolute()):
        raise CleanupPlanError("restartCommand executable must be an absolute path")
    return tuple(arguments)


def _idle_operation() -> dict[str, object]:
    return {
        "id": "",
        "requestId": "",
        "action": "",
        "state": "idle",
        "progress": 0,
        "error": "",
    }


def _iso_timestamp(value: float) -> str:
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _absolute_path(path: str | os.PathLike[str]) -> Path:
    return Path(
        os.path.abspath(os.path.expandvars(os.path.expanduser(os.fspath(path))))
    )


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _is_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        int(getattr(info, "st_file_attributes", 0) or 0) & WINDOWS_REPARSE_ATTRIBUTE
    )


def _lstat_tuple(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(getattr(info, "st_dev", 0) or 0),
        int(getattr(info, "st_ino", 0) or 0),
        int(info.st_mode),
        int(info.st_size),
        int(getattr(info, "st_mtime_ns", int(info.st_mtime * 1e9))),
    )


def _same_append_only_file(
    expected: tuple[int, int, int, int, int],
    current: tuple[int, int, int, int, int],
) -> bool:
    return (
        expected[:3] == current[:3]
        and stat.S_ISREG(current[2])
        and current[3] >= expected[3]
        and current[4] >= expected[4]
    )


def _measure_path(path: Path) -> _MeasuredPath:
    digest = hashlib.sha256()
    total_size = 0
    files = 0
    latest_mtime = 0.0
    contains_reparse = False
    errors = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            info = os.lstat(current)
        except OSError:
            errors += 1
            continue
        relative = os.path.normcase(os.path.relpath(current, path))
        digest.update(repr((relative, _lstat_tuple(info))).encode("utf-8", "replace"))
        latest_mtime = max(latest_mtime, float(info.st_mtime))
        if _is_reparse(info):
            contains_reparse = True
            continue
        if stat.S_ISDIR(info.st_mode):
            try:
                with os.scandir(current) as entries:
                    children = sorted(entries, key=lambda entry: entry.name.casefold())
            except OSError:
                errors += 1
                continue
            stack.extend(Path(entry.path) for entry in reversed(children))
        elif stat.S_ISREG(info.st_mode):
            total_size += max(0, int(info.st_size))
            files += 1
        else:
            errors += 1
    return _MeasuredPath(
        size=total_size,
        files=files,
        mtime=latest_mtime,
        fingerprint=digest.hexdigest(),
        contains_reparse=contains_reparse,
        errors=errors,
    )


def _secure_existing_path(root: Path, path: Path) -> Path:
    root_abs = _absolute_path(root)
    path_abs = _absolute_path(path)
    try:
        root_info = os.lstat(root_abs)
        if _is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise SafeCleanupError("approved root is not a plain directory")
        resolved_root = root_abs.resolve(strict=True)
        resolved_path = path_abs.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise SafeCleanupError("cleanup path escaped its approved root") from exc
    current = path_abs
    while True:
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise SafeCleanupError("cleanup path is no longer available") from exc
        if _is_reparse(info):
            raise SafeCleanupError("reparse paths are protected")
        if _path_key(current) == _path_key(root_abs):
            break
        parent = current.parent
        if parent == current:
            raise SafeCleanupError("cleanup path escaped its approved root")
        current = parent
    return resolved_path


def _canonical_directory(path: Path, *, create: bool) -> Path:
    absolute = _absolute_path(path)
    existing = absolute
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    current = existing
    while True:
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise SafeCleanupError("backup directory cannot be verified") from exc
        if _is_reparse(info):
            raise SafeCleanupError("backup directory cannot contain reparse points")
        if current.parent == current:
            break
        current = current.parent
    if create:
        absolute.mkdir(parents=True, exist_ok=True)
    elif not absolute.exists():
        try:
            suffix = absolute.relative_to(existing)
            return existing.resolve(strict=True).joinpath(*suffix.parts)
        except (OSError, ValueError) as exc:
            raise SafeCleanupError("backup directory cannot be resolved") from exc
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise SafeCleanupError("backup directory is unavailable") from exc
    current = absolute
    while True:
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise SafeCleanupError("backup directory cannot be verified") from exc
        if _is_reparse(info):
            raise SafeCleanupError("backup directory cannot contain reparse points")
        if current.parent == current:
            break
        current = current.parent
    if not resolved.is_dir():
        raise SafeCleanupError("backup destination is not a directory")
    return resolved


def _path_device(path: Path) -> int:
    current = _absolute_path(path)
    while not current.exists() and current.parent != current:
        current = current.parent
    try:
        return int(os.stat(current).st_dev)
    except OSError as exc:
        raise SafeCleanupError("backup volume cannot be verified") from exc


def _neutral_backup_directory_labels(directory: Path) -> tuple[str, str]:
    """Return a volume label and final directory name without exposing a path."""

    path = Path(directory)
    if not path.is_absolute():
        return "", ""
    volume_label = str(path.drive or "").rstrip("\\/")
    parts = path.parts
    if (
        not volume_label
        and len(parts) >= 3
        and parts[0] == path.anchor
        and parts[1].casefold() == "volumes"
    ):
        volume_label = parts[2]
    if not volume_label:
        volume_label = path.anchor.rstrip("\\/") or path.anchor
    directory_label = path.name or volume_label
    return volume_label, directory_label


def _remove_path(path: Path) -> None:
    info = os.lstat(path)
    if _is_reparse(info):
        raise SafeCleanupError("reparse paths are protected")
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path)
    elif stat.S_ISREG(info.st_mode):
        path.unlink()
    else:
        raise SafeCleanupError("unsupported filesystem object")


def _normalize_process_name(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    lowered = text.casefold()
    return lowered[:-4] if lowered.endswith(".exe") else lowered


def _process_family_active(running: set[str], related_processes: Sequence[str]) -> bool:
    for related in related_processes:
        expected = _normalize_process_name(related)
        if not expected:
            continue
        for actual in running:
            if actual == expected or actual.startswith(f"{expected} "):
                return True
    return False


def _ensure_related_processes_inactive(
    running_process_names: Callable[[], Iterable[str]],
    related_processes: Sequence[str],
    *,
    active_error: str,
) -> None:
    if not related_processes:
        return
    try:
        running = {
            _normalize_process_name(name) for name in running_process_names()
        }
    except Exception as exc:
        raise SafeCleanupError(
            "related application state could not be verified"
        ) from exc
    if _process_family_active(running, related_processes):
        raise SafeCleanupError(active_error)


def _system_running_process_names() -> set[str]:
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if completed.returncode != 0:
                raise SafeCleanupError("process inventory is unavailable")
            return {
                _normalize_process_name(line.split('","', 1)[0].strip('"'))
                for line in completed.stdout.splitlines()
                if line.strip()
            }
        completed = subprocess.run(
            ["ps", "-axo", "comm="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            raise SafeCleanupError("process inventory is unavailable")
        return {
            _normalize_process_name(line)
            for line in completed.stdout.splitlines()
            if line.strip()
        }
    except (OSError, subprocess.SubprocessError) as exc:
        raise SafeCleanupError("process inventory is unavailable") from exc


def _pid_is_active(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def default_hud_runtime_root(
    *,
    platform: str | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    platform_name = (platform or sys.platform).casefold()
    environment = os.environ if env is None else env
    user_home = Path.home() if home is None else Path(home)
    if platform_name.startswith("win"):
        local = environment.get("LOCALAPPDATA")
        base = Path(local) if local else user_home / "AppData" / "Local"
    elif platform_name == "darwin":
        base = user_home / "Library" / "Application Support"
    else:
        xdg = environment.get("XDG_STATE_HOME") or environment.get("XDG_RUNTIME_DIR")
        base = Path(xdg) if xdg else user_home / ".local" / "state"
    return _absolute_path(base / "codex-usage-hud")



def _plain_child_files(parent: Path, *, prefix: str = "") -> list[Path]:
    """Return plain (non-reparse) files under parent, optionally name-prefixed."""

    needle = str(prefix or "").casefold()
    try:
        with os.scandir(parent) as entries:
            result: list[Path] = []
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode) or _is_reparse(info):
                    continue
                if needle and not entry.name.casefold().startswith(needle):
                    continue
                result.append(Path(entry.path))
            return sorted(result, key=lambda path: path.name.casefold())
    except OSError:
        return []


def _windows_recycle_bin_roots(
    *,
    user_home: Path,
    env: Mapping[str, str],
) -> list[Path]:
    """Best-effort user Recycle Bin roots that do not require elevation."""

    roots: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        absolute = _absolute_path(path)
        key = _path_key(absolute)
        if key in seen:
            return
        seen.add(key)
        roots.append(absolute)

    # Classic per-volume $Recycle.Bin is often ACL-restricted; still try
    # the current home drive SID folder when it is readable as a directory.
    home_drive = str(user_home.drive or Path.home().drive or "C:").rstrip("\\/")
    if home_drive:
        recycle = Path(f"{home_drive}\\$Recycle.Bin")
        for child in _plain_child_directories(recycle):
            # Prefer interactive user SIDs (S-1-5-21-...); skip well-known system SIDs.
            if not child.name.casefold().startswith("s-1-5-21-"):
                continue
            add(child)

    # Some profiles expose a virtual Recycle Bin under the known folder path.
    local = Path(env.get("LOCALAPPDATA") or user_home / "AppData" / "Local")
    candidate = local / "Microsoft" / "Windows" / "Recycle Bin"
    if candidate.exists():
        add(candidate)
    return roots


def _plain_child_directories(parent: Path) -> list[Path]:
    try:
        with os.scandir(parent) as entries:
            result = []
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(info.st_mode) and not _is_reparse(info):
                    result.append(Path(entry.path))
            return sorted(result, key=lambda path: path.name.casefold())
    except OSError:
        return []


def _env_path(
    environment: Mapping[str, str],
    name: str,
    *fallbacks: Path,
) -> Path:
    value = environment.get(name)
    if value:
        return Path(value)
    if not fallbacks:
        raise ValueError(f"no fallback path for {name}")
    return fallbacks[0]


def _add_shared_package_manager_caches(
    add,
    *,
    user_home: Path,
    environment: Mapping[str, str],
    local: Path | None,
    caches: Path | None,
) -> None:
    """Register shared user-scoped package, ML, and tooling caches (P0/P1).

    Only regenerable download/build caches. Project workspaces, virtualenvs,
    Docker images, and credential stores stay out of scope.
    """

    yarn_fallbacks: list[Path] = []
    if local is not None:
        yarn_fallbacks.append(local / "Yarn" / "Cache")
    if caches is not None:
        yarn_fallbacks.append(caches / "Yarn")
    yarn_fallbacks.append(user_home / ".cache" / "yarn")
    add(
        "yarn",
        "developer_cache",
        _env_path(environment, "YARN_CACHE_FOLDER", *yarn_fallbacks),
        "Yarn download cache",
        "Packages may need to be downloaded again.",
        ("yarn", "node"),
    )

    pnpm_fallbacks: list[Path] = []
    if environment.get("PNPM_HOME"):
        pnpm_fallbacks.append(Path(environment["PNPM_HOME"]) / "store")
    if local is not None:
        pnpm_fallbacks.append(local / "pnpm" / "store")
    if caches is not None:
        pnpm_fallbacks.append(caches / "pnpm")
    pnpm_fallbacks.append(user_home / ".local" / "share" / "pnpm" / "store")
    add(
        "pnpm",
        "developer_cache",
        _env_path(environment, "PNPM_STORE_PATH", *pnpm_fallbacks),
        "pnpm store cache",
        "Packages may need to be downloaded again.",
        ("pnpm", "node"),
    )
    add(
        "bun",
        "developer_cache",
        _env_path(
            environment,
            "BUN_INSTALL_CACHE_DIR",
            user_home / ".bun" / "install" / "cache",
        ),
        "Bun install cache",
        "Packages may need to be downloaded again.",
        ("bun",),
    )
    add(
        "go_mod",
        "developer_cache",
        _env_path(environment, "GOMODCACHE", user_home / "go" / "pkg" / "mod"),
        "Go module cache",
        "Modules may need to be downloaded again.",
        ("go",),
    )
    cargo_home = _env_path(environment, "CARGO_HOME", user_home / ".cargo")
    add(
        "cargo_registry",
        "developer_cache",
        cargo_home / "registry",
        "Cargo registry cache",
        "Crates may need to be downloaded again.",
        ("cargo", "rustc"),
    )
    add(
        "cargo_git",
        "developer_cache",
        cargo_home / "git",
        "Cargo git checkout cache",
        "Git dependencies may need to be fetched again.",
        ("cargo", "rustc"),
    )
    gradle_home = _env_path(environment, "GRADLE_USER_HOME", user_home / ".gradle")
    add(
        "gradle",
        "developer_cache",
        gradle_home / "caches",
        "Gradle dependency cache",
        "Dependencies may need to be downloaded again.",
        ("java", "gradle", "gradlew"),
    )
    add(
        "maven",
        "developer_cache",
        user_home / ".m2" / "repository",
        "Maven local repository",
        "Artifacts may need to be downloaded again.",
        ("java", "mvn"),
    )

    # P1: language package managers, ML model caches, browser automation.
    uv_fallbacks: list[Path] = []
    if local is not None:
        uv_fallbacks.append(local / "uv" / "cache")
    if caches is not None:
        uv_fallbacks.append(caches / "uv")
    uv_fallbacks.append(user_home / ".cache" / "uv")
    add(
        "uv",
        "developer_cache",
        _env_path(environment, "UV_CACHE_DIR", *uv_fallbacks),
        "uv download cache",
        "Packages may need to be downloaded again.",
        ("uv", "python"),
    )
    poetry_fallbacks: list[Path] = []
    if local is not None:
        poetry_fallbacks.append(local / "pypoetry" / "Cache")
    if caches is not None:
        poetry_fallbacks.append(caches / "pypoetry")
    poetry_fallbacks.append(user_home / ".cache" / "pypoetry")
    add(
        "poetry",
        "developer_cache",
        _env_path(environment, "POETRY_CACHE_DIR", *poetry_fallbacks),
        "Poetry cache",
        "Packages may need to be downloaded again.",
        ("poetry", "python"),
    )
    composer_fallbacks: list[Path] = [user_home / ".composer"]
    if local is not None:
        composer_fallbacks.append(local / "Composer")
    composer_home = _env_path(environment, "COMPOSER_HOME", *composer_fallbacks)
    add(
        "composer",
        "developer_cache",
        composer_home / "cache",
        "Composer package cache",
        "Packages may need to be downloaded again.",
        ("composer", "php"),
    )
    if local is not None:
        add(
            "composer_files",
            "developer_cache",
            local / "Composer" / "files",
            "Composer package cache",
            "Packages may need to be downloaded again.",
            ("composer", "php"),
        )
    hf_fallbacks: list[Path] = [user_home / ".cache" / "huggingface"]
    if local is not None:
        hf_fallbacks.append(local / "huggingface")
    add(
        "huggingface",
        "developer_cache",
        _env_path(environment, "HF_HOME", *hf_fallbacks),
        "Hugging Face model cache",
        "Models and datasets may need to be downloaded again.",
        ("python", "huggingface-cli"),
    )
    torch_fallbacks: list[Path] = [user_home / ".cache" / "torch"]
    if local is not None:
        torch_fallbacks.append(local / "torch")
    add(
        "torch",
        "developer_cache",
        _env_path(environment, "TORCH_HOME", *torch_fallbacks),
        "PyTorch hub cache",
        "Models may need to be downloaded again.",
        ("python",),
    )
    add(
        "modelscope",
        "developer_cache",
        _env_path(
            environment,
            "MODELSCOPE_CACHE",
            user_home / ".cache" / "modelscope",
        ),
        "ModelScope model cache",
        "Models may need to be downloaded again.",
        ("python",),
    )
    ollama_fallbacks: list[Path] = [user_home / ".ollama" / "models"]
    if local is not None:
        ollama_fallbacks.append(local / "Ollama" / "models")
    add(
        "ollama_models",
        "developer_cache",
        _env_path(environment, "OLLAMA_MODELS", *ollama_fallbacks),
        "Ollama model weights",
        "Local models may need to be downloaded again.",
        ("ollama",),
    )
    playwright_fallbacks: list[Path] = []
    if local is not None:
        playwright_fallbacks.append(local / "ms-playwright")
    if caches is not None:
        playwright_fallbacks.append(caches / "ms-playwright")
    playwright_fallbacks.append(user_home / ".cache" / "ms-playwright")
    add(
        "playwright",
        "developer_cache",
        _env_path(environment, "PLAYWRIGHT_BROWSERS_PATH", *playwright_fallbacks),
        "Playwright browser cache",
        "Browser binaries may need to be downloaded again.",
        ("node", "playwright"),
    )
    cypress_fallbacks: list[Path] = []
    if local is not None:
        cypress_fallbacks.append(local / "Cypress" / "Cache")
    if caches is not None:
        cypress_fallbacks.append(caches / "Cypress")
    cypress_fallbacks.append(user_home / ".cache" / "Cypress")
    add(
        "cypress",
        "developer_cache",
        _env_path(environment, "CYPRESS_CACHE_FOLDER", *cypress_fallbacks),
        "Cypress binary cache",
        "Browser binaries may need to be downloaded again.",
        ("cypress", "node"),
    )
    electron_fallbacks: list[Path] = []
    if local is not None:
        electron_fallbacks.append(local / "electron" / "Cache")
    if caches is not None:
        electron_fallbacks.append(caches / "electron")
    electron_fallbacks.append(user_home / ".cache" / "electron")
    add(
        "electron",
        "developer_cache",
        _env_path(environment, "ELECTRON_CACHE", *electron_fallbacks),
        "Electron download cache",
        "Electron binaries may need to be downloaded again.",
        ("electron", "node"),
    )
    ccache_fallbacks: list[Path] = []
    if local is not None:
        ccache_fallbacks.append(local / "ccache")
    ccache_fallbacks.extend((user_home / ".ccache", user_home / ".cache" / "ccache"))
    add(
        "ccache",
        "developer_cache",
        _env_path(environment, "CCACHE_DIR", *ccache_fallbacks),
        "ccache compiler cache",
        "Compilations may take longer until the cache is rebuilt.",
        ("ccache", "clang", "gcc", "cl"),
    )
    sccache_fallbacks: list[Path] = [user_home / ".cache" / "sccache"]
    if local is not None:
        sccache_fallbacks.append(local / "Mozilla" / "sccache")
    add(
        "sccache",
        "developer_cache",
        _env_path(environment, "SCCACHE_DIR", *sccache_fallbacks),
        "sccache compiler cache",
        "Compilations may take longer until the cache is rebuilt.",
        ("sccache", "rustc", "cargo"),
    )
    add(
        "android_cache",
        "developer_cache",
        user_home / ".android" / "cache",
        "Android SDK cache",
        "Android tooling may rebuild cache data.",
        ("adb", "emulator", "java"),
    )
    if local is not None:
        add(
            "android_build_cache",
            "developer_cache",
            local / "Android" / "Sdk" / ".temp",
            "Android SDK temporary cache",
            "Android tooling may recreate temporary downloads.",
            ("adb", "java", "sdkmanager"),
        )
        scoop_root = _env_path(environment, "SCOOP", user_home / "scoop")
        add(
            "scoop_cache",
            "developer_cache",
            scoop_root / "cache",
            "Scoop package cache",
            "Packages may need to be downloaded again.",
            ("scoop",),
        )


def platform_cache_definitions(
    *,
    platform: str | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[CacheDefinition, ...]:
    """Return exact, user-scoped cache roots for Windows or macOS."""

    platform_name = (platform or sys.platform).casefold()
    environment = os.environ if env is None else env
    user_home = Path.home() if home is None else Path(home)
    definitions: list[CacheDefinition] = []
    seen: set[str] = set()

    def add(
        key: str,
        category: str,
        path: Path,
        label: str,
        impact: str,
        processes: Sequence[str] = (),
        *,
        tier: CleanupTier = "safe",
        mode: CacheMode = "root",
        min_age: float = 0.0,
    ) -> None:
        absolute = _absolute_path(path)
        path_key = _path_key(absolute)
        if path_key in seen:
            return
        seen.add(path_key)
        definitions.append(
            CacheDefinition(
                key=key,
                category=category,
                path=absolute,
                label=label,
                impact=impact,
                tier=tier,
                related_processes=tuple(processes),
                mode=mode,
                min_age_seconds=min_age,
            )
        )

    if platform_name.startswith("win"):
        local = Path(environment.get("LOCALAPPDATA") or user_home / "AppData" / "Local")
        roaming = Path(environment.get("APPDATA") or user_home / "AppData" / "Roaming")
        temp_root = Path(
            environment.get("TEMP") or environment.get("TMP") or local / "Temp"
        )
        add(
            "user_temp",
            "user_temp",
            temp_root,
            "Expired user temporary data",
            "Applications may recreate temporary files.",
            mode="expired_children",
            min_age=DEFAULT_TEMP_MIN_AGE_SECONDS,
        )
        for key, path, label in (
            ("directx_shader", local / "D3DSCache", "DirectX shader cache"),
            ("nvidia_dx_shader", local / "NVIDIA" / "DXCache", "GPU shader cache"),
            ("nvidia_gl_shader", local / "NVIDIA" / "GLCache", "GPU shader cache"),
            ("amd_dx_shader", local / "AMD" / "DxCache", "GPU shader cache"),
            ("amd_gl_shader", local / "AMD" / "GLCache", "GPU shader cache"),
            ("amd_vk_shader", local / "AMD" / "VkCache", "GPU shader cache"),
            ("intel_shader", local / "Intel" / "ShaderCache", "GPU shader cache"),
        ):
            add(
                key,
                "system_cache",
                path,
                label,
                "Graphics applications may rebuild shader data on next use.",
            )
        for key, path, label in (
            (
                "windows_crash_dumps",
                local / "CrashDumps",
                "Old Windows crash dumps",
            ),
            (
                "windows_error_archive",
                local / "Microsoft" / "Windows" / "WER" / "ReportArchive",
                "Old Windows error reports",
            ),
            (
                "windows_error_queue",
                local / "Microsoft" / "Windows" / "WER" / "ReportQueue",
                "Old queued Windows error reports",
            ),
        ):
            add(
                key,
                "diagnostic_history",
                path,
                label,
                "Old operating-system diagnostics will no longer be available.",
                ("werfault", "wermgr"),
                tier="consent",
                mode="expired_children",
                min_age=DEFAULT_DIAGNOSTIC_MIN_AGE_SECONDS,
            )
        add(
            "nuget",
            "developer_cache",
            Path(
                environment.get("NUGET_PACKAGES") or user_home / ".nuget" / "packages"
            ),
            "NuGet package cache",
            "Packages may need to be downloaded again.",
            ("dotnet", "msbuild", "devenv"),
        )
        add(
            "npm",
            "developer_cache",
            Path(environment.get("npm_config_cache") or local / "npm-cache"),
            "npm download cache",
            "Packages may need to be downloaded again.",
            ("npm", "node"),
        )
        add(
            "pip",
            "developer_cache",
            Path(environment.get("PIP_CACHE_DIR") or local / "pip" / "Cache"),
            "pip download cache",
            "Packages may need to be downloaded again.",
            ("pip",),
        )
        _add_shared_package_manager_caches(
            add,
            user_home=user_home,
            environment=environment,
            local=local,
            caches=None,
        )
        jetbrains = local / "JetBrains"
        for index, product in enumerate(_plain_child_directories(jetbrains)):
            for child_name in ("caches", "index", "LocalHistory"):
                add(
                    f"jetbrains_{index}_{child_name.casefold()}",
                    "editor_cache",
                    product / child_name,
                    "JetBrains IDE cache",
                    "The IDE may rebuild indexes and local caches.",
                    (
                        "idea64",
                        "pycharm64",
                        "webstorm64",
                        "clion64",
                        "goland64",
                        "rider64",
                        "phpstorm64",
                        "datagrip64",
                    ),
                )
        for app_key, app_dir, processes in (
            ("cursor", roaming / "Cursor", ("cursor",)),
            ("discord", roaming / "discord", ("discord",)),
            ("slack", roaming / "Slack", ("slack",)),
        ):
            for name in ("Cache", "Code Cache", "GPUCache", "CachedData", "DawnCache"):
                add(
                    f"{app_key}_{name.casefold().replace(' ', '_')}",
                    "editor_cache",
                    app_dir / name,
                    f"{app_key.title()} cache",
                    "The application may rebuild cached UI data.",
                    processes,
                )
        explorer = local / "Microsoft" / "Windows" / "Explorer"
        for index, child in enumerate(
            _plain_child_files(explorer, prefix="thumbcache_")
        ):
            add(
                f"thumbcache_{index}",
                "system_cache",
                child,
                "Windows thumbnail cache",
                "Explorer may rebuild thumbnails on next browse.",
                ("explorer",),
            )
        for sid_root in _windows_recycle_bin_roots(
            user_home=user_home, env=environment
        ):
            add(
                f"recycle_bin_{(sid_root.name or 'items')[:48]}",
                "user_temp",
                sid_root,
                "Recycle Bin items",
                "Deleted files in the Recycle Bin will be permanently removed.",
                mode="expired_children",
                min_age=DEFAULT_TEMP_MIN_AGE_SECONDS,
            )
        for name in ("Cache", "Code Cache", "GPUCache", "CachedData", "DawnCache"):
            add(
                f"vscode_{name.casefold().replace(' ', '_')}",
                "editor_cache",
                roaming / "Code" / name,
                "Visual Studio Code cache",
                "The editor may rebuild cached UI and code data.",
                ("code", "visual studio code"),
            )
        visual_studio = local / "Microsoft" / "VisualStudio"
        for index, instance in enumerate(_plain_child_directories(visual_studio)):
            for child_name in ("ComponentModelCache", "ImageLibrary", "Cache"):
                add(
                    f"visual_studio_{index}_{child_name.casefold()}",
                    "editor_cache",
                    instance / child_name,
                    "Visual Studio cache",
                    "Visual Studio may rebuild component and image caches.",
                    ("devenv", "msbuild"),
                )
        browser_specs = (
            ("chrome", local / "Google" / "Chrome" / "User Data", ("chrome",)),
            ("edge", local / "Microsoft" / "Edge" / "User Data", ("msedge",)),
            (
                "brave",
                local / "BraveSoftware" / "Brave-Browser" / "User Data",
                ("brave",),
            ),
        )
        firefox_root = roaming / "Mozilla" / "Firefox" / "Profiles"
        for profile_index, profile in enumerate(
            _plain_child_directories(firefox_root)
        ):
            add(
                f"firefox_{profile_index}_cache2",
                "browser_cache",
                profile / "cache2",
                "Firefox cache",
                "Pages may be cached again on next use.",
                ("firefox",),
            )
    elif platform_name == "darwin":
        caches = user_home / "Library" / "Caches"
        support = user_home / "Library" / "Application Support"
        temp_value = environment.get("TMPDIR")
        if temp_value:
            add(
                "user_temp",
                "user_temp",
                Path(temp_value),
                "Expired user temporary data",
                "Applications may recreate temporary files.",
                mode="expired_children",
                min_age=DEFAULT_TEMP_MIN_AGE_SECONDS,
            )
        add(
            "nuget",
            "developer_cache",
            Path(
                environment.get("NUGET_PACKAGES") or user_home / ".nuget" / "packages"
            ),
            "NuGet package cache",
            "Packages may need to be downloaded again.",
            ("dotnet", "msbuild"),
        )
        add(
            "npm",
            "developer_cache",
            Path(
                environment.get("npm_config_cache") or user_home / ".npm" / "_cacache"
            ),
            "npm download cache",
            "Packages may need to be downloaded again.",
            ("npm", "node"),
        )
        add(
            "pip",
            "developer_cache",
            Path(environment.get("PIP_CACHE_DIR") or caches / "pip"),
            "pip download cache",
            "Packages may need to be downloaded again.",
            ("pip",),
        )
        add(
            "homebrew",
            "developer_cache",
            Path(environment.get("HOMEBREW_CACHE") or caches / "Homebrew"),
            "Homebrew download cache",
            "Packages may need to be downloaded again.",
            ("brew",),
        )
        add(
            "xcode_derived_data",
            "editor_cache",
            user_home / "Library" / "Developer" / "Xcode" / "DerivedData",
            "Xcode derived data",
            "Xcode may rebuild indexes and build products.",
            ("xcode", "xcodebuild"),
        )
        _add_shared_package_manager_caches(
            add,
            user_home=user_home,
            environment=environment,
            local=None,
            caches=caches,
        )
        for key, path, label in (
            (
                "macos_diagnostic_reports",
                user_home / "Library" / "Logs" / "DiagnosticReports",
                "Old macOS diagnostic reports",
            ),
            (
                "macos_crash_reports",
                user_home / "Library" / "Logs" / "CrashReporter",
                "Old macOS crash reports",
            ),
        ):
            add(
                key,
                "diagnostic_history",
                path,
                label,
                "Old operating-system diagnostics will no longer be available.",
                ("reportcrash",),
                tier="consent",
                mode="expired_children",
                min_age=DEFAULT_DIAGNOSTIC_MIN_AGE_SECONDS,
            )
        jetbrains = caches / "JetBrains"
        for index, product in enumerate(_plain_child_directories(jetbrains)):
            add(
                f"jetbrains_{index}",
                "editor_cache",
                product,
                "JetBrains IDE cache",
                "The IDE may rebuild indexes and local caches.",
                ("idea", "pycharm", "webstorm", "clion", "goland", "rider", "phpstorm"),
            )
        for app_key, app_name, processes in (
            ("cursor", "Cursor", ("cursor",)),
            ("discord", "discord", ("discord",)),
            ("slack", "Slack", ("slack",)),
        ):
            for name in ("Cache", "Code Cache", "GPUCache", "CachedData", "DawnCache"):
                add(
                    f"{app_key}_{name.casefold().replace(' ', '_')}",
                    "editor_cache",
                    support / app_name / name,
                    f"{app_key.title()} cache",
                    "The application may rebuild cached UI data.",
                    processes,
                )
        for name in ("Cache", "Code Cache", "GPUCache", "CachedData", "DawnCache"):
            add(
                f"vscode_{name.casefold().replace(' ', '_')}",
                "editor_cache",
                support / "Code" / name,
                "Visual Studio Code cache",
                "The editor may rebuild cached UI and code data.",
                ("code", "visual studio code"),
            )
        add(
            "visual_studio",
            "editor_cache",
            caches / "VisualStudio",
            "Visual Studio cache",
            "Visual Studio may rebuild cached data.",
            ("visualstudio",),
        )
        browser_specs = (
            ("chrome", caches / "Google" / "Chrome", ("google chrome",)),
            ("edge", caches / "Microsoft Edge", ("microsoft edge",)),
            (
                "brave",
                caches / "BraveSoftware" / "Brave-Browser",
                ("brave browser",),
            ),
        )
        firefox_profiles = caches / "Firefox" / "Profiles"
        for profile_index, profile in enumerate(
            _plain_child_directories(firefox_profiles)
        ):
            add(
                f"firefox_{profile_index}",
                "browser_cache",
                profile,
                "Firefox cache",
                "Pages may be cached again on next use.",
                ("firefox",),
            )
        add(
            "recycle_bin",
            "user_temp",
            user_home / ".Trash",
            "Trash items",
            "Deleted files in Trash will be permanently removed.",
            mode="expired_children",
            min_age=DEFAULT_TEMP_MIN_AGE_SECONDS,
        )
    else:
        return ()

    for browser, profiles_root, processes in browser_specs:
        for profile_index, profile in enumerate(
            _plain_child_directories(profiles_root)
        ):
            if profile.name != "Default" and not profile.name.startswith("Profile "):
                continue
            for cache_name in (
                "Cache",
                "Code Cache",
                "GPUCache",
                "GrShaderCache",
                "ShaderCache",
                "DawnCache",
            ):
                add(
                    f"{browser}_{profile_index}_{cache_name.casefold().replace(' ', '_')}",
                    "browser_cache",
                    profile / cache_name,
                    f"{browser.title()} cache",
                    "Pages and shaders may be cached again on next use.",
                    processes,
                )
    return tuple(definitions)



def _matches_hud_log(name: str) -> bool:
    lowered = name.casefold()
    if lowered in _HUD_EXACT_AUDIT_FILES:
        return True
    for base in _HUD_LOG_BASES:
        if lowered == base or re.fullmatch(re.escape(base) + r"\.[1-9][0-9]*", lowered):
            return True
    return False


def _sqlite_uri(path: Path, *, mode: str) -> str:
    text = quote(path.resolve(strict=True).as_posix(), safe="/:")
    return f"file:{text}?mode={mode}"


def _read_only_sqlite(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(_sqlite_uri(path, mode="ro"), uri=True, timeout=0.5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 500")
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1]) for row in connection.execute(f"PRAGMA table_xinfo({table})")
    }


def _schema_signature(connection: sqlite3.Connection, kind: SQLiteKind) -> str:
    unsupported_objects = [
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('trigger', 'view') AND name NOT LIKE 'sqlite_%'"
        )
    ]
    if unsupported_objects:
        raise SafeCleanupError("SQLite database contains unsupported triggers or views")
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    description: dict[str, object] = {"tables": sorted(tables)}
    if kind == "logs":
        if "logs" not in tables or not tables.issubset(_LOGS_ALLOWED_TABLES):
            raise SafeCleanupError("logs database contains an unknown schema")
        columns = _table_columns(connection, "logs")
        if columns != _LOGS_COLUMNS:
            raise SafeCleanupError("logs database has incompatible columns")
        if connection.execute("PRAGMA foreign_key_list(logs)").fetchone() is not None:
            raise SafeCleanupError("logs database has unsupported foreign keys")
        migration_columns: set[str] = set()
        if "_sqlx_migrations" in tables:
            migration_columns = _table_columns(connection, "_sqlx_migrations")
            if migration_columns != _LOGS_MIGRATION_COLUMNS:
                raise SafeCleanupError(
                    "logs database has incompatible migration metadata"
                )
            failed_migration = connection.execute(
                "SELECT 1 FROM _sqlx_migrations WHERE success IS NOT 1 LIMIT 1"
            ).fetchone()
            if failed_migration is not None:
                raise SafeCleanupError("logs database has an incomplete migration")
        indexes = {
            str(row[1]): {
                "unique": int(row[2]),
                "origin": str(row[3]),
                "partial": int(row[4]),
            }
            for row in connection.execute("PRAGMA index_list(logs)")
        }
        if set(indexes) != set(_LOGS_INDEXES):
            raise SafeCleanupError("logs database has unknown or missing indexes")
        index_description: dict[str, object] = {}
        for index_name, (expected_columns, expected_partial) in _LOGS_INDEXES.items():
            index_columns = tuple(
                str(row[2])
                for row in connection.execute(f"PRAGMA index_info({index_name})")
            )
            metadata = indexes[index_name]
            if (
                index_columns != expected_columns
                or metadata["unique"] != 0
                or metadata["origin"] != "c"
                or bool(metadata["partial"]) != expected_partial
            ):
                raise SafeCleanupError("logs database has incompatible indexes")
            index_description[index_name] = {
                "columns": list(index_columns),
                **metadata,
            }
        description.update(
            {
                "columns": sorted(columns),
                "indexes": index_description,
                "migrationColumns": sorted(migration_columns),
            }
        )
    else:
        if tables != _BACKGROUND_ALLOWED_TABLES:
            raise SafeCleanupError(
                "background audit database contains an unknown schema"
            )
        column_description: dict[str, list[str]] = {}
        for table, required in _BACKGROUND_REQUIRED_COLUMNS.items():
            columns = _table_columns(connection, table)
            if not required.issubset(columns):
                raise SafeCleanupError(
                    "background audit database is missing required columns"
                )
            column_description[table] = sorted(columns)
        version_row = connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        schema_version = str(version_row[0]) if version_row is not None else ""
        if schema_version not in _BACKGROUND_SCHEMA_VERSIONS:
            raise SafeCleanupError("background audit schema version is unsupported")
        description["columns"] = column_description
        description["schemaVersion"] = schema_version
    encoded = json.dumps(description, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sqlite_group_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    for suffix in ("", "-wal", "-shm"):
        member = Path(f"{path}{suffix}")
        try:
            info = os.lstat(member)
        except FileNotFoundError:
            digest.update(repr((suffix, None)).encode("ascii"))
            continue
        if _is_reparse(info) or not stat.S_ISREG(info.st_mode):
            raise SafeCleanupError("SQLite runtime group contains an unsafe object")
        digest.update(repr((suffix, _lstat_tuple(info))).encode("ascii"))
    return digest.hexdigest()


def _sqlite_group_size(path: Path) -> int:
    return sum(
        max(0, int(os.lstat(member).st_size))
        for member in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        if member.exists()
    )


def audit_sqlite_target(
    target: SQLiteTarget,
    *,
    now: float | None = None,
    default_log_retention_hours: float = DEFAULT_LOG_RETENTION_HOURS,
    default_background_retention_days: float = DEFAULT_BACKGROUND_RETENTION_DAYS,
) -> _SQLiteAudit:
    path = _absolute_path(target.path)
    expected_name = (
        "logs_2.sqlite" if target.kind == "logs" else "background-usage.sqlite3"
    )
    if path.name.casefold() != expected_name.casefold():
        raise SafeCleanupError("SQLite maintenance target has an unexpected filename")
    _secure_existing_path(path.parent, path)
    if target.retention_seconds is not None:
        retention = max(1.0, float(target.retention_seconds))
    elif target.kind == "logs":
        retention = max(1.0, float(default_log_retention_hours) * 3600.0)
    else:
        retention = max(1.0, float(default_background_retention_days) * 86400.0)
    cutoff = int((time.time() if now is None else float(now)) - retention)
    with closing(_read_only_sqlite(path)) as connection:
        signature = _schema_signature(connection, target.kind)
        if target.kind == "logs":
            total = int(connection.execute("SELECT COUNT(*) FROM logs").fetchone()[0])
            deletable = int(
                connection.execute(
                    "SELECT COUNT(*) FROM logs INDEXED BY idx_logs_ts WHERE ts < ?",
                    (cutoff,),
                ).fetchone()[0]
            )
        else:
            total = 0
            deletable = 0
            for table in (
                "background_events",
                "background_requests",
                "process_evidence",
            ):
                total += int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
            deletable += int(
                connection.execute(
                    "SELECT COUNT(*) FROM background_requests WHERE occurred_at < ?",
                    (cutoff,),
                ).fetchone()[0]
            )
            deletable += int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM background_events AS event
                    WHERE event.last_seen_at < ?
                      AND NOT EXISTS (
                        SELECT 1 FROM background_requests AS request
                        WHERE request.event_id=event.event_id AND request.occurred_at >= ?
                      )
                    """,
                    (cutoff, cutoff),
                ).fetchone()[0]
            )
            deletable += int(
                connection.execute(
                    "SELECT COUNT(*) FROM process_evidence WHERE last_seen_at < ?",
                    (cutoff,),
                ).fetchone()[0]
            )
    group_fingerprint = _sqlite_group_fingerprint(path)
    main_size = _sqlite_group_size(path)
    estimated = min(main_size, int(main_size * deletable / max(1, total)))
    return _SQLiteAudit(
        kind=target.kind,
        cutoff=cutoff,
        retention_seconds=retention,
        deletable_rows=deletable,
        total_rows=total,
        estimated_bytes=estimated,
        schema_signature=signature,
        group_fingerprint=group_fingerprint,
    )


class SafeCleanupManager:
    """Explicit-scan inventory with tiered preview and one-use confirmation."""

    def __init__(
        self,
        *,
        platform: str | None = None,
        home: Path | None = None,
        env: Mapping[str, str] | None = None,
        hud_runtime_root: Path | None = None,
        cache_definitions: Sequence[CacheDefinition] | None = None,
        sqlite_targets: Sequence[SQLiteTarget] = (),
        codex_candidate_provider: (
            Callable[[], Sequence[CodexCleanupCandidate]] | None
        ) = None,
        backup_roots: Sequence[Path] = (),
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] | None = None,
        running_process_names: Callable[
            [], Iterable[str]
        ] = _system_running_process_names,
        pid_active: Callable[[int], bool] = _pid_is_active,
        lock_probe: Callable[[Path], bool] = path_is_locked,
        confirmation_ttl_seconds: float = DEFAULT_CONFIRMATION_TTL_SECONDS,
        log_retention_hours: float = DEFAULT_LOG_RETENTION_HOURS,
        background_retention_days: float = DEFAULT_BACKGROUND_RETENTION_DAYS,
        vacuum_min_reclaim_bytes: int = DEFAULT_VACUUM_MIN_RECLAIM_BYTES,
    ) -> None:
        self.platform = (platform or sys.platform).casefold()
        self.home = Path.home() if home is None else Path(home)
        self.env = dict(os.environ if env is None else env)
        self.hud_runtime_root = hud_runtime_root or default_hud_runtime_root(
            platform=self.platform, home=self.home, env=self.env
        )
        self._configured_cache_definitions = (
            tuple(cache_definitions) if cache_definitions is not None else None
        )
        self.sqlite_targets = tuple(sqlite_targets)
        self.codex_candidate_provider = codex_candidate_provider
        self.backup_roots = tuple(_absolute_path(path) for path in backup_roots)
        self.clock = clock
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self.running_process_names = running_process_names
        self.pid_active = pid_active
        self.lock_probe = lock_probe
        self.confirmation_ttl_seconds = max(1.0, float(confirmation_ttl_seconds))
        self.log_retention_hours = max(1.0 / 60.0, float(log_retention_hours))
        self.background_retention_days = max(
            1.0 / 24.0, float(background_retention_days)
        )
        self.vacuum_min_reclaim_bytes = max(0, int(vacuum_min_reclaim_bytes))
        self._inventory: CleanupInventory | None = None
        self._items: dict[str, CleanupItem] = {}
        self._confirmations: dict[str, _Confirmation] = {}
        self._revision_counter = 0
        self._operation: dict[str, object] = _idle_operation()
        # Optional sink for mid-scan snapshots (set by the worker thread).
        self.progress_publisher: Callable[[Mapping[str, object]], None] | None = None

    def snapshot(self) -> dict[str, object]:
        if self._inventory is None:
            return {
                "revision": "",
                "generatedAt": "",
                "platform": self.platform,
                "totals": {
                    "reclaimableBytes": 0,
                    "consentBytes": 0,
                    "protectedBytes": 0,
                    "backupBytes": 0,
                    "items": 0,
                },
                "groups": [],
                "defaultSelectedIds": [],
                "operation": dict(self._operation),
            }
        return self._inventory.to_payload(self._operation)

    def resolve_reveal_path(self, item_id: str, revision: str) -> Path:
        """Resolve one scanned item for a read-only local reveal action.

        Reveal is intentionally less restrictive than a deletion plan: it does
        not require a safe/consent tier or a stable content fingerprint.  It
        still requires the current inventory, approved-root boundary, and a
        plain existing path so a renderer cannot turn this into arbitrary
        process launch or path traversal.
        """

        item = self._selected_items((item_id,), revision)[0]
        if item._path is None or item._approved_root is None:
            raise SafeCleanupError("cleanup item has no revealable path")
        resolved = _secure_existing_path(item._approved_root, item._path)
        if _path_key(resolved) != _path_key(item._path):
            raise SafeCleanupError("cleanup path changed after scan")
        return resolved

    @staticmethod
    def scanning_revision(request_id: str) -> str:
        """Opaque temporary revision that must never back confirmation tokens."""

        token = str(request_id or "").strip() or "pending"
        return f"scanning:{token}"

    @staticmethod
    def is_scanning_revision(revision: object) -> bool:
        return str(revision or "").startswith("scanning:")

    def _phase_progress_ceiling(self, phase_index: int) -> int:
        """Inclusive 1-based phase index → cumulative progress (capped at 99)."""

        total = 0
        for index, (_phase, weight) in enumerate(SCAN_PHASE_WEIGHTS, start=1):
            if index > phase_index:
                break
            total += int(weight)
        return min(99, max(0, total))

    def _emit_scan_progress(
        self,
        *,
        request_id: str,
        phase: str,
        phase_index: int,
        progress: int,
        items: Sequence[CleanupItem],
        generated_at: float,
        phase_detail: str = "",
    ) -> None:
        """Publish a partial inventory while a scan is running.

        Operation progress fields stay path-neutral. Item payloads may include
        local display path metadata for the renderer, but the temporary
        ``scanning:`` revision must never back reveal, preview, or execute.
        """

        publisher = self.progress_publisher
        if not callable(publisher):
            return
        phase_key = str(phase or "").strip() or "hud"
        label = SCAN_PHASE_LABELS.get(phase_key, phase_key)
        if phase_detail:
            label = f"{label}: {phase_detail}"
        discovered_bytes = sum(
            int(item.size)
            for item in items
            if item.tier == "safe" and not item.blocked_reason
        )
        self.mark_operation(
            request_id=request_id,
            action="scan",
            state="scanning",
            progress=min(99, max(0, int(progress))),
            phase=phase_key,
            phaseLabel=label,
            phaseIndex=max(1, int(phase_index)),
            phaseCount=SCAN_PHASE_COUNT,
            discoveredGroups=len(items),
            discoveredBytes=discovered_bytes,
        )
        self._inventory = CleanupInventory(
            revision=self.scanning_revision(request_id),
            generated_at=float(generated_at),
            platform=self.platform,
            items=tuple(items),
        )
        self._items = {item.id: item for item in items}
        publisher(self.snapshot())

    def cancel(self, *, request_id: str = "") -> dict[str, object]:
        """Invalidate one-use confirmations without touching local data."""

        self._confirmations.clear()
        self._operation = {
            "id": str(request_id or self.token_factory()),
            "requestId": str(request_id or ""),
            "action": "cancel",
            "state": "cancelled",
            "progress": 100,
            "error": "",
        }
        return self.snapshot()

    def mark_operation(
        self,
        *,
        request_id: str,
        action: str,
        state: str,
        progress: int = 0,
        error: str = "",
        **values: object,
    ) -> dict[str, object]:
        """Publish orchestration state while preserving the current inventory."""

        operation = {
            "id": str(values.pop("id", "") or request_id or self.token_factory()),
            "requestId": str(request_id or ""),
            "action": str(action or ""),
            "state": str(state or ""),
            "progress": max(0, min(100, int(progress))),
            "error": str(error or ""),
        }
        operation.update(values)
        self._operation = operation
        return self.snapshot()

    def apply_maintenance_result(
        self,
        result: MaintenanceResult,
        *,
        request_id: str = "",
    ) -> dict[str, object]:
        """Project a helper result without exposing absolute backup paths."""

        result = MaintenanceResult.from_dict(result.to_dict())
        rows: list[dict[str, object]] = []
        backup_files: list[str] = []
        backup_volume_labels: list[str] = []
        backup_directory_labels: list[str] = []
        for action in result.actions:
            backup_file = Path(action.backup_path).name if action.backup_path else ""
            backup_volume_label = ""
            backup_directory_label = ""
            if action.backup_path:
                backup_volume_label, backup_directory_label = (
                    _neutral_backup_directory_labels(Path(action.backup_path).parent)
                )
            if backup_file and backup_file not in backup_files:
                backup_files.append(backup_file)
            if (
                backup_volume_label
                and backup_volume_label not in backup_volume_labels
            ):
                backup_volume_labels.append(backup_volume_label)
            if (
                backup_directory_label
                and backup_directory_label not in backup_directory_labels
            ):
                backup_directory_labels.append(backup_directory_label)
            rows.append(
                {
                    "id": action.item_id,
                    "category": action.category,
                    "state": action.state,
                    "estimatedBytes": int(action.estimated_bytes),
                    "actualBytes": int(action.actual_bytes),
                    "deletedRows": int(action.deleted_rows),
                    "backupFile": backup_file,
                    "backupBytes": int(action.backup_bytes),
                    "backupVolumeLabel": backup_volume_label,
                    "backupDirectoryLabel": backup_directory_label,
                    "restored": bool(action.restored),
                    "error": action.error,
                }
            )
        return self.mark_operation(
            id=result.plan_id,
            request_id=request_id,
            action="execute",
            state=result.state,
            progress=100,
            error=result.error,
            results=rows,
            estimatedBytes=sum(action.estimated_bytes for action in result.actions),
            actualBytes=sum(action.actual_bytes for action in result.actions),
            deletedRows=sum(action.deleted_rows for action in result.actions),
            backupBytes=sum(action.backup_bytes for action in result.actions),
            backupFiles=backup_files,
            backupVolumeLabel=(
                backup_volume_labels[0] if len(backup_volume_labels) == 1 else ""
            ),
            backupDirectoryLabel=(
                backup_directory_labels[0]
                if len(backup_directory_labels) == 1
                else ""
            ),
            backupVolumeLabels=backup_volume_labels,
            backupDirectoryLabels=backup_directory_labels,
            restartRequested=bool(result.restart_requested),
            restartState=result.restart_state,
            restartError=result.restart_error,
        )

    def scan(self, *, request_id: str = "") -> dict[str, object]:
        generated_at = float(self.clock())
        items: list[CleanupItem] = []
        request = str(request_id or "")

        def report(
            phase: str,
            phase_index: int,
            *,
            progress: int | None = None,
            phase_detail: str = "",
        ) -> None:
            ceiling = self._phase_progress_ceiling(phase_index)
            prior = self._phase_progress_ceiling(phase_index - 1) if phase_index > 1 else 0
            if progress is None:
                value = ceiling
            else:
                value = max(prior, min(ceiling, int(progress)))
            self._emit_scan_progress(
                request_id=request,
                phase=phase,
                phase_index=phase_index,
                progress=min(99, value),
                items=items,
                generated_at=generated_at,
                phase_detail=phase_detail,
            )

        report("hud", 1, progress=0)
        items.extend(self._scan_hud_runtime(generated_at))
        report("hud", 1)

        items.extend(self._scan_codex_candidates())
        report("codex", 2)

        try:
            running = {
                _normalize_process_name(name) for name in self.running_process_names()
            }
            process_error = ""
        except Exception:
            running = set()
            process_error = "Related application state could not be verified."
        report("processes", 3)

        definitions = self._configured_cache_definitions
        if definitions is None:
            definitions = platform_cache_definitions(
                platform=self.platform, home=self.home, env=self.env
            )
        definitions = tuple(definitions)
        cache_weight = next(
            (weight for key, weight in SCAN_PHASE_WEIGHTS if key == "caches"),
            40,
        )
        cache_base = self._phase_progress_ceiling(3)
        if not definitions:
            report("caches", 4)
        else:
            for index, definition in enumerate(definitions, start=1):
                items.extend(
                    self._scan_cache_definition(
                        definition,
                        generated_at,
                        running=running,
                        process_error=process_error,
                    )
                )
                fraction = index / max(1, len(definitions))
                report(
                    "caches",
                    4,
                    progress=min(99, cache_base + int(cache_weight * fraction)),
                    phase_detail=str(definition.label or definition.key),
                )

        items.extend(self._scan_old_backups(generated_at))
        report("backups", 5)

        for target in self.sqlite_targets:
            items.append(self._scan_sqlite_target(target, generated_at))
        report("sqlite", 6)

        self._revision_counter += 1
        revision = f"{self._revision_counter}-{self.token_factory()}"
        self._inventory = CleanupInventory(
            revision=revision,
            generated_at=generated_at,
            platform=self.platform,
            items=tuple(items),
        )
        self._items = {item.id: item for item in items}
        self._confirmations.clear()
        discovered_bytes = sum(
            int(item.size)
            for item in items
            if item.tier == "safe" and not item.blocked_reason
        )
        self._operation = {
            "id": str(request_id or self.token_factory()),
            "requestId": request_id,
            "action": "scan",
            "state": "completed",
            "progress": 100,
            "error": "",
            "phase": "sqlite",
            "phaseLabel": SCAN_PHASE_LABELS["sqlite"],
            "phaseIndex": SCAN_PHASE_COUNT,
            "phaseCount": SCAN_PHASE_COUNT,
            "discoveredGroups": len(items),
            "discoveredBytes": discovered_bytes,
        }
        return self.snapshot()

    def preview(
        self,
        item_ids: Sequence[str],
        revision: str,
        *,
        consent: bool = False,
        backup_directory: Path | None = None,
        request_id: str = "",
    ) -> dict[str, object]:
        items = self._selected_items(item_ids, revision)
        if any(item.tier == "protected" or item.blocked_reason for item in items):
            raise SafeCleanupError("selection contains protected cleanup items")
        if any(item.tier == "consent" for item in items) and not consent:
            raise SafeCleanupError("historical cleanup requires separate consent")
        has_sqlite = any(item.action == "sqlite" for item in items)
        canonical_backup: Path | None = None
        if has_sqlite:
            if backup_directory is None:
                raise SafeCleanupError("SQLite maintenance requires a backup directory")
            canonical_backup = _canonical_directory(
                Path(backup_directory), create=False
            )
            self._validate_backup_not_selected(canonical_backup, items)
        token = self.token_factory()
        self._confirmations[token] = _Confirmation(
            revision=revision,
            item_ids=tuple(item.id for item in items),
            consent=bool(consent),
            backup_directory=canonical_backup,
            expires_at=float(self.clock()) + self.confirmation_ttl_seconds,
        )
        sqlite_items = [
            item for item in items if item.action == "sqlite" and item._path is not None
        ]
        backup_bytes = sum(
            _sqlite_group_size(item._path) for item in sqlite_items if item._path
        )
        same_volume_backup_bytes = 0
        backup_volume_label = ""
        backup_directory_label = ""
        if canonical_backup is not None:
            backup_device = _path_device(canonical_backup)
            same_volume_backup_bytes = sum(
                _sqlite_group_size(item._path)
                for item in sqlite_items
                if item._path is not None
                and _path_device(item._path) == backup_device
            )
            backup_volume_label, backup_directory_label = (
                _neutral_backup_directory_labels(canonical_backup)
            )
        estimated_bytes = sum(item.size for item in items)
        self._operation = {
            "id": token,
            "requestId": request_id,
            "action": "preview",
            "state": "preview",
            "progress": 100,
            "error": "",
            "confirmationToken": token,
            "inventoryRevision": revision,
            "selectedIds": [item.id for item in items],
            "estimatedBytes": estimated_bytes,
            "backupBytes": backup_bytes,
            "sameVolumeBackupBytes": same_volume_backup_bytes,
            "netEstimatedBytes": max(0, estimated_bytes - same_volume_backup_bytes),
            "backupLabel": backup_directory_label,
            "backupVolumeLabel": backup_volume_label,
            "backupDirectoryLabel": backup_directory_label,
            "requiresOffline": any(item.requires_offline for item in items),
            "requiresBackup": any(item.requires_backup for item in items),
            "requiresCodexClose": any(item.requires_codex_close for item in items),
            "includesConsent": any(item.tier == "consent" for item in items),
        }
        return self.snapshot()

    def selection_requirements(
        self,
        item_ids: Sequence[str],
        revision: str,
    ) -> dict[str, bool]:
        items = self._selected_items(item_ids, revision)
        return {
            "requiresOffline": any(item.requires_offline for item in items),
            "requiresBackup": any(item.requires_backup for item in items),
            "requiresCodexClose": any(item.requires_codex_close for item in items),
        }

    def create_plan(
        self,
        item_ids: Sequence[str],
        revision: str,
        confirmation_token: str,
        *,
        parent_pid: int = 0,
        wait_pids: Sequence[int] = (),
        result_path: Path | None = None,
        restart_command: list[str] | None = None,
        plan_ttl_seconds: float = DEFAULT_PLAN_TTL_SECONDS,
        wait_timeout_seconds: float = DEFAULT_PROCESS_WAIT_SECONDS,
    ) -> MaintenancePlan:
        items, confirmation = self._consume_confirmation(
            item_ids, revision, confirmation_token
        )
        current_codex_candidates: dict[str, CodexCleanupCandidate] = {}
        if any(item.category == "codex_temp" for item in items):
            current_codex_candidates = {
                _path_key(candidate.path): candidate
                for candidate in self._load_codex_candidates()
            }
        actions: list[MaintenanceAction] = []
        for item in items:
            self._revalidate_item(
                item,
                revision,
                current_codex_candidates=current_codex_candidates,
            )
            if item._path is None or item._approved_root is None:
                raise SafeCleanupError("cleanup item has no approved path")
            sqlite_audit = item._sqlite
            actions.append(
                MaintenanceAction(
                    item_id=item.id,
                    kind="sqlite" if item.action == "sqlite" else "delete_path",
                    category=item.category,
                    tier=item.tier,
                    path=os.fspath(item._path),
                    approved_root=os.fspath(item._approved_root),
                    fingerprint=item._fingerprint,
                    lstat=item._lstat,
                    estimated_bytes=item.size,
                    requires_offline=item.requires_offline,
                    requires_backup=item.requires_backup,
                    requires_codex_close=item.requires_codex_close,
                    allows_growth=(
                        item.category == "hud_diagnostics"
                        and item.requires_offline
                        and _matches_hud_log(item._path.name)
                    ),
                    related_processes=item.related_processes,
                    sqlite_kind=sqlite_audit.kind if sqlite_audit else "",
                    cutoff=sqlite_audit.cutoff if sqlite_audit else 0,
                    retention_seconds=(
                        sqlite_audit.retention_seconds if sqlite_audit else 0.0
                    ),
                    schema_signature=(
                        sqlite_audit.schema_signature if sqlite_audit else ""
                    ),
                    expected_rows=(sqlite_audit.deletable_rows if sqlite_audit else 0),
                    vacuum_min_bytes=self.vacuum_min_reclaim_bytes,
                )
            )
        backup_directory = confirmation.backup_directory
        if (
            any(action.kind == "sqlite" for action in actions)
            and backup_directory is None
        ):
            raise SafeCleanupError("SQLite maintenance requires a backup directory")
        created_at = float(self.clock())
        plan_id = self.token_factory()
        if not _SAFE_PLAN_ID_RE.fullmatch(plan_id):
            raise SafeCleanupError("token factory returned an invalid cleanup plan id")
        encoded_result_path = (
            _absolute_text(os.fspath(result_path), "resultPath")
            if result_path is not None
            else ""
        )
        restart_argv = _restart_command(
            restart_command if restart_command is not None else []
        )
        plan = MaintenancePlan(
            id=plan_id,
            created_at=created_at,
            expires_at=created_at + max(1.0, float(plan_ttl_seconds)),
            parent_pid=max(0, int(parent_pid)),
            wait_pids=tuple(sorted({int(pid) for pid in wait_pids if int(pid) > 0})),
            wait_timeout_seconds=max(0.0, float(wait_timeout_seconds)),
            backup_directory=os.fspath(backup_directory) if backup_directory else "",
            actions=tuple(actions),
            result_path=encoded_result_path,
            restart_command=restart_argv,
        )
        self._operation = {
            "id": plan.id,
            "requestId": "",
            "action": "plan",
            "state": "planned",
            "progress": 100,
            "error": "",
            "inventoryRevision": revision,
            "selectedIds": [item.id for item in items],
            "estimatedBytes": sum(item.size for item in items),
        }
        return plan

    def _selected_items(
        self, item_ids: Sequence[str], revision: str
    ) -> list[CleanupItem]:
        normalized = tuple(dict.fromkeys(str(item_id or "") for item_id in item_ids))
        if not normalized or any(not item_id for item_id in normalized):
            raise SafeCleanupError("empty or invalid cleanup selection")
        if self._inventory is None or revision != self._inventory.revision:
            raise SafeCleanupError("cleanup inventory revision is stale")
        if self.is_scanning_revision(revision):
            raise SafeCleanupError("cleanup inventory revision is stale")
        try:
            return [self._items[item_id] for item_id in normalized]
        except KeyError as exc:
            raise SafeCleanupError("unknown cleanup item id") from exc

    def _consume_confirmation(
        self, item_ids: Sequence[str], revision: str, token: str
    ) -> tuple[list[CleanupItem], _Confirmation]:
        items = self._selected_items(item_ids, revision)
        confirmation = self._confirmations.pop(str(token or ""), None)
        if confirmation is None or confirmation.expires_at < float(self.clock()):
            raise SafeCleanupError("confirmation token is missing or expired")
        if (
            confirmation.revision != revision
            or confirmation.item_ids != tuple(item.id for item in items)
            or (
                any(item.tier == "consent" for item in items)
                and not confirmation.consent
            )
        ):
            raise SafeCleanupError("confirmation does not match cleanup selection")
        return items, confirmation

    def _validate_backup_not_selected(
        self, backup_directory: Path, items: Sequence[CleanupItem]
    ) -> None:
        backup_key = _path_key(backup_directory)
        for item in items:
            if item.action == "sqlite" or item._path is None:
                continue
            target_key = _path_key(item._path)
            try:
                common = os.path.commonpath((backup_key, target_key))
            except ValueError:
                continue
            if common == target_key:
                raise SafeCleanupError(
                    "backup directory is inside a selected cleanup item"
                )

    def _revalidate_item(
        self,
        item: CleanupItem,
        revision: str,
        *,
        current_codex_candidates: Mapping[str, CodexCleanupCandidate] | None = None,
    ) -> None:
        if self._inventory is None or self._inventory.revision != revision:
            raise SafeCleanupError("cleanup inventory revision is stale")
        if (
            item.tier == "protected"
            or item._path is None
            or item._approved_root is None
        ):
            raise SafeCleanupError("protected cleanup item cannot be executed")
        resolved = _secure_existing_path(item._approved_root, item._path)
        if _path_key(resolved) != _path_key(item._path):
            raise SafeCleanupError("cleanup path changed after scan")
        current_lstat = _lstat_tuple(os.lstat(resolved))
        if item.category == "codex_temp":
            current = (current_codex_candidates or {}).get(_path_key(resolved))
            if (
                current is None
                or _path_key(current.approved_root) != _path_key(item._approved_root)
                or current.lstat != item._lstat
                or current.fingerprint != item._fingerprint
            ):
                raise SafeCleanupError(
                    "Codex temporary data is no longer an approved candidate"
                )
        allows_growth = (
            item.category == "hud_diagnostics"
            and item.requires_offline
            and _matches_hud_log(resolved.name)
        )
        if (
            not _same_append_only_file(item._lstat, current_lstat)
            if allows_growth
            else current_lstat != item._lstat
        ):
            raise SafeCleanupError("cleanup item metadata changed after scan")
        if item.action == "sqlite":
            if _sqlite_group_fingerprint(resolved) != item._fingerprint:
                raise SafeCleanupError("SQLite runtime group changed after scan")
        else:
            measured = _measure_path(resolved)
            if (
                measured.contains_reparse
                or measured.errors
                or (not allows_growth and measured.fingerprint != item._fingerprint)
            ):
                raise SafeCleanupError("cleanup item changed after scan")
            if item.related_processes and not item.requires_offline:
                try:
                    running = {
                        _normalize_process_name(name)
                        for name in self.running_process_names()
                    }
                except Exception as exc:
                    raise SafeCleanupError(
                        "related application state could not be revalidated"
                    ) from exc
                if _process_family_active(running, item.related_processes):
                    raise SafeCleanupError(
                        "a related application started after the cleanup scan"
                    )

    def _new_item(
        self,
        *,
        category: str,
        tier: CleanupTier,
        label: str,
        measured: _MeasuredPath,
        impact: str,
        path: Path | None,
        approved_root: Path | None,
        retention: str = "",
        requires_offline: bool = False,
        requires_backup: bool = False,
        requires_codex_close: bool = False,
        blocked_reason: str = "",
        related_processes: Sequence[str] = (),
        action: str = "",
        fingerprint: str | None = None,
        sqlite_audit: _SQLiteAudit | None = None,
        size: int | None = None,
    ) -> CleanupItem:
        lstat_value = (
            _lstat_tuple(os.lstat(path))
            if path is not None and path.exists()
            else (0, 0, 0, 0, 0)
        )
        return CleanupItem(
            id=self.token_factory(),
            category=category,
            tier=tier,
            label=label,
            size=max(0, measured.size if size is None else int(size)),
            file_count=max(0, measured.files),
            impact=impact,
            retention=retention,
            requires_offline=requires_offline,
            requires_backup=requires_backup,
            requires_codex_close=requires_codex_close,
            blocked_reason=blocked_reason,
            related_processes=tuple(related_processes),
            action=action,
            modified_at=max(0.0, float(measured.mtime)),
            _path=path,
            _approved_root=approved_root,
            _fingerprint=fingerprint
            if fingerprint is not None
            else measured.fingerprint,
            _lstat=lstat_value,
            _sqlite=sqlite_audit,
        )

    def _load_codex_candidates(self) -> tuple[CodexCleanupCandidate, ...]:
        if self.codex_candidate_provider is None:
            return ()
        try:
            candidates = tuple(self.codex_candidate_provider())
        except Exception as exc:
            raise SafeCleanupError("Codex temporary data could not be audited") from exc
        for candidate in candidates:
            if not isinstance(candidate, CodexCleanupCandidate):
                raise SafeCleanupError("Codex candidate provider returned invalid data")
        return candidates

    def _scan_codex_candidates(self) -> list[CleanupItem]:
        if self.codex_candidate_provider is None:
            return []
        try:
            candidates = self._load_codex_candidates()
        except SafeCleanupError as exc:
            return [
                self._new_item(
                    category="codex_temp",
                    tier="protected",
                    label="Protected Codex temporary data",
                    measured=_MeasuredPath(0, 0, 0.0, "", False, 1),
                    impact="Codex temporary data remains unchanged.",
                    path=None,
                    approved_root=None,
                    blocked_reason=str(exc),
                )
            ]
        items: list[CleanupItem] = []
        for candidate in candidates:
            measured = _MeasuredPath(
                size=max(0, int(candidate.size)),
                files=max(0, int(candidate.file_count)),
                mtime=float(candidate.mtime),
                fingerprint=candidate.fingerprint,
                contains_reparse=False,
                errors=0,
            )
            items.append(
                self._new_item(
                    category="codex_temp",
                    tier="safe",
                    label="Expired Codex temporary data",
                    measured=measured,
                    impact="Codex may recreate temporary staging data when needed.",
                    path=_absolute_path(candidate.path),
                    approved_root=_absolute_path(candidate.approved_root),
                    retention=_duration_label(DEFAULT_BACKUP_MIN_AGE_SECONDS),
                    requires_offline=True,
                    requires_backup=False,
                    requires_codex_close=True,
                    related_processes=("Codex",),
                    action="delete_path",
                    fingerprint=candidate.fingerprint,
                )
            )
        return items

    def _scan_hud_runtime(self, now: float) -> list[CleanupItem]:
        root = _absolute_path(self.hud_runtime_root)
        if not root.exists():
            return []
        try:
            root_info = os.lstat(root)
            if _is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
                measured = _measure_path(root)
                return [
                    self._new_item(
                        category="hud_runtime",
                        tier="protected",
                        label="HUD runtime data",
                        measured=measured,
                        impact="HUD runtime root could not be verified.",
                        path=None,
                        approved_root=None,
                        blocked_reason="HUD runtime root is a reparse point or non-directory.",
                    )
                ]
            with os.scandir(root) as entries:
                children = sorted(entries, key=lambda entry: entry.name.casefold())
        except OSError:
            return []
        items: list[CleanupItem] = []
        for entry in children:
            path = Path(entry.path)
            measured = _measure_path(path)
            name = entry.name
            overlay_match = _OVERLAY_COMMAND_RE.fullmatch(name)
            if measured.contains_reparse or measured.errors:
                tier: CleanupTier = "protected"
                reason = "HUD path metadata could not be verified without following reparse points."
                category = "hud_runtime"
                action = ""
            elif _matches_hud_log(name):
                tier = "safe"
                reason = ""
                category = "hud_diagnostics"
                action = "delete_path"
            elif overlay_match is not None:
                pid = int(overlay_match.group("pid"))
                try:
                    active = bool(self.pid_active(pid))
                except Exception:
                    active = True
                tier = "protected" if active else "safe"
                reason = (
                    "Overlay command history belongs to an active HUD process."
                    if active
                    else ""
                )
                category = "hud_overlay_history"
                action = "" if active else "delete_path"
            elif _OLD_LOG_BACKUP_RE.fullmatch(name):
                old = now - measured.mtime >= DEFAULT_BACKUP_MIN_AGE_SECONDS
                tier = "safe" if old else "protected"
                reason = (
                    ""
                    if old
                    else "Cleanup backup is still within its retention period."
                )
                category = "cleanup_backups"
                action = "delete_path" if old else ""
            else:
                tier = "protected"
                category = (
                    "hud_configuration"
                    if name.casefold() in _HUD_PROTECTED_NAMES
                    or name.casefold().startswith("work-overlay-")
                    else "hud_unknown"
                )
                reason = "HUD configuration, state, databases, and unknown files are protected."
                action = ""
            items.append(
                self._new_item(
                    category=category,
                    tier=tier,
                    label=(
                        "HUD diagnostics"
                        if category == "hud_diagnostics"
                        else "HUD overlay history"
                        if category == "hud_overlay_history"
                        else "Old cleanup backup"
                        if category == "cleanup_backups"
                        else "Protected HUD data"
                    ),
                    measured=measured,
                    impact=(
                        "Old HUD diagnostics will no longer be available."
                        if tier == "safe"
                        else "Protected HUD data remains unchanged."
                    ),
                    path=path,
                    approved_root=root,
                    requires_offline=tier == "safe",
                    requires_backup=False,
                    blocked_reason=reason,
                    related_processes=("codex-usage-hud",) if tier == "safe" else (),
                    action=action,
                )
            )
        return items

    def _scan_cache_definition(
        self,
        definition: CacheDefinition,
        now: float,
        *,
        running: set[str],
        process_error: str,
    ) -> list[CleanupItem]:
        root = _absolute_path(definition.path)
        if not root.exists():
            return []
        blocked_reason = process_error if definition.related_processes else ""
        if not blocked_reason and _process_family_active(
            running, definition.related_processes
        ):
            blocked_reason = "A related application is currently running."
        if definition.mode == "expired_children":
            try:
                root_info = os.lstat(root)
                if _is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
                    raise OSError("unsafe temp root")
                with os.scandir(root) as entries:
                    paths = [Path(entry.path) for entry in entries]
            except OSError:
                measured = _measure_path(root)
                return [
                    self._new_item(
                        category=definition.category,
                        tier="protected",
                        label=definition.label,
                        measured=measured,
                        impact=definition.impact,
                        path=root,
                        approved_root=root.parent,
                        blocked_reason="Cache root could not be verified.",
                        related_processes=definition.related_processes,
                    )
                ]
            items: list[CleanupItem] = []
            for path in sorted(paths, key=lambda value: value.name.casefold()):
                measured = _measure_path(path)
                recent = now - measured.mtime < max(0.0, definition.min_age_seconds)
                unsafe = measured.contains_reparse or measured.errors
                reason = blocked_reason
                if unsafe:
                    reason = "Cleanup data contains a reparse point or unreadable entry."
                elif recent:
                    reason = "Cleanup data contains files newer than the retention threshold."
                tier: CleanupTier = "protected" if reason else definition.tier
                items.append(
                    self._new_item(
                        category=definition.category,
                        tier=tier,
                        label=definition.label,
                        measured=measured,
                        impact=definition.impact,
                        path=path,
                        approved_root=root,
                        retention=(
                            _duration_label(definition.min_age_seconds)
                            if definition.min_age_seconds > 0
                            else "All items"
                        ),
                        blocked_reason=reason,
                        related_processes=definition.related_processes,
                        action="delete_path" if tier != "protected" else "",
                    )
                )
            return items
        measured = _measure_path(root)
        reason = blocked_reason
        if measured.contains_reparse or measured.errors:
            reason = "Cache data contains a reparse point or unreadable entry."
        tier = "protected" if reason else definition.tier
        return [
            self._new_item(
                category=definition.category,
                tier=tier,
                label=definition.label,
                measured=measured,
                impact=definition.impact,
                path=root,
                approved_root=root.parent,
                blocked_reason=reason,
                related_processes=definition.related_processes,
                action="delete_path" if tier != "protected" else "",
            )
        ]

    def _scan_old_backups(self, now: float) -> list[CleanupItem]:
        items: list[CleanupItem] = []
        for root in self.backup_roots:
            if _path_key(root) == _path_key(_absolute_path(self.hud_runtime_root)):
                continue
            try:
                root_info = os.lstat(root)
                if _is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
                    continue
                with os.scandir(root) as entries:
                    paths = [
                        Path(entry.path)
                        for entry in entries
                        if _OLD_LOG_BACKUP_RE.fullmatch(entry.name)
                    ]
            except OSError:
                continue
            for path in paths:
                measured = _measure_path(path)
                old = now - measured.mtime >= DEFAULT_BACKUP_MIN_AGE_SECONDS
                safe = old and not measured.contains_reparse and not measured.errors
                items.append(
                    self._new_item(
                        category="cleanup_backups",
                        tier="safe" if safe else "protected",
                        label="Old cleanup backup",
                        measured=measured,
                        impact="A previous local cleanup backup will be removed.",
                        path=path,
                        approved_root=root,
                        retention=_duration_label(DEFAULT_BACKUP_MIN_AGE_SECONDS),
                        blocked_reason="Backup is recent or cannot be verified."
                        if not safe
                        else "",
                        action="delete_path" if safe else "",
                    )
                )
        return items

    def _scan_sqlite_target(self, target: SQLiteTarget, now: float) -> CleanupItem:
        path = _absolute_path(target.path)
        try:
            audit = audit_sqlite_target(
                target,
                now=now,
                default_log_retention_hours=self.log_retention_hours,
                default_background_retention_days=self.background_retention_days,
            )
            measured = _measure_path(path)
        except Exception as exc:
            measured = (
                _measure_path(path)
                if path.exists()
                else _MeasuredPath(0, 0, 0, "", False, 1)
            )
            return self._new_item(
                category="sqlite_history",
                tier="protected",
                label="Protected SQLite history",
                measured=measured,
                impact="The database remains unchanged.",
                path=path if path.exists() else None,
                approved_root=path.parent if path.exists() else None,
                blocked_reason=_safe_error(exc),
            )
        retention = _duration_label(audit.retention_seconds)
        if audit.deletable_rows <= 0:
            return self._new_item(
                category=(
                    "codex_logs_history"
                    if target.kind == "logs"
                    else "background_usage_history"
                ),
                tier="protected",
                label="Retained local history",
                measured=measured,
                impact="No history is older than the configured retention period.",
                path=path,
                approved_root=path.parent,
                retention=retention,
                blocked_reason="Nothing is currently eligible for row-level cleanup.",
                size=0,
            )
        return self._new_item(
            category=(
                "codex_logs_history"
                if target.kind == "logs"
                else "background_usage_history"
            ),
            tier="consent",
            label=(
                "Old Codex diagnostics"
                if target.kind == "logs"
                else "Old background usage history"
            ),
            measured=measured,
            impact="History older than the retention period will be permanently removed.",
            path=path,
            approved_root=path.parent,
            retention=retention,
            requires_offline=True,
            requires_backup=True,
            requires_codex_close=True,
            related_processes=("Codex", "codex-usage-hud"),
            action="sqlite",
            fingerprint=audit.group_fingerprint,
            sqlite_audit=audit,
            size=audit.estimated_bytes,
        )


def _duration_label(seconds: float) -> str:
    value = max(0, int(seconds))
    if value and value % 86400 == 0:
        days = value // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    if value and value % 3600 == 0:
        hours = value // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    return f"{value} seconds"


def _safe_error(error: BaseException) -> str:
    if isinstance(error, SafeCleanupError):
        return str(error)[:240]
    if isinstance(error, sqlite3.Error):
        return "SQLite database could not be safely audited."
    return error.__class__.__name__


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    destination = _absolute_path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_json(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_envelope(path: Path, *, expected_format: str) -> Mapping[str, object]:
    source = _absolute_path(path)
    try:
        if os.lstat(source).st_size > MAX_PLAN_BYTES:
            raise CleanupPlanError("cleanup file is too large")
        raw = json.loads(source.read_text(encoding="utf-8"))
    except CleanupPlanError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise CleanupPlanError("cleanup file could not be read") from exc
    envelope = _mapping(raw, "cleanup file")
    if envelope.get("format") != expected_format:
        raise CleanupPlanError("cleanup file format is invalid")
    payload = _mapping(envelope.get("payload"), "cleanup payload")
    expected_digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if not secrets.compare_digest(str(envelope.get("digest") or ""), expected_digest):
        raise CleanupPlanError("cleanup file integrity check failed")
    return payload


def write_maintenance_plan(path: Path, plan: MaintenancePlan) -> None:
    payload = plan.to_dict()
    _atomic_write_json(
        path,
        {
            "format": PLAN_FORMAT,
            "digest": hashlib.sha256(_canonical_json(payload)).hexdigest(),
            "payload": payload,
        },
    )


def read_maintenance_plan(path: Path) -> MaintenancePlan:
    return MaintenancePlan.from_dict(_read_envelope(path, expected_format=PLAN_FORMAT))


def write_maintenance_result(path: Path, result: MaintenanceResult) -> None:
    payload = result.to_dict()
    _atomic_write_json(
        path,
        {
            "format": RESULT_FORMAT,
            "digest": hashlib.sha256(_canonical_json(payload)).hexdigest(),
            "payload": payload,
        },
    )


def read_maintenance_result(path: Path) -> MaintenanceResult:
    return MaintenanceResult.from_dict(
        _read_envelope(path, expected_format=RESULT_FORMAT)
    )


def wait_for_plan_processes(
    plan: MaintenancePlan,
    *,
    pid_active: Callable[[int], bool] = _pid_is_active,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    pids = set(plan.wait_pids)
    if plan.parent_pid > 0:
        pids.add(plan.parent_pid)
    deadline = float(monotonic()) + max(0.0, plan.wait_timeout_seconds)
    while True:
        active = []
        for pid in sorted(pids):
            try:
                if pid_active(pid):
                    active.append(pid)
            except Exception:
                active.append(pid)
        if not active:
            return True
        if float(monotonic()) >= deadline:
            return False
        sleep(min(0.1, max(0.0, deadline - float(monotonic()))))


def _validate_plan_action(action: MaintenanceAction) -> Path:
    path = _absolute_path(action.path)
    root = _absolute_path(action.approved_root)
    resolved = _secure_existing_path(root, path)
    if _path_key(resolved) != _path_key(path):
        raise SafeCleanupError("maintenance path changed")
    current_lstat = _lstat_tuple(os.lstat(resolved))
    if (
        not _same_append_only_file(action.lstat, current_lstat)
        if action.allows_growth
        else current_lstat != action.lstat
    ):
        raise SafeCleanupError("maintenance path metadata changed")
    if action.kind == "sqlite":
        if action.sqlite_kind not in {"logs", "background"}:
            raise SafeCleanupError("maintenance plan has an unknown SQLite kind")
        if _sqlite_group_fingerprint(resolved) != action.fingerprint:
            raise SafeCleanupError("SQLite runtime group changed")
    else:
        measured = _measure_path(resolved)
        if (
            measured.contains_reparse
            or measured.errors
            or (not action.allows_growth and measured.fingerprint != action.fingerprint)
        ):
            raise SafeCleanupError("maintenance path fingerprint changed")
    return resolved


def _validate_plan_backup_directory(
    backup_directory: Path, actions: Sequence[MaintenanceAction]
) -> None:
    backup_key = _path_key(backup_directory)
    for action in actions:
        if action.kind != "delete_path":
            continue
        target_key = _path_key(Path(action.path))
        try:
            common = os.path.commonpath((backup_key, target_key))
        except ValueError:
            continue
        if common == target_key:
            raise CleanupPlanError("backup directory is inside a selected cleanup item")


def _integrity_check(connection: sqlite3.Connection) -> None:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    if row is None or str(row[0]).casefold() != "ok":
        raise SafeCleanupError("SQLite integrity check failed")


def _backup_database(
    source: Path,
    backup: Path,
    *,
    disk_usage: Callable[[Path], Any],
) -> int:
    if backup.exists():
        raise SafeCleanupError("cleanup backup already exists")
    required = max(1, _sqlite_group_size(source))
    available = int(getattr(disk_usage(backup.parent), "free", 0) or 0)
    if available < required:
        raise SafeCleanupError("backup destination does not have enough free space")
    try:
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with (
            closing(_read_only_sqlite(source)) as source_connection,
            closing(sqlite3.connect(backup, timeout=2.0)) as backup_connection,
        ):
            source_connection.backup(backup_connection)
            _integrity_check(backup_connection)
        return max(0, int(os.lstat(backup).st_size))
    except Exception:
        try:
            backup.unlink()
        except FileNotFoundError:
            pass
        raise


def _delete_sqlite_rows(
    connection: sqlite3.Connection, action: MaintenanceAction
) -> int:
    if action.sqlite_kind == "logs":
        cursor = connection.execute("DELETE FROM logs WHERE ts < ?", (action.cutoff,))
        return max(0, int(cursor.rowcount))
    deleted = 0
    cursor = connection.execute(
        "DELETE FROM background_requests WHERE occurred_at < ?", (action.cutoff,)
    )
    deleted += max(0, int(cursor.rowcount))
    cursor = connection.execute(
        """
        DELETE FROM background_events
        WHERE last_seen_at < ?
          AND NOT EXISTS (
            SELECT 1 FROM background_requests
            WHERE background_requests.event_id=background_events.event_id
              AND background_requests.occurred_at >= ?
          )
        """,
        (action.cutoff, action.cutoff),
    )
    deleted += max(0, int(cursor.rowcount))
    cursor = connection.execute(
        "DELETE FROM process_evidence WHERE last_seen_at < ?", (action.cutoff,)
    )
    deleted += max(0, int(cursor.rowcount))
    return deleted


def _critical_sqlite_check(
    connection: sqlite3.Connection, action: MaintenanceAction
) -> None:
    if action.sqlite_kind == "logs":
        connection.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM logs").fetchone()
        return
    version = connection.execute(
        "SELECT value FROM metadata WHERE key='schema_version'"
    ).fetchone()
    schema_version = str(version[0]) if version is not None else ""
    if schema_version not in _BACKGROUND_SCHEMA_VERSIONS:
        raise SafeCleanupError("background audit schema version changed")
    connection.execute("SELECT COUNT(*) FROM scan_state").fetchone()


def _restore_database(source: Path, backup: Path, plan_id: str) -> bool:
    temporary = source.with_name(f".{source.name}.restore-{plan_id}.tmp")
    quarantine = source.with_name(f"{source.name}.failed-{plan_id}")
    if temporary.exists() or quarantine.exists():
        return False
    try:
        shutil.copy2(backup, temporary)
        with closing(_read_only_sqlite(temporary)) as connection:
            _integrity_check(connection)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{source}{suffix}")
            try:
                sidecar.unlink()
            except FileNotFoundError:
                pass
        os.replace(source, quarantine)
        try:
            os.replace(temporary, source)
        except Exception:
            if not source.exists() and quarantine.exists():
                os.replace(quarantine, source)
            raise
        with closing(_read_only_sqlite(source)) as connection:
            _integrity_check(connection)
        return True
    except Exception:
        return False
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _run_delete_action(
    action: MaintenanceAction,
    *,
    lock_probe: Callable[[Path], bool],
    remove_path: Callable[[Path], None],
    running_process_names: Callable[[], Iterable[str]],
) -> MaintenanceActionResult:
    try:
        if action.related_processes and not action.requires_offline:
            _ensure_related_processes_inactive(
                running_process_names,
                action.related_processes,
                active_error="a related application is currently running",
            )
        path = _validate_plan_action(action)
        if lock_probe(path):
            raise SafeCleanupError("cleanup item is locked")
        measured = _measure_path(path)
        remove_path(path)
        if path.exists():
            raise SafeCleanupError("cleanup item still exists after removal")
        return MaintenanceActionResult(
            item_id=action.item_id,
            category=action.category,
            state="deleted",
            estimated_bytes=action.estimated_bytes,
            actual_bytes=measured.size,
        )
    except SafeCleanupError as exc:
        # Best-effort delete: occupancy / TOCTOU / reparse are skips, not job failure.
        return MaintenanceActionResult(
            item_id=action.item_id,
            category=action.category,
            state="skipped",
            estimated_bytes=action.estimated_bytes,
            error=_safe_error(exc),
        )
    except Exception as exc:
        return MaintenanceActionResult(
            item_id=action.item_id,
            category=action.category,
            state="failed",
            estimated_bytes=action.estimated_bytes,
            error=_safe_error(exc),
        )


def _run_sqlite_action(
    action: MaintenanceAction,
    *,
    plan_id: str,
    backup_directory: Path,
    lock_probe: Callable[[Path], bool],
    disk_usage: Callable[[Path], Any],
    running_process_names: Callable[[], Iterable[str]],
    failure_hook: Callable[[str, Path], None] | None,
) -> MaintenanceActionResult:
    backup_path = backup_directory / f"{Path(action.path).name}.pre-cleanup-{plan_id}"
    backup_bytes = 0
    mutated = False
    deleted_rows = 0
    path: Path | None = None
    before_bytes = 0
    try:
        path = _validate_plan_action(action)
        for member in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            if member.exists() and lock_probe(member):
                raise SafeCleanupError("SQLite runtime group is locked")
        with closing(_read_only_sqlite(path)) as connection:
            if (
                _schema_signature(connection, action.sqlite_kind)
                != action.schema_signature
            ):
                raise SafeCleanupError("SQLite schema changed after planning")
            _integrity_check(connection)
        _ensure_related_processes_inactive(
            running_process_names,
            _SQLITE_RELATED_PROCESSES,
            active_error="Codex started before SQLite backup",
        )
        if failure_hook is not None:
            failure_hook("before_backup", path)
        backup_bytes = _backup_database(path, backup_path, disk_usage=disk_usage)
        if failure_hook is not None:
            failure_hook("after_backup", path)
        _ensure_related_processes_inactive(
            running_process_names,
            _SQLITE_RELATED_PROCESSES,
            active_error="Codex started before SQLite maintenance",
        )
        before_bytes = _sqlite_group_size(path)
        with closing(sqlite3.connect(path, timeout=2.0)) as connection:
            connection.execute("PRAGMA busy_timeout = 2000")
            connection.execute("PRAGMA foreign_keys = ON")
            _integrity_check(connection)
            if (
                _schema_signature(connection, action.sqlite_kind)
                != action.schema_signature
            ):
                raise SafeCleanupError("SQLite schema changed after backup")
            connection.execute("BEGIN EXCLUSIVE")
            try:
                deleted_rows = _delete_sqlite_rows(connection, action)
                connection.commit()
                mutated = deleted_rows > 0
            except Exception:
                connection.rollback()
                raise
            if failure_hook is not None:
                failure_hook("after_commit", path)
            if action.estimated_bytes >= action.vacuum_min_bytes and deleted_rows > 0:
                checkpoint = connection.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
                if checkpoint is not None and int(checkpoint[0]) != 0:
                    raise SafeCleanupError("SQLite checkpoint remained busy")
                connection.execute("VACUUM")
            if failure_hook is not None:
                failure_hook("before_postcheck", path)
            _integrity_check(connection)
            if (
                _schema_signature(connection, action.sqlite_kind)
                != action.schema_signature
            ):
                raise SafeCleanupError("SQLite schema changed during maintenance")
            _critical_sqlite_check(connection, action)
        after_bytes = _sqlite_group_size(path)
        return MaintenanceActionResult(
            item_id=action.item_id,
            category=action.category,
            state="completed",
            estimated_bytes=action.estimated_bytes,
            actual_bytes=max(0, before_bytes - after_bytes),
            deleted_rows=deleted_rows,
            backup_path=os.fspath(backup_path),
            backup_bytes=backup_bytes,
        )
    except Exception as exc:
        restored = False
        if mutated and path is not None and backup_path.exists():
            restored = _restore_database(path, backup_path, plan_id)
        return MaintenanceActionResult(
            item_id=action.item_id,
            category=action.category,
            state="restored" if restored else "failed",
            estimated_bytes=action.estimated_bytes,
            backup_path=os.fspath(backup_path) if backup_path.exists() else "",
            backup_bytes=backup_bytes,
            restored=restored,
            error=_safe_error(exc),
        )


def run_maintenance_plan(
    plan: MaintenancePlan,
    *,
    clock: Callable[[], float] = time.time,
    pid_active: Callable[[int], bool] = _pid_is_active,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    lock_probe: Callable[[Path], bool] = path_is_locked,
    remove_path: Callable[[Path], None] = _remove_path,
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
    running_process_names: Callable[[], Iterable[str]] = _system_running_process_names,
    sqlite_failure_hook: Callable[[str, Path], None] | None = None,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> MaintenanceResult:
    """Run a validated plan without killing processes or launching applications."""

    plan = MaintenancePlan.from_dict(plan.to_dict())
    started_at = float(clock())
    if started_at > plan.expires_at:
        skipped = tuple(
            MaintenanceActionResult(
                item_id=action.item_id,
                category=action.category,
                state="skipped",
                estimated_bytes=action.estimated_bytes,
                error="cleanup plan expired before maintenance started",
            )
            for action in plan.actions
        )
        return MaintenanceResult(
            plan_id=plan.id,
            state="failed",
            started_at=started_at,
            completed_at=float(clock()),
            actions=skipped,
            error="cleanup plan expired before maintenance started",
        )
    if not wait_for_plan_processes(
        plan, pid_active=pid_active, monotonic=monotonic, sleep=sleep
    ):
        skipped = tuple(
            MaintenanceActionResult(
                item_id=action.item_id,
                category=action.category,
                state="skipped",
                estimated_bytes=action.estimated_bytes,
                error="required processes did not exit before the timeout",
            )
            for action in plan.actions
        )
        # Do not surface this as a hard failure: nothing was mutated.
        return MaintenanceResult(
            plan_id=plan.id,
            state="partial",
            started_at=started_at,
            completed_at=float(clock()),
            actions=skipped,
            error="required processes are still active; cleanup items were skipped",
        )
    if float(clock()) > plan.expires_at:
        skipped = tuple(
            MaintenanceActionResult(
                item_id=action.item_id,
                category=action.category,
                state="skipped",
                estimated_bytes=action.estimated_bytes,
                error="cleanup plan expired while waiting for processes",
            )
            for action in plan.actions
        )
        return MaintenanceResult(
            plan_id=plan.id,
            state="failed",
            started_at=started_at,
            completed_at=float(clock()),
            actions=skipped,
            error="cleanup plan expired while waiting for processes",
        )
    sqlite_actions = [action for action in plan.actions if action.kind == "sqlite"]
    backup_directory: Path | None = None
    if sqlite_actions:
        if not plan.backup_directory:
            raise CleanupPlanError("SQLite maintenance plan has no backup directory")
        requested_backup_directory = _absolute_path(plan.backup_directory)
        _validate_plan_backup_directory(requested_backup_directory, plan.actions)
        backup_directory = _canonical_directory(requested_backup_directory, create=True)
        _validate_plan_backup_directory(backup_directory, plan.actions)
    results: list[MaintenanceActionResult] = []
    total_actions = max(1, len(plan.actions))

    def _emit_progress(
        *,
        index: int,
        action: MaintenanceAction,
        stage: str,
        result: MaintenanceActionResult | None = None,
    ) -> None:
        if not callable(progress_callback):
            return
        completed = len(results)
        # Reserve 0-24 for orchestration; action work occupies 25-95.
        if stage == "start":
            fraction = max(0, index - 1) / total_actions
        else:
            fraction = index / total_actions
        progress = 25 + int(70 * fraction)
        rows: list[dict[str, object]] = [
            {
                "id": item.item_id,
                "category": item.category,
                "state": item.state,
                "estimatedBytes": int(item.estimated_bytes),
                "actualBytes": int(item.actual_bytes),
                "deletedRows": int(item.deleted_rows),
                "error": item.error,
            }
            for item in results
        ]
        if result is None:
            rows.append(
                {
                    "id": action.item_id,
                    "category": action.category,
                    "state": "running",
                    "estimatedBytes": int(action.estimated_bytes),
                    "actualBytes": 0,
                    "deletedRows": 0,
                    "error": "",
                }
            )
        progress_callback(
            {
                "index": int(index),
                "total": int(total_actions),
                "completed": int(completed if result is None else completed),
                "stage": stage,
                "itemId": action.item_id,
                "category": action.category,
                "kind": action.kind,
                "progress": min(95, max(25, progress)),
                "results": rows,
                "actualBytes": sum(int(item.actual_bytes) for item in results),
                "estimatedBytes": sum(
                    int(item.estimated_bytes) for item in plan.actions
                ),
            }
        )

    for index, action in enumerate(plan.actions, start=1):
        _emit_progress(index=index, action=action, stage="start")
        if action.kind == "delete_path":
            result = _run_delete_action(
                action,
                lock_probe=lock_probe,
                remove_path=remove_path,
                running_process_names=running_process_names,
            )
        else:
            if backup_directory is None:
                raise CleanupPlanError("SQLite maintenance requires a backup directory")
            result = _run_sqlite_action(
                action,
                plan_id=plan.id,
                backup_directory=backup_directory,
                lock_probe=lock_probe,
                disk_usage=disk_usage,
                running_process_names=running_process_names,
                failure_hook=sqlite_failure_hook,
            )
        results.append(result)
        _emit_progress(index=index, action=action, stage="done", result=result)
    success = sum(1 for result in results if result.state in {"deleted", "completed"})
    skipped = sum(1 for result in results if result.state == "skipped")
    restored = sum(1 for result in results if result.state == "restored")
    hard_failures = sum(1 for result in results if result.state == "failed")
    if hard_failures and not success and not restored:
        state = "failed"
    elif restored and not success and not hard_failures:
        state = "restored"
    elif success and not hard_failures and not skipped and not restored:
        state = "completed"
    else:
        # Mixed outcomes, pure skips, or soft issues become partial (not a job failure).
        state = "partial"
    incomplete = hard_failures + skipped + restored
    return MaintenanceResult(
        plan_id=plan.id,
        state=state,
        started_at=started_at,
        completed_at=float(clock()),
        actions=tuple(results),
        error=(
            f"{incomplete} cleanup action(s) did not complete"
            if incomplete and state != "completed"
            else ""
        ),
    )


def _helper_result_path(
    plan_path: Path,
    plan: MaintenancePlan,
    explicit_result_path: Path | None,
) -> tuple[Path, Path]:
    plan_source = _absolute_path(plan_path)
    try:
        plan_info = os.lstat(plan_source)
        plan_parent = plan_source.parent.resolve(strict=True)
    except OSError as exc:
        raise CleanupPlanError("cleanup plan path could not be verified") from exc
    if _is_reparse(plan_info) or not stat.S_ISREG(plan_info.st_mode):
        raise CleanupPlanError("cleanup plan must be a regular file")
    if _is_reparse(os.lstat(plan_parent)):
        raise CleanupPlanError("cleanup plan directory cannot be a reparse point")

    embedded = _absolute_path(plan.result_path) if plan.result_path else None
    explicit = _absolute_path(explicit_result_path) if explicit_result_path else None
    if (
        embedded is not None
        and explicit is not None
        and _path_key(embedded) != _path_key(explicit)
    ):
        raise CleanupPlanError(
            "cleanup result path conflicts with the maintenance plan"
        )
    result = embedded or explicit
    if result is None:
        raise CleanupPlanError("cleanup maintenance plan has no result path")
    if _path_key(result) == _path_key(plan_source):
        raise CleanupPlanError("cleanup result cannot replace the maintenance plan")
    try:
        result_parent = result.parent.resolve(strict=True)
    except OSError as exc:
        raise CleanupPlanError(
            "cleanup result directory could not be verified"
        ) from exc
    if _path_key(result_parent) != _path_key(plan_parent):
        raise CleanupPlanError("cleanup result must stay beside the maintenance plan")
    if _is_reparse(os.lstat(result_parent)):
        raise CleanupPlanError("cleanup result directory cannot be a reparse point")
    return plan_source, result


def _launch_detached_command(
    command: Sequence[str],
    *,
    launcher: Callable[..., object] = subprocess.Popen,
) -> object:
    return launcher(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if sys.platform.startswith("win")
            else 0
        ),
        start_new_session=not sys.platform.startswith("win"),
    )


def _launch_restart_command(command: Sequence[str]) -> object:
    return _launch_detached_command(command)


def reveal_cleanup_path(
    path: Path,
    *,
    platform: str | None = None,
    launcher: Callable[..., object] = subprocess.Popen,
) -> tuple[str, ...]:
    """Open a verified cleanup target in the native file manager.

    The caller must resolve the path from a current inventory item first.  This
    helper performs a final plain-file/directory check and accepts an injected
    launcher so tests never open a real Explorer or Finder window.
    """

    absolute = _absolute_path(path)
    try:
        info = os.lstat(absolute)
    except OSError as exc:
        raise SafeCleanupError("cleanup target is no longer available") from exc
    if _is_reparse(info):
        raise SafeCleanupError("reparse paths are protected")
    is_directory = stat.S_ISDIR(info.st_mode)
    if not is_directory and not stat.S_ISREG(info.st_mode):
        raise SafeCleanupError("cleanup target is not a file or directory")

    platform_name = (platform or sys.platform).casefold()
    if platform_name.startswith("win"):
        command = (
            ("explorer.exe", os.fspath(absolute))
            if is_directory
            else ("explorer.exe", "/select,", os.fspath(absolute))
        )
    elif platform_name == "darwin":
        command = (
            ("open", os.fspath(absolute))
            if is_directory
            else ("open", "-R", os.fspath(absolute))
        )
    else:
        raise SafeCleanupError("当前平台不支持打开清理目标")
    try:
        _launch_detached_command(command, launcher=launcher)
    except OSError as exc:
        raise SafeCleanupError("无法打开清理目标位置") from exc
    return command


def _failed_maintenance_result(
    plan: MaintenancePlan,
    error: Exception,
) -> MaintenanceResult:
    now = time.time()
    message = _safe_error(error)
    return MaintenanceResult(
        plan_id=plan.id,
        state="failed",
        started_at=now,
        completed_at=now,
        actions=tuple(
            MaintenanceActionResult(
                item_id=action.item_id,
                category=action.category,
                state="skipped",
                estimated_bytes=action.estimated_bytes,
                error=message,
            )
            for action in plan.actions
        ),
        error=message,
    )


def run_maintenance_plan_file(
    plan_path: Path,
    result_path: Path | None = None,
    *,
    restart_runner: Callable[[Sequence[str]], object] | None = None,
    **kwargs: Any,
) -> MaintenanceResult:
    """Consume, execute, persist, and optionally restart one helper plan."""

    plan = read_maintenance_plan(plan_path)
    plan_source, result_target = _helper_result_path(plan_path, plan, result_path)
    try:
        plan_source.unlink()
    except OSError as exc:
        raise CleanupPlanError(
            "cleanup maintenance plan could not be consumed"
        ) from exc

    try:
        result = run_maintenance_plan(plan, **kwargs)
    except Exception as exc:
        result = _failed_maintenance_result(plan, exc)

    if not plan.restart_command:
        write_maintenance_result(result_target, result)
        return result

    pending = replace(
        result,
        restart_requested=True,
        restart_state="pending",
        restart_error="",
    )
    write_maintenance_result(result_target, pending)
    runner = restart_runner or _launch_restart_command
    try:
        runner(plan.restart_command)
    except Exception as exc:
        completed = replace(
            pending,
            restart_state="failed",
            restart_error=_safe_error(exc),
        )
    else:
        completed = replace(pending, restart_state="launched")
    write_maintenance_result(result_target, completed)
    return completed


__all__ = [
    "CacheDefinition",
    "CleanupInventory",
    "CleanupItem",
    "CleanupPlanError",
    "MaintenanceAction",
    "MaintenanceActionResult",
    "MaintenancePlan",
    "MaintenanceResult",
    "PLAN_VERSION",
    "RESULT_VERSION",
    "SQLiteTarget",
    "SafeCleanupError",
    "SafeCleanupManager",
    "audit_sqlite_target",
    "default_hud_runtime_root",
    "platform_cache_definitions",
    "reveal_cleanup_path",
    "read_maintenance_plan",
    "read_maintenance_result",
    "run_maintenance_plan",
    "run_maintenance_plan_file",
    "wait_for_plan_processes",
    "write_maintenance_plan",
    "write_maintenance_result",
]
