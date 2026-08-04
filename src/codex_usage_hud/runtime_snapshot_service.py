"""Production snapshot ports and runtime-error projection."""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path

from .active_work import _effective_provider_scope, active_work_items_for_snapshot
from .core import BaseEstimate, ParsedSession
from .platforms import is_new_session_source, is_pending_session_source
from .runtime_context import RuntimeContext
from .runtime_diagnostics import RUNTIME_DEBUG_ENV, ensure_runtime_error_diagnostics
from . import snapshot_builder as snapshot_builder_owner
from . import session_snapshots
from .ui.renderer_domains import RendererHudClient
from .usage_insights import _refresh_usage_insights_payload, apply_family_session_usage


def _ensure_runtime_error_diagnostics(context: object) -> None:
    ensure_runtime_error_diagnostics(context)


def snapshot_session_key(snapshot: ParsedSession | None) -> str:
    if snapshot is None:
        return ""
    return session_snapshots.session_path_key(snapshot.session_path) or str(
        snapshot.session_id or ""
    )


def clone_cached_session_snapshot(snapshot: ParsedSession) -> ParsedSession:
    return session_snapshots.clone_cached_snapshot(snapshot)


def active_session_switch_pending(
    context: object,
    snapshot: ParsedSession | None,
) -> bool:
    resolver = getattr(context, "session_resolver", None)
    if resolver is None:
        return False
    try:
        session_path, _selection_source = resolver.resolve()
    except Exception:
        return False
    current_key = session_snapshots.session_path_key(session_path) or str(
        getattr(resolver, "session_id", "") or ""
    )
    return bool(current_key and current_key != snapshot_session_key(snapshot))

def _runtime_debug_enabled() -> bool:
    value = os.environ.get(RUNTIME_DEBUG_ENV)
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized not in {"", "0", "false", "no", "off"}


def _record_active_session_runtime_error(
    context: RuntimeContext,
    selection_source: str,
    session_path: Path | None,
) -> None:
    _ensure_runtime_error_diagnostics(context)
    registry = getattr(context, "runtime_errors", None)
    if registry is None:
        return
    source = str(selection_source or "")
    pending_codes = {
        "awaiting-canonical-id": "awaiting_canonical_id",
        "awaiting-persistence": "awaiting_persistence",
        "awaiting-exact-mapping": "awaiting_exact_mapping",
        "ambiguous-persisted-identity": "ambiguous_persisted_identity",
        "renderer-channel-unavailable": "renderer_channel_unavailable",
    }
    tracker = getattr(context, "active_session_tracker", None)
    follow_reason = str(getattr(tracker, "follow_reason", "") or "")
    if is_pending_session_source(source):
        code = pending_codes.get(follow_reason, "pending_mapping")
        registry.record(
            source="active_session",
            severity=(
                "error"
                if follow_reason
                in {"ambiguous-persisted-identity", "renderer-channel-unavailable"}
                else "warning"
            ),
            code=code,
            message="Renderer active session is waiting for exact local reconciliation.",
            context={
                "selectionSource": source,
                "followReason": follow_reason,
                "selectionSeq": int(getattr(tracker, "selection_seq", 0) or 0),
                "threadId": str(getattr(tracker, "latest_session_id", "") or ""),
                "title": str(getattr(tracker, "latest_title", "") or ""),
            },
        )
        return
    if source.startswith("renderer-unmatched"):
        registry.record(
            source="active_session",
            severity="error",
            code="unmatched_thread",
            message="Renderer active session could not be mapped to a local JSONL session.",
            context={
                "selectionSource": source,
                "sessionPath": str(session_path or ""),
                "threadId": str(getattr(tracker, "latest_session_id", "") or ""),
                "title": str(getattr(tracker, "latest_title", "") or ""),
                "trackerSource": str(getattr(tracker, "latest_source", "") or ""),
            },
        )
        return
    if source.startswith("renderer:") or is_new_session_source(source):
        registry.resolve(source="active_session", code="unmatched_thread")
        registry.resolve(source="active_session", code="pending_mapping")
        for code in pending_codes.values():
            registry.resolve(source="active_session", code=code)


def _record_cdp_update_failure(
    context: RuntimeContext,
    client: RendererHudClient,
    *,
    failures: int,
) -> None:
    _ensure_runtime_error_diagnostics(context)
    registry = getattr(context, "runtime_errors", None)
    if registry is None:
        return
    registry.record(
        source="cdp",
        severity="error",
        code="update_failed",
        message="Renderer HUD payload update failed.",
        context={
            "failures": int(failures),
            "status": str(getattr(client, "last_status", "") or ""),
            "error": str(getattr(client, "last_error", "") or ""),
            "timeoutSeconds": float(getattr(client, "timeout_seconds", 0.0) or 0.0),
            "metrics": dict(getattr(client, "last_update_metrics", {}) or {}),
        },
    )


def _resolve_cdp_update_failure(context: RuntimeContext) -> None:
    registry = getattr(context, "runtime_errors", None)
    if registry is not None:
        registry.resolve(source="cdp", code="update_failed")


def _runtime_errors_payload_for_context(context: RuntimeContext) -> list[dict[str, object]]:
    registry = getattr(context, "runtime_errors", None)
    if registry is None:
        return []
    payload = getattr(registry, "to_payload", None)
    return payload() if callable(payload) else []


def build_snapshot(
    context: RuntimeContext,
    *,
    refresh_budget_aggregate: bool | None = None,
    refresh_budget_paths: Iterable[Path] = (),
    refresh_active_work_items: bool = True,
    refresh_current_session_usage: bool = True,
    reuse_budget_from: ParsedSession | None = None,
    refresh_visible_app_error: bool = True,
) -> ParsedSession:
    ports = snapshot_builder_owner.SnapshotBuilderPorts(
        record_active_session_error=_record_active_session_runtime_error,
        provider_scope=_effective_provider_scope,
        refresh_usage_insights=_refresh_usage_insights_payload,
        active_work_items=active_work_items_for_snapshot,
        apply_family_usage=apply_family_session_usage,
    )
    return snapshot_builder_owner.build_snapshot(
        context,
        ports,
        refresh_budget_aggregate=refresh_budget_aggregate,
        refresh_budget_paths=refresh_budget_paths,
        refresh_active_work_items=refresh_active_work_items,
        refresh_current_session_usage=refresh_current_session_usage,
        reuse_budget_from=reuse_budget_from,
        refresh_visible_app_error=refresh_visible_app_error,
    )


def _update_session_cleanup_activity(
    context: RuntimeContext,
    snapshot: ParsedSession,
) -> None:
    snapshot_builder_owner.update_session_cleanup_activity(context, snapshot)


def _apply_pre_send_and_activity(
    context: RuntimeContext,
    snapshot: ParsedSession,
) -> None:
    snapshot_builder_owner.apply_pre_send_and_activity(context, snapshot)


def _apply_pre_send_pricing(
    context: RuntimeContext,
    snapshot: ParsedSession,
    base: "BaseEstimate",
) -> "BaseEstimate":
    return snapshot_builder_owner.apply_pre_send_pricing(context, snapshot, base)


def snapshot_to_text(snapshot: ParsedSession, compact: bool = False) -> str:
    return snapshot_builder_owner.snapshot_to_text(snapshot, compact=compact)
