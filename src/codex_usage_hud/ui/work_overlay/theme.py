"""Qt-free work-overlay palette and payload-signature functions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from .constants import DEFAULT_WORK_OVERLAY_THEME

def _theme_hex(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if text.startswith("#") and len(text) in {4, 7}:
        return text
    return fallback

def _theme_hex_to_rgb(value: object, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    text = _theme_hex(value, "")
    if len(text) == 4:
        text = f"#{text[1] * 2}{text[2] * 2}{text[3] * 2}"
    if len(text) != 7:
        return fallback
    try:
        return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)
    except ValueError:
        return fallback

def _theme_rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return (
        f"#{max(0, min(255, int(rgb[0]))):02x}"
        f"{max(0, min(255, int(rgb[1]))):02x}"
        f"{max(0, min(255, int(rgb[2]))):02x}"
    )

def _theme_mix(base: object, overlay: object, alpha: float, *, fallback: str) -> str:
    alpha = max(0.0, min(1.0, float(alpha)))
    base_rgb = _theme_hex_to_rgb(base, _theme_hex_to_rgb(fallback, (16, 22, 29)))
    overlay_rgb = _theme_hex_to_rgb(overlay, _theme_hex_to_rgb(fallback, (16, 22, 29)))
    channels = []
    for base_channel, overlay_channel in zip(base_rgb, overlay_rgb):
        channels.append(
            int(round((base_channel * (1.0 - alpha)) + (overlay_channel * alpha)))
        )
    return f"#{channels[0]:02x}{channels[1]:02x}{channels[2]:02x}"

def _theme_relative_luma(
    value: object,
    fallback: tuple[int, int, int] = (0, 0, 0),
) -> float:
    red, green, blue = _theme_hex_to_rgb(value, fallback)
    channels = []
    for channel in (red, green, blue):
        normalized = channel / 255.0
        if normalized <= 0.03928:
            channels.append(normalized / 12.92)
        else:
            channels.append(((normalized + 0.055) / 1.055) ** 2.4)
    return (channels[0] * 0.2126) + (channels[1] * 0.7152) + (channels[2] * 0.0722)

def _theme_contrast_ratio(left: object, right: object) -> float:
    left_luma = _theme_relative_luma(left)
    right_luma = _theme_relative_luma(right)
    lighter = max(left_luma, right_luma)
    darker = min(left_luma, right_luma)
    return (lighter + 0.05) / (darker + 0.05)

def _theme_contrast_choice(
    background: object,
    primary: object,
    secondary: object,
    *,
    fallback: str,
) -> str:
    primary_hex = _theme_hex(primary, fallback)
    secondary_hex = _theme_hex(secondary, fallback)
    if not primary_hex:
        return secondary_hex or fallback
    if not secondary_hex:
        return primary_hex
    if _theme_contrast_ratio(background, primary_hex) >= _theme_contrast_ratio(
        background,
        secondary_hex,
    ):
        return primary_hex
    return secondary_hex

def _theme_emphasis_ink(
    background: object,
    *,
    fallback: str,
) -> str:
    return _theme_contrast_choice(
        background,
        "#ffffff",
        "#111111",
        fallback=fallback,
    )

def _theme_readable_color(
    color: object,
    background: object,
    *,
    fallback: str,
    min_ratio: float = 4.5,
) -> str:
    background_hex = _theme_hex(background, "#10161d")
    candidate = _theme_hex(color, fallback)
    if _theme_contrast_ratio(background_hex, candidate) >= min_ratio:
        return candidate

    fallback_hex = _theme_hex(fallback, candidate)
    candidate_rgb = _theme_hex_to_rgb(candidate, _theme_hex_to_rgb(fallback_hex, (255, 255, 255)))
    fallback_rgb = _theme_hex_to_rgb(fallback_hex, candidate_rgb)
    best = candidate
    best_ratio = _theme_contrast_ratio(background_hex, candidate)
    for ratio in (0.18, 0.32, 0.46, 0.60, 0.74, 0.88, 1.0):
        mixed = _theme_rgb_to_hex(
            (
                int(round(candidate_rgb[0] + ((fallback_rgb[0] - candidate_rgb[0]) * ratio))),
                int(round(candidate_rgb[1] + ((fallback_rgb[1] - candidate_rgb[1]) * ratio))),
                int(round(candidate_rgb[2] + ((fallback_rgb[2] - candidate_rgb[2]) * ratio))),
            )
        )
        mixed_ratio = _theme_contrast_ratio(background_hex, mixed)
        if mixed_ratio > best_ratio:
            best = mixed
            best_ratio = mixed_ratio
        if mixed_ratio >= min_ratio:
            return mixed
    high_contrast = _theme_emphasis_ink(background_hex, fallback=fallback_hex)
    high_ratio = _theme_contrast_ratio(background_hex, high_contrast)
    return high_contrast if high_ratio > best_ratio else best

def _resolved_overlay_theme(theme_tokens: Mapping[str, object] | None) -> dict[str, str]:
    resolved = dict(DEFAULT_WORK_OVERLAY_THEME)
    if theme_tokens is None:
        return resolved
    for key, fallback in DEFAULT_WORK_OVERLAY_THEME.items():
        resolved[key] = _theme_hex(theme_tokens.get(key), fallback)
    return resolved

def _overlay_payload_signature(
    items: Sequence[Mapping[str, object]],
    theme_tokens: Mapping[str, object] | None = None,
) -> str:
    return json.dumps(
        {
            "items": list(items),
            "theme": _resolved_overlay_theme(theme_tokens),
        },
        ensure_ascii=False,
        sort_keys=True,
    )

def _color_for(
    status: str,
    theme_tokens: Mapping[str, object] | None = None,
) -> tuple[str, str, str, str]:
    theme = _resolved_overlay_theme(theme_tokens)
    base_card = theme["requestPanelSurface"]
    base_border = theme["panelBorder"]
    if status == "error":
        accent = theme["error"]
        return (
            accent,
            _theme_mix(theme["surface"], accent, 0.10, fallback=base_card),
            _theme_mix(base_card, accent, 0.16, fallback=base_card),
            _theme_mix(base_border, accent, 0.55, fallback=base_border),
        )
    if status == "waiting_user":
        accent = theme["warning"]
        return (
            accent,
            _theme_mix(theme["surface"], accent, 0.10, fallback=base_card),
            _theme_mix(base_card, accent, 0.12, fallback=base_card),
            _theme_mix(base_border, accent, 0.45, fallback=base_border),
        )
    if status == "background_usage":
        accent = theme["warning"]
        return (
            accent,
            _theme_mix(theme["surface"], accent, 0.12, fallback=base_card),
            _theme_mix(base_card, accent, 0.15, fallback=base_card),
            _theme_mix(base_border, accent, 0.58, fallback=base_border),
        )
    if status == "tool":
        accent = theme["info"]
        return (
            accent,
            _theme_mix(theme["surface"], accent, 0.10, fallback=base_card),
            _theme_mix(base_card, accent, 0.12, fallback=base_card),
            _theme_mix(base_border, accent, 0.45, fallback=base_border),
        )
    if status == "recent":
        accent = theme["success"]
        return (
            accent,
            _theme_mix(theme["surface"], accent, 0.11, fallback=base_card),
            _theme_mix(base_card, accent, 0.16, fallback=base_card),
            _theme_mix(base_border, accent, 0.55, fallback=base_border),
        )
    if status == "rest_completed":
        accent = theme["success"]
        return (
            accent,
            _theme_mix(theme["surface"], accent, 0.10, fallback=base_card),
            _theme_mix(base_card, accent, 0.12, fallback=base_card),
            _theme_mix(base_border, accent, 0.45, fallback=base_border),
        )
    accent = theme["accent"]
    return (
        accent,
        _theme_mix(theme["surface"], accent, 0.10, fallback=base_card),
        _theme_mix(base_card, accent, 0.12, fallback=base_card),
        _theme_mix(base_border, accent, 0.40, fallback=base_border),
    )

def _round_badge_palette(
    status: str,
    theme_tokens: Mapping[str, object] | None = None,
) -> dict[str, str]:
    theme = _resolved_overlay_theme(theme_tokens)
    accent, _, _, _ = _color_for(status, theme)
    background = _theme_mix(accent, theme["surface"], 0.10, fallback=accent)
    border = _theme_mix(theme["panelBorder"], accent, 0.68, fallback=theme["panelBorder"])
    text = _theme_contrast_choice(
        background,
        theme["text"],
        theme["surface"],
        fallback=theme["text"],
    )
    return {
        "background": background,
        "border": border,
        "text": text,
    }

def _completed_badge_palette(
    theme_tokens: Mapping[str, object] | None = None,
) -> dict[str, str]:
    theme = _resolved_overlay_theme(theme_tokens)
    text = theme["text"]
    surface = theme["surface"]
    panel = theme["panelSurface"]
    request_panel = theme["requestPanelSurface"]
    is_light = _theme_relative_luma(surface) >= _theme_relative_luma(text)
    success = _theme_mix(theme["success"], theme["accent"], 0.10, fallback=theme["success"])
    accent = _theme_mix(theme["accent"], theme["success"], 0.20, fallback=theme["accent"])
    fill_start = _theme_mix(
        panel,
        accent,
        0.05 if is_light else 0.10,
        fallback=panel,
    )
    fill_mid = _theme_mix(
        request_panel,
        success,
        0.10 if is_light else 0.16,
        fallback=request_panel,
    )
    fill_end = _theme_mix(
        surface,
        accent,
        0.04 if is_light else 0.12,
        fallback=surface,
    )
    border = _theme_readable_color(
        _theme_mix(theme["panelBorder"], accent, 0.52, fallback=theme["panelBorder"]),
        fill_mid,
        fallback=text,
        min_ratio=1.8,
    )
    primary_ink = _theme_readable_color(
        text,
        fill_mid,
        fallback=_theme_emphasis_ink(fill_mid, fallback=text),
    )
    secondary_ink = _theme_readable_color(theme["muted"], fill_mid, fallback=primary_ink)
    check_text = _theme_readable_color(
        _theme_mix(accent, text, 0.36 if is_light else 0.22, fallback=accent),
        fill_mid,
        fallback=primary_ink,
    )
    elapsed_ink = _theme_readable_color(
        _theme_mix(theme["muted"], success, 0.14, fallback=theme["muted"]),
        fill_mid,
        fallback=primary_ink,
    )
    ring = _theme_readable_color(
        _theme_mix(accent, success, 0.25, fallback=accent),
        fill_mid,
        fallback=text,
        min_ratio=2.15,
    )
    dashed_ring = _theme_readable_color(
        _theme_mix(ring, theme["muted"], 0.30, fallback=ring),
        fill_mid,
        fallback=text,
        min_ratio=1.8,
    )
    stat_box_fill = _theme_mix(
        request_panel,
        success,
        0.07 if is_light else 0.13,
        fallback=request_panel,
    )
    stat_box_border = _theme_readable_color(
        _theme_mix(theme["panelBorder"], accent, 0.34, fallback=theme["panelBorder"]),
        stat_box_fill,
        fallback=primary_ink,
        min_ratio=1.8,
    )
    stat_value = _theme_readable_color(text, stat_box_fill, fallback=primary_ink)
    stat_label = _theme_readable_color(theme["muted"], stat_box_fill, fallback=stat_value)
    return {
        "fillStart": fill_start,
        "fillMid": fill_mid,
        "fillEnd": fill_end,
        "border": border,
        "ring": ring,
        "dashedRing": dashed_ring,
        "titleText": primary_ink,
        "workdirText": secondary_ink,
        "checkText": check_text,
        "elapsedText": elapsed_ink,
        "statBoxFill": stat_box_fill,
        "statBoxBorder": stat_box_border,
        "statValue": stat_value,
        "statLabel": stat_label,
    }

def _transition_palette(
    transition_type: str,
    theme_tokens: Mapping[str, object] | None = None,
) -> dict[str, str]:
    theme = _resolved_overlay_theme(theme_tokens)
    if transition_type == "card_to_completed":
        completed = _completed_badge_palette(theme)
        return {
            "fillStart": completed["fillStart"],
            "fillMid": completed["fillMid"],
            "fillEnd": completed["fillEnd"],
            "border": completed["border"],
            "titleText": completed["titleText"],
            "subtitleText": completed["statLabel"],
            "markText": completed["checkText"],
        }
    base = _theme_mix(theme["accent"], theme["info"], 0.22, fallback=theme["accent"])
    fill_start = _theme_mix(base, theme["text"], 0.12, fallback=base)
    fill_mid = _theme_mix(base, theme["info"], 0.26, fallback=base)
    fill_end = _theme_mix(
        theme["requestPanelSurface"],
        base,
        0.52,
        fallback=theme["requestPanelSurface"],
    )
    border = _theme_mix(theme["panelBorder"], base, 0.62, fallback=theme["panelBorder"])
    primary_ink = _theme_contrast_choice(
        fill_mid,
        theme["text"],
        theme["surface"],
        fallback=theme["text"],
    )
    subtitle_ink = _theme_mix(primary_ink, theme["info"], 0.34, fallback=primary_ink)
    return {
        "fillStart": fill_start,
        "fillMid": fill_mid,
        "fillEnd": fill_end,
        "border": border,
        "titleText": primary_ink,
        "subtitleText": subtitle_ink,
        "markText": primary_ink,
    }

__all__ = [
    "_theme_hex",
    "_theme_hex_to_rgb",
    "_theme_rgb_to_hex",
    "_theme_mix",
    "_theme_relative_luma",
    "_theme_contrast_ratio",
    "_theme_contrast_choice",
    "_theme_emphasis_ink",
    "_theme_readable_color",
    "_resolved_overlay_theme",
    "_overlay_payload_signature",
    "_color_for",
    "_round_badge_palette",
    "_completed_badge_palette",
    "_transition_palette",
]
