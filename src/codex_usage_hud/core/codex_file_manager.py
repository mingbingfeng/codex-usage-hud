"""Conservative, command-driven management of local Codex storage metadata.

The manager deliberately does no filesystem work during construction.  A scan
is performed only for an explicit user command and never reads transcript or
database contents.  Raw deletion is limited to narrowly recognized expired
temporary artifacts; everything else is protected or delegated to an official
``codex`` command.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePath
import queue
import secrets
import shutil
import stat
import subprocess
import threading
import time
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: fail closed without a dependency.
    tomllib = None  # type: ignore[assignment]


POLICIES = ("candidate", "managed", "blocked", "unknown")
MANAGED_ACTIONS = {
    "archive_session",
    "delete_session",
    "remove_plugin",
    "logout",
}
WORKER_ACTIONS = {"scan", "preview", "execute", "execute_pending", "cancel"} | MANAGED_ACTIONS
WINDOWS_REPARSE_ATTRIBUTE = 0x400
DEFAULT_ITEM_LIMIT = 20_000
DEFAULT_TEMP_MIN_AGE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_CONFIRMATION_TTL_SECONDS = 5 * 60


class FileManagementError(RuntimeError):
    """A storage operation was rejected without changing protected data."""


class InventoryCancelled(FileManagementError):
    """An explicit inventory request was cancelled."""


class InventoryLimitReached(FileManagementError):
    """An inventory stopped at its configured metadata-entry limit."""


@dataclass(frozen=True)
class CodexRoots:
    """Resolved local roots.  Absolute paths never enter renderer payloads."""

    codex_home: Path
    sqlite_homes: tuple[Path, ...] = ()

    def scan_roots(self) -> tuple[tuple[str, Path], ...]:
        roots: list[tuple[str, Path]] = [("codex", self.codex_home)]
        seen = {_path_key(self.codex_home)}
        for path in self.sqlite_homes:
            key = _path_key(path)
            if key in seen:
                continue
            seen.add(key)
            roots.append(("sqlite", path))
        return tuple(roots)


@dataclass(frozen=True)
class InventoryItem:
    id: str
    root_key: str
    relative_path: str
    category: str
    policy: str
    size: int
    file_count: int
    mtime: float
    source: str
    risk: str
    reason: str
    allowed_actions: tuple[str, ...] = ()
    _path: Path = field(default=Path(), repr=False, compare=False)
    _fingerprint: str = field(default="", repr=False, compare=False)
    _lstat: tuple[int, int, int, int, int] = field(
        default=(0, 0, 0, 0, 0), repr=False, compare=False
    )
    _managed_identifier: str = field(default="", repr=False, compare=False)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "relativePath": self.relative_path,
            "category": self.category,
            "policy": self.policy,
            "size": int(self.size),
            "fileCount": int(self.file_count),
            "mtime": float(self.mtime),
            "source": self.source,
            "risk": self.risk,
            "reason": self.reason,
        }
        if self.allowed_actions:
            payload["allowedActions"] = list(self.allowed_actions)
        return payload


@dataclass(frozen=True)
class CodexCleanupCandidate:
    """Python-only projection of one raw-mutation candidate."""

    path: Path
    approved_root: Path
    size: int
    file_count: int
    mtime: float
    fingerprint: str
    lstat: tuple[int, int, int, int, int]


@dataclass(frozen=True)
class Inventory:
    revision: str
    generated_at: float
    items: tuple[InventoryItem, ...]
    total_bytes: int
    total_files: int
    visited_entries: int
    truncated: bool = False
    errors: int = 0

    def to_payload(self, operation: Mapping[str, object] | None = None) -> dict[str, object]:
        category_totals: dict[tuple[str, str], dict[str, object]] = {}
        policy_totals = {
            policy: {"bytes": 0, "items": 0, "files": 0} for policy in POLICIES
        }
        for item in self.items:
            key = (item.category, item.policy)
            bucket = category_totals.setdefault(
                key,
                {
                    "category": item.category,
                    "policy": item.policy,
                    "size": 0,
                    "items": 0,
                    "files": 0,
                    "risk": item.risk,
                    "reason": item.reason,
                },
            )
            bucket["size"] = int(bucket["size"]) + item.size
            bucket["items"] = int(bucket["items"]) + 1
            bucket["files"] = int(bucket["files"]) + item.file_count
            totals = policy_totals[item.policy]
            totals["bytes"] += item.size
            totals["items"] += 1
            totals["files"] += item.file_count
        categories = sorted(
            category_totals.values(),
            key=lambda value: (-int(value["size"]), str(value["category"])),
        )
        return {
            "rootLabel": "CODEX_HOME",
            "generatedAt": _iso_timestamp(self.generated_at),
            "revision": self.revision,
            "totals": {
                "bytes": int(self.total_bytes),
                "files": int(self.total_files),
                "items": len(self.items),
                "visitedEntries": int(self.visited_entries),
                "truncated": bool(self.truncated),
                "errors": int(self.errors),
                "policies": policy_totals,
            },
            "categories": categories,
            "items": [item.to_payload() for item in self.items],
            "operation": dict(operation or _idle_operation()),
        }


@dataclass(frozen=True)
class _MeasuredPath:
    size: int
    files: int
    mtime: float
    fingerprint: str
    contains_reparse: bool
    errors: int
    visited: int


@dataclass
class _Confirmation:
    revision: str
    item_ids: tuple[str, ...]
    action: str
    expires_at: float


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path.expanduser())))


def _absolute_path(path: str | os.PathLike[str], *, base: Path | None = None) -> Path:
    value = Path(os.path.expandvars(os.path.expanduser(os.fspath(path))))
    if not value.is_absolute() and base is not None:
        value = base / value
    return Path(os.path.abspath(os.fspath(value)))


def _config_path_values(value: object) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        values.append(value)
    elif isinstance(value, Mapping):
        for nested in value.values():
            values.extend(_config_path_values(nested))
    elif isinstance(value, list):
        for nested in value:
            values.extend(_config_path_values(nested))
    return values


def _read_config(codex_home: Path) -> dict[str, object]:
    path = codex_home / "config.toml"
    if tomllib is None:
        if not path.exists():
            return {}
        result: dict[str, object] = {"__config_unparsed__": True}
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                key, separator, raw_value = line.partition("=")
                if separator and key.strip() == "sqlite_home":
                    value = raw_value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                        result["sqlite_home"] = value[1:-1]
                    break
        except OSError:
            return {"__config_unparsed__": True}
        return result
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except OSError:
        return {}
    except Exception:
        return {"__config_unparsed__": True}
    return value if isinstance(value, dict) else {}


def resolve_codex_roots(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    platform_candidates: Sequence[Path] = (),
    read_config: bool = True,
) -> CodexRoots:
    """Resolve configured storage roots without scanning them.

    ``CODEX_HOME`` is authoritative.  Otherwise the documented ``~/.codex``
    default is used, with existing platform candidates retained only for legacy
    installations where that default does not exist.
    """

    environment = os.environ if env is None else env
    base_home = Path.home() if home is None else Path(home)
    configured_home = str(environment.get("CODEX_HOME") or "").strip()
    if configured_home:
        codex_home = _absolute_path(configured_home, base=base_home)
    else:
        documented = _absolute_path(base_home / ".codex")
        codex_home = documented
        if not documented.exists():
            for candidate in platform_candidates:
                normalized = _absolute_path(candidate, base=base_home)
                if normalized.exists():
                    codex_home = normalized
                    break

    sqlite_values: list[str | os.PathLike[str]] = []
    environment_sqlite = str(environment.get("CODEX_SQLITE_HOME") or "").strip()
    if environment_sqlite:
        sqlite_values.append(environment_sqlite)
    elif read_config:
        config = _read_config(codex_home)
        configured_sqlite = config.get("sqlite_home")
        if isinstance(configured_sqlite, str) and configured_sqlite.strip():
            sqlite_values.append(configured_sqlite)

    sqlite_homes: list[Path] = []
    seen: set[str] = set()
    for value in sqlite_values:
        path = _absolute_path(value, base=codex_home)
        key = _path_key(path)
        if key == _path_key(codex_home) or key in seen:
            continue
        seen.add(key)
        sqlite_homes.append(path)
    return CodexRoots(codex_home=codex_home, sqlite_homes=tuple(sqlite_homes))


def _is_reparse(stat_result: os.stat_result) -> bool:
    return stat.S_ISLNK(stat_result.st_mode) or bool(
        int(getattr(stat_result, "st_file_attributes", 0) or 0)
        & WINDOWS_REPARSE_ATTRIBUTE
    )


def _lstat_tuple(stat_result: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(getattr(stat_result, "st_dev", 0) or 0),
        int(getattr(stat_result, "st_ino", 0) or 0),
        int(stat_result.st_mode),
        int(stat_result.st_size),
        int(getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1e9))),
    )


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _idle_operation() -> dict[str, object]:
    return {
        "id": "",
        "requestId": "",
        "action": "",
        "state": "idle",
        "progress": 0,
        "error": "",
    }


def _empty_inventory_payload(operation: Mapping[str, object] | None = None) -> dict[str, object]:
    return {
        "rootLabel": "CODEX_HOME",
        "generatedAt": "",
        "revision": "",
        "totals": {
            "bytes": 0,
            "files": 0,
            "items": 0,
            "visitedEntries": 0,
            "truncated": False,
            "errors": 0,
            "policies": {
                policy: {"bytes": 0, "items": 0, "files": 0} for policy in POLICIES
            },
        },
        "categories": [],
        "items": [],
        "operation": dict(operation or _idle_operation()),
    }


class CodexFileManager:
    """Thread-safe inventory and fail-closed execution engine."""

    def __init__(
        self,
        roots: CodexRoots | None = None,
        *,
        env: Mapping[str, str] | None = None,
        home: Path | None = None,
        platform_candidates: Sequence[Path] = (),
        clock: Callable[[], float] = time.time,
        item_limit: int = DEFAULT_ITEM_LIMIT,
        temp_min_age_seconds: float = DEFAULT_TEMP_MIN_AGE_SECONDS,
        confirmation_ttl_seconds: float = DEFAULT_CONFIRMATION_TTL_SECONDS,
        process_gate: Callable[[], bool] | None = None,
        lock_probe: Callable[[Path], bool] | None = None,
        command_runner: Callable[[Sequence[str]], int] | None = None,
        remove_path: Callable[[Path], None] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._roots_from_resolver = roots is None
        self._resolver_env = env
        self._resolver_home = home
        self._resolver_platform_candidates = tuple(platform_candidates)
        self.roots = roots or resolve_codex_roots(
            env=env,
            home=home,
            platform_candidates=platform_candidates,
            read_config=False,
        )
        self.clock = clock
        self.item_limit = max(1, int(item_limit))
        self.temp_min_age_seconds = max(0.0, float(temp_min_age_seconds))
        self.confirmation_ttl_seconds = max(1.0, float(confirmation_ttl_seconds))
        self.process_gate = process_gate or codex_processes_active
        self.lock_probe = lock_probe or path_is_locked
        self.command_runner = command_runner or _run_official_command
        self.remove_path = remove_path or _remove_path
        self.token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._inventory: Inventory | None = None
        self._items: dict[str, InventoryItem] = {}
        self._confirmations: dict[str, _Confirmation] = {}
        self._revision_counter = 0
        self._operation: dict[str, object] = _idle_operation()

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if str(self._operation.get("state") or "") in {"running", "accepted"}:
                self._operation = {**self._operation, "state": "cancelling"}

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            inventory = self._inventory
            operation = dict(self._operation)
        if inventory is None:
            return _empty_inventory_payload(operation)
        return inventory.to_payload(operation)

    def cleanup_candidates(self) -> tuple[CodexCleanupCandidate, ...]:
        """Return only already-scanned raw candidates for local orchestration."""

        with self._lock:
            inventory = self._inventory
        if inventory is None:
            return ()
        roots = dict(self.roots.scan_roots())
        candidates: list[CodexCleanupCandidate] = []
        for item in inventory.items:
            if item.policy != "candidate":
                continue
            root = roots.get(item.root_key)
            if root is None:
                continue
            candidates.append(
                CodexCleanupCandidate(
                    path=item._path,
                    approved_root=root,
                    size=item.size,
                    file_count=item.file_count,
                    mtime=item.mtime,
                    fingerprint=item._fingerprint,
                    lstat=item._lstat,
                )
            )
        return tuple(candidates)

    def scan(self, *, request_id: str = "") -> dict[str, object]:
        self._cancel.clear()
        self._set_operation(request_id=request_id, action="scan", state="running", progress=0)
        try:
            inventory = self._build_inventory()
        except InventoryCancelled:
            self._set_operation(
                request_id=request_id,
                action="scan",
                state="cancelled",
                progress=0,
            )
            return self.snapshot()
        except Exception as exc:
            self._set_operation(
                request_id=request_id,
                action="scan",
                state="failed",
                progress=0,
                error=_safe_error(exc),
            )
            return self.snapshot()
        with self._lock:
            self._inventory = inventory
            self._items = {item.id: item for item in inventory.items}
            self._confirmations.clear()
        self._set_operation(
            request_id=request_id,
            action="scan",
            state="completed",
            progress=100,
        )
        return self.snapshot()

    def preview(
        self,
        item_ids: Sequence[str],
        revision: str,
        *,
        request_id: str = "",
        action: str = "execute",
    ) -> dict[str, object]:
        items = self._selected_items(item_ids, revision)
        if action == "execute":
            rejected = [item for item in items if item.policy != "candidate"]
        else:
            rejected = [item for item in items if action not in item.allowed_actions]
        if rejected:
            raise FileManagementError("selection contains protected or unsupported items")
        token = self.token_factory()
        with self._lock:
            self._confirmations[token] = _Confirmation(
                revision=revision,
                item_ids=tuple(item.id for item in items),
                action=action,
                expires_at=self.clock() + self.confirmation_ttl_seconds,
            )
            self._operation = {
                "id": token,
                "requestId": request_id,
                "action": "preview",
                "state": "preview",
                "progress": 100,
                "error": "",
                "confirmationToken": token,
                "inventoryRevision": revision,
                "itemIds": [item.id for item in items],
                "items": [item.to_payload() for item in items],
                "bytes": sum(item.size for item in items),
                "managedAction": "" if action == "execute" else action,
            }
        return self.snapshot()

    def execute(
        self,
        item_ids: Sequence[str],
        revision: str,
        confirmation_token: str,
        *,
        request_id: str = "",
    ) -> dict[str, object]:
        items = self._consume_confirmation(
            item_ids, revision, confirmation_token, action="execute"
        )
        if self.process_gate():
            self._set_operation(
                request_id=request_id,
                action="execute",
                state="queued_exit",
                progress=0,
                extra={
                    "inventoryRevision": revision,
                    "itemIds": [item.id for item in items],
                    "confirmationToken": confirmation_token,
                },
            )
            return self.snapshot()
        return self._execute_candidates(items, revision, request_id=request_id)

    def execute_queued(self, *, request_id: str = "") -> dict[str, object]:
        with self._lock:
            operation = dict(self._operation)
        if operation.get("state") != "queued_exit":
            return self.snapshot()
        if self.process_gate():
            return self.snapshot()
        revision = str(operation.get("inventoryRevision") or "")
        item_ids = operation.get("itemIds")
        items = self._selected_items(
            item_ids if isinstance(item_ids, list) else [], revision
        )
        return self._execute_candidates(items, revision, request_id=request_id)

    def execute_managed(
        self,
        action: str,
        item_ids: Sequence[str],
        revision: str,
        confirmation_token: str,
        *,
        request_id: str = "",
    ) -> dict[str, object]:
        if action not in MANAGED_ACTIONS:
            raise FileManagementError("unsupported managed action")
        items = self._consume_confirmation(
            item_ids, revision, confirmation_token, action=action
        )
        if len(items) != 1:
            raise FileManagementError("managed actions require exactly one item")
        item = items[0]
        command = _official_command(action, item._managed_identifier)
        self._set_operation(
            request_id=request_id, action=action, state="running", progress=0
        )
        try:
            return_code = int(self.command_runner(command))
        except Exception as exc:
            return_code = -1
            error = _safe_error(exc)
        else:
            error = "" if return_code == 0 else "official Codex command failed"
        self._set_operation(
            request_id=request_id,
            action=action,
            state="completed" if return_code == 0 else "failed",
            progress=100,
            error=error,
        )
        return self.snapshot()

    def _execute_candidates(
        self,
        items: Sequence[InventoryItem],
        revision: str,
        *,
        request_id: str,
    ) -> dict[str, object]:
        self._cancel.clear()
        self._set_operation(
            request_id=request_id,
            action="execute",
            state="running",
            progress=0,
        )
        results: list[dict[str, object]] = []
        total = max(1, len(items))
        for index, item in enumerate(items):
            if self._cancel.is_set():
                self._set_operation(
                    request_id=request_id,
                    action="execute",
                    state="cancelled",
                    progress=round(index * 100 / total),
                    extra={"results": results},
                )
                return self.snapshot()
            try:
                self._revalidate_candidate(item, revision)
                if self.lock_probe(item._path):
                    raise FileManagementError("item is locked")
                self.remove_path(item._path)
            except Exception as exc:
                results.append(
                    {"id": item.id, "state": "failed", "error": _safe_error(exc)}
                )
            else:
                results.append({"id": item.id, "state": "deleted", "error": ""})
            self._set_operation(
                request_id=request_id,
                action="execute",
                state="running",
                progress=round((index + 1) * 100 / total),
                extra={"results": results},
            )
        failures = sum(1 for result in results if result["state"] == "failed")
        self._set_operation(
            request_id=request_id,
            action="execute",
            state="partial" if failures else "completed",
            progress=100,
            error=f"{failures} item(s) failed" if failures else "",
            extra={"results": results},
        )
        return self.snapshot()

    def _set_operation(
        self,
        *,
        request_id: str,
        action: str,
        state: str,
        progress: int,
        error: str = "",
        extra: Mapping[str, object] | None = None,
    ) -> None:
        operation: dict[str, object] = {
            "id": str(request_id or self.token_factory()),
            "requestId": str(request_id or ""),
            "action": action,
            "state": state,
            "progress": max(0, min(100, int(progress))),
            "error": error,
        }
        if extra:
            operation.update(extra)
        with self._lock:
            self._operation = operation

    def _selected_items(
        self, item_ids: Sequence[str], revision: str
    ) -> list[InventoryItem]:
        normalized = tuple(dict.fromkeys(str(item_id or "") for item_id in item_ids))
        if not normalized or any(not item_id for item_id in normalized):
            raise FileManagementError("empty or invalid item selection")
        with self._lock:
            inventory = self._inventory
            items = dict(self._items)
        if inventory is None or not revision or revision != inventory.revision:
            raise FileManagementError("inventory revision is stale")
        try:
            return [items[item_id] for item_id in normalized]
        except KeyError as exc:
            raise FileManagementError("unknown inventory item id") from exc

    def _consume_confirmation(
        self,
        item_ids: Sequence[str],
        revision: str,
        token: str,
        *,
        action: str,
    ) -> list[InventoryItem]:
        items = self._selected_items(item_ids, revision)
        with self._lock:
            confirmation = self._confirmations.pop(str(token or ""), None)
        if confirmation is None:
            raise FileManagementError("confirmation token is missing or expired")
        if confirmation.expires_at < self.clock():
            raise FileManagementError("confirmation token is missing or expired")
        if (
            confirmation.revision != revision
            or confirmation.action != action
            or confirmation.item_ids != tuple(item.id for item in items)
        ):
            raise FileManagementError("confirmation does not match inventory selection")
        return items

    def _revalidate_candidate(self, item: InventoryItem, revision: str) -> None:
        with self._lock:
            inventory = self._inventory
        if inventory is None or inventory.revision != revision:
            raise FileManagementError("inventory revision is stale")
        if item.policy != "candidate":
            raise FileManagementError("raw mutation is restricted to candidates")
        root = dict(self.roots.scan_roots()).get(item.root_key)
        if root is None:
            raise FileManagementError("inventory root is no longer approved")
        resolved = self._secure_resolve(root, item.relative_path)
        if resolved != item._path:
            raise FileManagementError("inventory path changed")
        measured = self._measure_path(resolved, enforce_limit=False)
        current_lstat = _lstat_tuple(os.lstat(resolved))
        if (
            measured.contains_reparse
            or measured.errors
            or measured.fingerprint != item._fingerprint
            or current_lstat != item._lstat
        ):
            raise FileManagementError("item metadata changed after preview")

    def _build_inventory(self) -> Inventory:
        if self._roots_from_resolver:
            self.roots = resolve_codex_roots(
                env=self._resolver_env,
                home=self._resolver_home,
                platform_candidates=self._resolver_platform_candidates,
                read_config=True,
            )
        generated_at = self.clock()
        active_refs = self._active_references()
        items: list[InventoryItem] = []
        total_bytes = 0
        total_files = 0
        visited = 0
        errors = 0
        truncated = False
        for root_key, root in self.roots.scan_roots():
            if self._cancel.is_set():
                raise InventoryCancelled("scan cancelled")
            if not root.exists():
                continue
            try:
                root_stat = os.lstat(root)
                if _is_reparse(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
                    errors += 1
                    continue
                units = self._inventory_units(root_key, root)
                for path, relative_path, managed_identifier, managed_actions in units:
                    if self._cancel.is_set():
                        raise InventoryCancelled("scan cancelled")
                    measured = self._measure_path(
                        path,
                        visited_start=visited,
                        enforce_limit=True,
                    )
                    visited += measured.visited
                    total_bytes += measured.size
                    total_files += measured.files
                    errors += measured.errors
                    policy = self._classify(
                        root_key,
                        relative_path,
                        path,
                        measured,
                        active_refs,
                        generated_at,
                        managed_actions,
                    )
                    item = InventoryItem(
                        id=self.token_factory(),
                        root_key=root_key,
                        relative_path=relative_path,
                        category=policy[0],
                        policy=policy[1],
                        size=measured.size,
                        file_count=measured.files,
                        mtime=measured.mtime,
                        source=policy[2],
                        risk=policy[3],
                        reason=policy[4],
                        allowed_actions=managed_actions if policy[1] == "managed" else (),
                        _path=path,
                        _fingerprint=measured.fingerprint,
                        _lstat=_lstat_tuple(os.lstat(path)),
                        _managed_identifier=managed_identifier,
                    )
                    items.append(item)
            except InventoryLimitReached:
                truncated = True
                break
            if truncated:
                break
        with self._lock:
            self._revision_counter += 1
            revision = f"{self._revision_counter}-{self.token_factory()}"
        return Inventory(
            revision=revision,
            generated_at=generated_at,
            items=tuple(items),
            total_bytes=total_bytes,
            total_files=total_files,
            visited_entries=visited,
            truncated=truncated,
            errors=errors,
        )

    def _inventory_units(
        self, root_key: str, root: Path
    ) -> list[tuple[Path, str, str, tuple[str, ...]]]:
        units: list[tuple[Path, str, str, tuple[str, ...]]] = []
        with os.scandir(root) as entries:
            top_entries = sorted(entries, key=lambda entry: entry.name.casefold())
        for entry in top_entries:
            path = Path(entry.path)
            name = entry.name
            relative = name if root_key == "codex" else f"sqlite/{name}"
            lowered = name.casefold()
            if root_key == "codex" and lowered in {"sessions", "archived_sessions"}:
                units.extend(self._session_units(path, name))
                continue
            if root_key == "codex" and lowered == "plugins":
                units.extend(self._plugin_units(path, name))
                continue
            if root_key == "codex" and lowered == ".tmp":
                units.extend(self._temp_units(path, name))
                continue
            managed_identifier = ""
            actions: tuple[str, ...] = ()
            if root_key == "codex" and lowered == "auth.json":
                actions = ("logout",)
            units.append((path, relative, managed_identifier, actions))
        return units

    def _session_units(
        self, root: Path, prefix: str
    ) -> list[tuple[Path, str, str, tuple[str, ...]]]:
        if not root.is_dir() or root.is_symlink():
            return [(root, prefix, "", ())]
        units: list[tuple[Path, str, str, tuple[str, ...]]] = []
        stack = [root]
        while stack:
            directory = stack.pop()
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda entry: entry.name.casefold())
            for entry in children:
                path = Path(entry.path)
                relative = path.relative_to(self.roots.codex_home).as_posix()
                info = entry.stat(follow_symlinks=False)
                if _is_reparse(info):
                    units.append((path, relative, "", ()))
                elif stat.S_ISDIR(info.st_mode):
                    stack.append(path)
                else:
                    identifier = path.stem if path.suffix.casefold() == ".jsonl" else ""
                    actions = ("delete_session",)
                    if prefix.casefold() == "sessions":
                        actions = ("archive_session", "delete_session")
                    units.append((path, relative, identifier, actions if identifier else ()))
        return units

    def _plugin_units(
        self, root: Path, prefix: str
    ) -> list[tuple[Path, str, str, tuple[str, ...]]]:
        if not root.is_dir() or root.is_symlink():
            return [(root, prefix, "", ())]
        units: list[tuple[Path, str, str, tuple[str, ...]]] = []
        with os.scandir(root) as entries:
            children = sorted(entries, key=lambda entry: entry.name.casefold())
        for entry in children:
            path = Path(entry.path)
            relative = path.relative_to(self.roots.codex_home).as_posix()
            identifier = entry.name
            actions = ("remove_plugin",) if not identifier.startswith(".") else ()
            units.append((path, relative, identifier, actions))
        return units

    def _temp_units(
        self, root: Path, prefix: str
    ) -> list[tuple[Path, str, str, tuple[str, ...]]]:
        if not root.is_dir() or root.is_symlink():
            return [(root, prefix, "", ())]
        units: list[tuple[Path, str, str, tuple[str, ...]]] = []
        with os.scandir(root) as entries:
            children = sorted(entries, key=lambda entry: entry.name.casefold())
        for entry in children:
            path = Path(entry.path)
            if entry.name.casefold() == "bundled-marketplaces" and path.is_dir():
                try:
                    with os.scandir(path) as nested_entries:
                        nested = sorted(
                            nested_entries, key=lambda nested_entry: nested_entry.name.casefold()
                        )
                except OSError:
                    nested = []
                for nested_entry in nested:
                    nested_path = Path(nested_entry.path)
                    nested_relative = nested_path.relative_to(
                        self.roots.codex_home
                    ).as_posix()
                    units.append((nested_path, nested_relative, "", ()))
                continue
            relative = path.relative_to(self.roots.codex_home).as_posix()
            units.append((path, relative, "", ()))
        return units

    def _measure_path(
        self,
        path: Path,
        *,
        visited_start: int = 0,
        enforce_limit: bool,
    ) -> _MeasuredPath:
        digest = hashlib.sha256()
        total_size = 0
        file_count = 0
        latest_mtime = 0.0
        contains_reparse = False
        errors = 0
        visited = 0
        stack = [path]
        while stack:
            if self._cancel.is_set():
                raise InventoryCancelled("scan cancelled")
            current = stack.pop()
            visited += 1
            if enforce_limit and visited_start + visited > self.item_limit:
                raise InventoryLimitReached("inventory item limit reached")
            try:
                info = os.lstat(current)
            except OSError:
                errors += 1
                continue
            relative_key = os.path.normcase(os.path.relpath(current, path))
            identity = _lstat_tuple(info)
            digest.update(repr((relative_key, identity)).encode("utf-8", "replace"))
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
                file_count += 1
            else:
                errors += 1
        return _MeasuredPath(
            size=total_size,
            files=file_count,
            mtime=latest_mtime,
            fingerprint=digest.hexdigest(),
            contains_reparse=contains_reparse,
            errors=errors,
            visited=visited,
        )

    def _active_references(self) -> tuple[Path, ...]:
        config = _read_config(self.roots.codex_home)
        if config.get("__config_unparsed__"):
            # Without a trusted TOML parser, protect every temp unit instead
            # of guessing whether a configured marketplace/source is active.
            return (self.roots.codex_home / ".tmp",)
        references: list[Path] = []
        for raw in _config_path_values(config):
            value = raw.strip()
            if not value or "://" in value:
                continue
            try:
                references.append(_absolute_path(value, base=self.roots.codex_home))
            except (OSError, ValueError):
                continue
        return tuple(references)

    def _classify(
        self,
        root_key: str,
        relative_path: str,
        path: Path,
        measured: _MeasuredPath,
        active_refs: Sequence[Path],
        now: float,
        managed_actions: tuple[str, ...],
    ) -> tuple[str, str, str, str, str]:
        parts = tuple(part.casefold() for part in PurePath(relative_path).parts)
        name = parts[-1] if parts else ""
        if measured.contains_reparse:
            return (
                "reparse_points",
                "blocked",
                "documented",
                "error",
                "Symlinks, junctions, and reparse points are never followed or deleted.",
            )
        if measured.errors:
            return (
                "unreadable",
                "blocked",
                "inferred",
                "error",
                "Metadata could not be verified completely.",
            )
        if root_key == "sqlite" or "sqlite" in parts or _looks_like_sqlite(name):
            return (
                "sqlite_runtime",
                "blocked",
                "documented",
                "error",
                "SQLite databases and WAL/SHM sidecars are protected as one runtime group.",
            )
        if name == "auth.json" and managed_actions == ("logout",):
            return (
                "identity",
                "managed",
                "documented",
                "info",
                "Credentials can only be managed with the official codex logout action.",
            )
        protected_names = {
            "config.toml",
            ".codex-global-state.json",
            "session_index.jsonl",
            "secrets",
            ".sandbox-secrets",
            ".sandbox-bin",
        }
        if any(part in protected_names for part in parts):
            return (
                "configuration_runtime",
                "blocked",
                "documented",
                "warning",
                "Configuration, secrets, indexes, and active runtimes are protected.",
            )
        if parts and parts[0] in {"sessions", "archived_sessions"}:
            return (
                "sessions",
                "managed" if managed_actions else "blocked",
                "documented",
                "info" if managed_actions else "warning",
                "Transcripts require an explicit official Codex archive/delete action."
                if managed_actions
                else "Session metadata is protected from raw file mutation.",
            )
        if parts and parts[0] == "plugins":
            if name == ".plugin-appserver" or not managed_actions:
                return (
                    "plugins_runtime",
                    "blocked",
                    "documented",
                    "error",
                    "Active plugin runtime/cache paths cannot be removed directly.",
                )
            return (
                "plugins",
                "managed",
                "documented",
                "info",
                "Plugins can only be removed with the official codex plugin remove action.",
            )
        if parts and parts[0] == ".tmp":
            if self._path_intersects_references(path, active_refs):
                return (
                    "active_temp_sources",
                    "blocked",
                    "inferred",
                    "error",
                    "This temporary path is referenced by active Codex configuration.",
                )
            age = max(0.0, now - measured.mtime)
            safe_name = any(token in name for token in ("temp", "tmp", "staging", "clone"))
            if safe_name and age >= self.temp_min_age_seconds:
                return (
                    "expired_temp",
                    "candidate",
                    "inferred",
                    "success",
                    "Expired unreferenced temporary staging/clone data; revalidated before deletion.",
                )
            return (
                "temporary_unknown",
                "unknown",
                "inferred",
                "warning",
                "Temporary data is too recent or cannot be proven inactive.",
            )
        if name.endswith(".log"):
            return (
                "logs",
                "unknown",
                "inferred",
                "warning",
                "Plain logs remain protected unless a stable inactive-owner rule is available.",
            )
        return (
            "unknown",
            "unknown",
            "inferred",
            "warning",
            "Unknown Codex-private data is protected by default.",
        )

    @staticmethod
    def _path_intersects_references(path: Path, references: Sequence[Path]) -> bool:
        path_key = _path_key(path)
        for reference in references:
            reference_key = _path_key(reference)
            try:
                common = os.path.commonpath((path_key, reference_key))
            except ValueError:
                continue
            if common in {path_key, reference_key}:
                return True
        return False

    @staticmethod
    def _secure_resolve(root: Path, relative_path: str) -> Path:
        pure = PurePath(relative_path)
        parts = pure.parts
        if pure.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
            raise FileManagementError("invalid inventory relative path")
        if parts[0].casefold() == "sqlite":
            parts = parts[1:]
        if not parts:
            raise FileManagementError("inventory root cannot be selected")
        root_abs = Path(os.path.abspath(root))
        candidate = root_abs.joinpath(*parts)
        try:
            resolved_root = root_abs.resolve(strict=True)
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise FileManagementError("inventory path escaped its approved root") from exc
        if resolved == resolved_root:
            raise FileManagementError("inventory root cannot be selected")
        current = resolved
        while current != resolved_root:
            if _is_reparse(os.lstat(current)):
                raise FileManagementError("reparse paths are protected")
            current = current.parent
        return resolved


class CodexFileManagerWorker:
    """One blocking queue worker; idle state performs no recurring work."""

    def __init__(
        self,
        manager: CodexFileManager,
        *,
        on_update: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.manager = manager
        self.on_update = on_update
        self._queue: queue.Queue[dict[str, object] | None] = queue.Queue()
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="codex-usage-hud-file-manager",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, command: Mapping[str, object]) -> dict[str, object]:
        action = str(command.get("action") or "").strip()
        if action not in WORKER_ACTIONS:
            raise FileManagementError("unsupported file-management command")
        if self._closed.is_set():
            raise FileManagementError("file manager is closed")
        request_id = str(command.get("requestId") or command.get("id") or "").strip()
        if not request_id:
            request_id = secrets.token_urlsafe(12)
        payload = dict(command)
        payload["requestId"] = request_id
        if action == "cancel":
            self.manager.cancel()
            callback = self.on_update
            if callback is not None:
                try:
                    callback(self.manager.snapshot())
                except Exception:
                    pass
            return {"status": "accepted", "requestId": request_id, "action": action}
        self._queue.put_nowait(payload)
        return {"status": "accepted", "requestId": request_id, "action": action}

    def close(self, timeout_seconds: float = 2.0) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self.manager.cancel()
        self._queue.put_nowait(None)
        self._thread.join(timeout=max(0.0, float(timeout_seconds)))

    def _run(self) -> None:
        while True:
            command = self._queue.get()
            if command is None:
                return
            request_id = str(command.get("requestId") or "")
            action = str(command.get("action") or "")
            try:
                if action == "scan":
                    snapshot = self.manager.scan(request_id=request_id)
                elif action == "preview":
                    snapshot = self.manager.preview(
                        _string_list(command.get("itemIds")),
                        str(command.get("inventoryRevision") or ""),
                        request_id=request_id,
                        action=str(command.get("managedAction") or "execute"),
                    )
                elif action == "execute":
                    snapshot = self.manager.execute(
                        _string_list(command.get("itemIds")),
                        str(command.get("inventoryRevision") or ""),
                        str(command.get("confirmationToken") or ""),
                        request_id=request_id,
                    )
                elif action == "execute_pending":
                    snapshot = self.manager.execute_queued(request_id=request_id)
                else:
                    snapshot = self.manager.execute_managed(
                        action,
                        _string_list(command.get("itemIds")),
                        str(command.get("inventoryRevision") or ""),
                        str(command.get("confirmationToken") or ""),
                        request_id=request_id,
                    )
            except Exception as exc:
                self.manager._set_operation(
                    request_id=request_id,
                    action=action,
                    state="failed",
                    progress=0,
                    error=_safe_error(exc),
                )
                snapshot = self.manager.snapshot()
            callback = self.on_update
            if callback is not None:
                try:
                    callback(snapshot)
                except Exception:
                    pass


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _looks_like_sqlite(name: str) -> bool:
    lower = name.casefold()
    if lower.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm", ".db", ".db-wal", ".db-shm")):
        return True
    return lower.endswith(("-wal", "-shm")) and ("state" in lower or "sqlite" in lower)


def _safe_error(error: BaseException) -> str:
    if isinstance(error, FileManagementError):
        return str(error)[:240]
    return error.__class__.__name__


def _official_command(action: str, identifier: str) -> list[str]:
    if action == "logout":
        return ["codex", "logout"]
    normalized = str(identifier or "").strip()
    if (
        not normalized
        or normalized.startswith("-")
        or ".." in normalized
        or any(character in normalized for character in "\r\n\x00/\\")
    ):
        raise FileManagementError("managed item has no safe official identifier")
    if action == "archive_session":
        return ["codex", "archive", normalized]
    if action == "delete_session":
        return ["codex", "delete", normalized]
    if action == "remove_plugin":
        return ["codex", "plugin", "remove", normalized]
    raise FileManagementError("unsupported managed action")


def _run_official_command(command: Sequence[str]) -> int:
    completed = subprocess.run(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )
    return int(completed.returncode)


def _remove_path(path: Path) -> None:
    info = os.lstat(path)
    if _is_reparse(info):
        raise FileManagementError("reparse paths are protected")
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path)
    elif stat.S_ISREG(info.st_mode):
        path.unlink()
    else:
        raise FileManagementError("unsupported filesystem object")


def codex_processes_active() -> bool:
    """Conservative process gate, invoked only for an explicit execute command."""

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
                return True
            names = {line.split('","', 1)[0].strip('"').casefold() for line in completed.stdout.splitlines()}
            return any(
                name in {"codex.exe", "codex-app-server.exe", "plugin-appserver.exe"}
                or "appserver" in name
                or ("codex" in name and "plugin" in name)
                for name in names
            )
        completed = subprocess.run(
            ["ps", "-axo", "comm=,args="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            return True
        for line in completed.stdout.splitlines():
            lowered = line.casefold()
            if "codex-usage-hud" in lowered:
                continue
            if (
                lowered.startswith("codex ")
                or lowered.startswith("codex\t")
                or "/codex " in lowered
                or " codex app-server" in lowered
                or "plugin-appserver" in lowered
            ):
                return True
        return False
    except (OSError, subprocess.SubprocessError):
        return True


def path_is_locked(path: Path) -> bool:
    """Return True when an exclusive metadata handle cannot be acquired."""

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            create_file = ctypes.windll.kernel32.CreateFileW
            create_file.argtypes = (
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            )
            create_file.restype = wintypes.HANDLE
            handle = create_file(
                str(path),
                0x80000000,
                0,
                None,
                3,
                0x02000000 if path.is_dir() else 0,
                None,
            )
            invalid = ctypes.c_void_p(-1).value
            if int(handle) == int(invalid):
                return True
            ctypes.windll.kernel32.CloseHandle(handle)
            return False
        except Exception:
            return True
    try:
        import fcntl

        descriptor = os.open(path, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
        return False
    except (OSError, ImportError):
        return True


__all__ = [
    "CodexCleanupCandidate",
    "CodexFileManager",
    "CodexFileManagerWorker",
    "CodexRoots",
    "FileManagementError",
    "Inventory",
    "InventoryCancelled",
    "InventoryItem",
    "InventoryLimitReached",
    "MANAGED_ACTIONS",
    "POLICIES",
    "WORKER_ACTIONS",
    "codex_processes_active",
    "path_is_locked",
    "resolve_codex_roots",
]
