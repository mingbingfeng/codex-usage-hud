"""Safe local-store orchestration for permanent session deletion."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path, PureWindowsPath
import secrets
import shutil
import sqlite3
import time
import uuid

from .parser import classify_session_client


DEFAULT_CONFIRMATION_TTL_SECONDS = 300.0
_ACTIVE_EDGE_STATES = {"active", "in_progress", "pending", "running", "starting"}


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
    model_provider: str = "unknown"
    client_kind: str = "unknown"
    _session_id: str = field(default="", repr=False, compare=False)
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


def _canonical_uuid(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        canonical = str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""
    return canonical if candidate.casefold() == canonical else ""


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _read_write_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=rw", uri=True, timeout=5.0)
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
        return datetime.fromtimestamp(value / 1000.0).astimezone().isoformat(
            timespec="seconds"
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
                if not isinstance(record, Mapping) or record.get("type") != "session_meta":
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

    def snapshot(self) -> dict[str, object]:
        sessions = sorted(
            self._items.values(),
            key=lambda item: (item.updated_at, item.title.casefold()),
            reverse=True,
        )
        selectable = [item for item in sessions if item.selectable]
        return {
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
            "sessions": [item.to_payload() for item in sessions],
            "operation": dict(self._operation),
        }

    def workdir_for_item(self, item_id: object, revision: object) -> Path | None:
        """Resolve one current inventory item without exposing its path in the payload."""
        normalized_id = str(item_id or "").strip()
        normalized_revision = str(revision or "").strip()
        if not normalized_id or not normalized_revision or normalized_revision != self._revision:
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
        def report(phase: str, phase_label: str, phase_index: int, progress: int) -> None:
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
                publisher(self.snapshot())

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
                    title=(root.title or titles.get(root_id) or "Untitled session").strip(),
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
                    model_provider=metadata.model_provider,
                    client_kind=metadata.client_kind,
                    _session_id=root_id,
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
        state = "completed" if deleted == len(results) else (
            "partial" if deleted else "failed"
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
        if (
            confirmation.revision != revision
            or confirmation.item_ids != tuple(item.id for item in items)
        ):
            raise SessionCleanupError("Confirmation does not match the session selection.")
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
        return {
            canonical
            for value in values
            if (canonical := _canonical_uuid(value))
        }

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
                    raise SessionCleanupError("Codex state database has no threads table.")
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
            raise SessionCleanupError("Codex state database could not be read.") from exc
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
            current_descendants = tuple(self._descendants(item._session_id, records, parents))
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
