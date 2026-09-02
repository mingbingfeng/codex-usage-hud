"""Progressive warm-up job for the session search index.

Implements the single-task warm-job state machine from the PRD
(``docs/PRD_SESSION_SEARCH_PROGRESSIVE_INDEX.md``):

- ``idle`` -> ``running`` on startup / extend / new session
- ``running`` -> ``attached`` when the session-management dialog subscribes
- ``running`` -> ``paused`` after the current batch commits and the cursor is
  persisted (committed batches stay searchable)
- ``running`` -> ``idle`` on range completion (coverage -> ``range_done``)
- exception -> ``error`` (already-built batches are kept, job is retryable)

The job is a single background worker: at most one warm job may run at any
time; entering session management merely attaches (subscribes) to the same
job's progress events.  State/cursor persistence lives in a small JSON file
next to the index database and is written *after* each committed batch, so the
persisted state never outruns the actually-built index (PRD §8).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from .session_search import (
    DEFAULT_RANGE,
    RANGE_OPTIONS,
    range_candidates,
    range_label,
)

# Throughput baselines measured on a real corpus (PRD §5.3): a full 971-session
# / 1.69 GB build completes in roughly 71.5 s => ~23.6 MB/s.  These are only
# used for the initial estimate; live speed corrects the remaining time.
THROUGHPUT_MBPS = 23.6
THROUGHPUT_SESSIONS_PER_SEC = 13.6

# Progress refresh cadence (PRD §9.3): no more than 4 Hz.
PROGRESS_MAX_HZ = 4.0

# Batch size for the warm job's ``sync_batches`` call.
WARM_BATCH_SIZE = 64

# Max process-pool parallelism while building (resource governance §11).
MAX_BUILD_WORKERS = max(1, min(8, (os.cpu_count() or 4) // 2))


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Persisted state
# ---------------------------------------------------------------------------


@dataclass
class WarmJobState:
    """Persisted, auditable state for one warm job (no private content)."""

    selected_range: str = DEFAULT_RANGE
    completed_range: str = ""
    coverage_boundary: float = 0.0
    job_state: str = "idle"  # idle | running | paused | error
    cursor: str = ""
    built_count: int = 0
    total_count: int = 0
    started_at: float = 0.0
    updated_at: float = 0.0
    last_error: str = ""

    @classmethod
    def load(cls, path: Path) -> WarmJobState:
        try:
            payload = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return cls()
        try:
            data = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        state = cls()
        state.selected_range = str(
            data.get("selected_range", DEFAULT_RANGE)
        ).strip() or DEFAULT_RANGE
        state.completed_range = str(data.get("completed_range") or "").strip()
        try:
            boundary = float(data.get("coverage_boundary") or 0.0)
        except (TypeError, ValueError):
            boundary = 0.0
        state.coverage_boundary = boundary
        state.job_state = str(data.get("job_state") or "idle").strip()
        state.cursor = str(data.get("cursor") or "").strip()
        state.built_count = max(0, int(data.get("built_count") or 0))
        state.total_count = max(0, int(data.get("total_count") or 0))
        try:
            state.started_at = float(data.get("started_at") or 0.0)
            state.updated_at = float(data.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            pass
        state.last_error = str(data.get("last_error") or "").strip()
        return state

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "selected_range": self.selected_range,
            "completed_range": self.completed_range,
            "coverage_boundary": self.coverage_boundary,
            "job_state": self.job_state,
            "cursor": self.cursor,
            "built_count": self.built_count,
            "total_count": self.total_count,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "last_error": self.last_error,
        }
        # Atomic-ish write: temp file then rename so a crash can never leave a
        # half-written state that looks like a completed job.
        temp_path = path.with_name(path.name + ".tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temp_path, path)
        except OSError:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Time budget helpers
# ---------------------------------------------------------------------------


def estimated_remaining_seconds(
    pending_bytes: int | None,
    pending_sessions: int,
    *,
    effective_mbps: float = THROUGHPUT_MBPS,
    effective_sessions_per_sec: float = THROUGHPUT_SESSIONS_PER_SEC,
) -> tuple[float, bool]:
    """Return (seconds, reliable) for one remaining workload.

    ``reliable`` is False when we have no byte estimate (PRD §12: degrade to
    "sessions only", no fake precision).
    """
    seconds = 0.0
    if pending_sessions > 0 and effective_sessions_per_sec > 0:
        seconds = max(seconds, pending_sessions / effective_sessions_per_sec)
    if pending_bytes is not None and pending_bytes > 0 and effective_mbps > 0:
        seconds = max(seconds, pending_bytes / (effective_mbps * 1_048_576.0))
    return round(seconds, 1), pending_bytes is not None and pending_bytes > 0


def estimate_range_bytes(entries: Sequence[object]) -> int | None:
    """Approximate the byte volume of a candidate list for the time budget.

    Returns None when no file size can be read so the caller degrades to
    session-count-only estimates.
    """
    total = 0
    seen = 0
    for entry in entries:
        paths = entry[1] if len(entry) > 1 else ()
        if not isinstance(paths, Sequence):
            continue
        for path in paths:
            try:
                total += path.stat().st_size
                seen += 1
            except (OSError, ValueError):
                continue
    return total if seen else None


# ---------------------------------------------------------------------------
# Warm job
# ---------------------------------------------------------------------------


@dataclass
class WarmJobSnapshot:
    """Immutable point-in-time status of the warm job (safe to publish)."""

    coverage: str = "empty"  # empty | partial(range) | range_done(range) | full
    coverage_boundary: float = 0.0
    job_state: str = "idle"  # idle | running | attached | paused | error
    built_count: int = 0
    total_count: int = 0
    estimated_remaining_sec: float = 0.0
    selected_range: str = DEFAULT_RANGE
    can_extend: bool = False
    error: str = ""


class SessionIndexWarmJob:
    """Single background task that progressively builds the search index.

    Owned by the HUD runtime; never touches the renderer directly.  The
    session-management UI can attach (subscribe) to progress via ``attach``,
    and detach (``cancel_ui``) without stopping the job.
    """

    def __init__(
        self,
        search_index: object,
        *,
        state_path: Path | str,
        clock: Callable[[], float] = time.time,
        progress_callback: Callable[[WarmJobSnapshot], None] | None = None,
    ) -> None:
        self._search_index = search_index
        # The production wiring passes the cleanup manager, which owns the
        # candidate source (``search_index_entries``) and the search database
        # (``_search_index``); keep both so the job can resolve capabilities
        # from either surface (PRD §4.2 / D3 incremental extension).
        self._index_core = getattr(search_index, "_search_index", None)
        self._state_path = Path(state_path)
        self._clock = clock
        self._progress_callback = progress_callback
        self._lock = threading.RLock()
        self._state = WarmJobState.load(self._state_path)
        self._attached = False
        self._requested_pause = threading.Event()
        self._worker: threading.Thread | None = None
        self._closed = False
        self._last_progress_emitted = 0.0
        self._run_range_key: str | None = None

    # ------------------------------------------------------------------
    # status surface
    # ------------------------------------------------------------------

    def status(self) -> dict[str, object]:
        with self._lock:
            return self._snapshot_dict_locked()

    def snapshot(self) -> WarmJobSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_dict_locked(self) -> dict[str, object]:
        snapshot = self._snapshot_locked()
        return {
            "coverage": snapshot.coverage,
            "coverageBoundary": snapshot.coverage_boundary,
            "jobState": snapshot.job_state,
            "builtCount": snapshot.built_count,
            "totalCount": snapshot.total_count,
            "estimatedRemainingSec": snapshot.estimated_remaining_sec,
            "selectedRange": snapshot.selected_range,
            "canExtend": snapshot.can_extend,
            "error": snapshot.error,
        }

    def _snapshot_locked(self) -> WarmJobSnapshot:
        state = self._state
        return WarmJobSnapshot(
            coverage=self._coverage_locked(),
            coverage_boundary=state.coverage_boundary,
            job_state=self._job_state_locked(),
            built_count=state.built_count,
            total_count=state.total_count,
            estimated_remaining_sec=self._estimated_remaining_locked(),
            selected_range=state.selected_range,
            can_extend=state.selected_range != "all",
            error=state.last_error,
        )

    def _job_state_locked(self) -> str:
        state = self._state
        if state.job_state in ("running", "attached"):
            return "attached" if self._attached else "running"
        return state.job_state

    def _coverage_locked(self) -> str:
        state = self._state
        built = state.built_count
        total = state.total_count
        if state.selected_range == "all":
            return (
                "full"
                if built > 0 and built >= total
                else "partial(all)"
            )
        if (
            state.completed_range == state.selected_range
            and built > 0
            and built >= total
        ):
            return f"range_done({state.selected_range})"
        if built > 0 or total > 0:
            return f"partial({state.selected_range})"
        return "empty"

    def _estimated_remaining_locked(self) -> float:
        state = self._state
        if state.job_state not in {"running", "attached", "paused"}:
            return 0.0
        rate = self._effective_sessions_per_sec_locked()
        pending = max(0, state.total_count - state.built_count)
        if pending <= 0 or rate <= 0:
            return 0.0
        return round(pending / rate, 1)

    def _effective_sessions_per_sec_locked(self) -> float:
        state = self._state
        elapsed = max(0.0, self._clock() - state.started_at)
        if elapsed <= 0 or state.built_count <= 0:
            return 0.0
        return max(
            0.0,
            state.built_count / (elapsed * 1.15),
        ) or THROUGHPUT_SESSIONS_PER_SEC

    # ------------------------------------------------------------------
    # control API
    # ------------------------------------------------------------------

    def start(self, range_key: str = DEFAULT_RANGE) -> bool:
        """Begin (or resume) a warm job for ``range_key``.

        Idempotent: a running/attached job just returns True with its state
        intact; a paused job resumes from its cursor; a fresh coverage starts
        a new bucket.  Returns True when the job is running.
        """
        with self._lock:
            if self._closed:
                return False
            key = str(range_key or DEFAULT_RANGE).strip().casefold()
            if key not in RANGE_OPTIONS:
                key = DEFAULT_RANGE
            if self._worker is not None and self._worker.is_alive():
                # Already running/attached: reuse it unchanged.
                self._state.selected_range = key
                self._state.completed_range = ""
                self._state.job_state = "running"
                self._state.updated_at = self._clock()
                self._maybe_persist_locked()
                # A resume arriving while the previous worker is still
                # unwinding a pause must re-arm it (otherwise the pending
                # pause event would stop the next batch and flip back to
                # ``paused`` right after the UI confirmed the resume).
                self._requested_pause.clear()
                return True
            if self._state.job_state == "paused":
                self._state.job_state = "running"
                self._state.updated_at = self._clock()
                self._maybe_persist_locked()
            else:
                self._state.selected_range = key
                self._state.job_state = "running"
                self._state.built_count = 0
                self._state.total_count = 0
                self._state.cursor = ""
                self._state.last_error = ""
                self._state.started_at = self._clock()
                self._state.updated_at = self._clock()
                self._maybe_persist_locked()
            self._requested_pause.clear()
            self._attached = False
            self._spawn_worker()
            return True

    def extend(self, range_key: str) -> bool:
        """Extend the coverage window; only appends older sessions (D3)."""
        with self._lock:
            if self._closed:
                return False
            key = str(range_key or "").strip().casefold()
            if key not in RANGE_OPTIONS:
                return False
            if self._state.job_state in ("running", "attached") and self._worker is not None and self._worker.is_alive():
                if self._state.selected_range == key:
                    return True
                # Range changed while running: let the current pass finish
                # then the next start() re-buckets; update the label now so
                # the UI is honest about the selected target.
                self._state.selected_range = key
                self._state.updated_at = self._clock()
                self._maybe_persist_locked()
                return True
            self._state.selected_range = key
            self._state.job_state = "running"
            self._state.cursor = ""
            self._state.last_error = ""
            self._state.started_at = self._clock()
            self._state.updated_at = self._clock()
            self._maybe_persist_locked()
            if self._worker is not None and self._worker.is_alive():
                return True
            self._spawn_worker()
            return True

    def pause(self) -> bool:
        """Pause the current job; committed batches remain searchable."""
        with self._lock:
            if self._state.job_state not in ("running", "attached"):
                return False
            self._requested_pause.set()
            self._attached = False
            return True

    def resume(self) -> bool:
        return self.start(self._state.selected_range)

    def cancel_ui(self) -> bool:
        """Detach the UI subscription, keep building in the background."""
        with self._lock:
            self._attached = False
            return True

    def attach(self) -> bool:
        """Subscribe the UI; does not create any new task."""
        with self._lock:
            if self._state.job_state in ("running", "attached", "paused"):
                self._attached = True
                return True
            return False

    def detach(self) -> bool:
        return self.cancel_ui()

    def control(self, command: Mapping[str, object]) -> dict[str, object]:
        """Handle one ``sessionIndexControl`` payload (CDP and HTTP share this).

        Returns the same status shape as :meth:`status` plus ``accepted`` and
        ``requestId``, keeping the CDP and HTTP fallback contracts identical
        (PRD §9.4).
        """
        control_action = str(command.get("control") or "").strip().casefold()
        valid = {"start", "extend", "pause", "resume", "cancel_ui"}
        request_id = str(command.get("requestId") or command.get("id") or "")
        if control_action not in valid:
            payload = dict(self.status())
            payload["accepted"] = False
            payload["error"] = "unknown_action"
            payload["requestId"] = request_id
            return payload
        range_key = str(command.get("range") or "").strip().casefold()
        try:
            if control_action == "start":
                accepted = self.start(range_key)
            elif control_action == "extend":
                accepted = self.extend(range_key)
            elif control_action == "pause":
                accepted = self.pause()
            elif control_action == "resume":
                accepted = self.resume()
            else:
                accepted = self.cancel_ui()
        except Exception as exc:
            payload = dict(self.status())
            payload["accepted"] = False
            payload["error"] = str(exc) or type(exc).__name__
            payload["requestId"] = request_id
            return payload
        payload = dict(self.status())
        payload["accepted"] = bool(accepted)
        payload["error"] = ""
        payload["requestId"] = request_id
        return payload

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._requested_pause.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _spawn_worker(self) -> None:
        thread = threading.Thread(
            target=self._run,
            name="session-index-warm-job",
            daemon=True,
        )
        self._worker = thread
        thread.start()

    def _maybe_persist_locked(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._state.dump(self._state_path)
        except OSError:
            pass

    def _publish_locked(self) -> None:
        if not callable(self._progress_callback) or self._closed:
            return
        now = self._clock()
        if now - self._last_progress_emitted < 1.0 / PROGRESS_MAX_HZ:
            return
        self._last_progress_emitted = now
        try:
            self._progress_callback(self._snapshot_locked())
        except Exception:
            pass

    def _capability(self, name: str) -> Callable | None:
        """Resolve one warm-job capability from the job's index surface.

        Candidates live on the owning manager, while batch sync and the
        covered-session set live on the underlying ``SessionSearchIndex``;
        working against the manager directly means both are reachable here.
        """
        fn = getattr(self._search_index, name, None)
        if callable(fn):
            return fn
        core = self._index_core
        if core is not None:
            fn = getattr(core, name, None)
        return fn if callable(fn) else None

    def _should_stop(self) -> bool:
        if self._closed or self._requested_pause.is_set():
            return True
        with self._lock:
            return self._state.job_state not in ("running", "attached")

    def _run(self) -> None:
        try:
            self._run_loop()
        except Exception as exc:
            with self._lock:
                self._state.job_state = "error"
                self._state.last_error = str(exc) or type(exc).__name__
                self._state.updated_at = self._clock()
                self._maybe_persist_locked()
                self._publish_locked()
        finally:
            with self._lock:
                self._worker = None

    def _run_loop(self) -> None:
        """Run coverage passes until the selected range is fully built.

        Each pass buckets the *current* ``selected_range``.  If the range was
        extended while a pass was running, the next pass re-buckets so the
        extension is applied incrementally instead of being silently dropped
        (PRD D3) -- and coverage never claims a larger range than what was
        actually built (PRD §12, no fake completion).
        """
        while not self._closed:
            with self._lock:
                self._run_range_key = self._state.selected_range
            self._run_once()
            with self._lock:
                if self._state.job_state in ("paused", "error"):
                    return
                if (
                    self._state.job_state == "idle"
                    and self._state.selected_range == self._run_range_key
                ):
                    return

    def _run_once(self) -> None:
        entries_fn = self._capability("search_index_entries")
        if not callable(entries_fn):
            self._fail("search_index_entries unavailable")
            return
        candidates = entries_fn()
        if not candidates:
            with self._lock:
                self._finish_range(self._state.selected_range)
            return

        covered_fn = self._capability("indexed_session_ids")
        covered_ids = frozenset(covered_fn()) if callable(covered_fn) else frozenset()
        with self._lock:
            range_key = self._state.selected_range
        pass_range = range_key
        bucket = range_candidates(
            list(candidates),
            range_key,
            covered_ids=covered_ids,
        )
        if not bucket:
            self._finish_range(pass_range)
            return
        total = len(bucket)

        with self._lock:
            self._state.total_count = total
            self._state.built_count = 0
            self._state.started_at = self._clock()
            self._state.updated_at = self._clock()
            self._state.job_state = "running"
            self._maybe_persist_locked()
        self._publish_locked()

        sync_batches = self._capability("sync_batches")
        if not callable(sync_batches):
            self._fail("sync_batches unavailable")
            return

        def report(processed: int, _total: int, _indexed: int) -> None:
            with self._lock:
                self._state.built_count = max(0, int(processed))
                self._state.total_count = max(0, int(_total)) or total
                self._state.updated_at = self._clock()
                self._publish_locked()

        try:
            sync_batches(
                bucket,
                total=total,
                batch_size=WARM_BATCH_SIZE,
                progress_callback=report,
                cancelled=self._should_stop,
            )
        except Exception as exc:
            if self._should_stop():
                pass
            else:
                self._fail(str(exc) or type(exc).__name__)
                return

        if self._should_stop():
            self._persist_paused()
            return
        self._finish_range(pass_range)

    def _persist_paused(self) -> None:
        with self._lock:
            self._state.job_state = "paused"
            self._state.cursor = str(self._state.built_count)
            self._state.updated_at = self._clock()
            self._maybe_persist_locked()
            self._publish_locked()

    def _finish_range(self, pass_range: str) -> None:
        covered_count: int | None = None
        covered_fn = self._capability("indexed_session_ids")
        if callable(covered_fn):
            try:
                covered_count = len(covered_fn())
            except Exception:
                covered_count = None
        with self._lock:
            self._state.job_state = "idle"
            if covered_count is not None and covered_count > 0:
                self._state.built_count = covered_count
                self._state.total_count = max(self._state.total_count, covered_count)
            else:
                self._state.built_count = self._state.total_count
            self._state.completed_range = str(pass_range or "").strip().casefold()
            self._state.cursor = ""
            self._state.coverage_boundary = self._clock()
            self._state.updated_at = self._clock()
            self._maybe_persist_locked()
            self._publish_locked()
        self._warm_memory()

    def _warm_memory(self) -> None:
        """Pre-load the resident search snapshot in the background.

        The resident index is a pickled snapshot (``.memory``, ~200 MB on a
        full corpus) whose first deserialisation costs on the order of 20 s on
        a cold page cache.  Without this step that cost lands on the user's
        first search (PRD §14.1 "seconds to first result").  The warm job is
        already the startup-time background task, so it is the natural place
        to pull that cost off the interactive path: ``load()`` populates the
        in-process ``SessionSearchIndex`` instance that the renderer searches
        against, and its ``_memory_loaded`` flag makes this idempotent across
        the whole process.

        This must never block the worker's state transitions or fail the job:
        a missing ``load`` capability, an already-loaded index, or any error
        is simply skipped (PRD §12: warm-up is best-effort).
        """
        load_fn = self._capability("load")
        if not callable(load_fn):
            return
        try:
            load_fn()
        except Exception:
            # Best-effort only; the indexer already surfaced any real error.
            pass

    def _fail(self, message: str) -> None:
        with self._lock:
            self._state.job_state = "error"
            self._state.last_error = str(message) or "internal error"
            self._state.updated_at = self._clock()
            self._maybe_persist_locked()
            self._publish_locked()


__all__ = [
    "PROGRESS_MAX_HZ",
    "THROUGHPUT_MBPS",
    "THROUGHPUT_SESSIONS_PER_SEC",
    "WARM_BATCH_SIZE",
    "WarmJobSnapshot",
    "WarmJobState",
    "SessionIndexWarmJob",
    "estimate_range_bytes",
    "estimated_remaining_seconds",
    "range_label",
]
