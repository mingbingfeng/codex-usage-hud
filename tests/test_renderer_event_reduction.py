from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from codex_usage_hud import renderer_event_loop, renderer_event_reduction
from codex_usage_hud.renderer_event_loop import RendererLoopState


def _event(event_type: str, **context: object) -> SimpleNamespace:
    return SimpleNamespace(type=event_type, context=context)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_renderer_event_loop_reexports_reduction_owner() -> None:
    for name in ("RefreshPlan", "reduce_event", "reduce_events"):
        assert getattr(renderer_event_loop, name) is getattr(
            renderer_event_reduction, name
        )


def test_reduction_owner_keeps_layout_and_unknown_events_work_free() -> None:
    _, plan = renderer_event_reduction.reduce_events(
        RendererLoopState(),
        [_event("renderer_layout_changed"), _event("unknown")],
    )

    assert not plan.snapshot
    assert not plan.force_fast
    assert not plan.domains
    assert not plan.background_usage


def test_reduction_owner_has_no_runtime_or_composition_dependency() -> None:
    path = Path("src/codex_usage_hud/renderer_event_reduction.py")
    imports = _imports(path)
    runtime_imports = _top_level_imports(path)
    source = path.read_text(encoding="utf-8")

    assert "renderer_event_loop" not in runtime_imports
    assert "runtime_orchestration" not in source
    assert "codex_usage_hud.cli" not in source
    assert "renderer_client" not in source
    assert "threading" not in imports
    assert "subprocess" not in imports
