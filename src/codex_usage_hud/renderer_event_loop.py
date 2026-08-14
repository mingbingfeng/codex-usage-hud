"""Pure renderer runtime event reduction and loop data contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from pathlib import Path

from .core import ParsedSession
from . import runtime_policies
from .renderer_event_normalization import (
    NormalizedEventBatch,
    event_bus_timestamp,
    normalize_runtime_events,
)
from .renderer_event_reduction import RefreshPlan, reduce_event, reduce_events
from .renderer_wait import (
    RendererWaitPlanner,
    RendererWaitPorts,
    ScheduledDeadlines,
    scheduled_wait_delay,
)


_LOGGER = logging.getLogger(__name__)


@dataclass
class RendererTickInputs:
    """Immutable snapshot of wakeup reasons sampled at tick start."""

    started: float
    update_state: dict[str, object]
    bridge_wakeup: bool
    active_session_wakeup: bool
    file_change_reasons: set[str]
    file_change_paths: set[Path]
    command: dict[str, object] | None
    budget_window_keys: tuple[str, str]
    runtime_events: list[object]
    event_refresh_request: RefreshPlan

    @property
    def file_refresh_requested(self) -> bool:
        return bool(self.file_change_reasons)


@dataclass
class RendererLoopState:
    """State carried across renderer ticks."""

    failures: int = 0
    settings_command_status: dict[str, object] = field(default_factory=dict)
    next_daemon_check_at: float = 0.0
    latest_snapshot: ParsedSession | None = None
    latest_budget_signature: tuple[object, ...] | None = None
    latest_budget_window_keys: tuple[str, str] | None = None
    latest_update_state_signature: object | None = None
    latest_update_state: dict[str, object] | None = None
    latest_active_work_refresh_at: float = 0.0
    active_work_refresh_pending: bool = False
    active_work_refresh_not_before: float = 0.0
    background_usage_response_retry_attempts: int = 0
    background_usage_response_retry_not_before: float = 0.0
    soft_reinstall_pending: bool = False
    activity_wake_pending: str = ""
    request_rows_limit: int = 30
    request_rows_session_id: str = ""
    pending_refresh_plan: RefreshPlan = field(default_factory=RefreshPlan)
    pending_retry_not_before: float = 0.0


@dataclass(frozen=True, slots=True)
class RendererLoopExecutorPorts:
    sample_inputs: Callable[[], RendererTickInputs]
    apply_inputs: Callable[[RendererTickInputs], None]
    exit_requested: Callable[[], bool]
    restart_requested: Callable[[], bool]
    restart_result: Callable[[], int]
    daemon_tick: Callable[[], int | None]
    compute_force_fast: Callable[[RendererTickInputs], bool]
    apply_refresh: Callable[[RendererTickInputs, bool], object]
    current_snapshot: Callable[[], object | None]
    apply_domain_update: Callable[[RendererTickInputs], bool]
    keep_alive: Callable[[], None]
    after_iteration: Callable[[object | None], None]
    compute_wait_delay: Callable[[object, RendererTickInputs, bool], float]
    wait: Callable[[float], object]
    update_gate: Callable[[], tuple[bool, str, float]] = lambda: (True, "", 0.0)
    record_refresh_merge: Callable[[], None] = lambda: None


@dataclass(frozen=True, slots=True)
class RendererTickSamplerPorts:
    monotonic: Callable[[], float]
    wall_time: Callable[[], float]
    take_active_work: Callable[[], tuple[int, list[object]] | None]
    tracker: Callable[[], object | None]
    stabilize_active_work: Callable[[list[object]], list[object]]
    publish_active_work: Callable[[list[object]], None]
    update_state: Callable[[], dict[str, object]]
    update_state_signature: Callable[[dict[str, object]], object]
    rest_reminder: Callable[[], object | None]
    publish_rest_reminder: Callable[[dict[str, object]], None]
    publish_event: Callable[..., object] | None
    current_session: Callable[[], str | None]
    budget_window_keys: Callable[[], tuple[str, str]]
    bridge_wake_event: object
    active_session_wake_event: object
    take_file_changes: Callable[[], tuple[set[str], set[Path]]]
    invalidate_mapping: Callable[[], None]
    take_command: Callable[[], dict[str, object] | None]
    drain_events: Callable[[], list[object]]
    event_bus: object | None
    path_key: Callable[[Path | None], str]
    background_response_pending: Callable[[dict[str, object]], bool]


class RendererTickSampler:
    """Sample runtime ports once and normalize them into immutable tick inputs."""

    def __init__(
        self,
        state: RendererLoopState,
        ports: RendererTickSamplerPorts,
    ) -> None:
        self.state = state
        self.ports = ports

    def sample(self) -> RendererTickInputs:
        started_at = self.ports.monotonic()
        self._apply_active_work_result()
        bridge_wake = self._take_event_flag(self.ports.bridge_wake_event)
        active_session_wake = self._take_event_flag(
            self.ports.active_session_wake_event
        )
        reasons, paths = self.ports.take_file_changes()
        session_map_changed = "session-map" in reasons
        if session_map_changed:
            self._rematerialize_mapping()
        command = self.ports.take_command()
        runtime_events = self.ports.drain_events()
        active_session_only = self._active_session_only_wake(
            active_session_wake=active_session_wake,
            command=command,
            file_change_reasons=reasons,
            runtime_events=runtime_events,
        )
        if active_session_only:
            # The selected-session payload can reuse the last sampled runtime
            # state. Defer update checks, reminder transitions, and budget
            # boundary detection to the next ordinary wake so they cannot
            # delay the visible active-session handoff.
            update_state = dict(self.state.latest_update_state or {})
            budget_window_keys = self.state.latest_budget_window_keys or (
                "",
                "",
            )
        else:
            update_state = self.ports.update_state()
            self._sample_rest_reminder()
            self._publish_update_state_change(update_state)
            budget_window_keys = self._publish_budget_window_change()
        if (
            self.state.active_work_refresh_pending
            and self.ports.monotonic()
            >= self.state.active_work_refresh_not_before
        ):
            self._publish(
                "active_work_refresh_requested",
                source="renderer_loop",
                context={"reason": "pending_after_active_session_refresh"},
            )
        runtime_events.extend(self.ports.drain_events())
        normalized = normalize_runtime_events(
            runtime_events,
            file_change_reasons=reasons,
            file_change_paths=paths,
            session_map_changed=session_map_changed,
            active_session_wake=active_session_wake,
            current_session=self.ports.current_session(),
            timestamp=event_bus_timestamp(
                self.ports.event_bus,
                fallback_clock=self.ports.wall_time,
            ),
            path_key=self.ports.path_key,
            existing_activity_wake_reason=self.state.activity_wake_pending,
        )
        events = list(normalized.events)
        self.state.activity_wake_pending = normalized.activity_wake_reason
        _, plan = reduce_events(self.state, events)
        if (
            self.ports.background_response_pending(
                self.state.settings_command_status
            )
            and self.state.background_usage_response_retry_attempts > 0
            and self.ports.monotonic()
            >= self.state.background_usage_response_retry_not_before
        ):
            plan.request_domains("backgroundUsage", force_fast=True)
        return RendererTickInputs(
            started=started_at,
            update_state=update_state,
            bridge_wakeup=bridge_wake,
            active_session_wakeup=active_session_wake,
            file_change_reasons=reasons,
            file_change_paths=paths,
            command=command,
            budget_window_keys=budget_window_keys,
            runtime_events=events,
            event_refresh_request=plan,
        )

    def _active_session_only_wake(
        self,
        *,
        active_session_wake: bool,
        command: dict[str, object] | None,
        file_change_reasons: set[str],
        runtime_events: list[object],
    ) -> bool:
        """Return whether this wake can use the visible-session fast path."""
        if not active_session_wake or self.state.latest_snapshot is None:
            return False
        if command or file_change_reasons:
            return False
        return not any(
            str(getattr(event, "type", "") or "") != "active_session_changed"
            for event in runtime_events
        )

    def _apply_active_work_result(self) -> None:
        result = self.ports.take_active_work()
        if result is None:
            return
        result_seq, result_items = result
        tracker = self.ports.tracker()
        current_seq = int(getattr(tracker, "selection_seq", 0) or 0)
        latest_seq = int(
            getattr(self.state.latest_snapshot, "selection_seq", 0) or 0
        )
        if (
            self.state.latest_snapshot is not None
            and result_seq == current_seq
            and result_seq == latest_seq
        ):
            stable_items = self.ports.stabilize_active_work(result_items)
            self.state.latest_snapshot.active_work_items = list(stable_items)
            self.ports.publish_active_work(stable_items)
            return
        _LOGGER.info(
            "renderer_active_work_discarded result_seq=%s current_seq=%s latest_seq=%s",
            result_seq,
            current_seq,
            latest_seq,
        )

    def _sample_rest_reminder(self) -> None:
        reminder = self.ports.rest_reminder()
        if reminder is None:
            return
        try:
            payload = reminder.tick()
        except Exception:
            _LOGGER.debug("rest_reminder_tick_failed", exc_info=True)
            payload = None
        try:
            self.ports.publish_rest_reminder(reminder.desktop_bubble_payload())
        except Exception:
            _LOGGER.debug(
                "rest_reminder_desktop_bubble_update_failed",
                exc_info=True,
            )
        if payload is not None:
            self._publish(
                "rest_reminder_due",
                source="rest_reminder",
                context=dict(payload),
            )

    def _publish_update_state_change(self, update_state: dict[str, object]) -> None:
        signature = self.ports.update_state_signature(update_state)
        if self.state.latest_update_state_signature is None:
            self.state.latest_update_state_signature = signature
            self.state.latest_update_state = dict(update_state)
            return
        previous_signature = self.state.latest_update_state_signature
        if signature == previous_signature:
            return
        previous = dict(self.state.latest_update_state or {})
        self._publish(
            "update_state_changed",
            source="update_manager",
            context={"previous": previous, "current": dict(update_state)},
        )
        self.state.latest_update_state_signature = signature
        self.state.latest_update_state = dict(update_state)

    def _publish_budget_window_change(self) -> tuple[str, str]:
        current = self.ports.budget_window_keys()
        previous = self.state.latest_budget_window_keys
        if previous is not None and current != previous:
            previous_day, previous_week = previous
            current_day, current_week = current
            self._publish(
                "budget_window_changed",
                source="budget_window",
                context={
                    "previousDay": previous_day,
                    "previousWeek": previous_week,
                    "currentDay": current_day,
                    "currentWeek": current_week,
                },
            )
        self.state.latest_budget_window_keys = current
        return current

    def _rematerialize_mapping(self) -> None:
        self.ports.invalidate_mapping()
        tracker = self.ports.tracker()
        rematerialize = getattr(tracker, "rematerialize_renderer_mapping", None)
        if not callable(rematerialize):
            return
        try:
            if rematerialize(force=True):
                self.state.activity_wake_pending = ""
                setter = getattr(self.ports.bridge_wake_event, "set", None)
                if callable(setter):
                    setter()
        except Exception as exc:
            _LOGGER.debug("renderer_mapping_rematerialize_failed error=%s", exc)

    def _publish(
        self,
        event_type: str,
        *,
        source: str,
        context: dict[str, object],
    ) -> None:
        if callable(self.ports.publish_event):
            self.ports.publish_event(
                event_type,
                source=source,
                session=self.ports.current_session(),
                context=context,
            )

    @staticmethod
    def _take_event_flag(event: object) -> bool:
        is_set = getattr(event, "is_set", None)
        wake = bool(callable(is_set) and is_set())
        if wake:
            clear = getattr(event, "clear", None)
            if callable(clear):
                clear()
        return wake


@dataclass(frozen=True, slots=True)
class RendererRefreshPorts:
    monotonic: Callable[[], float]
    wall_time: Callable[[], float]
    perf_counter: Callable[[], float]
    budget_signature: Callable[[], tuple[object, ...]]
    build_snapshot: Callable[[dict[str, object]], ParsedSession]
    selection_is_stale: Callable[[ParsedSession], bool]
    current_selection_seq: Callable[[], int]
    refresh_current_work: Callable[
        [list[object], ParsedSession],
        list[object],
    ]
    request_active_work: Callable[[ParsedSession], bool]
    update_snapshot_activity: Callable[[ParsedSession], None]
    push_lightweight: Callable[[ParsedSession], bool]
    push_full: Callable[[ParsedSession, RendererTickInputs], bool]
    build_domain_payload: Callable[
        [ParsedSession, RendererTickInputs],
        dict[str, object],
    ]
    push_domain_payload: Callable[[dict[str, object]], bool]
    publish_overlay: Callable[[ParsedSession], None]
    update_metrics: Callable[[], dict[str, object]]
    connection_success: Callable[[], None]
    connection_failure: Callable[[], None]
    sync_connection: Callable[[ParsedSession], None]
    wake: Callable[[], None]
    background_response_pending: Callable[[dict[str, object]], bool]
    reset_background_retry: Callable[[], None]
    schedule_background_retry: Callable[[], None]
    resolve_update_failure: Callable[[], None]
    record_update_failure: Callable[[int], None]
    client_status: Callable[[], str]
    client_error: Callable[[], str]
    path_key: Callable[[Path | None], str]
    active_work_rescan_seconds: float
    active_work_after_session_seconds: float
    slow_operation_ms: float
    capture_active_session_observation: Callable[[], object | None] = lambda: None
    acknowledge_active_session_update: Callable[
        [int, object | None, bool], None
    ] = (
        lambda _selection_seq, _observation_key, _succeeded: None
    )
    retry_active_session_update: Callable[[], None] = lambda: None


class RendererRefreshExecutor:
    """Build, validate, publish, and account for one snapshot refresh."""

    def __init__(
        self,
        state: RendererLoopState,
        ports: RendererRefreshPorts,
    ) -> None:
        self.state = state
        self.ports = ports

    def apply(
        self,
        inputs: RendererTickInputs,
        force_fast: bool,
    ) -> ParsedSession:
        del force_fast
        refresh_started = self.ports.perf_counter()
        latest = self.state.latest_snapshot
        active_session_observation_key = (
            self.ports.capture_active_session_observation()
        )
        budget_signature = self.ports.budget_signature()
        decision = snapshot_refresh_decision(
            inputs,
            latest_snapshot=latest,
            latest_budget_signature=self.state.latest_budget_signature,
            budget_signature=budget_signature,
            latest_active_work_refresh_at=self.state.latest_active_work_refresh_at,
            now_monotonic=self.ports.monotonic(),
            active_work_refresh_pending=self.state.active_work_refresh_pending,
            active_work_refresh_not_before=self.state.active_work_refresh_not_before,
            active_work_rescan_seconds=self.ports.active_work_rescan_seconds,
            has_settings_command_status=bool(self.state.settings_command_status),
            path_key=self.ports.path_key,
        )
        snapshot_started = self.ports.perf_counter()
        snapshot_started_at_ms = int(self.ports.wall_time() * 1000)
        fresh = self.ports.build_snapshot(dict(decision.snapshot_kwargs))
        snapshot_built_at_ms = int(self.ports.wall_time() * 1000)
        fresh.follow_timing = {
            **dict(fresh.follow_timing or {}),
            "snapshotStartedAt": snapshot_started_at_ms,
            "snapshotBuiltAt": snapshot_built_at_ms,
        }
        snapshot_ms = (self.ports.perf_counter() - snapshot_started) * 1000.0
        if latest is not None and self.ports.selection_is_stale(fresh):
            _LOGGER.info(
                "renderer_refresh_discarded reason=stale_selection fresh_seq=%s current_seq=%s",
                fresh.selection_seq,
                self.ports.current_selection_seq(),
            )
            self.ports.acknowledge_active_session_update(
                fresh.selection_seq,
                active_session_observation_key,
                False,
            )
            self.ports.retry_active_session_update()
            return latest
        self._merge_active_work(fresh, latest, decision)
        self._reset_request_rows_page_for_session(fresh)
        self.ports.update_snapshot_activity(fresh)
        self.state.latest_snapshot = fresh
        self.state.latest_budget_signature = self.ports.budget_signature()
        fresh.follow_timing["payloadSendStartedAt"] = int(
            self.ports.wall_time() * 1000
        )
        update_started = self.ports.perf_counter()
        update_ok = (
            self.ports.push_lightweight(fresh)
            if decision.lightweight_active_session
            else self.ports.push_full(fresh, inputs)
        )
        update_ms = (self.ports.perf_counter() - update_started) * 1000.0
        self.ports.acknowledge_active_session_update(
            fresh.selection_seq,
            active_session_observation_key,
            update_ok,
        )
        if not decision.lightweight_active_session:
            self.ports.publish_overlay(fresh)
        self._log_slow_refresh(
            fresh,
            refresh_started=refresh_started,
            snapshot_ms=snapshot_ms,
            update_ms=update_ms,
        )
        if update_ok:
            self._record_success(fresh, decision)
        else:
            self._record_failure()
        return fresh

    def _reset_request_rows_page_for_session(self, snapshot: ParsedSession) -> None:
        session_id = str(
            snapshot.renderer_session_id or snapshot.session_id or ""
        ).strip()
        if not session_id or session_id == self.state.request_rows_session_id:
            return
        self.state.request_rows_session_id = session_id
        self.state.request_rows_limit = 30

    def apply_domains(self, inputs: RendererTickInputs) -> bool:
        snapshot = self.state.latest_snapshot
        domains = inputs.event_refresh_request.domains
        if snapshot is None or not domains:
            return True
        payload = self.ports.build_domain_payload(snapshot, inputs)
        if not payload:
            return True
        if self.ports.push_domain_payload(payload):
            if "backgroundUsage" in domains and (
                self.state.settings_command_status.get(
                    "backgroundUsageOpenEventId"
                )
                or self.state.settings_command_status.get(
                    "backgroundUsageResponse"
                )
            ):
                self.ports.reset_background_retry()
                self.state.settings_command_status = {}
            self.state.failures = 0
            self.ports.connection_success()
            self.ports.sync_connection(snapshot)
            self.ports.resolve_update_failure()
            return True
        self.state.failures += 1
        self.ports.connection_failure()
        self.ports.schedule_background_retry()
        self.ports.record_update_failure(self.state.failures)
        _LOGGER.info(
            "renderer_hud_domain_update_failed failures=%s status=%s error=%s domains=%s",
            self.state.failures,
            self.ports.client_status(),
            self.ports.client_error(),
            sorted(domains),
        )
        return False

    def _merge_active_work(
        self,
        fresh: ParsedSession,
        latest: ParsedSession | None,
        decision: SnapshotRefreshDecision,
    ) -> None:
        if latest is not None:
            fresh.active_work_items = list(latest.active_work_items)
            if not decision.lightweight_active_session:
                fresh.active_work_items = self.ports.refresh_current_work(
                    list(fresh.active_work_items),
                    fresh,
                )
        if decision.refresh_active_work_items and latest is not None:
            self.ports.request_active_work(fresh)
        if decision.refresh_active_work_items:
            self.state.latest_active_work_refresh_at = self.ports.monotonic()
            self.state.active_work_refresh_pending = False
            self.state.active_work_refresh_not_before = 0.0

    def _record_success(
        self,
        fresh: ParsedSession,
        decision: SnapshotRefreshDecision,
    ) -> None:
        self.ports.connection_success()
        self.ports.sync_connection(fresh)
        if decision.lightweight_active_session:
            self.state.active_work_refresh_pending = True
            self.state.active_work_refresh_not_before = (
                self.ports.monotonic()
                + self.ports.active_work_after_session_seconds
            )
            self.ports.wake()
        if self.ports.background_response_pending(
            self.state.settings_command_status
        ):
            self.ports.reset_background_retry()
        self.state.settings_command_status = {}
        self.state.failures = 0
        self.ports.resolve_update_failure()

    def _record_failure(self) -> None:
        self.state.failures += 1
        self.ports.connection_failure()
        self.ports.schedule_background_retry()
        self.ports.record_update_failure(self.state.failures)
        _LOGGER.info(
            "renderer_hud_update_failed failures=%s status=%s error=%s",
            self.state.failures,
            self.ports.client_status(),
            self.ports.client_error(),
        )

    def _log_slow_refresh(
        self,
        fresh: ParsedSession,
        *,
        refresh_started: float,
        snapshot_ms: float,
        update_ms: float,
    ) -> None:
        refresh_ms = (self.ports.perf_counter() - refresh_started) * 1000.0
        if refresh_ms < self.ports.slow_operation_ms:
            return
        metrics = self.ports.update_metrics()
        attribution = (
            "python_snapshot"
            if snapshot_ms >= update_ms
            else str(metrics.get("attribution") or "hud_or_cdp")
        )
        _LOGGER.info(
            "renderer_refresh_timing attribution=%s total_ms=%.1f snapshot_ms=%.1f hud_update_ms=%.1f target_ms=%s transport=%s cdp_ms=%s persistent_ms=%s fallback_ms=%s fallback_reason=%s renderer_apply_ms=%s source=%s",
            attribution,
            refresh_ms,
            snapshot_ms,
            update_ms,
            metrics.get("targetDiscoveryMs", "-"),
            metrics.get("transport", "-"),
            metrics.get("cdpMs", "-"),
            metrics.get("persistentMs", "-"),
            metrics.get("fallbackMs", "-"),
            metrics.get("persistentFallbackReason", "-") or "-",
            metrics.get("rendererApplyMs", "-"),
            fresh.selection_source,
        )


class RendererEventLoop:
    """Sequence one renderer session through explicit, injectable ports."""

    def __init__(
        self,
        state: RendererLoopState,
        ports: RendererLoopExecutorPorts,
    ) -> None:
        self.state = state
        self.ports = ports

    def run(self) -> int:
        while True:
            daemon_result = self.ports.daemon_tick()
            if daemon_result is not None:
                return daemon_result
            inputs = self.ports.sample_inputs()
            self.ports.apply_inputs(inputs)
            if self.ports.exit_requested():
                return 0
            if self.ports.restart_requested():
                return self.ports.restart_result()
            force_fast = self.ports.compute_force_fast(inputs)
            plan = inputs.event_refresh_request
            if self.state.pending_refresh_plan.has_work:
                pending = self.state.pending_refresh_plan
                self.ports.record_refresh_merge()
                pending.merge(plan)
                plan = pending
                self.state.pending_refresh_plan = RefreshPlan()
                inputs.event_refresh_request = plan
            snapshot_requested = bool(
                plan.snapshot
                or self.state.latest_snapshot is None
                or self.state.soft_reinstall_pending
            )
            if snapshot_requested and not plan.snapshot:
                plan.request_snapshot(force_fast=force_fast)
            allowed, _reason, remaining = self.ports.update_gate()
            if not allowed:
                if plan.has_work:
                    self.state.pending_refresh_plan.merge(plan)
                self.state.pending_retry_not_before = max(
                    self.state.pending_retry_not_before,
                    inputs.started + max(0.05, float(remaining or 0.0)),
                )
                snapshot = self.ports.current_snapshot()
            elif snapshot_requested:
                snapshot = self.ports.apply_refresh(inputs, force_fast)
                if self.state.failures:
                    self.state.pending_refresh_plan.merge(plan)
                else:
                    self.state.pending_retry_not_before = 0.0
                    if self.state.soft_reinstall_pending:
                        self.state.soft_reinstall_pending = False
            else:
                snapshot = self.ports.current_snapshot()
                domain_ok = self.ports.apply_domain_update(inputs)
                if domain_ok:
                    self.state.pending_retry_not_before = 0.0
                elif plan.has_work and self._should_retain_failed_plan(plan):
                    self.state.pending_refresh_plan.merge(plan)
                self.ports.keep_alive()
            allowed_after, _reason_after, remaining_after = self.ports.update_gate()
            if not allowed_after:
                self.state.pending_retry_not_before = max(
                    self.state.pending_retry_not_before,
                    inputs.started + max(0.05, float(remaining_after or 0.0)),
                )
            self.ports.after_iteration(snapshot)
            delay = self.ports.compute_wait_delay(snapshot, inputs, force_fast)
            self.ports.wait(delay)

    def _should_retain_failed_plan(self, plan: RefreshPlan) -> bool:
        # Background-usage responses have their own bounded retry budget.
        # When that budget is exhausted, do not requeue the same response
        # through the generic CDP pending-plan path.
        if not (plan.background_usage or "backgroundUsage" in plan.domains):
            return True
        non_background_domains = plan.domains - {"backgroundUsage"}
        return bool(
            non_background_domains
            or self.state.background_usage_response_retry_attempts > 0,
        )


@dataclass(frozen=True, slots=True)
class SnapshotRefreshDecision:
    refresh_budget_aggregate: bool
    refresh_budget_paths: tuple[Path, ...]
    refresh_active_work_items: bool
    lightweight_active_session: bool
    hydrated_session: bool
    snapshot_kwargs: dict[str, object]


def snapshot_refresh_decision(
    inputs: RendererTickInputs,
    *,
    latest_snapshot: ParsedSession | None,
    latest_budget_signature: tuple[object, ...] | None,
    budget_signature: tuple[object, ...],
    latest_active_work_refresh_at: float,
    active_work_refresh_pending: bool,
    active_work_refresh_not_before: float,
    now_monotonic: float,
    active_work_rescan_seconds: float,
    has_settings_command_status: bool,
    path_key: Callable[[Path | None], str],
) -> SnapshotRefreshDecision:
    has_latest_snapshot = latest_snapshot is not None
    paths = tuple(dict.fromkeys(Path(path) for path in inputs.file_change_paths))
    incremental_paths = (
        tuple(sorted(paths, key=path_key))
        if paths and all(path.suffix.lower() == ".jsonl" for path in paths)
        else ()
    )
    refresh_budget_aggregate = runtime_policies.should_refresh_budget_aggregate(
        has_snapshot=has_latest_snapshot,
        signature_changed=budget_signature != latest_budget_signature,
        file_change_reasons=inputs.file_change_reasons,
        has_incremental_jsonl_paths=bool(incremental_paths),
    )
    refresh_budget_paths = () if refresh_budget_aggregate else incremental_paths
    pending_due = bool(
        active_work_refresh_pending
        and now_monotonic >= active_work_refresh_not_before
    )
    refresh_active_work_items = runtime_policies.should_refresh_active_work_items(
        has_snapshot=has_latest_snapshot,
        latest_refresh_at=latest_active_work_refresh_at,
        now_monotonic=now_monotonic,
        refresh_pending=pending_due,
        file_change_reasons=inputs.file_change_reasons,
        file_change_paths=inputs.file_change_paths,
        rescan_seconds=active_work_rescan_seconds,
    )
    lightweight = runtime_policies.should_use_visible_first_active_session(
        active_session_requested=bool(
            inputs.active_session_wakeup
            or inputs.event_refresh_request.active_session
        ),
        has_snapshot=has_latest_snapshot,
        has_command=bool(inputs.command),
        has_settings_command_status=has_settings_command_status,
        update_phase=str(inputs.update_state.get("phase") or ""),
    )
    if lightweight:
        refresh_budget_aggregate = False
        refresh_budget_paths = ()
        refresh_active_work_items = False
    elif active_work_refresh_pending and not pending_due:
        refresh_active_work_items = False
    hydrated = any(
        str(getattr(event, "type", "") or "") == "session_snapshot_hydrated"
        for event in inputs.runtime_events
    )
    snapshot_kwargs: dict[str, object] = {
        "refresh_budget_aggregate": refresh_budget_aggregate,
        "refresh_budget_paths": refresh_budget_paths,
        "refresh_active_work_items": bool(
            refresh_active_work_items and not has_latest_snapshot
        ),
    }
    if lightweight:
        snapshot_kwargs["reuse_budget_from"] = latest_snapshot
        snapshot_kwargs["refresh_visible_app_error"] = False
    if lightweight or hydrated:
        snapshot_kwargs["refresh_current_session_usage"] = False
    return SnapshotRefreshDecision(
        refresh_budget_aggregate=refresh_budget_aggregate,
        refresh_budget_paths=refresh_budget_paths,
        refresh_active_work_items=refresh_active_work_items,
        lightweight_active_session=lightweight,
        hydrated_session=hydrated,
        snapshot_kwargs=snapshot_kwargs,
    )


__all__ = [
    "NormalizedEventBatch",
    "RefreshPlan",
    "RendererLoopState",
    "RendererRefreshExecutor",
    "RendererRefreshPorts",
    "RendererEventLoop",
    "RendererLoopExecutorPorts",
    "RendererTickInputs",
    "RendererTickSampler",
    "RendererTickSamplerPorts",
    "RendererWaitPlanner",
    "RendererWaitPorts",
    "ScheduledDeadlines",
    "SnapshotRefreshDecision",
    "event_bus_timestamp",
    "normalize_runtime_events",
    "reduce_event",
    "reduce_events",
    "scheduled_wait_delay",
    "snapshot_refresh_decision",
]
