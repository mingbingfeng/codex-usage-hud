"""Serialized session-cleanup worker with injected usage refresh integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import logging
import queue
from pathlib import Path
from threading import Event, Thread, current_thread
import uuid

from .core.session_cleanup import (
    SessionCleanupError,
    SessionCleanupItem,
    SessionCleanupManager,
)
from .core.session_transfer import CodexAppServerClient
from .core.deleted_usage import DeletedUsageLedger, DeletedUsageLedgerError


_LOGGER = logging.getLogger("codex_usage_hud.session_cleanup_runtime")


class DeletedUsageTransactions:
    """Own prepare/commit/discard around the deleted-session usage ledger."""

    def __init__(
        self,
        ledger: DeletedUsageLedger | None,
        parser: object,
        *,
        on_commit: Callable[[], None],
    ) -> None:
        self.ledger = ledger
        self.parser = parser
        self.on_commit = on_commit

    def prepare(self, item: object) -> str:
        if self.ledger is None:
            raise DeletedUsageLedgerError(
                "Deleted-session usage ledger is not configured."
            )
        return self.ledger.prepare(
            session_id=getattr(item, "_session_id"),
            family_session_ids=(
                getattr(item, "_session_id"),
                *getattr(item, "_descendant_ids"),
            ),
            title=getattr(item, "title"),
            workdir_name=getattr(item, "workdir_name"),
            rollout_paths=getattr(item, "_rollout_paths"),
            parser=self.parser,
        )

    def commit(self, receipt: object) -> None:
        if self.ledger is None:
            raise DeletedUsageLedgerError(
                "Deleted-session usage ledger is not configured."
            )
        self.ledger.commit(str(receipt or ""))
        self.on_commit()

    def discard(self, receipt: object) -> None:
        if self.ledger is not None:
            self.ledger.discard(str(receipt or ""))


def cleanup_string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item) for item in value if str(item or "").strip()]


class SessionCleanupWorker:
    _ACTIONS = {
        "sessionCleanupScan",
        "sessionCleanupPreview",
        "sessionCleanupExecute",
        "sessionCleanupCancel",
        "providerDelete",
        "sessionTransfer",
    }

    def __init__(
        self,
        context: object,
        manager: SessionCleanupManager,
        *,
        on_deleted: Callable[[object, str], None],
    ) -> None:
        self._context = context
        self.manager = manager
        self._on_deleted = on_deleted
        self._queue: queue.Queue[dict[str, object] | None] = queue.Queue()
        self._closed = Event()
        self._worker = Thread(
            target=self._run,
            name="codex-usage-hud-session-cleanup",
            daemon=True,
        )
        self._worker.start()

    def enqueue(self, command: Mapping[str, object]) -> dict[str, object]:
        action = str(command.get("action") or "").strip()
        if action == "deleteProvider":
            action = "providerDelete"
        if action not in self._ACTIONS:
            raise SessionCleanupError("unsupported session-cleanup command")
        if self._closed.is_set():
            raise SessionCleanupError("session cleanup worker is closed")
        request_id = str(command.get("requestId") or "").strip() or uuid.uuid4().hex
        payload = dict(command)
        payload["action"] = action
        payload["requestId"] = request_id
        if action == "sessionCleanupCancel":
            self._publish(self.manager.cancel(request_id=request_id))
        else:
            self._publish(
                self.manager.mark_operation(
                    request_id=request_id,
                    action=action,
                    state="scanning" if action == "sessionCleanupScan" else "accepted",
                    progress=0,
                )
            )
            self._queue.put_nowait(payload)
        return {"status": "accepted", "requestId": request_id, "action": action}

    def close(self, timeout_seconds: float = 2.0) -> bool:
        if self._closed.is_set():
            return not self._worker.is_alive()
        self._closed.set()
        self._queue.put_nowait(None)
        if self._worker is not current_thread() and self._worker.is_alive():
            self._worker.join(timeout=max(0.0, float(timeout_seconds)))
        return not self._worker.is_alive()

    def _run(self) -> None:
        while True:
            command = self._queue.get()
            if command is None:
                return
            action = str(command.get("action") or "")
            request_id = str(command.get("requestId") or "")
            refresh_after_delete = False
            try:
                if action == "sessionCleanupScan":
                    previous_publisher = getattr(self.manager, "progress_publisher", None)
                    self.manager.progress_publisher = self._publish
                    try:
                        snapshot = self.manager.scan(request_id=request_id)
                    finally:
                        self.manager.progress_publisher = previous_publisher
                elif action == "sessionCleanupPreview":
                    snapshot = self.manager.preview(
                        cleanup_string_list(command.get("itemIds") or command.get("sessionIds")),
                        str(command.get("inventoryRevision") or ""),
                        request_id=request_id,
                    )
                elif action == "providerDelete":
                    # config.toml / 单价删除已在 dispatch 同步请求阶段完成；此处只负责
                    # 后台清理该供应商的会话历史，避免历史较多时阻塞前端删除反馈。
                    provider_id = str(
                        command.get("provider") or command.get("providerId") or ""
                    ).strip().casefold()
                    app_provider = str(
                        getattr(self._context, "app_provider", "") or ""
                    ).strip().casefold()
                    if provider_id and provider_id == app_provider:
                        raise SessionCleanupError(
                            "默认 Codex App Provider 不支持删除供应商配置。"
                        )
                    history_snapshot: Mapping[str, object] | None = None
                    if bool(command.get("deleteSessionHistory")):
                        history_snapshot = self.manager.delete_provider_history(
                            provider_id,
                            request_id=request_id,
                        )
                    history_operation = (
                        history_snapshot.get("operation")
                        if isinstance(history_snapshot, Mapping)
                        else {}
                    )
                    history_deleted = int(
                        history_operation.get("deletedCount") or 0
                        if isinstance(history_operation, Mapping)
                        else 0
                    )
                    history_actual_bytes = int(
                        history_operation.get("actualBytes") or 0
                        if isinstance(history_operation, Mapping)
                        else 0
                    )
                    snapshot = self.manager.mark_operation(
                        request_id=request_id,
                        action=action,
                        state="completed",
                        progress=100,
                        provider=provider_id,
                        historyDeletedCount=history_deleted,
                        deletedCount=history_deleted,
                        actualBytes=history_actual_bytes,
                        failedCount=0,
                    )
                    refresh_after_delete = history_deleted > 0
                elif action == "sessionTransfer":
                    source_provider = str(
                        command.get("sourceProvider") or command.get("source_provider") or ""
                    ).strip().casefold()
                    target_provider = str(
                        command.get("targetProvider") or command.get("target_provider") or ""
                    ).strip().casefold()
                    mode = str(command.get("mode") or "copy").strip().casefold()
                    self._validate_transfer_provider(
                        source_provider,
                        target_provider,
                    )
                    item_ids = cleanup_string_list(
                        command.get("itemIds") or command.get("sessionIds")
                    )
                    revision = str(command.get("inventoryRevision") or "")
                    previous_publisher = getattr(self.manager, "progress_publisher", None)
                    self.manager.progress_publisher = self._publish
                    try:
                        sessions_root = getattr(self._context, "sessions_root", None)
                        codex_home = (
                            Path(sessions_root).parent
                            if sessions_root is not None
                            else None
                        )
                        with CodexAppServerClient(codex_home=codex_home) as app_server:
                            snapshot = self.manager.transfer(
                                item_ids,
                                revision,
                                source_provider,
                                target_provider,
                                mode,
                                fork=lambda session_id, provider, cwd: app_server.fork(
                                    session_id,
                                    provider,
                                    cwd=cwd,
                                ),
                                verify=lambda session_id, provider: app_server.verify_persistent_thread(
                                    session_id,
                                    provider,
                                ),
                                request_id=request_id,
                            )
                    finally:
                        self.manager.progress_publisher = previous_publisher
                    transfer_operation = snapshot.get("operation")
                    refresh_after_delete = bool(
                        isinstance(transfer_operation, Mapping)
                        and int(transfer_operation.get("migratedCount") or 0) > 0
                    )
                else:
                    snapshot = self.manager.execute(
                        cleanup_string_list(command.get("itemIds") or command.get("sessionIds")),
                        str(command.get("inventoryRevision") or ""),
                        str(command.get("confirmationToken") or ""),
                        request_id=request_id,
                    )
                    operation = snapshot.get("operation")
                    if isinstance(operation, Mapping) and int(
                        operation.get("deletedCount") or 0
                    ) > 0:
                        refresh_after_delete = True
            except Exception as exc:
                failure_values: dict[str, object] = {}
                if action == "providerDelete":
                    failure_values["provider"] = str(
                        command.get("provider") or command.get("providerId") or ""
                    ).strip().lower()
                snapshot = self.manager.mark_operation(
                    request_id=request_id,
                    action=action,
                    state="failed",
                    progress=100,
                    error=str(exc) or type(exc).__name__,
                    **failure_values,
                )
            self._publish(snapshot)
            if refresh_after_delete:
                Thread(
                    target=self._refresh_deleted_usage,
                    args=(request_id,),
                    name="codex-usage-hud-deleted-usage-refresh",
                    daemon=True,
                ).start()

    def _validate_transfer_provider(self, source: str, target: str) -> None:
        if not source or not target:
            raise SessionCleanupError("会话迁移需要源和目标 Provider。")
        if source == target:
            raise SessionCleanupError("源 Provider 与目标 Provider 不能相同。")
        registry = getattr(self._context, "provider_registry", None)
        entries = getattr(registry, "entries", {})
        target_entry = entries.get(target) if isinstance(entries, Mapping) else None
        app_provider = str(
            getattr(self._context, "app_provider", "") or ""
        ).strip().casefold()
        if target != app_provider and target_entry is None:
            raise SessionCleanupError("目标 Provider 不在当前 Codex 配置中。")
        if target != app_provider and target_entry is not None:
            configured = any(
                bool(getattr(target_entry, name, False))
                for name in (
                    "from_base_config",
                    "from_profile",
                    "from_provider_definition",
                    "from_saved_settings",
                )
            )
            if not configured:
                raise SessionCleanupError("目标 Provider 只有历史记录，尚未配置。")

    def _refresh_deleted_usage(self, request_id: str) -> None:
        try:
            self._on_deleted(self._context, request_id)
        except Exception:
            _LOGGER.exception("deleted_session_usage_refresh_callback_failed")

    def _publish(self, payload: Mapping[str, object]) -> None:
        snapshot = dict(payload)
        setattr(self._context, "session_cleanup_payload", snapshot)
        event_bus = getattr(self._context, "runtime_events", None)
        publish = getattr(event_bus, "publish", None)
        if not callable(publish):
            return
        operation = snapshot.get("operation")
        values = operation if isinstance(operation, Mapping) else {}
        publish(
            "session_cleanup_changed",
            source="session_cleanup",
            context={
                "requestId": str(values.get("requestId") or ""),
                "action": str(values.get("action") or ""),
                "state": str(values.get("state") or ""),
                "revision": str(snapshot.get("revision") or ""),
            },
        )


def _session_cleanup_current_ids(context: object) -> tuple[str, ...]:
    values: list[str] = []
    values.append(str(getattr(context, "session_management_current_session_id", "") or ""))
    resolver = getattr(context, "session_resolver", None)
    values.append(str(getattr(resolver, "session_id", "") or ""))
    tracker = getattr(context, "active_session_tracker", None)
    values.append(str(getattr(tracker, "latest_session_id", "") or ""))
    return tuple(values)


def _session_cleanup_active_ids(context: object) -> tuple[str, ...]:
    values = getattr(context, "session_management_active_session_ids", set())
    return tuple(str(value) for value in values)


def _prepare_session_cleanup_usage(
    context: object, item: SessionCleanupItem
) -> object:
    try:
        return getattr(context, "usage_cache").prepare_deleted_session_usage(item)
    except DeletedUsageLedgerError as exc:
        raise SessionCleanupError(str(exc)) from exc


def _commit_session_cleanup_usage(context: object, receipt: object) -> None:
    try:
        getattr(context, "usage_cache").commit_deleted_session_usage(receipt)
    except DeletedUsageLedgerError as exc:
        raise SessionCleanupError(str(exc)) from exc


def _discard_session_cleanup_usage(context: object, receipt: object) -> None:
    try:
        getattr(context, "usage_cache").discard_deleted_session_usage(receipt)
    except DeletedUsageLedgerError:
        pass


def _build_session_cleanup_manager(context: object) -> SessionCleanupManager:
    return SessionCleanupManager(
        state_db_path=Path(getattr(context, "state_db_path")),
        sessions_root=Path(getattr(context, "sessions_root")),
        session_index_path=Path(getattr(context, "session_index_path")),
        current_session_ids=lambda: _session_cleanup_current_ids(context),
        active_session_ids=lambda: _session_cleanup_active_ids(context),
        usage_snapshot_prepare=lambda item: _prepare_session_cleanup_usage(
            context, item
        ),
        usage_snapshot_commit=lambda receipt: _commit_session_cleanup_usage(
            context, receipt
        ),
        usage_snapshot_discard=lambda receipt: _discard_session_cleanup_usage(
            context, receipt
        ),
    )
