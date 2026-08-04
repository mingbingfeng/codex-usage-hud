"""Explicit public command-line facade.

The runtime composition root owns implementation. This module intentionally
exports only the supported CLI contract plus the one documented compatibility
symbol; private runtime names must be imported from their terminal owners.
"""

from __future__ import annotations

from . import runtime_orchestration as _owner


# Keep entry-point and repository-tool imports as direct, inspectable bindings.
UsageSummaryCache = _owner.UsageSummaryCache
current_budget_windows = _owner.current_budget_windows
main = _owner.main
renderer_diagnostic_path = _owner.renderer_diagnostic_path


__all__ = [
    "UsageSummaryCache",
    "current_budget_windows",
    "main",
    "renderer_diagnostic_path",
]
