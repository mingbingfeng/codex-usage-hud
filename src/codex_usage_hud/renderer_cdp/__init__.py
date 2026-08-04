"""Explicit compatibility facade for Renderer CDP transport owners."""

from .bindings import _RendererBinding
from .target import RendererTargetDiscovery

__all__ = ["RendererTargetDiscovery", "_RendererBinding"]
