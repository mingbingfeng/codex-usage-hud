from __future__ import annotations

import os
from pathlib import Path

import pytest

from codex_usage_hud.instance_lock import (
    HudAlreadyRunningError,
    HudInstanceLock,
    process_exists,
    read_pid,
    stop_recorded_instance,
    terminate_process,
)


@pytest.fixture(autouse=True)
def _disable_native_mutex(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HudInstanceLock, "_acquire_native_mutex", lambda self: None)


def test_read_pid_accepts_only_positive_integers(tmp_path: Path) -> None:
    path = tmp_path / "hud.pid"
    assert read_pid(path) is None
    path.write_text("not-a-pid", encoding="utf-8")
    assert read_pid(path) is None
    path.write_text("0", encoding="utf-8")
    assert read_pid(path) is None
    path.write_text("123", encoding="utf-8")
    assert read_pid(path) == 123


def test_lock_prevents_a_second_live_owner_and_releases_pid_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hud.pid"
    owner = HudInstanceLock(
        path,
        pid_provider=lambda: 101,
        process_probe=lambda pid: pid == 101,
    )
    owner.acquire()

    contender = HudInstanceLock(
        path,
        pid_provider=lambda: 202,
        process_probe=lambda pid: pid == 101,
    )
    with pytest.raises(HudAlreadyRunningError, match="PID 101"):
        contender.acquire()

    assert path.read_text(encoding="utf-8") == "101"
    owner.release()
    assert not path.exists()


def test_lock_replaces_stale_pid_and_acquire_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "hud.pid"
    path.write_text("999", encoding="utf-8")
    lock = HudInstanceLock(
        path,
        pid_provider=lambda: 303,
        process_probe=lambda _pid: False,
    )

    lock.acquire()
    lock.acquire()

    assert lock.owned
    assert path.read_text(encoding="utf-8") == "303"
    lock.release()
    assert not path.exists()


def test_release_does_not_remove_a_replaced_owner_file(tmp_path: Path) -> None:
    path = tmp_path / "hud.pid"
    lock = HudInstanceLock(
        path,
        pid_provider=lambda: 404,
        process_probe=lambda _pid: False,
    )
    lock.acquire()
    path.write_text("505", encoding="utf-8")

    lock.release()

    assert path.read_text(encoding="utf-8") == "505"


def test_process_helpers_refuse_invalid_or_current_pid() -> None:
    assert process_exists(os.getpid())
    assert not process_exists(0)
    assert not terminate_process(0)
    assert not terminate_process(os.getpid())


def test_stop_recorded_instance_runs_cleanup_before_removing_invalid_lock(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hud.pid"
    path.write_text("invalid", encoding="utf-8")
    events: list[str] = []

    message = stop_recorded_instance(path, before_stop=lambda: events.append("cleanup"))

    assert message == "No running codex-usage-hud instance was recorded."
    assert events == ["cleanup"]
    assert not path.exists()


def test_stop_recorded_instance_terminates_live_pid_and_clears_lock(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hud.pid"
    path.write_text("606", encoding="utf-8")
    alive = {606}
    terminated: list[int] = []

    def terminate(pid: int) -> bool:
        terminated.append(pid)
        alive.discard(pid)
        return True

    message = stop_recorded_instance(
        path,
        process_probe=lambda pid: pid in alive,
        terminate=terminate,
        sleep=lambda _seconds: None,
    )

    assert message == "Stopped codex-usage-hud PID 606."
    assert terminated == [606]
    assert not path.exists()
