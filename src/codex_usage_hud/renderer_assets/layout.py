"""Renderer layout domain asset assembled from static raw fragments."""

from .layout_anchors import TEXT as ANCHORS
from .layout_gestures import TEXT as GESTURES
from .layout_markup import TEXT as MARKUP
from .layout_observers import TEXT as OBSERVERS
from .layout_style import TEXT as STYLE


# Keep one manifest-level layout asset while making its static ownership
# boundaries explicit. Joining is deliberately byte-preserving.
TEXT = "".join((STYLE, MARKUP, GESTURES, ANCHORS, OBSERVERS))

__all__ = ["TEXT"]
