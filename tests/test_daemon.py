"""Unit tests for daemon process matching and state transitions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.daemon import (
    CodexDaemonManager,
    DaemonState,
    ProcessListenerError,
    ProcessSnapshot,
    is_codex_client_process,
)


class _FakeListener:
    def __init__(self, snapshots: list[ProcessSnapshot]) -> None:
        self.snapshots = list(snapshots)
        self.calls = 0

    def snapshot(self) -> ProcessSnapshot:
        self.calls += 1
        if not self.snapshots:
            return ProcessSnapshot(found=False)
        if len(self.snapshots) == 1:
            return self.snapshots[0]
        return self.snapshots.pop(0)


class _FailingListener:
    def snapshot(self) -> ProcessSnapshot:
        raise RuntimeError("process api denied")


class _FakeExitMonitor:
    def __init__(self, states: list[bool | None]) -> None:
        self.states = list(states)
        self.calls = 0
        self.closed = False

    def is_running(self) -> bool | None:
        self.calls += 1
        if not self.states:
            return None
        if len(self.states) == 1:
            return self.states[0]
        return self.states.pop(0)

    def close(self) -> None:
        self.closed = True


class DaemonProcessMatchingTests(unittest.TestCase):
    def test_codex_process_names_are_detected(self) -> None:
        self.assertTrue(is_codex_client_process("Codex.exe"))
        self.assertTrue(is_codex_client_process("codex-client.exe"))
        self.assertTrue(is_codex_client_process("OpenAI Codex.exe"))

    def test_hud_and_python_processes_are_not_detected(self) -> None:
        self.assertFalse(is_codex_client_process("codex-hud.exe"))
        self.assertFalse(is_codex_client_process("codex_usage_hud.exe"))
        self.assertFalse(is_codex_client_process("codex-plus-plus.exe"))
        self.assertFalse(is_codex_client_process("codex-plus-plus-manager.exe"))
        self.assertFalse(is_codex_client_process("codex-computer-use.exe"))
        self.assertFalse(is_codex_client_process("python.exe"))
        self.assertFalse(is_codex_client_process(""))


class DaemonStateMachineTests(unittest.TestCase):
    def test_wait_for_codex_moves_to_running_when_process_appears(self) -> None:
        listener = _FakeListener(
            [
                ProcessSnapshot(found=False),
                ProcessSnapshot(found=True, pids=(123,), names=("Codex.exe",)),
            ]
        )
        manager = CodexDaemonManager(listener=listener, poll_ms=1)

        snapshot = manager.wait_for_codex()

        self.assertTrue(snapshot.found)
        self.assertEqual(snapshot.primary_pid, 123)
        self.assertEqual(manager.state, DaemonState.HUD_RUNNING)
        self.assertGreaterEqual(listener.calls, 2)

    def test_listener_failure_moves_to_fallback(self) -> None:
        manager = CodexDaemonManager(listener=_FailingListener(), poll_ms=1)

        with self.assertRaises(ProcessListenerError):
            manager.snapshot()

        self.assertEqual(manager.state, DaemonState.FALLBACK)

    def test_missing_process_moves_to_exiting_after_hud_started(self) -> None:
        manager = CodexDaemonManager(
            listener=_FakeListener([ProcessSnapshot(found=False)]),
            poll_ms=1,
        )

        self.assertFalse(manager.codex_is_running())

        self.assertEqual(manager.state, DaemonState.EXITING)

    def test_codex_is_running_prefers_exit_monitor_over_full_snapshot(self) -> None:
        listener = _FakeListener([ProcessSnapshot(found=True, pids=(123,))])
        monitor = _FakeExitMonitor([True])
        manager = CodexDaemonManager(
            listener=listener,
            poll_ms=1,
            exit_monitor_factory=lambda snapshot: monitor,
        )
        manager.snapshot()
        calls_after_initial_snapshot = listener.calls

        self.assertTrue(manager.codex_is_running())

        self.assertEqual(listener.calls, calls_after_initial_snapshot)
        self.assertEqual(monitor.calls, 1)

    def test_codex_is_running_rescans_after_exit_monitor_reports_exit(self) -> None:
        listener = _FakeListener(
            [
                ProcessSnapshot(found=True, pids=(123,)),
                ProcessSnapshot(found=False),
            ]
        )
        monitor = _FakeExitMonitor([False])
        manager = CodexDaemonManager(
            listener=listener,
            poll_ms=1,
            exit_monitor_factory=lambda snapshot: monitor,
        )
        manager.snapshot()

        self.assertFalse(manager.codex_is_running())

        self.assertTrue(monitor.closed)
        self.assertEqual(manager.state, DaemonState.EXITING)


if __name__ == "__main__":
    unittest.main()
