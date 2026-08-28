"""Regression tests for frozen-process environment boundaries."""

from __future__ import annotations

import os
from pathlib import Path

from codex_usage_hud import desktop_overlay, loading_feedback
from codex_usage_hud.process_environment import (
    external_environment_scope,
    external_process_environment,
)


def test_external_process_environment_filters_pyi_variables() -> None:
    source = {
        "_PYI_ARCHIVE_FILE": "C:/hud.exe",
        "_PYI_APPLICATION_HOME_DIR": "C:/temp/_MEI123",
        "ordinary": "kept",
    }

    environment = external_process_environment(source)

    assert environment == {"ordinary": "kept"}


def test_external_environment_scope_restores_markers_after_external_launch(monkeypatch) -> None:
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "C:/hud.exe")

    with external_environment_scope():
        assert "_PYI_ARCHIVE_FILE" not in os.environ

    assert os.environ["_PYI_ARCHIVE_FILE"] == "C:/hud.exe"


def test_loading_feedback_frozen_helper_keeps_pyi_environment(tmp_path: Path, monkeypatch) -> None:
    process_calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 321

    def fake_popen(_command: list[str], **kwargs: object) -> FakeProcess:
        process_calls.append(kwargs)
        return FakeProcess()

    monkeypatch.setattr(loading_feedback.sys, "frozen", True, raising=False)
    monkeypatch.setattr(loading_feedback.sys, "executable", "C:/codex-hud.exe")
    monkeypatch.setattr(loading_feedback, "_default_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(loading_feedback.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")

    feedback = loading_feedback.HudLoadingFeedback("title", "message", enabled=True)
    feedback.start()

    assert process_calls[0]["env"]["_PYI_PARENT_PROCESS_LEVEL"] == "1"


def test_desktop_overlay_frozen_helper_keeps_pyi_environment(tmp_path: Path, monkeypatch) -> None:
    process_calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 654

    def fake_popen(_command: list[str], **kwargs: object) -> FakeProcess:
        process_calls.append(kwargs)
        return FakeProcess()

    monkeypatch.setattr(desktop_overlay.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_overlay.sys, "executable", "C:/codex-hud.exe")
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "C:/temp/_MEI123")
    monkeypatch.setattr(desktop_overlay.subprocess, "Popen", fake_popen)

    overlay = desktop_overlay.DesktopWorkOverlay(
        enabled=True,
        state_path=tmp_path / "work-overlay-1-1.json",
    )
    overlay._start()

    assert process_calls[0]["env"]["_PYI_APPLICATION_HOME_DIR"] == "C:/temp/_MEI123"
