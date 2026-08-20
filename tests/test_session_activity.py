from __future__ import annotations

from threading import Event

from codex_usage_hud.session_activity import (
    RendererActivityGate,
    WindowsSessionLockMonitor,
)


def test_activity_gate_blocks_until_one_unlock_transition() -> None:
    gate = RendererActivityGate()

    assert not gate.is_suspended()
    assert gate.suspend()
    assert gate.is_suspended()
    assert not gate.wait_until_resumed(0.01)
    assert not gate.suspend()

    assert gate.resume()
    assert not gate.is_suspended()
    assert gate.wait_until_resumed(0.01)
    assert not gate.resume()


def test_monitor_emits_only_lock_state_transitions() -> None:
    states = iter((False, True, True, False, False))
    transitions: list[str] = []
    monitor = WindowsSessionLockMonitor(
        on_lock=lambda: transitions.append("lock"),
        on_unlock=lambda: transitions.append("unlock"),
        locked_probe=lambda: next(states),
    )

    for _ in range(5):
        monitor.poll_once()

    assert transitions == ["lock", "unlock"]
