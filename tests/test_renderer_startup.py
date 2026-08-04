from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_usage_hud.codex_app_runtime import CodexDesktopProcess
from codex_usage_hud import renderer_startup as startup


def _process(pid: int, command_line: str) -> CodexDesktopProcess:
    return CodexDesktopProcess(pid, "ChatGPT.exe", "C:/Codex/ChatGPT.exe", command_line)


def test_initial_port_prefers_live_requested_port_over_successful(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "renderer_cdp_state.json"
    state.write_text(
        json.dumps({"lastRequestedPort": 60123, "lastSuccessfulPort": 59629}),
        encoding="utf-8",
    )
    monkeypatch.delenv(startup.CDP_PORT_ENV, raising=False)

    port = startup.select_initial_cdp_port(
        state_path=lambda: state,
        listening=lambda value: value == 60123,
    )

    assert port == 60123
    assert startup.os.environ[startup.CDP_PORT_ENV] == "60123"


def test_launch_port_allocates_once_when_all_preferred_ports_are_occupied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "missing.json"
    monkeypatch.setenv(startup.CDP_PORT_ENV, "9444")
    allocated: list[str] = []

    port = startup.select_launch_cdp_port(
        state_path=lambda: state,
        available=lambda _port: False,
        allocate=lambda: allocated.append("fresh") or 9555,
    )

    assert port == 9555
    assert allocated == ["fresh"]
    assert startup.os.environ[startup.CDP_PORT_ENV] == "9555"


def test_observed_plain_launch_requests_one_bounded_relaunch() -> None:
    ports = startup.RendererStartupPorts(
        desktop_processes=lambda: [],
        audited_desktop_processes=lambda: [_process(22, "ChatGPT.exe")],
        desktop_running=lambda: True,
        diagnostic=lambda *_args, **_kwargs: None,
        state_path=lambda: Path("unused"),
    )

    plan = startup.startup_plan(observed_codex_launch=True, ports=ports)

    assert plan.scenario == startup.RENDERER_STARTUP_RELAUNCH_OBSERVED
    assert plan.reason == "observed-codex-has-no-declared-cdp-port"


def test_observed_conflicting_declared_ports_fail_closed() -> None:
    ports = startup.RendererStartupPorts(
        desktop_processes=lambda: [],
        audited_desktop_processes=lambda: [
            _process(22, "ChatGPT.exe --remote-debugging-port=59629"),
            _process(23, "ChatGPT.exe --remote-debugging-port 60123"),
        ],
        desktop_running=lambda: True,
        diagnostic=lambda *_args, **_kwargs: None,
        state_path=lambda: Path("unused"),
    )

    plan = startup.startup_plan(observed_codex_launch=True, ports=ports)

    assert plan.scenario == startup.RENDERER_STARTUP_RESTART_REQUIRED
    assert "conflicting" in plan.reason


def test_running_desktop_attaches_only_to_verified_candidate() -> None:
    diagnostics: list[str] = []
    candidate = startup.RendererCdpPortCandidate(59629, "desktop-process", 22)
    ports = startup.RendererStartupPorts(
        desktop_processes=lambda: [_process(22, "ChatGPT.exe")],
        audited_desktop_processes=lambda: [],
        desktop_running=lambda: True,
        diagnostic=lambda stage, **_fields: diagnostics.append(stage),
        state_path=lambda: Path("unused"),
    )

    plan = startup.startup_plan(
        ports=ports,
        find_existing=lambda: candidate,
    )

    assert plan == startup.RendererStartupPlan(
        startup.RENDERER_STARTUP_ATTACH,
        port=59629,
        port_source="desktop-process",
    )
    assert diagnostics == ["renderer_startup_classified"]
