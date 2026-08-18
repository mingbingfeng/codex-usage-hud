"""Safe local-store orchestration for permanent session deletion."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PureWindowsPath
import secrets
import shutil
import sqlite3
import time
import uuid

from .parser import classify_session_client
from .session_materializer import (
    SessionMaterializationError,
    materialize_forked_rollout,
)


DEFAULT_CONFIRMATION_TTL_SECONDS = 300.0
_ACTIVE_EDGE_STATES = {"active", "in_progress", "pending", "running", "starting"}
# Fork writers can take a short moment to create both the rollout and the
# state-db record.  Transfer-only retries are bounded and never run as HUD
# background polling.
_TRANSFER_READY_RETRY_DELAYS_SECONDS = (0.0, 0.1, 0.25, 0.5, 0.75, 1.25)


class SessionCleanupError(RuntimeError):
    """Raised when a permanent-delete request cannot be proven safe."""


@dataclass(frozen=True)
class SessionDeleteCapability:
    available: bool
    reason: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "available": bool(self.available),
            "command": "local SQLite transaction" if self.available else "",
            "reason": str(self.reason or ""),
        }


@dataclass(frozen=True)
class _ThreadRecord:
    session_id: str
    rollout_path: Path | None
    title: str
    cwd: str
    archived: bool
    updated_at_ms: int


@dataclass(frozen=True)
class _SessionMetadata:
    model_provider: str = "unknown"
    client_kind: str = "unknown"


@dataclass(frozen=True)
class SessionCleanupItem:
    id: str
    title: str
    workdir_name: str
    updated_at: str
    status: str
    archived: bool
    size: int
    descendant_count: int
    selectable: bool
    blocked_reason: str
    transferable: bool = True
    transfer_blocked_reason: str = ""
    model_provider: str = "unknown"
    client_kind: str = "unknown"
    _session_id: str = field(default="", repr=False, compare=False)
    _cwd: str = field(default="", repr=False, compare=False)
    _descendant_ids: tuple[str, ...] = field(default=(), repr=False, compare=False)
    _rollout_paths: tuple[Path, ...] = field(default=(), repr=False, compare=False)

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "workdirName": self.workdir_name,
            "updatedAt": self.updated_at,
            "status": self.status,
            "archived": bool(self.archived),
            "bytes": max(0, int(self.size)),
            "descendantCount": max(0, int(self.descendant_count)),
            "selectable": bool(self.selectable),
            "blockedReason": self.blocked_reason,
            "transferable": bool(self.transferable),
            "transferBlockedReason": self.transfer_blocked_reason,
            "modelProvider": self.model_provider,
            "clientKind": self.client_kind,
        }


@dataclass(frozen=True)
class _Confirmation:
    revision: str
    item_ids: tuple[str, ...]
    expires_at: float


UsageSnapshotPrepare = Callable[[SessionCleanupItem], object]
UsageSnapshotCommit = Callable[[object], None]
UsageSnapshotDiscard = Callable[[object], None]
DesktopSourceLifecycle = Callable[[str, str], Mapping[str, object]]


def _canonical_uuid(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        canonical = str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""
    return canonical if candidate.casefold() == canonical else ""


def _normalise_fork_target_uuid(value: object) -> str:
    """Canonicalise an App Server-generated target id before collision checks.

    Renderer-provided ids remain strict via :func:`_canonical_uuid`, but a
    successful App Server may serialize a UUID with uppercase hex.  Treat that
    trusted response as the same opaque id so it cannot evade either the
    existing-store or same-batch collision gates.
    """
    candidate = str(value or "").strip()
    try:
        return str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _read_write_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=rw", uri=True, timeout=5.0
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _normalized_rollout_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text or "\x00" in text:
        return None
    if os.name == "nt" and text.startswith("\\\\?\\"):
        text = text[4:]
    try:
        return Path(text).expanduser().resolve(strict=False)
    except OSError:
        return None


def _path_under(path: Path, roots: Sequence[Path]) -> bool:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(root.resolve(strict=False))
        except (OSError, ValueError):
            continue
        return True
    return False


def _workdir_leaf(value: object) -> str:
    text = str(value or "").strip().rstrip("/\\")
    if not text:
        return ""
    if "\\" in text:
        return PureWindowsPath(text).name
    return Path(text).name


def _updated_at_iso(value: int) -> str:
    if value <= 0:
        return ""
    try:
        return (
            datetime.fromtimestamp(value / 1000.0)
            .astimezone()
            .isoformat(timespec="seconds")
        )
    except (OSError, OverflowError, ValueError):
        return ""


def _session_metadata(path: Path | None) -> _SessionMetadata:
    """Read only the session metadata record needed for inventory filters."""
    if path is None:
        return _SessionMetadata()
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    not isinstance(record, Mapping)
                    or record.get("type") != "session_meta"
                ):
                    continue
                payload = record.get("payload")
                if not isinstance(payload, Mapping):
                    return _SessionMetadata()
                return _SessionMetadata(
                    model_provider=(
                        str(payload.get("model_provider") or "").strip().lower()
                        or "unknown"
                    ),
                    client_kind=classify_session_client(
                        payload.get("originator"),
                        payload.get("source"),
                    ),
                )
    except (OSError, UnicodeError):
        pass
    return _SessionMetadata()


def _missing_paginated_history_source(
    path: Path | None,
    session_id: str,
    records: Mapping[str, _ThreadRecord],
    allowed_roots: Sequence[Path],
) -> bool:
    """Return whether a paginated rollout points at an unavailable source."""
    if path is None:
        return False
    try:
        with path.open("r", encoding="utf-8") as handle:
            first_line = handle.readline()
        record = json.loads(first_line)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(record, Mapping):
        return False
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        return False
    if str(payload.get("history_mode") or "").strip().casefold() != "paginated":
        return False
    history_base = payload.get("history_base")
    # A paginated root starts a new history and legitimately has no base.
    if history_base is None:
        return False
    base_id = (
        _canonical_uuid(history_base.get("thread_id"))
        if isinstance(history_base, Mapping)
        else ""
    )
    if not base_id or base_id == session_id:
        return True
    source = records.get(base_id)
    source_path = source.rollout_path if source is not None else None
    return (
        source_path is None
        or not _path_under(source_path, allowed_roots)
        or not source_path.is_file()
    )


class SessionCleanupManager:
    """Build a root-session inventory and delete verified local session trees."""

    def __init__(
        self,
        *,
        state_db_path: Path,
        sessions_root: Path,
        session_index_path: Path,
        current_session_ids: Callable[[], Iterable[str]] | None = None,
        active_session_ids: Callable[[], Iterable[str]] | None = None,
        usage_snapshot_prepare: UsageSnapshotPrepare | None = None,
        usage_snapshot_commit: UsageSnapshotCommit | None = None,
        usage_snapshot_discard: UsageSnapshotDiscard | None = None,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] | None = None,
        confirmation_ttl_seconds: float = DEFAULT_CONFIRMATION_TTL_SECONDS,
        transfer_ready_retry_delays: Sequence[
            float
        ] = _TRANSFER_READY_RETRY_DELAYS_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.state_db_path = Path(state_db_path)
        self.sessions_root = Path(sessions_root)
        self.session_index_path = Path(session_index_path)
        self.current_session_ids = current_session_ids or (lambda: ())
        self.active_session_ids = active_session_ids or (lambda: ())
        self.usage_snapshot_prepare = usage_snapshot_prepare
        self.usage_snapshot_commit = usage_snapshot_commit
        self.usage_snapshot_discard = usage_snapshot_discard
        self.clock = clock
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self.confirmation_ttl_seconds = max(1.0, float(confirmation_ttl_seconds))
        self.transfer_ready_retry_delays = tuple(
            max(0.0, float(delay)) for delay in transfer_ready_retry_delays
        ) or (0.0,)
        self._sleep = sleep
        self._revision_counter = 0
        self._revision = ""
        self._generated_at = 0.0
        self._capability = SessionDeleteCapability(False, "Not scanned yet.")
        self._items: dict[str, SessionCleanupItem] = {}
        self._confirmations: dict[str, _Confirmation] = {}
        self._operation: dict[str, object] = self._idle_operation()
        self._unresolved_count = 0

    @staticmethod
    def _idle_operation() -> dict[str, object]:
        return {
            "id": "",
            "requestId": "",
            "action": "idle",
            "state": "idle",
            "progress": 0,
            "error": "",
        }

    def snapshot(self, *, include_sessions: bool = True) -> dict[str, object]:
        sessions = sorted(
            self._items.values(),
            key=lambda item: (item.updated_at, item.title.casefold()),
            reverse=True,
        )
        selectable = [item for item in sessions if item.selectable]
        payload: dict[str, object] = {
            "revision": self._revision,
            "generatedAt": _updated_at_iso(int(self._generated_at * 1000)),
            "capability": self._capability.to_payload(),
            "totals": {
                "sessions": len(sessions),
                "selectable": len(selectable),
                "blocked": len(sessions) - len(selectable),
                "unresolved": self._unresolved_count,
                "bytes": sum(item.size for item in sessions),
                "descendants": sum(item.descendant_count for item in sessions),
            },
            "operation": dict(self._operation),
        }
        if include_sessions:
            payload["sessions"] = [item.to_payload() for item in sessions]
        return payload

    def workdir_for_item(self, item_id: object, revision: object) -> Path | None:
        """Resolve one current inventory item without exposing its path in the payload."""
        normalized_id = str(item_id or "").strip()
        normalized_revision = str(revision or "").strip()
        if (
            not normalized_id
            or not normalized_revision
            or normalized_revision != self._revision
        ):
            return None
        item = self._items.get(normalized_id)
        if item is None or not item._session_id:
            return None
        records, _parents, _edge_states, _unsafe_ids, _unresolved = self._load_state()
        # A completed scan can replace the opaque inventory while the state DB is read.
        if (
            normalized_revision != self._revision
            or self._items.get(normalized_id) is not item
        ):
            return None
        record = records.get(item._session_id)
        raw_path = str(record.cwd or "").strip() if record is not None else ""
        if not raw_path or "\x00" in raw_path:
            return None
        try:
            path = Path(raw_path)
            return path if path.is_absolute() and path.is_dir() else None
        except OSError:
            return None

    def workdir_for_transfer_target(
        self,
        session_id: object,
        target_provider: object,
    ) -> Path | None:
        """Resolve a verified transfer target to an existing launch directory.

        The renderer only receives the opaque target id.  Re-read its current
        local rollout and metadata before launching ``codex resume`` so a stale
        result card cannot resume an unrelated Provider or arbitrary path.
        """
        canonical = _canonical_uuid(session_id)
        provider = str(target_provider or "").strip().casefold()
        if not canonical or not provider:
            return None
        records, _parents, _edge_states, _unsafe_ids, _unresolved = self._load_state()
        record = records.get(canonical)
        if (
            record is None
            or record.rollout_path is None
            or not _path_under(record.rollout_path, self._allowed_rollout_roots())
            or not record.rollout_path.is_file()
            or _session_metadata(record.rollout_path).model_provider.casefold()
            != provider
        ):
            return None
        raw_path = str(record.cwd or "").strip()
        if not raw_path or "\x00" in raw_path:
            return None
        try:
            path = Path(raw_path)
            return path if path.is_absolute() and path.is_dir() else None
        except OSError:
            return None

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

    def cancel(self, *, request_id: str = "") -> dict[str, object]:
        self._confirmations.clear()
        return self.mark_operation(
            request_id=request_id,
            action="cancel",
            state="cancelled",
            progress=100,
        )

    def probe_capability(self) -> SessionDeleteCapability:
        if not self.state_db_path.is_file():
            return SessionDeleteCapability(
                False,
                "Codex local session store is unavailable.",
            )
        try:
            with closing(_read_write_connection(self.state_db_path)) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(threads)")
                }
        except (OSError, sqlite3.Error) as exc:
            return SessionDeleteCapability(
                False,
                f"Codex local session store could not be opened ({type(exc).__name__}).",
            )
        if "threads" not in tables or not {"id", "rollout_path"}.issubset(columns):
            return SessionDeleteCapability(
                False,
                "Codex local session store schema is not recognized.",
            )
        return SessionDeleteCapability(True)

    def scan(self, *, request_id: str = "") -> dict[str, object]:
        request = str(request_id or "")
        publisher = getattr(self, "progress_publisher", None)

        def report(
            phase: str, phase_label: str, phase_index: int, progress: int
        ) -> None:
            self.mark_operation(
                request_id=request,
                action="scan",
                state="scanning",
                progress=min(99, max(0, int(progress))),
                phase=phase,
                phaseLabel=phase_label,
                phaseIndex=phase_index,
                phaseCount=3,
            )
            if callable(publisher):
                publisher(self.snapshot(include_sessions=False))

        report("sessions", "Reading session index", 1, 10)
        capability = self.probe_capability()
        records, parents, edge_states, unsafe_ids, unresolved = self._load_state()
        report("merge", "Merging root sessions and subagents", 2, 55)
        titles = self._session_index_titles()
        current_ids = self._protected_ids(self.current_session_ids)
        active_ids = self._protected_ids(self.active_session_ids)
        roots, unsafe_roots, unresolved_roots = self._root_ids(
            records,
            parents,
            unsafe_ids,
        )
        unresolved += unresolved_roots
        allowed_roots = self._allowed_rollout_roots()
        report("protect", "Checking deletion protection", 3, 60)
        items: list[SessionCleanupItem] = []
        total_roots = len(roots)
        progress_interval = max(1, total_roots // 32)
        for root_index, root_id in enumerate(roots, 1):
            descendants = self._descendants(root_id, records, parents)
            family = (root_id, *descendants)
            family_records = [records[session_id] for session_id in family]
            rollout_paths = tuple(
                record.rollout_path
                for record in family_records
                if record.rollout_path is not None
            )
            missing_rollout = any(
                record.rollout_path is None
                or not _path_under(record.rollout_path, allowed_roots)
                or not record.rollout_path.is_file()
                for record in family_records
            )
            missing_history_source = any(
                _missing_paginated_history_source(
                    record.rollout_path,
                    record.session_id,
                    records,
                    allowed_roots,
                )
                for record in family_records
            )
            current = bool(set(family) & current_ids)
            active = bool(set(family) & active_ids) or any(
                edge_states.get(session_id, "").casefold() in _ACTIVE_EDGE_STATES
                for session_id in descendants
            )
            blocked_reason = ""
            status = "archived" if records[root_id].archived else "idle"
            if not capability.available:
                blocked_reason = capability.reason
                status = "unavailable"
            elif current:
                blocked_reason = "The current session cannot be permanently deleted."
                status = "current"
            elif active:
                blocked_reason = "This session tree still has active work."
                status = "running"
            elif root_id in unsafe_roots:
                blocked_reason = "The session spawn relation could not be verified."
                status = "unresolved"
            elif missing_rollout:
                blocked_reason = "The session rollout mapping could not be verified."
                status = "unresolved"
            root = records[root_id]
            metadata = _session_metadata(root.rollout_path)
            size = 0
            for path in rollout_paths:
                try:
                    size += max(0, int(path.stat().st_size))
                except OSError:
                    pass
            items.append(
                SessionCleanupItem(
                    id=f"session-{self.token_factory()}",
                    title=(
                        root.title or titles.get(root_id) or "Untitled session"
                    ).strip(),
                    workdir_name=_workdir_leaf(root.cwd),
                    updated_at=_updated_at_iso(
                        max(record.updated_at_ms for record in family_records)
                    ),
                    status=status,
                    archived=root.archived,
                    size=size,
                    descendant_count=len(descendants),
                    selectable=not blocked_reason,
                    blocked_reason=blocked_reason,
                    transferable=not missing_history_source,
                    transfer_blocked_reason=(
                        "The paginated history source rollout could not be verified."
                        if missing_history_source
                        else ""
                    ),
                    model_provider=metadata.model_provider,
                    client_kind=metadata.client_kind,
                    _session_id=root_id,
                    _cwd=root.cwd,
                    _descendant_ids=tuple(descendants),
                    _rollout_paths=rollout_paths,
                )
            )
            if root_index == total_roots or root_index % progress_interval == 0:
                report(
                    "protect",
                    "Checking deletion protection",
                    3,
                    60 + round(39 * root_index / max(1, total_roots)),
                )
        self._revision_counter += 1
        self._revision = f"{self._revision_counter}-{self.token_factory()}"
        self._generated_at = float(self.clock())
        self._capability = capability
        self._items = {item.id: item for item in items}
        self._unresolved_count = unresolved
        self._confirmations.clear()
        self._operation = {
            "id": str(request_id or self.token_factory()),
            "requestId": request_id,
            "action": "scan",
            "state": "completed",
            "progress": 100,
            "error": "",
        }
        return self.snapshot()

    def delete_provider_history(
        self,
        provider: str,
        *,
        request_id: str = "",
    ) -> dict[str, object]:
        """Delete every safe session tree whose root belongs to one provider.

        The provider-delete dialog is already an explicit user confirmation, so
        this method performs the same scan/preview/execute safety gates as the
        session-management screen without exposing session paths to the
        renderer.
        """
        normalized_provider = str(provider or "").strip().casefold()
        if not normalized_provider:
            raise SessionCleanupError("Provider history deletion requires a provider.")
        inventory = self.scan(request_id=request_id)
        capability = inventory.get("capability")
        if not isinstance(capability, Mapping) or not bool(capability.get("available")):
            reason = str(
                capability.get("reason")
                if isinstance(capability, Mapping)
                else "Codex local session store is unavailable."
            )
            raise SessionCleanupError(
                f"无法删除 Provider {normalized_provider} 的会话历史：{reason}"
            )
        matching = [
            item
            for item in self._items.values()
            if item.model_provider.casefold() == normalized_provider
        ]
        blocked = [item for item in matching if not item.selectable]
        if blocked:
            reasons = sorted(
                {
                    str(item.blocked_reason or "").strip()
                    for item in blocked
                    if str(item.blocked_reason or "").strip()
                }
            )
            detail = "；".join(reasons[:2]) or "存在受保护的会话"
            raise SessionCleanupError(
                f"Provider {normalized_provider} 仍有 {len(blocked)} 个受保护会话，未执行历史删除：{detail}"
            )
        if not matching:
            return self.mark_operation(
                request_id=request_id,
                action="providerHistoryDelete",
                state="completed",
                progress=100,
                provider=normalized_provider,
                selectedIds=[],
                sessionCount=0,
                deletedCount=0,
                failedCount=0,
            )
        item_ids = [item.id for item in matching]
        preview = self.preview(
            item_ids,
            self._revision,
            request_id=request_id,
        )
        operation = preview.get("operation") if isinstance(preview, Mapping) else {}
        confirmation_token = str(
            operation.get("confirmationToken") if isinstance(operation, Mapping) else ""
        )
        if not confirmation_token:
            raise SessionCleanupError("Provider 会话删除确认令牌生成失败。")
        result = self.execute(
            item_ids,
            self._revision,
            confirmation_token,
            request_id=request_id,
        )
        executed = result.get("operation") if isinstance(result, Mapping) else {}
        deleted_count = int(
            executed.get("deletedCount") or 0 if isinstance(executed, Mapping) else 0
        )
        actual_bytes = int(
            executed.get("actualBytes") or 0 if isinstance(executed, Mapping) else 0
        )
        if (
            not isinstance(executed, Mapping)
            or str(executed.get("state") or "") != "completed"
            or deleted_count != len(matching)
        ):
            raise SessionCleanupError(
                f"Provider {normalized_provider} 会话历史删除未完成："
                f"成功 {deleted_count}/{len(matching)}。"
            )
        return self.mark_operation(
            request_id=request_id,
            action="providerHistoryDelete",
            state="completed",
            progress=100,
            provider=normalized_provider,
            selectedIds=item_ids,
            sessionCount=len(matching),
            deletedCount=deleted_count,
            failedCount=0,
            actualBytes=actual_bytes,
        )

    def materialize_target_rollout(self, target_id: str, source_id: str) -> None:
        """Flatten a forked target before any optional source deletion."""
        canonical_target = _canonical_uuid(target_id)
        canonical_source = _canonical_uuid(source_id)
        if not canonical_target or not canonical_source:
            raise SessionCleanupError("目标或源会话标识无效。")
        last_error = ""
        for index, delay in enumerate(self.transfer_ready_retry_delays):
            if index and delay:
                self._sleep(delay)
            try:
                records, _parents, _edge_states, _unsafe_ids, _unresolved = (
                    self._load_state()
                )
                target = records.get(canonical_target)
                allowed_roots = self._allowed_rollout_roots()
                if (
                    target is None
                    or target.rollout_path is None
                    or not _path_under(target.rollout_path, allowed_roots)
                    or not target.rollout_path.is_file()
                ):
                    raise SessionCleanupError("Codex 目标会话 rollout 尚不可用。")
                rollout_paths = {
                    session_id: record.rollout_path
                    for session_id, record in records.items()
                    if (
                        record.rollout_path is not None
                        and _path_under(record.rollout_path, allowed_roots)
                        and record.rollout_path.is_file()
                    )
                }
                materialize_forked_rollout(
                    target_id=canonical_target,
                    source_id=canonical_source,
                    target_path=target.rollout_path,
                    rollout_paths=rollout_paths,
                )
                return
            except (SessionCleanupError, SessionMaterializationError) as exc:
                # A missing freshly-forked target and a transient Windows file
                # handle are both expected just after fork.  The source remains
                # untouched until this bounded materialization succeeds.
                last_error = str(exc) or type(exc).__name__
        raise SessionCleanupError(last_error or "Codex 目标会话历史物化未完成。")

    def _register_session_index(self, session_id: str, title: str) -> None:
        """Make a newly forked thread discoverable by ``codex resume``."""
        canonical = _canonical_uuid(session_id)
        if not canonical:
            raise SessionCleanupError("目标会话标识无效，无法更新 CLI 会话索引。")
        existing_ids: set[str] = set()
        if self.session_index_path.is_file():
            try:
                with self.session_index_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(payload, Mapping):
                            existing = _canonical_uuid(payload.get("id"))
                            if existing:
                                existing_ids.add(existing)
            except (OSError, UnicodeError) as exc:
                raise SessionCleanupError("Codex CLI 会话索引无法读取。") from exc
        if canonical in existing_ids:
            return
        payload = {
            "id": canonical,
            "thread_name": str(title or "Untitled session").strip()
            or "Untitled session",
            "updated_at": datetime.now(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
        }
        try:
            self.session_index_path.parent.mkdir(parents=True, exist_ok=True)
            with self.session_index_path.open(
                "a", encoding="utf-8", newline="\n"
            ) as handle:
                handle.write(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, UnicodeError) as exc:
            raise SessionCleanupError("Codex CLI 会话索引无法更新。") from exc

    def transfer(
        self,
        item_ids: Sequence[str],
        revision: str,
        source_provider: str,
        target_provider: str,
        mode: str,
        *,
        fork: Callable[[str, str, str], str],
        materialize: Callable[[str, str], object] | None = None,
        verify: Callable[[str, str], bool] | None = None,
        desktop_source_lifecycle: DesktopSourceLifecycle | None = None,
        request_id: str = "",
    ) -> dict[str, object]:
        """Fork selected sessions to another Provider, optionally removing sources.

        ``fork`` is injected by the runtime worker so this manager remains
        responsible for inventory/revalidation.  Migration source deletion is
        intentionally delegated to the running Desktop lifecycle so the
        Desktop-owned local thread catalog receives the archive/delete events.
        """
        normalized_source = str(source_provider or "").strip().casefold()
        normalized_target = str(target_provider or "").strip().casefold()
        normalized_mode = str(mode or "copy").strip().casefold()
        if not normalized_source or not normalized_target:
            raise SessionCleanupError("会话迁移需要源和目标 Provider。")
        if normalized_source == normalized_target:
            raise SessionCleanupError("源 Provider 与目标 Provider 不能相同。")
        if normalized_mode not in {"copy", "migrate"}:
            raise SessionCleanupError("不支持的会话迁移模式。")
        if not callable(fork):
            raise SessionCleanupError("Codex fork 服务当前不可用。")
        if not callable(materialize):
            raise SessionCleanupError("Codex 目标会话物化服务当前不可用。")
        if not callable(verify):
            raise SessionCleanupError("Codex 目标会话持久化验证当前不可用。")
        items = self._selected_items(item_ids, revision)
        if any(item.model_provider.casefold() != normalized_source for item in items):
            raise SessionCleanupError(
                "所选会话已不属于当前源 Provider，请重新扫描后再试。"
            )
        if any(not item.transferable for item in items):
            raise SessionCleanupError("所选会话的分页历史源无法验证，不能复制或迁移。")
        results: list[dict[str, object]] = []
        migration_candidates: list[tuple[SessionCleanupItem, dict[str, object]]] = []
        existing_session_ids = set(self._load_state()[0])
        created_target_ids: set[str] = set()
        publisher = getattr(self, "progress_publisher", None)
        total = len(items)
        last_progress_publish = 0.0

        def publish_progress(*, force: bool = False) -> None:
            nonlocal last_progress_publish
            now = time.monotonic()
            # App-server notifications and renderer payloads are relatively
            # expensive for large selections. Keep progress responsive while
            # coalescing bursts from fast local forks.
            if (
                not force
                and last_progress_publish
                and now - last_progress_publish < 0.075
            ):
                return
            target_ready_count = sum(
                bool(result.get("targetVisible"))
                and bool(result.get("targetResumable"))
                for result in results
            )
            target_failed_count = len(results) - target_ready_count
            migrated_count = sum(
                bool(result.get("sourceDeleted")) for result in results
            )
            self.mark_operation(
                request_id=request_id,
                action="sessionTransfer",
                state="running",
                progress=min(99, round(80 * len(results) / max(1, total))),
                sourceProvider=normalized_source,
                targetProvider=normalized_target,
                mode=normalized_mode,
                selectedIds=[item.id for item in items],
                completedCount=len(results),
                copiedCount=target_ready_count,
                targetReadyCount=target_ready_count,
                migratedCount=migrated_count,
                sourceRetainedCount=sum(
                    bool(result.get("sourceRetained")) for result in results
                ),
                sourceArchivedCount=sum(
                    bool(result.get("sourceArchived")) for result in results
                ),
                targetFailedCount=target_failed_count,
                unmigratedCount=(
                    len(results) - migrated_count if normalized_mode == "migrate" else 0
                ),
                # A migration is not terminal until Desktop has confirmed both
                # source lifecycle notifications.  Do not label ready targets
                # as failed while that gate is still running.
                failedCount=target_failed_count,
            )
            if callable(publisher):
                publisher(self.snapshot(include_sessions=False))
            last_progress_publish = now

        for item in items:
            forked = False
            target_ready = False
            history_materialized = False
            new_session_id = ""
            index_warning = ""
            try:
                new_session_id = _normalise_fork_target_uuid(
                    fork(item._session_id, normalized_target, item._cwd)
                )
                if not new_session_id or new_session_id == item._session_id:
                    raise SessionCleanupError("Codex fork 未返回新的目标会话。")
                if (
                    new_session_id in existing_session_ids
                    or new_session_id in created_target_ids
                ):
                    raise SessionCleanupError(
                        "Codex fork 返回了与现有目标会话冲突的标识。"
                    )
                created_target_ids.add(new_session_id)
                forked = True
                # App Server forks initially contain only lineage metadata.
                # Flatten the source history for both copy and migration so
                # thread/list can discover the target through its rollout
                # fallback before we claim that it is visible and resumable.
                assert callable(materialize)
                materialized = materialize(new_session_id, item._session_id)
                if materialized is False:
                    raise SessionCleanupError("Codex 目标会话历史物化未完成。")
                history_materialized = True
                if verify(new_session_id, normalized_target) is not True:
                    raise SessionCleanupError(
                        "目标会话已创建，但未通过目标 Provider 可见和续聊验证。"
                    )
                target_ready = True
                # The state-db/list proof above is authoritative.  The legacy
                # CLI index is only a best-effort convenience for older CLI
                # builds and must not turn a usable target into a false failure.
                try:
                    self._register_session_index(new_session_id, item.title)
                except SessionCleanupError as exc:
                    index_warning = str(exc) or type(exc).__name__
                result = {
                    "id": item.id,
                    "title": item.title,
                    "workdir": item._cwd,
                    "targetSessionId": new_session_id,
                    "state": "copied",
                    "forked": True,
                    "targetCreated": True,
                    "targetVisible": True,
                    "targetResumable": True,
                    "historyMaterialized": history_materialized,
                    "sourceDeleted": False,
                    "sourceRetained": normalized_mode == "migrate",
                    "sourceArchived": False,
                    "desktopLifecycleVerified": normalized_mode != "migrate",
                    "indexWarning": index_warning,
                    "error": "",
                }
                results.append(result)
                if normalized_mode == "migrate":
                    migration_candidates.append((item, result))
            except Exception as exc:
                error_text = str(exc) or type(exc).__name__
                results.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "workdir": item._cwd,
                        "targetSessionId": new_session_id,
                        "state": "copied"
                        if target_ready
                        else ("targetCreated" if forked else "failed"),
                        "forked": forked,
                        "targetCreated": forked,
                        "targetVisible": target_ready,
                        "targetResumable": target_ready,
                        "historyMaterialized": history_materialized,
                        "sourceDeleted": False,
                        "sourceRetained": normalized_mode == "migrate",
                        "sourceArchived": False,
                        "desktopLifecycleVerified": False,
                        "indexWarning": index_warning,
                        "error": error_text,
                    }
                )
            publish_progress()

        if normalized_mode == "migrate":
            if len(migration_candidates) != total:
                # Multi-select migration is all-or-none for source deletion.
                # A usable target remains available, but retaining every source
                # avoids silently producing a mixed migration.
                for _item, result in migration_candidates:
                    if not str(result.get("error") or "").strip():
                        result["error"] = (
                            "源会话已保留：至少一个目标会话尚未通过迁移就绪验证。"
                        )
            elif migration_candidates:
                fresh_candidates: list[
                    tuple[SessionCleanupItem, dict[str, object]]
                ] = []
                blockers: dict[str, str] = {}
                target_session_ids = {
                    _canonical_uuid(result.get("targetSessionId"))
                    for _original, result in migration_candidates
                    if _canonical_uuid(result.get("targetSessionId"))
                }
                try:
                    # The pre-fork opaque inventory is stale after App Server
                    # work. Re-scan before preview/execute and map by the
                    # private source id, never by the old renderer id.
                    self.scan(request_id=request_id)
                    current_by_session = {
                        candidate._session_id: candidate
                        for candidate in self._items.values()
                    }
                    for original, result in migration_candidates:
                        current = current_by_session.get(original._session_id)
                        if current is None:
                            blockers[original.id] = "源会话在删除前已改变或不再可用。"
                        elif set(current._descendant_ids) & target_session_ids:
                            # A future App Server may model ``thread/fork`` as
                            # a spawn edge. The normal source-tree delete would
                            # then stage the freshly-created target alongside
                            # its source, so retain every source instead of
                            # risking deletion of a usable target conversation.
                            blockers[original.id] = (
                                "目标会话仍属于源会话子树，无法安全删除源会话。"
                            )
                        elif current.model_provider.casefold() != normalized_source:
                            blockers[original.id] = "源会话的 Provider 在删除前已改变。"
                        elif not current.selectable:
                            blockers[original.id] = (
                                current.blocked_reason or "源会话当前不允许安全删除。"
                            )
                        elif not current.transferable:
                            blockers[original.id] = (
                                current.transfer_blocked_reason
                                or "源会话的分页历史当前无法验证。"
                            )
                        else:
                            fresh_candidates.append((current, result))
                except Exception as exc:
                    detail = str(exc) or type(exc).__name__
                    blockers = {
                        original.id: f"删除前刷新源会话失败：{detail}"
                        for original, _result in migration_candidates
                    }

                if blockers:
                    for original, result in migration_candidates:
                        result["error"] = blockers.get(
                            original.id,
                            "源会话已保留：同一批次中另一个源会话未满足安全删除条件。",
                        )
                elif len(fresh_candidates) != total:
                    for _original, result in migration_candidates:
                        result["error"] = "源会话已保留：删除前会话清单不完整。"
                else:
                    if not callable(desktop_source_lifecycle):
                        for _current, result in fresh_candidates:
                            result["error"] = (
                                "Codex Desktop 归档/删除通道当前不可用，源会话已保留。"
                            )
                    else:
                        for current, result in fresh_candidates:
                            source_family_ids = tuple(
                                dict.fromkeys(
                                    (
                                        current._session_id,
                                        *current._descendant_ids,
                                    )
                                )
                            )
                            usage_receipt: object | None = None
                            reports: list[Mapping[str, object]] = []
                            lifecycle_error = ""
                            try:
                                if self.usage_snapshot_prepare is not None:
                                    usage_receipt = self.usage_snapshot_prepare(current)
                                for source_id in source_family_ids:
                                    report = desktop_source_lifecycle(
                                        source_id,
                                        current._cwd,
                                    )
                                    if not isinstance(report, Mapping):
                                        raise SessionCleanupError(
                                            "Codex Desktop 归档/删除通道返回了无效结果。"
                                        )
                                    if (
                                        _canonical_uuid(report.get("threadId"))
                                        != source_id
                                    ):
                                        raise SessionCleanupError(
                                            "Codex Desktop 归档/删除通道返回了不匹配的会话标识。"
                                        )
                                    reports.append(report)
                            except Exception as exc:
                                lifecycle_error = str(exc) or type(exc).__name__

                            archived = (
                                bool(reports)
                                and len(reports) == len(source_family_ids)
                                and all(
                                    bool(report.get("archived"))
                                    and bool(report.get("archiveNotification"))
                                    for report in reports
                                )
                            )
                            deleted = archived and all(
                                bool(report.get("deleted"))
                                and bool(report.get("deleteNotification"))
                                and bool(report.get("verified"))
                                for report in reports
                            )
                            report_errors = [
                                str(report.get("error") or "").strip()
                                for report in reports
                                if str(report.get("error") or "").strip()
                            ]
                            detail = lifecycle_error or "; ".join(report_errors)

                            if deleted:
                                result["state"] = "migrated"
                                result["sourceDeleted"] = True
                                result["sourceRetained"] = False
                                result["sourceArchived"] = True
                                result["desktopLifecycleVerified"] = True
                                result["error"] = ""
                                if (
                                    usage_receipt
                                    and self.usage_snapshot_commit is not None
                                ):
                                    try:
                                        self.usage_snapshot_commit(usage_receipt)
                                    except Exception as exc:
                                        result["usageWarning"] = (
                                            "源会话已由 Codex Desktop 删除，"
                                            "但使用量归档失败："
                                            f"{str(exc) or type(exc).__name__}"
                                        )
                                continue

                            if (
                                usage_receipt
                                and self.usage_snapshot_discard is not None
                            ):
                                try:
                                    self.usage_snapshot_discard(usage_receipt)
                                except Exception:
                                    pass
                            result["sourceArchived"] = archived
                            result["desktopLifecycleVerified"] = False
                            result["sourceRetained"] = True
                            if archived:
                                result["error"] = (
                                    "源会话已由 Codex Desktop 归档，但永久删除未完成；"
                                    "已保留归档副本。" + (f"{detail}" if detail else "")
                                )
                            else:
                                result["error"] = (
                                    "Codex Desktop 未确认源会话已归档，源会话已保留。"
                                    + (f"{detail}" if detail else "")
                                )
                publish_progress(force=True)

        if any(bool(result.get("forked")) for result in results):
            try:
                self.scan(request_id=request_id)
            except Exception:
                # The transfer result is still useful even if the follow-up
                # inventory refresh is unavailable; the next manual scan will
                # reconcile the list.
                pass
        copied_count = sum(
            bool(result.get("targetVisible")) and bool(result.get("targetResumable"))
            for result in results
        )
        migrated_count = sum(result.get("state") == "migrated" for result in results)
        desktop_lifecycle_failure_count = sum(
            bool(result.get("forked"))
            and bool(result.get("targetVisible"))
            and bool(result.get("targetResumable"))
            and not bool(result.get("sourceDeleted"))
            and normalized_mode == "migrate"
            for result in results
        )
        source_retained_count = sum(
            bool(result.get("sourceRetained")) for result in results
        )
        failed_count = (
            total - migrated_count
            if normalized_mode == "migrate"
            else total - copied_count
        )
        target_failed_count = total - copied_count
        unmigrated_count = total - migrated_count if normalized_mode == "migrate" else 0
        completed = (
            migrated_count == total
            if normalized_mode == "migrate"
            else copied_count == total
        )
        has_forked_result = any(bool(result.get("forked")) for result in results)
        state = (
            "completed" if completed else ("partial" if has_forked_result else "failed")
        )
        return self.mark_operation(
            request_id=request_id,
            action="sessionTransfer",
            state=state,
            progress=100,
            sourceProvider=normalized_source,
            targetProvider=normalized_target,
            mode=normalized_mode,
            selectedIds=[item.id for item in items],
            results=results,
            sessionCount=total,
            copiedCount=copied_count,
            migratedCount=migrated_count,
            sourceRetainedCount=source_retained_count,
            sourceArchivedCount=sum(
                bool(result.get("sourceArchived")) for result in results
            ),
            desktopLifecycleFailureCount=desktop_lifecycle_failure_count,
            targetFailedCount=max(0, target_failed_count),
            unmigratedCount=max(0, unmigrated_count),
            failedCount=max(0, failed_count),
        )

    def preview(
        self,
        item_ids: Sequence[str],
        revision: str,
        *,
        request_id: str = "",
    ) -> dict[str, object]:
        items = self._selected_items(item_ids, revision)
        token = self.token_factory()
        self._confirmations[token] = _Confirmation(
            revision=revision,
            item_ids=tuple(item.id for item in items),
            expires_at=float(self.clock()) + self.confirmation_ttl_seconds,
        )
        return self.mark_operation(
            id=token,
            request_id=request_id,
            action="preview",
            state="preview",
            progress=100,
            confirmationToken=token,
            inventoryRevision=revision,
            selectedIds=[item.id for item in items],
            sessionCount=len(items),
            descendantCount=sum(item.descendant_count for item in items),
            estimatedBytes=sum(item.size for item in items),
        )

    def execute(
        self,
        item_ids: Sequence[str],
        revision: str,
        confirmation_token: str,
        *,
        request_id: str = "",
    ) -> dict[str, object]:
        items = self._consume_confirmation(item_ids, revision, confirmation_token)
        capability = self.probe_capability()
        self._capability = capability
        usage_receipts: list[object] = []
        deleted = False
        try:
            self._revalidate_batch(items, capability=capability)
            for item in items:
                if self.usage_snapshot_prepare is not None:
                    receipt = self.usage_snapshot_prepare(item)
                    if receipt:
                        usage_receipts.append(receipt)
            self._revalidate_pending_activity(items)
            self._delete_local_batch(items)
            deleted = True
            self._verify_deleted_batch(items)
            if self.usage_snapshot_commit is not None:
                for receipt in usage_receipts:
                    self.usage_snapshot_commit(receipt)
        except SessionCleanupError as exc:
            if not deleted and self.usage_snapshot_discard is not None:
                for receipt in usage_receipts:
                    try:
                        self.usage_snapshot_discard(receipt)
                    except Exception:
                        pass
            results = [self._failed_result(item, str(exc)) for item in items]
        except Exception as exc:
            if not deleted and self.usage_snapshot_discard is not None:
                for receipt in usage_receipts:
                    try:
                        self.usage_snapshot_discard(receipt)
                    except Exception:
                        pass
            results = [self._failed_result(item, type(exc).__name__) for item in items]
        else:
            results = [
                {
                    "id": item.id,
                    "title": item.title,
                    "state": "deleted",
                    "descendantCount": item.descendant_count,
                    "actualBytes": item.size,
                    "error": "",
                }
                for item in items
            ]
        return self._finish_execute(
            items,
            results,
            request_id=request_id,
        )

    @staticmethod
    def _failed_result(item: SessionCleanupItem, error: str = "") -> dict[str, object]:
        return {
            "id": item.id,
            "title": item.title,
            "state": "failed",
            "descendantCount": item.descendant_count,
            "actualBytes": 0,
            "error": error,
        }

    def _finish_execute(
        self,
        items: Sequence[SessionCleanupItem],
        results: Sequence[Mapping[str, object]],
        *,
        request_id: str,
        interrupted: bool = False,
    ) -> dict[str, object]:
        deleted = sum(row.get("state") == "deleted" for row in results)
        state = (
            "completed"
            if deleted == len(results)
            else ("partial" if deleted else "failed")
        )
        self._reload_after_execute()
        return self.mark_operation(
            request_id=request_id,
            action="execute",
            state=state,
            progress=100,
            results=results,
            selectedIds=[item.id for item in items],
            sessionCount=len(items),
            descendantCount=sum(item.descendant_count for item in items),
            deletedCount=deleted,
            failedCount=len(results) - deleted,
            actualBytes=sum(int(row["actualBytes"]) for row in results),
            interrupted=interrupted,
        )

    def _selected_items(
        self,
        item_ids: Sequence[str],
        revision: str,
    ) -> list[SessionCleanupItem]:
        normalized = tuple(dict.fromkeys(str(item_id or "") for item_id in item_ids))
        if not normalized or any(not item_id for item_id in normalized):
            raise SessionCleanupError("Session selection is empty or invalid.")
        if not self._revision or revision != self._revision:
            raise SessionCleanupError("Session inventory revision is stale.")
        try:
            items = [self._items[item_id] for item_id in normalized]
        except KeyError as exc:
            raise SessionCleanupError("Unknown session inventory item.") from exc
        if any(not item.selectable for item in items):
            raise SessionCleanupError("Session selection contains a protected item.")
        return items

    def _consume_confirmation(
        self,
        item_ids: Sequence[str],
        revision: str,
        token: str,
    ) -> list[SessionCleanupItem]:
        items = self._selected_items(item_ids, revision)
        confirmation = self._confirmations.pop(str(token or ""), None)
        if confirmation is None or confirmation.expires_at < float(self.clock()):
            raise SessionCleanupError("Confirmation token is missing or expired.")
        if confirmation.revision != revision or confirmation.item_ids != tuple(
            item.id for item in items
        ):
            raise SessionCleanupError(
                "Confirmation does not match the session selection."
            )
        return items

    def _protected_ids(
        self,
        provider: Callable[[], Iterable[str]],
    ) -> set[str]:
        try:
            values = provider()
        except Exception as exc:
            raise SessionCleanupError(
                f"Active session state could not be verified ({type(exc).__name__})."
            ) from exc
        return {canonical for value in values if (canonical := _canonical_uuid(value))}

    def _allowed_rollout_roots(self) -> tuple[Path, ...]:
        roots = [self.sessions_root]
        if self.sessions_root.name.casefold() == "sessions":
            roots.append(self.sessions_root.parent / "archived_sessions")
        return tuple(roots)

    def _load_state(
        self,
    ) -> tuple[
        dict[str, _ThreadRecord],
        dict[str, set[str]],
        dict[str, str],
        set[str],
        int,
    ]:
        if not self.state_db_path.is_file():
            raise SessionCleanupError("Codex state database is unavailable.")
        try:
            with closing(_read_only_connection(self.state_db_path)) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "threads" not in tables:
                    raise SessionCleanupError(
                        "Codex state database has no threads table."
                    )
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(threads)")
                }
                if not {"id", "rollout_path"}.issubset(columns):
                    raise SessionCleanupError("Codex thread schema is not recognized.")
                selected = [
                    name
                    for name in (
                        "id",
                        "rollout_path",
                        "title",
                        "cwd",
                        "archived",
                        "updated_at_ms",
                        "updated_at",
                        "recency_at_ms",
                    )
                    if name in columns
                ]
                rows = connection.execute(
                    f"SELECT {', '.join(selected)} FROM threads"
                ).fetchall()
                edge_rows = (
                    connection.execute(
                        "SELECT parent_thread_id, child_thread_id, status "
                        "FROM thread_spawn_edges"
                    ).fetchall()
                    if "thread_spawn_edges" in tables
                    else []
                )
        except sqlite3.Error as exc:
            raise SessionCleanupError(
                "Codex state database could not be read."
            ) from exc
        records: dict[str, _ThreadRecord] = {}
        unresolved = 0
        for row in rows:
            session_id = _canonical_uuid(row["id"])
            if not session_id:
                unresolved += 1
                continue
            values = {name: row[name] for name in selected}
            updated_at_ms = int(values.get("updated_at_ms") or 0)
            if updated_at_ms <= 0:
                updated_at_ms = int(values.get("recency_at_ms") or 0)
            if updated_at_ms <= 0:
                updated_at_ms = int(values.get("updated_at") or 0) * 1000
            records[session_id] = _ThreadRecord(
                session_id=session_id,
                rollout_path=_normalized_rollout_path(values.get("rollout_path")),
                title=str(values.get("title") or "").strip(),
                cwd=str(values.get("cwd") or "").strip(),
                archived=bool(values.get("archived")),
                updated_at_ms=max(0, updated_at_ms),
            )
        parents: dict[str, set[str]] = defaultdict(set)
        edge_states: dict[str, str] = {}
        unsafe_ids: set[str] = set()
        for row in edge_rows:
            parent = _canonical_uuid(row[0])
            child = _canonical_uuid(row[1])
            if not parent or not child:
                unresolved += 1
                if parent:
                    unsafe_ids.add(parent)
                if child:
                    unsafe_ids.add(child)
                continue
            parents[child].add(parent)
            edge_states[child] = str(row[2] or "").strip()
            if parent == child:
                unsafe_ids.add(child)
            if parent not in records:
                unsafe_ids.add(child)
            if child not in records:
                unsafe_ids.add(parent)
        return records, parents, edge_states, unsafe_ids, unresolved

    @staticmethod
    def _root_ids(
        records: Mapping[str, _ThreadRecord],
        parents: Mapping[str, set[str]],
        unsafe_ids: Iterable[str] = (),
    ) -> tuple[list[str], set[str], int]:
        roots = [session_id for session_id in records if not parents.get(session_id)]
        unsafe = set(unsafe_ids)
        unsafe_roots: set[str] = set()
        unresolved_nodes: set[str] = set()

        for start_id in records:
            pending = [start_id]
            visited: set[str] = set()
            lineage_roots: set[str] = set()
            valid = True
            while pending:
                session_id = pending.pop()
                if session_id in visited:
                    valid = False
                    continue
                visited.add(session_id)
                linked = parents.get(session_id, set())
                if not linked:
                    lineage_roots.add(session_id)
                    continue
                if len(linked) != 1:
                    valid = False
                for parent in linked:
                    if parent not in records:
                        valid = False
                        continue
                    pending.append(parent)
            if len(lineage_roots) != 1:
                valid = False
            if not valid or start_id in unsafe:
                unresolved_nodes.add(start_id)
                unsafe_roots.update(lineage_roots)

        return roots, unsafe_roots, len(unresolved_nodes)

    @staticmethod
    def _descendants(
        root_id: str,
        records: Mapping[str, _ThreadRecord],
        parents: Mapping[str, set[str]],
    ) -> list[str]:
        children: dict[str, list[str]] = defaultdict(list)
        for child, linked in parents.items():
            if len(linked) != 1 or child not in records:
                continue
            parent = next(iter(linked))
            if parent in records:
                children[parent].append(child)
        result: list[str] = []
        pending = list(children.get(root_id, ()))
        seen = {root_id}
        while pending:
            session_id = pending.pop()
            if session_id in seen:
                continue
            seen.add(session_id)
            result.append(session_id)
            pending.extend(children.get(session_id, ()))
        return sorted(result)

    def _session_index_metadata(
        self,
        *,
        strict: bool = False,
    ) -> tuple[dict[str, str], set[str]]:
        if not self.session_index_path.is_file():
            return {}, set()
        titles: dict[str, str] = {}
        session_ids: set[str] = set()
        try:
            with self.session_index_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        if strict:
                            raise SessionCleanupError(
                                "Codex session index could not be verified."
                            )
                        continue
                    if not isinstance(payload, Mapping):
                        if strict:
                            raise SessionCleanupError(
                                "Codex session index could not be verified."
                            )
                        continue
                    session_id = _canonical_uuid(payload.get("id"))
                    title = str(payload.get("thread_name") or "").strip()
                    if session_id:
                        session_ids.add(session_id)
                        if title:
                            titles[session_id] = title
        except OSError as exc:
            if strict:
                raise SessionCleanupError(
                    "Codex session index could not be verified."
                ) from exc
            return {}, set()
        return titles, session_ids

    def _session_index_titles(self) -> dict[str, str]:
        return self._session_index_metadata()[0]

    def _session_index_ids(self, *, strict: bool = False) -> set[str]:
        return self._session_index_metadata(strict=strict)[1]

    def _delete_local_batch(self, items: Sequence[SessionCleanupItem]) -> None:
        session_ids = tuple(
            dict.fromkeys(
                session_id
                for item in items
                for session_id in (item._session_id, *item._descendant_ids)
            )
        )
        rollout_paths = tuple(
            dict.fromkeys(path for item in items for path in item._rollout_paths)
        )
        if not session_ids or not rollout_paths:
            raise SessionCleanupError("Session deletion target is incomplete.")
        retained_index_lines: list[str] | None = None
        if self.session_index_path.is_file():
            try:
                retained_index_lines = []
                with self.session_index_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            retained_index_lines.append(line)
                            continue
                        payload = json.loads(line)
                        if not isinstance(payload, Mapping):
                            raise SessionCleanupError(
                                "Codex session index could not be verified."
                            )
                        if _canonical_uuid(payload.get("id")) not in session_ids:
                            retained_index_lines.append(line)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise SessionCleanupError(
                    "Codex session index could not be verified."
                ) from exc
        staging_parent = self.sessions_root.parent / ".hud-session-delete-staging"
        staging = staging_parent / secrets.token_hex(12)
        moved: list[tuple[Path, Path]] = []
        staged_index: Path | None = None
        database_committed = False
        try:
            staging.mkdir(parents=True, exist_ok=False)
            for index, path in enumerate(rollout_paths, start=1):
                staged_path = staging / f"{index:04d}-{path.name}"
                os.replace(path, staged_path)
                moved.append((path, staged_path))
            if retained_index_lines is not None:
                staged_index = staging / "session_index.jsonl"
                os.replace(self.session_index_path, staged_index)
                self.session_index_path.write_text(
                    "".join(retained_index_lines),
                    encoding="utf-8",
                )
            with closing(_read_write_connection(self.state_db_path)) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                columns = {
                    table: {
                        str(row[1])
                        for row in connection.execute(
                            f'PRAGMA table_info("{table.replace(chr(34), chr(34) * 2)}")'
                        )
                    }
                    for table in tables
                }
                placeholders = ",".join("?" for _ in session_ids)
                with connection:
                    for table, column in (
                        ("thread_dynamic_tools", "thread_id"),
                        ("thread_goals", "thread_id"),
                        ("stage1_outputs", "thread_id"),
                    ):
                        if table in tables and column in columns[table]:
                            connection.execute(
                                f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
                                session_ids,
                            )
                    if (
                        "thread_spawn_edges" in tables
                        and {"parent_thread_id", "child_thread_id"}
                        <= columns["thread_spawn_edges"]
                    ):
                        connection.execute(
                            "DELETE FROM thread_spawn_edges "
                            f"WHERE parent_thread_id IN ({placeholders}) "
                            f"OR child_thread_id IN ({placeholders})",
                            (*session_ids, *session_ids),
                        )
                    if (
                        "agent_job_items" in tables
                        and "assigned_thread_id" in columns["agent_job_items"]
                    ):
                        connection.execute(
                            "UPDATE agent_job_items SET assigned_thread_id = NULL "
                            f"WHERE assigned_thread_id IN ({placeholders})",
                            session_ids,
                        )
                    connection.execute(
                        f"DELETE FROM threads WHERE id IN ({placeholders})",
                        session_ids,
                    )
            database_committed = True
            shutil.rmtree(staging)
            try:
                staging_parent.rmdir()
            except OSError:
                pass
        except (OSError, sqlite3.Error) as exc:
            if not database_committed:
                if staged_index is not None:
                    try:
                        self.session_index_path.unlink(missing_ok=True)
                        if staged_index.exists():
                            os.replace(staged_index, self.session_index_path)
                    except OSError:
                        pass
                for original_path, staged_path in reversed(moved):
                    try:
                        if staged_path.exists() and not original_path.exists():
                            os.replace(staged_path, original_path)
                    except OSError:
                        pass
                try:
                    staging.rmdir()
                    staging_parent.rmdir()
                except OSError:
                    pass
            detail = (
                "Local session database was deleted but staged rollout cleanup failed."
                if database_committed
                else "Local session deletion transaction failed."
            )
            raise SessionCleanupError(detail) from exc

    def _revalidate_batch(
        self,
        items: Sequence[SessionCleanupItem],
        *,
        capability: SessionDeleteCapability,
    ) -> None:
        if not capability.available:
            raise SessionCleanupError(
                capability.reason or "Codex local session store is unavailable."
            )
        records, parents, edge_states, unsafe_ids, _unresolved = self._load_state()
        current_ids = self._protected_ids(self.current_session_ids)
        active_ids = self._protected_ids(self.active_session_ids)
        roots, unsafe_roots, _graph_unresolved = self._root_ids(
            records,
            parents,
            unsafe_ids,
        )
        allowed_roots = self._allowed_rollout_roots()
        for item in items:
            family = (item._session_id, *item._descendant_ids)
            if any(session_id not in records for session_id in family):
                raise SessionCleanupError("Session state changed after scanning.")
            if set(family) & current_ids:
                raise SessionCleanupError("The current session cannot be deleted.")
            if set(family) & active_ids or any(
                edge_states.get(session_id, "").casefold() in _ACTIVE_EDGE_STATES
                for session_id in item._descendant_ids
            ):
                raise SessionCleanupError("The session tree still has active work.")
            if item._session_id not in roots or item._session_id in unsafe_roots:
                raise SessionCleanupError(
                    "The session spawn relation changed after scanning."
                )
            current_descendants = tuple(
                self._descendants(item._session_id, records, parents)
            )
            if current_descendants != item._descendant_ids:
                raise SessionCleanupError("Session spawn tree changed after scanning.")
            current_paths = tuple(
                records[session_id].rollout_path
                for session_id in family
                if records[session_id].rollout_path is not None
            )
            if current_paths != item._rollout_paths:
                raise SessionCleanupError(
                    "Session rollout mapping changed after scanning."
                )
            if any(
                not _path_under(path, allowed_roots) or not path.is_file()
                for path in current_paths
            ):
                raise SessionCleanupError("Session rollout mapping is no longer valid.")

    def _revalidate_pending_activity(
        self,
        items: Sequence[SessionCleanupItem],
    ) -> None:
        current_ids = self._protected_ids(self.current_session_ids)
        active_ids = self._protected_ids(self.active_session_ids)
        for item in items:
            family = {item._session_id, *item._descendant_ids}
            if family & current_ids:
                raise SessionCleanupError("The current session cannot be deleted.")
            if family & active_ids:
                raise SessionCleanupError("The session tree still has active work.")

    def _verify_deleted_batch(self, items: Sequence[SessionCleanupItem]) -> None:
        family = {
            session_id
            for item in items
            for session_id in (item._session_id, *item._descendant_ids)
        }
        rollout_paths = [path for item in items for path in item._rollout_paths]
        try:
            records, _parents, _states, _unsafe_ids, _unresolved = self._load_state()
        except SessionCleanupError as exc:
            if not self.state_db_path.exists():
                raise SessionCleanupError(
                    "Codex state database disappeared after deletion."
                ) from exc
            raise
        if family & set(records):
            raise SessionCleanupError("Codex state still contains the deleted session.")
        if any(path.exists() for path in rollout_paths):
            raise SessionCleanupError("A deleted session rollout still exists.")
        remaining_index_ids = self._session_index_ids(strict=True)
        if family & remaining_index_ids:
            raise SessionCleanupError("Codex session index still contains the session.")

    def _reload_after_execute(self) -> None:
        try:
            records, parents, edge_states, unsafe_ids, unresolved = self._load_state()
        except SessionCleanupError:
            return
        remaining_session_ids = set(records)
        self._items = {
            item_id: item
            for item_id, item in self._items.items()
            if item._session_id in remaining_session_ids
        }
        _roots, _unsafe_roots, graph_unresolved = self._root_ids(
            records,
            parents,
            unsafe_ids,
        )
        self._unresolved_count = unresolved + graph_unresolved
        del edge_states


__all__ = [
    "SessionCleanupError",
    "SessionCleanupItem",
    "SessionCleanupManager",
    "SessionDeleteCapability",
]
