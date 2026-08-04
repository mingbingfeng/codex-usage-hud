from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from codex_usage_hud.runtime_diagnostics import (
    CRASH_DIAGNOSTICS_ENV,
    CrashDiagnostics,
    JsonlDiagnosticSink,
    append_runtime_error_diagnostic,
    ensure_runtime_error_diagnostics,
)


FIXED_NOW = datetime(2026, 7, 31, 12, 30, tzinfo=timezone.utc)


def test_jsonl_sink_writes_structured_record_and_omits_empty_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "renderer.log"
    sink = JsonlDiagnosticSink(path, now=lambda: FIXED_NOW)

    assert sink.append("initial_connect_failed", reason="timeout", empty="", none=None)

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record == {
        "time": "2026-07-31T12:30:00+00:00",
        "stage": "initial_connect_failed",
        "reason": "timeout",
    }


def test_runtime_error_adapter_preserves_existing_payload_fields() -> None:
    event = SimpleNamespace(
        source="cdp",
        severity="error",
        code="cdp.update_failed",
        message="timed out",
        to_payload=lambda: {
            "context": {"attempt": 2},
            "count": 3,
            "firstSeenAt": "first",
            "lastSeenAt": "last",
        },
    )
    records: list[tuple[str, dict[str, object]]] = []

    append_runtime_error_diagnostic(
        "recorded",
        event,
        append=lambda stage, **fields: records.append((stage, fields)),
    )

    assert records == [
        (
            "runtime_error_recorded",
            {
                "source": "cdp",
                "severity": "error",
                "code": "cdp.update_failed",
                "message": "timed out",
                "context": {"attempt": 2},
                "count": 3,
                "firstSeenAt": "first",
                "lastSeenAt": "last",
            },
        )
    ]


def test_runtime_error_callback_is_installed_once() -> None:
    registry = SimpleNamespace(diagnostic_callback=None)
    context = SimpleNamespace(runtime_errors=registry)

    def callback(_action: str, _event: object) -> None:
        return None

    assert ensure_runtime_error_diagnostics(context, callback=callback)
    assert registry.diagnostic_callback is callback
    assert not ensure_runtime_error_diagnostics(context, callback=lambda *_args: None)
    assert registry.diagnostic_callback is callback


class _FakeFaultHandler:
    def __init__(self) -> None:
        self.enable_calls: list[dict[str, object]] = []
        self.disable_calls = 0

    def enable(self, **kwargs: object) -> None:
        self.enable_calls.append(dict(kwargs))

    def disable(self) -> None:
        self.disable_calls += 1


def test_crash_diagnostics_owns_and_closes_faulthandler_file(tmp_path: Path) -> None:
    path = tmp_path / "crash.log"
    fake = _FakeFaultHandler()
    manager = CrashDiagnostics(
        path_provider=lambda: path,
        platform_provider=lambda: "win32",
        environ_provider=lambda: {},
        now=lambda: FIXED_NOW,
        faulthandler_loader=lambda: fake,
    )

    assert manager.enable() == path
    assert manager.enable() == path
    assert len(fake.enable_calls) == 1
    assert fake.enable_calls[0]["all_threads"] is True
    assert "pid=" in path.read_text(encoding="utf-8")

    manager.close()

    assert fake.disable_calls == 1
    assert fake.enable_calls[0]["file"].closed


def test_crash_diagnostics_respects_disable_flag(tmp_path: Path) -> None:
    path = tmp_path / "crash.log"
    manager = CrashDiagnostics(
        path_provider=lambda: path,
        platform_provider=lambda: "win32",
        environ_provider=lambda: {CRASH_DIAGNOSTICS_ENV: "off"},
    )

    assert manager.enable() is None
    assert not path.exists()
