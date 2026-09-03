"""Tests for the progressive session-index warm job (PRD §13/§14).

Covers the range bucketing helpers, persisted state round-trips, the warm-job
state machine (pause / resume / restart / incremental extend / coverage
honesty), the HTTP fallback bridge, and the progress throttle bound.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.config import UserConfigStore
from codex_usage_hud.core.session_index_job import (
    SessionIndexWarmJob,
    WarmJobSnapshot,
    WarmJobState,
    range_candidates,
    range_label,
)
from codex_usage_hud.core.session_search import (
    DEFAULT_RANGE,
    RANGE_OPTIONS,
    range_days,
)
from codex_usage_hud.settings_bridge import SettingsBridgeServer


def _wait_until(predicate, timeout: float = 8.0, interval: float = 0.01) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _make_entries(
    tmp_path: Path,
    ages_days: dict[str, float],
) -> list[tuple[str, tuple[Path, ...], str, str, str, str]]:
    """Build candidate tuples whose rollout mtime encodes ``ages_days``."""
    now = time.time()
    entries: list[tuple[str, tuple[Path, ...], str, str, str, str]] = []
    for session_id, age_days in ages_days.items():
        path = tmp_path / f"{session_id}.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        stamp = now - max(0.0, float(age_days)) * 86_400.0
        os.utime(path, (stamp, stamp))
        entries.append(
            (session_id, (path,), f"title-{session_id}", "/cwd", "provider", "cli")
        )
    return entries


class _FakeSearchIndex:
    """Minimal SessionSearchIndex surface used by the warm job."""

    def __init__(
        self,
        entries: list[tuple[str, tuple[Path, ...], str, str, str, str]],
        *,
        delay_per_entry: float = 0.0,
        load_side_effect=None,
    ) -> None:
        self._entries = list(entries)
        self._committed: list[str] = []
        self._delay = delay_per_entry
        self.sync_calls: list[list[str]] = []
        self.load_calls = 0
        self._load_side_effect = load_side_effect

    def search_index_entries(self):
        return list(self._entries)

    def indexed_session_ids(self) -> frozenset[str]:
        return frozenset(self._committed)

    def load(self) -> dict[str, object]:
        self.load_calls += 1
        if self._load_side_effect is not None:
            if isinstance(self._load_side_effect, Exception):
                raise self._load_side_effect
            self._load_side_effect()
        return {"indexed": len(self._committed), "indexAvailable": True}

    def sync_batches(
        self,
        entries,
        *,
        total: int | None = None,
        batch_size: int = 24,
        progress_callback=None,
        cancelled=None,
        write_snapshot: bool = True,
    ) -> int:
        del batch_size, write_snapshot
        ids = [str(entry[0]).strip() for entry in entries]
        self.sync_calls.append(ids)
        processed = 0
        for entry in entries:
            if callable(cancelled) and cancelled():
                break
            if self._delay:
                time.sleep(self._delay)
            if callable(cancelled) and cancelled():
                break
            self._committed.append(str(entry[0]).strip())
            processed += 1
            if callable(progress_callback):
                progress_callback(
                    processed,
                    max(0, int(total or 0)),
                    len(self._committed),
                )
        return processed


# ---------------------------------------------------------------------------
# range helpers
# ---------------------------------------------------------------------------


def test_range_helpers_map_options_and_fallbacks() -> None:
    assert RANGE_OPTIONS == ("1m", "3m", "6m", "1y", "all")
    assert DEFAULT_RANGE == "1m"
    assert range_days("1m") == 30
    assert range_days("3m") == 91
    assert range_days("6m") == 183
    assert range_days("1y") == 365
    assert range_days("all") is None
    assert range_days("bogus") == 30
    assert range_label("1m") == "最近 1 个月"
    assert range_label("ALL") == "全部"
    assert range_label("bogus") == "最近 1 个月"


def test_range_candidates_newest_first_mtime_window_and_delta(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"newest": 0.1, "recent": 3, "oldish": 300})
    month = range_candidates(list(entries), "1m")
    assert [entry[0] for entry in month] == ["newest", "recent"]
    quarter = range_candidates(list(entries), "3m")
    assert {entry[0] for entry in quarter} == {"newest", "recent"}
    delta = range_candidates(
        list(entries),
        "1m",
        covered_ids=frozenset({"newest"}),
    )
    assert [entry[0] for entry in delta] == ["recent"]


# ---------------------------------------------------------------------------
# persisted state
# ---------------------------------------------------------------------------


def test_warm_job_state_roundtrip_and_corruption(tmp_path: Path) -> None:
    path = tmp_path / "warm.json"
    state = WarmJobState(
        selected_range="6m",
        completed_range="1m",
        coverage_boundary=123.5,
        job_state="paused",
        cursor="7",
        built_count=7,
        total_count=9,
        started_at=1.0,
        updated_at=2.0,
        last_error="",
    )
    state.dump(path)
    assert WarmJobState.load(path) == state
    path.write_text("{broken json", encoding="utf-8")
    assert WarmJobState.load(path) == WarmJobState()
    assert WarmJobState.load(tmp_path / "missing.json") == WarmJobState()


# ---------------------------------------------------------------------------
# warm job state machine
# ---------------------------------------------------------------------------


def test_warm_job_builds_default_range_and_finishes(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 5, "b": 10, "c": 20})
    index = _FakeSearchIndex(entries)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        status = job.status()
        assert status["coverage"] == "range_done(1m)"
        assert status["builtCount"] == 3
        assert status["canExtend"] is True
        assert sorted(index.indexed_session_ids()) == ["a", "b", "c"]
    finally:
        job.close()


def test_warm_job_start_announces_scanning_phase_immediately(tmp_path: Path) -> None:
    # The UI must see an explicit "scanning" phase the instant start() is
    # called, before the first real progress frame, so the silent enumeration
    # window never looks frozen (UX regression guard).
    entries = _make_entries(tmp_path, {"a": 5, "b": 10})
    index = _FakeSearchIndex(entries)
    seen_phases: list[str] = []
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        progress_callback=lambda s: seen_phases.append(s.phase),
    )
    try:
        # start() force-publishes under the same lock, so the callback fires
        # synchronously before start() returns.
        assert job.start("1m") is True
        assert job.status()["phase"] == "scanning"
        assert "scanning" in seen_phases
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["phase"] == ""
    finally:
        job.close()


def test_warm_job_extend_announces_scanning_phase_immediately(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 5, "b": 10, "c": 20, "d": 200})
    index = _FakeSearchIndex(entries)
    seen_phases: list[str] = []
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        progress_callback=lambda s: seen_phases.append(s.phase),
    )
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        # Extend to a wider range: the live channel must flip to scanning at
        # once, not stay on the completed idle state.
        assert job.extend("3m") is True
        assert job.status()["phase"] == "scanning"
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["phase"] == ""
    finally:
        job.close()


def test_warm_job_phase_transitions_scanning_to_indexing(tmp_path: Path) -> None:
    # With a perceptible per-entry delay the streaming "indexing" phase must be
    # observed between the initial "scanning" and the terminal "".
    entries = _make_entries(tmp_path, {f"s{i}": i for i in range(6)})
    index = _FakeSearchIndex(entries, delay_per_entry=0.12)
    seen_phases: list[str] = []
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        progress_callback=lambda s: seen_phases.append(s.phase),
    )
    try:
        assert job.start("1m") is True
        assert "scanning" in seen_phases
        assert _wait_until(lambda: job.status()["phase"] == "indexing", timeout=6)
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["phase"] == ""
        assert "indexing" in seen_phases
    finally:
        job.close()


def test_warm_job_preloads_resident_snapshot_after_finish(tmp_path: Path) -> None:
    # The warm job must pull the resident-snapshot deserialisation off the
    # interactive path by calling ``load()`` in the background worker once the
    # range is done (PRD §14.1 "seconds to first result").
    entries = _make_entries(tmp_path, {"a": 5, "b": 10, "c": 20})
    index = _FakeSearchIndex(entries)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert _wait_until(lambda: index.load_calls >= 1)
        assert job.status()["coverage"] == "range_done(1m)"
    finally:
        job.close()


def test_warm_job_preload_failure_never_fails_the_job(tmp_path: Path) -> None:
    # A failing ``load()`` (e.g. corrupt snapshot) is best-effort: it must not
    # flip the job into ``error`` or lose the completed coverage (PRD §12).
    entries = _make_entries(tmp_path, {"a": 5})
    index = _FakeSearchIndex(entries, load_side_effect=RuntimeError("boom"))
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert _wait_until(lambda: index.load_calls >= 1)
        status = job.status()
        assert status["coverage"] == "range_done(1m)"
        assert status["jobState"] == "idle"
        assert status["error"] == ""
    finally:
        job.close()


def test_warm_job_skips_preload_without_load_capability(tmp_path: Path) -> None:
    # A surface without ``load`` (e.g. a partial manager that only exposes
    # search_index_entries/sync_batches) must not crash the warm job.
    class NoLoadIndex:
        def __init__(self, entries: list) -> None:
            self._entries = list(entries)
            self._committed: list[str] = []

        def search_index_entries(self):
            return list(self._entries)

        def indexed_session_ids(self) -> frozenset[str]:
            return frozenset(self._committed)

        def sync_batches(self, entries, *, total=None, batch_size=24,
                         progress_callback=None, cancelled=None,
                         write_snapshot: bool = True) -> int:
            del total, batch_size, progress_callback, cancelled, write_snapshot
            self._committed.extend(str(e[0]) for e in entries)
            return len(entries)

    entries = _make_entries(tmp_path, {"a": 5})
    index = NoLoadIndex(entries)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["coverage"] == "range_done(1m)"
    finally:
        job.close()


def test_pause_preserves_batches_and_resume_completes(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"s{i}": 1 + i * 2 for i in range(10)})
    index = _FakeSearchIndex(entries, delay_per_entry=0.02)
    state_path = tmp_path / "warm.json"
    job = SessionIndexWarmJob(index, state_path=state_path)
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: int(job.status()["builtCount"]) >= 3)
        assert job.pause() is True
        assert _wait_until(lambda: job.status()["jobState"] == "paused")
        paused = job.status()
        built = int(paused["builtCount"])
        assert 0 < built < 10
        persisted = WarmJobState.load(state_path)
        assert persisted.job_state == "paused"
        assert persisted.cursor == str(built)
        assert job.resume() is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["coverage"] == "range_done(1m)"
        assert len(index.indexed_session_ids()) == 10
    finally:
        job.close()


def test_restart_resumes_from_persisted_paused_state(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"r{i}": 1 + i for i in range(8)})
    state_path = tmp_path / "warm.json"
    persisted = WarmJobState(
        selected_range="1m",
        job_state="paused",
        cursor="3",
        built_count=3,
        total_count=8,
        started_at=time.time() - 10,
        updated_at=time.time() - 9,
    )
    persisted.dump(state_path)
    index = _FakeSearchIndex(entries, delay_per_entry=0.01)
    # The real database keeps the first three rows across a restart.
    index._committed = ["r0", "r1", "r2"]
    job = SessionIndexWarmJob(index, state_path=state_path)
    try:
        assert WarmJobState.load(state_path).job_state == "paused"
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["coverage"] == "range_done(1m)"
        assert len(index.indexed_session_ids()) == 8
        assert index.sync_calls == [["r3", "r4", "r5", "r6", "r7"]]
    finally:
        job.close()


def test_extend_appends_only_older_sessions(tmp_path: Path) -> None:
    entries = _make_entries(
        tmp_path,
        {
            "new-a": 5,
            "new-b": 10,
            "mid-c": 45,
            "mid-d": 60,
            "old-e": 200,
            "old-f": 300,
        },
    )
    index = _FakeSearchIndex(entries)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["coverage"] == "range_done(1m)"
        assert sorted(index.indexed_session_ids()) == ["new-a", "new-b"]
        assert index.sync_calls == [["new-a", "new-b"]]

        assert job.extend("3m") is True
        assert _wait_until(
            lambda: job.status()["jobState"] == "idle"
            and job.status()["coverage"] == "range_done(3m)"
        )
        assert sorted(index.indexed_session_ids()) == ["mid-c", "mid-d", "new-a", "new-b"]
        assert index.sync_calls == [["new-a", "new-b"], ["mid-c", "mid-d"]]

        assert job.extend("all") is True
        assert _wait_until(
            lambda: job.status()["jobState"] == "idle"
            and job.status()["coverage"] == "full"
        )
        assert sorted(index.indexed_session_ids()) == [
            "mid-c",
            "mid-d",
            "new-a",
            "new-b",
            "old-e",
            "old-f",
        ]
    finally:
        job.close()


def test_extend_while_running_never_claims_unbuilt_range(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 5, "b": 10, "c": 45, "d": 60})
    index = _FakeSearchIndex(entries, delay_per_entry=0.03)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: int(job.status()["builtCount"]) >= 1)
        assert job.extend("3m") is True
        assert job.status()["selectedRange"] == "3m"

        seen_range_done_while_busy = False
        saw_honest_partial = False
        deadline = time.time() + 8.0
        while True:
            status = job.status()
            if status["jobState"] != "idle" and status["coverage"] == "range_done(3m)":
                seen_range_done_while_busy = True
            if status["coverage"] == "partial(3m)":
                saw_honest_partial = True
            if (
                status["jobState"] == "idle"
                and status["coverage"] == "range_done(3m)"
            ):
                break
            if time.time() > deadline:
                break
            time.sleep(0.01)
        assert not seen_range_done_while_busy
        assert saw_honest_partial
        assert sorted(index.indexed_session_ids()) == ["a", "b", "c", "d"]
    finally:
        job.close()


def test_attach_and_cancel_ui_never_stop_the_job(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"t{i}": 1 + i for i in range(12)})
    index = _FakeSearchIndex(entries, delay_per_entry=0.03)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(
            lambda: job.status()["jobState"] in ("running", "attached")
            and int(job.status()["builtCount"]) >= 1
        )
        assert job.attach() is True
        assert _wait_until(lambda: job.status()["jobState"] == "attached")
        attached = job.status()
        assert 0 < int(attached["builtCount"]) < 12
        assert job.cancel_ui() is True
        assert job.status()["jobState"] == "running"
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert len(index.indexed_session_ids()) == 12
    finally:
        job.close()


def test_control_action_mapping_and_unknown_action(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 1, "b": 2})
    index = _FakeSearchIndex(entries, delay_per_entry=0.02)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        response = job.control({"control": "explode", "requestId": "r1"})
        assert response["accepted"] is False
        assert response["error"] == "unknown_action"
        assert response["requestId"] == "r1"
        assert job.status()["jobState"] == "idle"

        response = job.control({"control": "start", "range": "1m", "requestId": "r2"})
        assert response["accepted"] is True
        assert response["requestId"] == "r2"
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.control({"control": "pause"})["accepted"] is False
        assert job.control({"control": "cancel_ui"})["accepted"] is True
    finally:
        job.close()


def test_progress_publish_throttled_to_four_hz(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"p{i}": 1 + i for i in range(10)})
    index = _FakeSearchIndex(entries, delay_per_entry=0.05)
    events: list[tuple[float, WarmJobSnapshot]] = []

    def record(snapshot: WarmJobSnapshot) -> None:
        events.append((time.monotonic(), snapshot))

    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        progress_callback=record,
    )
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        final_status = job.status()
    finally:
        job.close()

    assert len(events) >= 2
    assert len(events) <= 6
    timestamps = [stamp for stamp, _snapshot in events]
    gaps = [
        timestamps[i + 1] - timestamps[i]
        for i in range(len(timestamps) - 1)
    ]
    # Terminal idle/error transitions are deliberately delivered immediately
    # so a fast job cannot leave the attached UI stuck in "indexing". Only
    # non-terminal progress notifications are subject to the 4 Hz throttle.
    assert all(gap >= 0.20 for gap in gaps[:-1])
    counts = [snapshot.built_count for _stamp, snapshot in events]
    assert counts == sorted(counts)
    assert final_status["jobState"] == "idle"
    assert final_status["coverage"] == "range_done(1m)"


def test_extend_publishes_range_change_before_worker_bucket(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"e{i}": 1 + i for i in range(6)})
    index = _FakeSearchIndex(entries)
    events: list[WarmJobSnapshot] = []
    job = SessionIndexWarmJob(
        index,
        state_path=tmp_path / "warm.json",
        progress_callback=events.append,
    )
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        events.clear()
        assert job.extend("3m") is True
        # The live sessionIndex channel must report the new range on the frame
        # the extend is accepted. Without this publish, a renderer push landing
        # before the worker's first bucket publish would show "最近1个月" as
        # the current range while a 3m job is already running.
        assert events, "extend() 必须立刻发布一次进度，否则实时通道落后于命令响应"
        assert events[0].selected_range == "3m"
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
    finally:
        job.close()


def test_pause_announces_paused_state_to_attached_ui(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {f"s{i}": 1 + i * 2 for i in range(10)})
    index = _FakeSearchIndex(entries, delay_per_entry=0.02)
    job = SessionIndexWarmJob(index, state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert job.attach() is True
        assert _wait_until(lambda: int(job.status()["builtCount"]) >= 3)
        assert job.pause() is True
        # The ``paused`` transition is published by the worker, and a detached
        # job publishes no runtime event at all. Detaching on pause would
        # therefore leave the panel stuck on "索引中" until an unrelated
        # full refresh happened to clear the command status.
        assert job.is_attached() is True
        assert _wait_until(lambda: job.status()["jobState"] == "paused")
    finally:
        job.close()


def test_attached_progress_callback_exposes_attachment_state(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"a": 1})
    index = _FakeSearchIndex(entries)
    state_path = tmp_path / "warm.json"
    WarmJobState(job_state="running").dump(state_path)
    job = SessionIndexWarmJob(index, state_path=state_path)
    try:
        assert job.is_attached() is False
        assert job.attach() is True
        assert job.is_attached() is True
        assert job.cancel_ui() is True
        assert job.is_attached() is False
    finally:
        job.close()


def test_job_fails_cleanly_when_surface_is_missing(tmp_path: Path) -> None:
    class Broken:
        pass

    job = SessionIndexWarmJob(Broken(), state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "error")
        assert job.status()["error"] == "search_index_entries unavailable"
        assert WarmJobState.load(tmp_path / "warm.json").job_state == "error"
    finally:
        job.close()


def test_job_resolves_capabilities_from_manager_and_core(tmp_path: Path) -> None:
    entries = _make_entries(tmp_path, {"m1": 1, "m2": 2})
    core = _FakeSearchIndex(entries)

    class ManagerLike:
        _search_index = core

        def search_index_entries(self):
            return core.search_index_entries()

    job = SessionIndexWarmJob(ManagerLike(), state_path=tmp_path / "warm.json")
    try:
        assert job.start("1m") is True
        assert _wait_until(lambda: job.status()["jobState"] == "idle")
        assert job.status()["coverage"] == "range_done(1m)"
        assert len(core.indexed_session_ids()) == 2
    finally:
        job.close()


# ---------------------------------------------------------------------------
# HTTP fallback bridge (PRD §9.4)
# ---------------------------------------------------------------------------


def _start_bridge(
    store: UserConfigStore,
    *,
    status=None,
    control=None,
) -> SettingsBridgeServer:
    bridge = SettingsBridgeServer(
        store,
        port=0,
        session_index_status_callback=status,
        session_index_control_callback=control,
    )
    bridge.start()
    return bridge


def test_session_index_http_requires_token_and_maps_payloads(tmp_path: Path) -> None:
    store = UserConfigStore(Path(tmp_path) / "hud_settings.json")
    status = _FakeCallback(
        {
            "jobState": "running",
            "coverage": "partial(1m)",
            "builtCount": 3,
            "totalCount": 9,
            "selectedRange": "1m",
        }
    )
    control = _FakeCallback({"accepted": True, "jobState": "running"})
    bridge = _start_bridge(store, status=status.call, control=control.call)
    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(f"{bridge.url}/session-index/status", timeout=2)
        assert denied.value.code == 403
        assert status.calls == 0

        with urlopen(bridge.session_index_url, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["status"] == "ok"
        assert payload["sessionIndex"]["jobState"] == "running"
        assert status.calls == 1

        control_url = bridge.session_index_url.replace(
            "/session-index/status?",
            "/session-index/control?",
        )
        request = Request(
            control_url,
            data=json.dumps({"control": "extend", "range": "3m"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            control_payload = json.loads(response.read().decode("utf-8"))
        assert control_payload["sessionIndex"]["accepted"] is True
        assert control.calls == 1
        assert control.received["accessTokenCheck"] is True
        assert control.received["control"] == "extend"
    finally:
        bridge.close()


def test_session_index_http_503_without_callbacks(tmp_path: Path) -> None:
    store = UserConfigStore(Path(tmp_path) / "hud_settings.json")
    bridge = _start_bridge(store)
    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(bridge.session_index_url, timeout=2)
        assert denied.value.code == 503
        control_url = bridge.session_index_url.replace(
            "/session-index/status?",
            "/session-index/control?",
        )
        request = Request(
            control_url,
            data=json.dumps({"control": "pause"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as denied_control:
            urlopen(request, timeout=2)
        assert denied_control.value.code == 503
    finally:
        bridge.close()


class _FakeCallback:
    def __init__(self, result: dict[str, object]) -> None:
        self._result = dict(result)
        self.calls = 0
        self.received: dict[str, object] = {}

    def call(self, *args, **kwargs) -> dict[str, object]:
        self.calls += 1
        self.received.update(kwargs)
        if args:
            body = args[0]
            if isinstance(body, dict):
                self.received.update(body)
        return dict(self._result)
