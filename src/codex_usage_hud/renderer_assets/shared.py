"""Renderer shared JavaScript capabilities used by domain factories."""

TEXT = r"""
  const shared = {
    normalize,
    clamp,
    px,
    visible,
    cssEscape,
  };
"""

__all__ = ["TEXT"]
