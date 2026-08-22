from __future__ import annotations

from codex_usage_hud.session_activity import WindowsSessionLockMonitor


def test_monitor_emits_only_lock_state_transitions() -> None:
    transitions: list[str] = []
    monitor = WindowsSessionLockMonitor(
        on_lock=lambda: transitions.append("lock"),
        on_unlock=lambda: transitions.append("unlock"),
        locked_probe=lambda: False,
    )

    monitor._emit_transition(False)  # noqa: SLF001
    monitor._emit_transition(True)  # noqa: SLF001
    monitor._emit_transition(True)  # noqa: SLF001
    monitor._emit_transition(False)  # noqa: SLF001
    monitor._emit_transition(False)  # noqa: SLF001

    assert transitions == ["lock", "unlock"]
