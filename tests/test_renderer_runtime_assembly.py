from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codex_usage_hud.renderer_runtime_assembly import (
    RendererSessionBase,
    assemble_renderer_session,
    create_renderer_session_base,
)
from codex_usage_hud.runtime_ports import RuntimeServices


def _services(
    *,
    context: object,
    overlay_factory,
    calls: list[str] | None = None,
    bridge_callbacks: dict[str, object] | None = None,
) -> RuntimeServices:
    calls = calls if calls is not None else []

    def mark(name: str, value: object):
        calls.append(name)
        return value

    def bridge_factory(*args: object, **kwargs: object) -> object:
        del args
        if bridge_callbacks is not None:
            bridge_callbacks.update(kwargs)
        return mark(
            "bridge",
            SimpleNamespace(
                start=lambda: "http://127.0.0.1:8765",
                background_usage_url="",
                close=MagicMock(),
            ),
        )

    return RuntimeServices(
        clock=SimpleNamespace(),
        context_factory=lambda _args: mark("context", context),
        renderer_factory=lambda _port, _timeout: mark(
            "renderer", SimpleNamespace(close=MagicMock())
        ),
        overlay_factory=overlay_factory,
        update_manager_factory=lambda: mark(
            "updates", SimpleNamespace(close=MagicMock())
        ),
        bridge_factory=bridge_factory,
        snapshot_builder=lambda _context: None,
    )


def test_base_closes_context_when_overlay_construction_fails() -> None:
    context = SimpleNamespace(
        user_config=SimpleNamespace(display_mode="renderer"),
        close=MagicMock(),
    )
    services = _services(
        context=context,
        overlay_factory=MagicMock(side_effect=RuntimeError("overlay failed")),
    )

    with pytest.raises(RuntimeError, match="overlay failed"):
        create_renderer_session_base(SimpleNamespace(), services=services)

    context.close.assert_called_once_with()


def test_assembly_registers_resources_and_runtime_adapters_in_order() -> None:
    calls: list[str] = []
    bridge_callbacks: dict[str, object] = {}
    unsubscribe = MagicMock()
    tracker = SimpleNamespace(set_change_callback=MagicMock())
    event_bus = SimpleNamespace(
        subscribe=lambda _callback: calls.append("subscribe") or unsubscribe,
        publish=MagicMock(),
        drain=lambda: [],
    )
    context = SimpleNamespace(
        user_config=SimpleNamespace(display_mode="renderer"),
        settings_store=SimpleNamespace(path=Path("settings.json")),
        active_session_tracker=tracker,
        pre_send_estimator=SimpleNamespace(),
        runtime_events=event_bus,
        close=MagicMock(),
    )
    overlay = SimpleNamespace(close=MagicMock())
    services = _services(
        context=context,
        overlay_factory=lambda _context: calls.append("overlay") or overlay,
        calls=calls,
        bridge_callbacks=bridge_callbacks,
    )
    base = create_renderer_session_base(SimpleNamespace(), services=services)

    client = SimpleNamespace(
        close=MagicMock(),
        set_active_session_callback=MagicMock(),
        set_settings_command_callback=MagicMock(),
        set_attachments_callback=MagicMock(),
        set_layout_callback=MagicMock(),
        set_theme_callback=MagicMock(),
    )
    services = RuntimeServices(
        clock=services.clock,
        context_factory=services.context_factory,
        renderer_factory=lambda _port, _timeout: calls.append("renderer") or client,
        overlay_factory=services.overlay_factory,
        update_manager_factory=services.update_manager_factory,
        bridge_factory=services.bridge_factory,
        snapshot_builder=services.snapshot_builder,
    )
    ports = SimpleNamespace(RENDERER_ACTIVE_SESSION_BOOTSTRAP_WAIT_SECONDS=0.0)

    assembly = assemble_renderer_session(
        base,
        startup_plan=SimpleNamespace(port=9333),
        renderer_cdp_timeout=0.35,
        services=services,
        ports=ports,
    )

    assert isinstance(assembly.base, RendererSessionBase)
    assert assembly.resources.client is client
    assert assembly.resources.update_manager is assembly.update_manager
    assert assembly.resources.bridge is not None
    assert assembly.bridge_url == "http://127.0.0.1:8765"
    assert assembly.runtime_event_drain() == []
    assert calls == ["context", "overlay", "updates", "renderer", "subscribe", "bridge"]
    assert tracker.set_change_callback.call_count == 1
    assert client.set_active_session_callback.call_count == 1
    assert client.set_theme_callback.call_count == 1

    assembly.restart_requested.clear()
    assembly.command_refresh_requested.clear()
    restart_callback = bridge_callbacks["restart_callback"]
    assert callable(restart_callback)
    restart_callback()
    assert assembly.restart_requested.is_set()
    assert assembly.command_refresh_requested.is_set()

    assembly.resources.close()
    unsubscribe.assert_called_once_with()
    context.close.assert_called_once_with()
    overlay.close.assert_called_once_with()


def test_assembly_owner_has_no_composition_root_or_facade_dependency() -> None:
    source = Path("src/codex_usage_hud/renderer_runtime_assembly.py").read_text(
        encoding="utf-8"
    )

    assert "runtime_orchestration" not in source
    assert "codex_usage_hud.cli" not in source
    assert "ui.renderer_hud" not in source
