"""Safe inventory and official-command orchestration for permanent session deletion."""

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
import subprocess
import time
from typing import Any
import uuid


DEFAULT_CONFIRMATION_TTL_SECONDS = 300.0
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120.0
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
            "command": "codex delete --force <UUID>" if self.available else "",
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
        }


@dataclass(frozen=True)
class _Confirmation:
    revision: str
    item_ids: tuple[str, ...]
    expires_at: float


CommandRunner = Callable[[Sequence[str], Mapping[str, str]], Any]
UsageSnapshotPrepare = Callable[[SessionCleanupItem], object]
UsageSnapshotCommit = Callable[[object], None]
UsageSnapshotDiscard = Callable[[object], None]


def _default_command_runner(
    command: Sequence[str],
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    resolved_command = list(command)
    if resolved_command:
        executable = shutil.which(
            resolved_command[0],
            path=environment.get("PATH"),
        )
        if executable:
            resolved_command[0] = executable
    return subprocess.run(
        resolved_command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=DEFAULT_COMMAND_TIMEOUT_SECONDS,
        check=False,
        env=dict(environment),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


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


def _result_text(result: object, name: str) -> str:
    value = getattr(result, name, "")
    return str(value or "").strip()


class SessionCleanupManager:
    """Build a root-session inventory and delete only through the Codex CLI."""

    def __init__(
        self,
        *,
        state_db_path: Path,
        sessions_root: Path,
        session_index_path: Path,
        current_session_ids: Callable[[], Iterable[str]] | None = None,
        active_session_ids: Callable[[], Iterable[str]] | None = None,
        codex_command: Sequence[str] = ("codex",),
        command_runner: CommandRunner = _default_command_runner,
        usage_snapshot_prepare: UsageSnapshotPrepare | None = None,
        usage_snapshot_commit: UsageSnapshotCommit | None = None,
        usage_snapshot_discard: UsageSnapshotDiscard | None = None,
        environment: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] | None = None,
        confirmation_ttl_seconds: float = DEFAULT_CONFIRMATION_TTL_SECONDS,
    ) -> None:
        self.state_db_path = Path(state_db_path)
        self.sessions_root = Path(sessions_root)
        self.session_index_path = Path(session_index_path)
        self.current_session_ids = current_session_ids or (lambda: ())
        self.active_session_ids = active_session_ids or (lambda: ())
        self.codex_command = tuple(str(part) for part in codex_command if str(part))
        self.command_runner = command_runner
        self.usage_snapshot_prepare = usage_snapshot_prepare
        self.usage_snapshot_commit = usage_snapshot_commit
        self.usage_snapshot_discard = usage_snapshot_discard
        self.environment = dict(os.environ if environment is None else environment)
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
        if not self.codex_command:
            return SessionDeleteCapability(False, "Codex CLI command is unavailable.")
        try:
            result = self.command_runner(
                (*self.codex_command, "delete", "--help"),
                self.environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return SessionDeleteCapability(
                False,
                f"Codex delete capability could not be verified ({type(exc).__name__}).",
            )
        if int(getattr(result, "returncode", 1) or 0) != 0:
            return SessionDeleteCapability(False, "This Codex CLI cannot delete sessions.")
        output = f"{_result_text(result, 'stdout')}\n{_result_text(result, 'stderr')}"
        if "--force" not in output:
            return SessionDeleteCapability(
                False,
                "This Codex CLI does not expose non-interactive permanent deletion.",
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
        items: list[SessionCleanupItem] = []
        for root_id in roots:
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
                    updated_at=_updated_at_iso(root.updated_at_ms),
                    status=status,
                    archived=root.archived,
                    size=size,
                    descendant_count=len(descendants),
                    selectable=not blocked_reason,
                    blocked_reason=blocked_reason,
                    _session_id=root_id,
                    _descendant_ids=tuple(descendants),
                    _rollout_paths=rollout_paths,
                )
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
        results: list[dict[str, object]] = []
        for item in items:
            result_row = {
                "id": item.id,
                "title": item.title,
                "state": "failed",
                "descendantCount": item.descendant_count,
                "actualBytes": 0,
                "error": "",
            }
            usage_receipt: object | None = None
            delete_accepted = False
            try:
                self._revalidate(item)
                if self.usage_snapshot_prepare is not None:
                    usage_receipt = self.usage_snapshot_prepare(item)
                command = (*self.codex_command, "delete", "--force", item._session_id)
                completed = self.command_runner(command, self.environment)
                if int(getattr(completed, "returncode", 1) or 0) != 0:
                    detail = _result_text(completed, "stderr") or _result_text(
                        completed, "stdout"
                    )
                    raise SessionCleanupError(
                        detail[:240] or "Codex CLI rejected permanent deletion."
                    )
                delete_accepted = True
                self._verify_deleted(item)
                if usage_receipt and self.usage_snapshot_commit is not None:
                    self.usage_snapshot_commit(usage_receipt)
            except Exception as exc:
                if (
                    usage_receipt
                    and not delete_accepted
                    and self.usage_snapshot_discard is not None
                ):
                    try:
                        self.usage_snapshot_discard(usage_receipt)
                    except Exception:
                        pass
                result_row["error"] = (
                    str(exc)[:240]
                    if isinstance(exc, SessionCleanupError)
                    else type(exc).__name__
                )
            else:
                result_row["state"] = "deleted"
                result_row["actualBytes"] = item.size
            results.append(result_row)
        deleted = sum(row["state"] == "deleted" for row in results)
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

    def _revalidate(self, item: SessionCleanupItem) -> None:
        if not self.probe_capability().available:
            raise SessionCleanupError("Codex permanent-delete capability is unavailable.")
        records, parents, edge_states, unsafe_ids, _unresolved = self._load_state()
        current_ids = self._protected_ids(self.current_session_ids)
        active_ids = self._protected_ids(self.active_session_ids)
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
        roots, unsafe_roots, _graph_unresolved = self._root_ids(
            records,
            parents,
            unsafe_ids,
        )
        if item._session_id not in roots or item._session_id in unsafe_roots:
            raise SessionCleanupError("The session spawn relation changed after scanning.")
        current_descendants = tuple(self._descendants(item._session_id, records, parents))
        if current_descendants != item._descendant_ids:
            raise SessionCleanupError("Session spawn tree changed after scanning.")
        current_paths = tuple(
            records[session_id].rollout_path
            for session_id in family
            if records[session_id].rollout_path is not None
        )
        if current_paths != item._rollout_paths:
            raise SessionCleanupError("Session rollout mapping changed after scanning.")
        allowed_roots = self._allowed_rollout_roots()
        if any(
            not _path_under(path, allowed_roots) or not path.is_file()
            for path in current_paths
        ):
            raise SessionCleanupError("Session rollout mapping is no longer valid.")

    def _verify_deleted(self, item: SessionCleanupItem) -> None:
        family = {item._session_id, *item._descendant_ids}
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
        if any(path.exists() for path in item._rollout_paths):
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
