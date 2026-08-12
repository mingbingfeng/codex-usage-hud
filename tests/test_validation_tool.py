from __future__ import annotations

import sys

from tools.run_validation import DEFAULT_FOCUSED_TESTS, build_phases


def test_validation_defaults_to_fast_focused_closeout() -> None:
    phases = build_phases()
    assert [name for name, _ in phases] == [
        "compileall",
        "renderer-contract",
        "focused-tests",
    ]
    assert phases[-1][1] == [sys.executable, "-m", "pytest", *DEFAULT_FOCUSED_TESTS, "-q"]
    assert "tests/test_renderer_contract_tool.py" in DEFAULT_FOCUSED_TESTS


def test_validation_full_mode_appends_timed_full_suite() -> None:
    phases = build_phases(full=True, focused_tests=("tests/test_renderer_hud.py",))
    assert [name for name, _ in phases] == [
        "compileall",
        "renderer-contract",
        "focused-tests",
        "full-tests",
    ]
    assert phases[-1][1][-1] == "--durations=10"
