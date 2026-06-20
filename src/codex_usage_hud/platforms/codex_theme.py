"""Codex App theme parsing and HUD token derivation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from .cdp_probe import (
    DEFAULT_CDP_CACHE_SECONDS,
    DEFAULT_CDP_FAILURE_COOLDOWN_SECONDS,
    DEFAULT_CDP_PORT,
    DEFAULT_CDP_TIMEOUT_SECONDS,
    cdp_enabled_from_env,
    cdp_port_from_env,
    list_targets,
    pick_page_target,
    send_cdp_command,
    runtime_evaluate_params,
)

CODEX_THEME_SHARE_PREFIX = "codex-theme-v1:"
DEFAULT_CODE_THEME_ID = "codex"
THEME_PROBE_SCRIPT = r"""
(() => {
  const normalize = (value) => typeof value === "string" ? value.trim() : "";
  const parseMaybeJson = (value) => {
    const text = normalize(value);
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch (_) {
      return null;
    }
  };
  const readStorage = (key) => {
    try {
      return localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  };
  const normalizeHex = (value) => {
    const text = normalize(value).toLowerCase();
    if (!text) return "";
    const shortHex = text.match(/^#([0-9a-f]{3})$/i);
    if (shortHex) {
      return `#${shortHex[1][0]}${shortHex[1][0]}${shortHex[1][1]}${shortHex[1][1]}${shortHex[1][2]}${shortHex[1][2]}`;
    }
    const longHex = text.match(/^#([0-9a-f]{6})$/i);
    if (longHex) return `#${longHex[1]}`;
    const rgbMatch = text.match(/^rgba?\(([^)]+)\)$/i);
    if (!rgbMatch) return "";
    const parts = rgbMatch[1].split(",").map((item) => item.trim());
    if (parts.length < 3) return "";
    const channels = parts.slice(0, 3).map((item) => {
      if (item.endsWith("%")) {
        const numeric = Number.parseFloat(item.slice(0, -1));
        if (!Number.isFinite(numeric)) return null;
        return Math.max(0, Math.min(255, Math.round((numeric / 100) * 255)));
      }
      const numeric = Number.parseFloat(item);
      if (!Number.isFinite(numeric)) return null;
      return Math.max(0, Math.min(255, Math.round(numeric)));
    });
    if (channels.some((channel) => channel == null)) return "";
    return `#${channels.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
  };
  const css = getComputedStyle(document.documentElement);
  const cssValue = (...names) => {
    for (const name of names) {
      const value = normalize(css.getPropertyValue(name));
      if (value) return value;
    }
    return "";
  };
  const colorValue = (...names) => normalizeHex(cssValue(...names));
  const rawMode = normalize(readStorage("appearanceTheme")).toLowerCase();
  const mode = ["system", "light", "dark"].includes(rawMode) ? rawMode : "system";
  const lightTheme = parseMaybeJson(readStorage("appearanceLightChromeTheme"));
  const darkTheme = parseMaybeJson(readStorage("appearanceDarkChromeTheme"));
  const lightCodeThemeId = normalize(readStorage("appearanceLightCodeThemeId"));
  const darkCodeThemeId = normalize(readStorage("appearanceDarkCodeThemeId"));
  const classList = Array.from(document.documentElement.classList || []);
  const classText = classList.join(" ").toLowerCase();
  const colorScheme = normalize(css.colorScheme).toLowerCase();
  const prefersDark = !!window.matchMedia?.("(prefers-color-scheme: dark)")?.matches;
  let effectiveVariant = prefersDark ? "dark" : "light";
  if (colorScheme.includes("dark") || classText.includes("dark")) effectiveVariant = "dark";
  else if (colorScheme.includes("light") || classText.includes("light")) effectiveVariant = "light";
  return {
    mode,
    lightCodeThemeId,
    darkCodeThemeId,
    lightTheme,
    darkTheme,
    effectiveVariant,
    classList,
    colorScheme,
    cssTheme: {
      accent: colorValue("--vscode-focusBorder", "--vscode-button-background", "--vscode-textLink-foreground"),
      surface: colorValue("--vscode-editor-background", "--vscode-sideBar-background", "--vscode-panel-background", "--vscode-activityBar-background"),
      ink: colorValue("--vscode-editor-foreground", "--vscode-foreground", "--vscode-sideBarTitle-foreground"),
      diffAdded: colorValue("--vscode-gitDecoration-addedResourceForeground", "--vscode-terminal-ansiGreen"),
      diffRemoved: colorValue("--vscode-gitDecoration-deletedResourceForeground", "--vscode-terminal-ansiRed"),
      skill: colorValue("--vscode-terminal-ansiMagenta", "--vscode-textLink-foreground", "--vscode-terminal-ansiBlue"),
    },
  };
})()
"""


def _normalize_hex(value: object, fallback: str = "") -> str:
    text = str(value or "").strip().lower()
    if len(text) == 4 and text.startswith("#"):
        return f"#{text[1] * 2}{text[2] * 2}{text[3] * 2}"
    if len(text) == 7 and text.startswith("#"):
        return text
    return fallback


def _hex_to_rgb(value: object, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    text = _normalize_hex(value)
    if not text:
        return fallback
    try:
        return (
            int(text[1:3], 16),
            int(text[3:5], 16),
            int(text[5:7], 16),
        )
    except ValueError:
        return fallback


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    red, green, blue = rgb
    return (
        f"#{max(0, min(255, int(red))):02x}"
        f"{max(0, min(255, int(green))):02x}"
        f"{max(0, min(255, int(blue))):02x}"
    )


def _mix_color(start: object, end: object, ratio: float, *, fallback: str) -> str:
    ratio = max(0.0, min(1.0, float(ratio)))
    start_rgb = _hex_to_rgb(start, _hex_to_rgb(fallback, (0, 0, 0)))
    end_rgb = _hex_to_rgb(end, _hex_to_rgb(fallback, (0, 0, 0)))
    channels = []
    for start_channel, end_channel in zip(start_rgb, end_rgb):
        channels.append(int(round(start_channel + ((end_channel - start_channel) * ratio))))
    return _rgb_to_hex((channels[0], channels[1], channels[2]))


def _surface_luma(value: object) -> float:
    red, green, blue = _hex_to_rgb(value, (0, 0, 0))
    channels = []
    for channel in (red, green, blue):
        normalized = channel / 255.0
        if normalized <= 0.03928:
            channels.append(normalized / 12.92)
        else:
            channels.append(((normalized + 0.055) / 1.055) ** 2.4)
    return (channels[0] * 0.2126) + (channels[1] * 0.7152) + (channels[2] * 0.0722)


def _contrast_ratio(left: object, right: object) -> float:
    left_luma = _surface_luma(left)
    right_luma = _surface_luma(right)
    lighter = max(left_luma, right_luma)
    darker = min(left_luma, right_luma)
    return (lighter + 0.05) / (darker + 0.05)


def _contrast_choice(
    background: object,
    primary: object,
    secondary: object,
    *,
    fallback: str,
) -> str:
    primary_hex = _normalize_hex(primary, fallback)
    secondary_hex = _normalize_hex(secondary, fallback)
    if not primary_hex:
        return secondary_hex or fallback
    if not secondary_hex:
        return primary_hex
    primary_ratio = _contrast_ratio(background, primary_hex)
    secondary_ratio = _contrast_ratio(background, secondary_hex)
    if primary_ratio + 0.2 >= secondary_ratio:
        return primary_hex
    return secondary_hex


def _fill_text_color(background: object, *, fallback: str) -> str:
    return _contrast_choice(
        background,
        "#ffffff",
        "#111111",
        fallback=fallback,
    )


def _infer_variant(surface: object, ink: object) -> str:
    if _normalize_hex(surface) and _normalize_hex(ink):
        return "dark" if _surface_luma(surface) < _surface_luma(ink) else "light"
    return "dark"


def _effective_variant(
    preference_variant: str,
    *,
    fallback_variant: str,
) -> str:
    normalized = str(preference_variant or "").strip().lower()
    if normalized in {"light", "dark"}:
        return normalized
    return "dark" if fallback_variant == "dark" else "light"


def _windows_apps_use_light_theme() -> bool | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
    except Exception:
        return None
    try:
        return bool(int(value))
    except Exception:
        return None


def _default_codex_config_path() -> Path:
    codex_home = str(os.environ.get("CODEX_HOME") or "").strip()
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def _try_parse_toml(text: str) -> dict[str, Any] | None:
    try:
        import tomllib  # type: ignore[attr-defined]
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return None
    try:
        payload = tomllib.loads(text)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _strip_toml_comment(text: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(text):
        if in_double:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_double = False
            continue
        if in_single:
            if char == "'":
                in_single = False
            continue
        if char == '"':
            in_double = True
            continue
        if char == "'":
            in_single = True
            continue
        if char == "#":
            return text[:index]
    return text


def _split_toml_top_level(text: str, delimiter: str) -> list[str]:
    items: list[str] = []
    current: list[str] = []
    depth_curly = 0
    depth_square = 0
    in_single = False
    in_double = False
    escaped = False
    for char in text:
        if in_double:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_double = False
            continue
        if in_single:
            current.append(char)
            if char == "'":
                in_single = False
            continue
        if char == '"':
            in_double = True
            current.append(char)
            continue
        if char == "'":
            in_single = True
            current.append(char)
            continue
        if char == "{":
            depth_curly += 1
            current.append(char)
            continue
        if char == "}":
            depth_curly = max(0, depth_curly - 1)
            current.append(char)
            continue
        if char == "[":
            depth_square += 1
            current.append(char)
            continue
        if char == "]":
            depth_square = max(0, depth_square - 1)
            current.append(char)
            continue
        if char == delimiter and depth_curly == 0 and depth_square == 0:
            items.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def _split_toml_once(text: str, delimiter: str) -> tuple[str, str] | None:
    depth_curly = 0
    depth_square = 0
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(text):
        if in_double:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_double = False
            continue
        if in_single:
            if char == "'":
                in_single = False
            continue
        if char == '"':
            in_double = True
            continue
        if char == "'":
            in_single = True
            continue
        if char == "{":
            depth_curly += 1
            continue
        if char == "}":
            depth_curly = max(0, depth_curly - 1)
            continue
        if char == "[":
            depth_square += 1
            continue
        if char == "]":
            depth_square = max(0, depth_square - 1)
            continue
        if char == delimiter and depth_curly == 0 and depth_square == 0:
            return text[:index], text[index + 1 :]
    return None


def _parse_toml_string(text: str) -> str:
    raw = text.strip()
    if len(raw) < 2 or raw[0] not in {"'", '"'} or raw[-1] != raw[0]:
        return raw
    if raw[0] == "'":
        return raw[1:-1].replace("''", "'")
    try:
        return str(json.loads(raw))
    except json.JSONDecodeError:
        return raw[1:-1]


def _parse_toml_scalar(text: str) -> Any:
    raw = text.strip()
    if not raw:
        return ""
    if raw[0] in {"'", '"'} and raw[-1] == raw[0]:
        return _parse_toml_string(raw)
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if raw.startswith("{") and raw.endswith("}"):
        body = raw[1:-1].strip()
        result: dict[str, Any] = {}
        for item in _split_toml_top_level(body, ","):
            pair = _split_toml_once(item, "=")
            if pair is None:
                continue
            key_text, value_text = pair
            key = key_text.strip().strip('"').strip("'")
            if not key:
                continue
            result[key] = _parse_toml_scalar(value_text)
        return result
    if raw.startswith("[") and raw.endswith("]"):
        body = raw[1:-1].strip()
        if not body:
            return []
        return [_parse_toml_scalar(item) for item in _split_toml_top_level(body, ",")]
    numeric = raw.replace("_", "")
    try:
        if any(char in numeric for char in ".eE"):
            return float(numeric)
        return int(numeric, 10)
    except ValueError:
        return raw


def _set_nested_value(root: dict[str, Any], path: list[str], value: Any) -> None:
    cursor = root
    for part in path[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[path[-1]] = value


def _parse_codex_config_subset(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_path: list[str] = []
    for raw_line in text.splitlines():
        line = _strip_toml_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current_path = [
                part.strip().strip('"').strip("'")
                for part in line[1:-1].strip().split(".")
                if part.strip()
            ]
            continue
        pair = _split_toml_once(line, "=")
        if pair is None:
            continue
        key_text, value_text = pair
        key = key_text.strip().strip('"').strip("'")
        if not key:
            continue
        _set_nested_value(
            result,
            [*current_path, key],
            _parse_toml_scalar(value_text),
        )
    return result


def _read_codex_desktop_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path).expanduser() if config_path is not None else _default_codex_config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    payload = _try_parse_toml(text)
    if payload is None:
        payload = _parse_codex_config_subset(text)
    desktop = payload.get("desktop") if isinstance(payload, dict) else None
    return desktop if isinstance(desktop, dict) else {}


def _persisted_theme_export(
    raw: Any,
    *,
    code_theme_id: str,
    variant: str,
) -> CodexThemeExport | None:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            if text.startswith(CODEX_THEME_SHARE_PREFIX):
                export = CodexThemeExport.from_share_string(text)
            else:
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    return None
                if "theme" in parsed or "codeThemeId" in parsed:
                    export = CodexThemeExport.from_dict(parsed, fallback_variant=variant)
                else:
                    export = CodexThemeExport(
                        code_theme_id=code_theme_id,
                        theme=CodexThemeConfig.from_dict(parsed),
                        variant=variant,
                    )
        except Exception:
            return None
    elif isinstance(raw, dict):
        if "theme" in raw or "codeThemeId" in raw:
            export = CodexThemeExport.from_dict(raw, fallback_variant=variant)
        else:
            export = CodexThemeExport(
                code_theme_id=code_theme_id,
                theme=CodexThemeConfig.from_dict(raw),
                variant=variant,
            )
    else:
        return None
    return CodexThemeExport(
        code_theme_id=code_theme_id or export.code_theme_id or DEFAULT_CODE_THEME_ID,
        theme=export.theme,
        variant=variant if variant in {"light", "dark"} else export.variant,
    )


def _persisted_theme_snapshot(config_path: str | Path | None = None) -> CodexThemeSnapshot | None:
    desktop = _read_codex_desktop_config(config_path)
    theme_keys = (
        "appearanceTheme",
        "appearanceLightChromeTheme",
        "appearanceDarkChromeTheme",
        "appearanceLightCodeThemeId",
        "appearanceDarkCodeThemeId",
    )
    if not any(key in desktop for key in theme_keys):
        return None

    preference_variant = str(desktop.get("appearanceTheme") or "system").strip().lower()
    if preference_variant not in {"system", "light", "dark"}:
        preference_variant = "system"
    light_code_theme_id = str(desktop.get("appearanceLightCodeThemeId") or DEFAULT_CODE_THEME_ID).strip() or DEFAULT_CODE_THEME_ID
    dark_code_theme_id = str(desktop.get("appearanceDarkCodeThemeId") or DEFAULT_CODE_THEME_ID).strip() or DEFAULT_CODE_THEME_ID
    light_export = _persisted_theme_export(
        desktop.get("appearanceLightChromeTheme"),
        code_theme_id=light_code_theme_id,
        variant="light",
    )
    dark_export = _persisted_theme_export(
        desktop.get("appearanceDarkChromeTheme"),
        code_theme_id=dark_code_theme_id,
        variant="dark",
    )

    use_light_theme = _windows_apps_use_light_theme()
    fallback_variant = "light" if use_light_theme is True else "dark"
    if use_light_theme is None:
        if light_export is not None and dark_export is None:
            fallback_variant = "light"
        elif dark_export is not None and light_export is None:
            fallback_variant = "dark"

    payload: dict[str, Any] = {
        "mode": preference_variant,
        "effectiveVariant": _effective_variant(
            preference_variant,
            fallback_variant=fallback_variant,
        ),
        "lightCodeThemeId": light_code_theme_id,
        "darkCodeThemeId": dark_code_theme_id,
    }
    if light_export is not None:
        payload["lightTheme"] = light_export.theme.to_dict()
    if dark_export is not None:
        payload["darkTheme"] = dark_export.theme.to_dict()
    return CodexThemeSnapshot.from_probe_result(payload, source="persisted")


@dataclass(frozen=True)
class CodexThemeFonts:
    code: str | None = None
    ui: str | None = None

    @classmethod
    def from_dict(cls, payload: Any) -> "CodexThemeFonts":
        data = payload if isinstance(payload, dict) else {}
        code = data.get("code")
        ui = data.get("ui")
        return cls(
            code=str(code).strip() if isinstance(code, str) and code.strip() else None,
            ui=str(ui).strip() if isinstance(ui, str) and ui.strip() else None,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "ui": self.ui,
        }


@dataclass(frozen=True)
class CodexThemeSemanticColors:
    diff_added: str = "#40c977"
    diff_removed: str = "#fa423e"
    skill: str = "#ad7bf9"

    @classmethod
    def from_dict(cls, payload: Any) -> "CodexThemeSemanticColors":
        data = payload if isinstance(payload, dict) else {}
        return cls(
            diff_added=_normalize_hex(data.get("diffAdded"), "#40c977"),
            diff_removed=_normalize_hex(data.get("diffRemoved"), "#fa423e"),
            skill=_normalize_hex(data.get("skill"), "#ad7bf9"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "diffAdded": self.diff_added,
            "diffRemoved": self.diff_removed,
            "skill": self.skill,
        }


@dataclass(frozen=True)
class CodexThemeConfig:
    accent: str
    contrast: int
    fonts: CodexThemeFonts = field(default_factory=CodexThemeFonts)
    ink: str = "#ffffff"
    opaque_windows: bool = False
    semantic_colors: CodexThemeSemanticColors = field(
        default_factory=CodexThemeSemanticColors
    )
    surface: str = "#181818"

    @classmethod
    def from_dict(cls, payload: Any) -> "CodexThemeConfig":
        data = payload if isinstance(payload, dict) else {}
        raw_contrast = data.get("contrast")
        try:
            contrast = int(raw_contrast)
        except (TypeError, ValueError):
            contrast = 60
        contrast = max(0, min(100, contrast))
        return cls(
            accent=_normalize_hex(data.get("accent"), "#339cff"),
            contrast=contrast,
            fonts=CodexThemeFonts.from_dict(data.get("fonts")),
            ink=_normalize_hex(data.get("ink"), "#ffffff"),
            opaque_windows=bool(data.get("opaqueWindows", False)),
            semantic_colors=CodexThemeSemanticColors.from_dict(data.get("semanticColors")),
            surface=_normalize_hex(data.get("surface"), "#181818"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "accent": self.accent,
            "contrast": int(self.contrast),
            "fonts": self.fonts.to_dict(),
            "ink": self.ink,
            "opaqueWindows": bool(self.opaque_windows),
            "semanticColors": self.semantic_colors.to_dict(),
            "surface": self.surface,
        }


@dataclass(frozen=True)
class CodexThemeExport:
    code_theme_id: str
    theme: CodexThemeConfig
    variant: str

    @classmethod
    def from_dict(cls, payload: Any, *, fallback_variant: str = "dark") -> "CodexThemeExport":
        data = payload if isinstance(payload, dict) else {}
        variant = str(data.get("variant") or fallback_variant or "dark").strip().lower()
        if variant not in {"light", "dark"}:
            variant = "dark" if fallback_variant == "dark" else "light"
        code_theme_id = str(data.get("codeThemeId") or DEFAULT_CODE_THEME_ID).strip()
        if not code_theme_id:
            code_theme_id = DEFAULT_CODE_THEME_ID
        return cls(
            code_theme_id=code_theme_id,
            theme=CodexThemeConfig.from_dict(data.get("theme")),
            variant=variant,
        )

    @classmethod
    def from_share_string(cls, text: str) -> "CodexThemeExport":
        raw = str(text or "").strip()
        if not raw.startswith(CODEX_THEME_SHARE_PREFIX):
            raise ValueError("Theme share string prefix mismatch")
        payload = json.loads(raw[len(CODEX_THEME_SHARE_PREFIX) :])
        return cls.from_dict(payload)

    def to_dict(self) -> dict[str, object]:
        return {
            "codeThemeId": self.code_theme_id,
            "theme": self.theme.to_dict(),
            "variant": self.variant,
        }

    def to_share_string(self) -> str:
        return f"{CODEX_THEME_SHARE_PREFIX}{json.dumps(self.to_dict(), ensure_ascii=False, separators=(',', ':'))}"


@dataclass(frozen=True)
class HudThemeTokens:
    variant: str
    surface: str
    panel_surface: str
    panel_border: str
    header_surface: str
    divider: str
    text: str
    muted: str
    accent: str
    info: str
    warning: str
    error: str
    success: str
    request_surface: str
    request_header_surface: str
    request_panel_surface: str
    request_text: str
    request_muted: str
    progress_track: str
    progress_track_border: str
    progress_track_text: str
    progress_cache: str
    progress_cache_end: str
    progress_cache_text: str
    progress_day: str
    progress_day_end: str
    progress_day_text: str
    progress_week: str
    progress_week_end: str
    progress_week_text: str
    progress_overflow: str
    progress_overflow_highlight: str
    progress_overflow_anchor: str
    progress_overflow_anchor_edge: str
    progress_overflow_badge: str
    progress_overflow_badge_edge: str
    progress_overflow_badge_text: str

    @classmethod
    def from_theme(cls, export: CodexThemeExport | None, *, variant: str | None = None) -> "HudThemeTokens":
        if export is None:
            normalized_variant = "dark" if variant == "dark" else "light"
            surface = "#181818" if normalized_variant == "dark" else "#f7f8fb"
            ink = "#ffffff" if normalized_variant == "dark" else "#101418"
            accent = "#339cff"
            theme = CodexThemeConfig(
                accent=accent,
                contrast=60 if normalized_variant == "dark" else 40,
                ink=ink,
                opaque_windows=False,
                semantic_colors=CodexThemeSemanticColors(),
                surface=surface,
            )
            export = CodexThemeExport(
                code_theme_id=DEFAULT_CODE_THEME_ID,
                theme=theme,
                variant=normalized_variant,
            )
        theme = export.theme
        normalized_variant = variant or export.variant or _infer_variant(theme.surface, theme.ink)
        normalized_variant = "dark" if normalized_variant == "dark" else "light"
        surface = _normalize_hex(theme.surface, "#181818" if normalized_variant == "dark" else "#f7f8fb")
        text = _normalize_hex(theme.ink, "#ffffff" if normalized_variant == "dark" else "#111111")
        accent = _normalize_hex(theme.accent, "#339cff")
        success = _normalize_hex(theme.semantic_colors.diff_added, "#40c977")
        error = _normalize_hex(theme.semantic_colors.diff_removed, "#fa423e")
        info = _normalize_hex(theme.semantic_colors.skill, accent)
        panel_surface = _mix_color(surface, text, 0.07 if normalized_variant == "dark" else 0.03, fallback=surface)
        header_surface = _mix_color(surface, text, 0.13 if normalized_variant == "dark" else 0.08, fallback=surface)
        panel_border = _mix_color(surface, text, 0.22 if normalized_variant == "dark" else 0.18, fallback=surface)
        divider = _mix_color(surface, text, 0.14 if normalized_variant == "dark" else 0.12, fallback=surface)
        muted = _mix_color(text, surface, 0.42 if normalized_variant == "dark" else 0.52, fallback=text)
        request_surface = _mix_color(surface, text, 0.03 if normalized_variant == "dark" else 0.015, fallback=surface)
        request_header_surface = _mix_color(surface, text, 0.09 if normalized_variant == "dark" else 0.05, fallback=surface)
        request_panel_surface = _mix_color(surface, text, 0.055 if normalized_variant == "dark" else 0.025, fallback=surface)
        request_text = _mix_color(text, surface, 0.08 if normalized_variant == "dark" else 0.04, fallback=text)
        request_muted = _mix_color(text, surface, 0.50 if normalized_variant == "dark" else 0.58, fallback=text)
        warning = _mix_color(accent, error, 0.28 if normalized_variant == "dark" else 0.34, fallback=accent)
        progress_track = _mix_color(surface, text, 0.10 if normalized_variant == "dark" else 0.06, fallback=surface)
        progress_track_border = _mix_color(surface, text, 0.20 if normalized_variant == "dark" else 0.14, fallback=surface)
        progress_track_text = _mix_color(text, surface, 0.18 if normalized_variant == "dark" else 0.32, fallback=text)
        progress_cache = _mix_color(accent, info, 0.35, fallback=accent)
        progress_cache_end = _mix_color(accent, info, 0.55, fallback=accent)
        progress_cache_text = _mix_color(surface, text, 0.12 if normalized_variant == "dark" else 0.86, fallback=text)
        progress_day = accent if normalized_variant == "dark" else _mix_color(accent, "#111111", 0.04, fallback=accent)
        progress_day_end = _mix_color(progress_day, text, 0.04 if normalized_variant == "dark" else 0.06, fallback=progress_day)
        progress_day_text = _fill_text_color(
            _mix_color(progress_day, progress_day_end, 0.45, fallback=progress_day),
            fallback=text,
        )
        progress_week = _mix_color(accent, success, 0.62 if normalized_variant == "dark" else 0.72, fallback=success)
        progress_week_end = _mix_color(progress_week, success, 0.18, fallback=progress_week)
        progress_week_text = _fill_text_color(
            _mix_color(progress_week, progress_week_end, 0.45, fallback=progress_week),
            fallback=text,
        )
        progress_overflow = error
        progress_overflow_highlight = _mix_color(error, "#ffffff", 0.55, fallback=error)
        progress_overflow_anchor = _mix_color(error, warning, 0.22, fallback=error)
        progress_overflow_anchor_edge = _mix_color(error, "#ffffff", 0.38, fallback=error)
        progress_overflow_badge = _mix_color(error, surface, 0.42 if normalized_variant == "dark" else 0.24, fallback=error)
        progress_overflow_badge_edge = _mix_color(error, "#ffffff", 0.18 if normalized_variant == "dark" else 0.32, fallback=error)
        progress_overflow_badge_text = _mix_color("#ffffff" if normalized_variant == "dark" else "#111111", error, 0.12 if normalized_variant == "dark" else 0.18, fallback=text)
        return cls(
            variant=normalized_variant,
            surface=surface,
            panel_surface=panel_surface,
            panel_border=panel_border,
            header_surface=header_surface,
            divider=divider,
            text=text,
            muted=muted,
            accent=accent,
            info=info,
            warning=warning,
            error=error,
            success=success,
            request_surface=request_surface,
            request_header_surface=request_header_surface,
            request_panel_surface=request_panel_surface,
            request_text=request_text,
            request_muted=request_muted,
            progress_track=progress_track,
            progress_track_border=progress_track_border,
            progress_track_text=progress_track_text,
            progress_cache=progress_cache,
            progress_cache_end=progress_cache_end,
            progress_cache_text=progress_cache_text,
            progress_day=progress_day,
            progress_day_end=progress_day_end,
            progress_day_text=progress_day_text,
            progress_week=progress_week,
            progress_week_end=progress_week_end,
            progress_week_text=progress_week_text,
            progress_overflow=progress_overflow,
            progress_overflow_highlight=progress_overflow_highlight,
            progress_overflow_anchor=progress_overflow_anchor,
            progress_overflow_anchor_edge=progress_overflow_anchor_edge,
            progress_overflow_badge=progress_overflow_badge,
            progress_overflow_badge_edge=progress_overflow_badge_edge,
            progress_overflow_badge_text=progress_overflow_badge_text,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "variant": self.variant,
            "surface": self.surface,
            "panelSurface": self.panel_surface,
            "panelBorder": self.panel_border,
            "headerSurface": self.header_surface,
            "divider": self.divider,
            "text": self.text,
            "muted": self.muted,
            "accent": self.accent,
            "info": self.info,
            "warning": self.warning,
            "error": self.error,
            "success": self.success,
            "requestSurface": self.request_surface,
            "requestHeaderSurface": self.request_header_surface,
            "requestPanelSurface": self.request_panel_surface,
            "requestText": self.request_text,
            "requestMuted": self.request_muted,
            "progressTrack": self.progress_track,
            "progressTrackBorder": self.progress_track_border,
            "progressTrackText": self.progress_track_text,
            "progressCache": self.progress_cache,
            "progressCacheEnd": self.progress_cache_end,
            "progressCacheText": self.progress_cache_text,
            "progressDay": self.progress_day,
            "progressDayEnd": self.progress_day_end,
            "progressDayText": self.progress_day_text,
            "progressWeek": self.progress_week,
            "progressWeekEnd": self.progress_week_end,
            "progressWeekText": self.progress_week_text,
            "progressOverflow": self.progress_overflow,
            "progressOverflowHighlight": self.progress_overflow_highlight,
            "progressOverflowAnchor": self.progress_overflow_anchor,
            "progressOverflowAnchorEdge": self.progress_overflow_anchor_edge,
            "progressOverflowBadge": self.progress_overflow_badge,
            "progressOverflowBadgeEdge": self.progress_overflow_badge_edge,
            "progressOverflowBadgeText": self.progress_overflow_badge_text,
        }


@dataclass(frozen=True)
class CodexThemeSnapshot:
    preference_variant: str
    effective_variant: str
    light_theme: CodexThemeExport
    dark_theme: CodexThemeExport
    effective_theme: CodexThemeExport
    source: str = ""
    css_theme: dict[str, str] = field(default_factory=dict)

    @property
    def hud_tokens(self) -> HudThemeTokens:
        return HudThemeTokens.from_theme(self.effective_theme, variant=self.effective_variant)

    def to_dict(self) -> dict[str, object]:
        return {
            "preferenceVariant": self.preference_variant,
            "effectiveVariant": self.effective_variant,
            "lightTheme": self.light_theme.to_dict(),
            "darkTheme": self.dark_theme.to_dict(),
            "effectiveTheme": self.effective_theme.to_dict(),
            "source": self.source,
            "cssTheme": dict(self.css_theme),
            "hudTokens": self.hud_tokens.to_dict(),
        }

    @classmethod
    def from_probe_result(
        cls,
        payload: Any,
        *,
        source: str = "cdp",
    ) -> "CodexThemeSnapshot | None":
        if not isinstance(payload, dict):
            return None
        preference_variant = str(payload.get("mode") or "system").strip().lower()
        css_theme = payload.get("cssTheme") if isinstance(payload.get("cssTheme"), dict) else {}
        light_code_theme_id = str(payload.get("lightCodeThemeId") or DEFAULT_CODE_THEME_ID).strip() or DEFAULT_CODE_THEME_ID
        dark_code_theme_id = str(payload.get("darkCodeThemeId") or DEFAULT_CODE_THEME_ID).strip() or DEFAULT_CODE_THEME_ID
        light_theme_payload = payload.get("lightTheme")
        dark_theme_payload = payload.get("darkTheme")
        light_theme = None
        dark_theme = None
        if isinstance(light_theme_payload, dict):
            light_theme = CodexThemeExport(
                code_theme_id=light_code_theme_id,
                theme=CodexThemeConfig.from_dict(light_theme_payload),
                variant="light",
            )
        if isinstance(dark_theme_payload, dict):
            dark_theme = CodexThemeExport(
                code_theme_id=dark_code_theme_id,
                theme=CodexThemeConfig.from_dict(dark_theme_payload),
                variant="dark",
            )
        css_surface = _normalize_hex(css_theme.get("surface"), "")
        css_ink = _normalize_hex(css_theme.get("ink"), "")
        css_variant = str(payload.get("effectiveVariant") or "").strip().lower()
        inferred_variant = (
            css_variant
            if css_variant in {"light", "dark"}
            else _infer_variant(css_surface or "#181818", css_ink or "#ffffff")
        )
        css_export = None
        if css_surface and css_ink:
            css_export = CodexThemeExport(
                code_theme_id=(
                    dark_code_theme_id if inferred_variant == "dark" else light_code_theme_id
                ),
                theme=CodexThemeConfig(
                    accent=_normalize_hex(css_theme.get("accent"), "#339cff"),
                    contrast=60 if inferred_variant == "dark" else 40,
                    ink=css_ink,
                    opaque_windows=False,
                    semantic_colors=CodexThemeSemanticColors(
                        diff_added=_normalize_hex(css_theme.get("diffAdded"), "#40c977"),
                        diff_removed=_normalize_hex(css_theme.get("diffRemoved"), "#fa423e"),
                        skill=_normalize_hex(css_theme.get("skill"), _normalize_hex(css_theme.get("accent"), "#339cff")),
                    ),
                    surface=css_surface,
                ),
                variant=inferred_variant,
            )
        if light_theme is None and css_export is not None and css_export.variant == "light":
            light_theme = css_export
        if dark_theme is None and css_export is not None and css_export.variant == "dark":
            dark_theme = css_export
        fallback_variant = inferred_variant
        if light_theme is None:
            light_theme = _fallback_snapshot(variant="light").light_theme
        if dark_theme is None:
            dark_theme = _fallback_snapshot(variant="dark").dark_theme
        effective_variant = _effective_variant(
            preference_variant,
            fallback_variant=fallback_variant,
        )
        if effective_variant == "light":
            effective_theme = light_theme
        else:
            effective_theme = dark_theme
        if css_export is not None and css_export.variant == effective_variant:
            effective_theme = css_export
        return cls(
            preference_variant=preference_variant if preference_variant in {"system", "light", "dark"} else "system",
            effective_variant=effective_variant,
            light_theme=light_theme,
            dark_theme=dark_theme,
            effective_theme=effective_theme,
            source=str(source or "cdp"),
            css_theme={
                str(key): _normalize_hex(value, "")
                for key, value in css_theme.items()
                if _normalize_hex(value, "")
            },
        )


def _fallback_snapshot(*, variant: str | None = None) -> CodexThemeSnapshot:
    use_light_theme = _windows_apps_use_light_theme()
    fallback_variant = "light" if use_light_theme else "dark"
    if use_light_theme is None:
        fallback_variant = "light" if variant == "light" else "dark"
    preference_variant = variant if variant in {"light", "dark"} else "system"
    effective_variant = _effective_variant(
        preference_variant,
        fallback_variant=fallback_variant,
    )
    light_theme = CodexThemeExport(
        code_theme_id=DEFAULT_CODE_THEME_ID,
        theme=CodexThemeConfig(
            accent="#339cff",
            contrast=40,
            fonts=CodexThemeFonts(),
            ink="#171717",
            opaque_windows=False,
            semantic_colors=CodexThemeSemanticColors(
                diff_added="#40c977",
                diff_removed="#fa423e",
                skill="#ad7bf9",
            ),
            surface="#f7f8fb",
        ),
        variant="light",
    )
    dark_theme = CodexThemeExport(
        code_theme_id=DEFAULT_CODE_THEME_ID,
        theme=CodexThemeConfig(
            accent="#339cff",
            contrast=60,
            fonts=CodexThemeFonts(),
            ink="#ffffff",
            opaque_windows=False,
            semantic_colors=CodexThemeSemanticColors(
                diff_added="#40c977",
                diff_removed="#fa423e",
                skill="#ad7bf9",
            ),
            surface="#181818",
        ),
        variant="dark",
    )
    return CodexThemeSnapshot(
        preference_variant=preference_variant,
        effective_variant=effective_variant,
        light_theme=light_theme,
        dark_theme=dark_theme,
        effective_theme=light_theme if effective_variant == "light" else dark_theme,
        source="fallback",
        css_theme={},
    )


class CodexThemeProbe:
    """Best-effort CDP probe for current Codex appearance settings."""

    def __init__(
        self,
        *,
        port: int | None = None,
        timeout_seconds: float = DEFAULT_CDP_TIMEOUT_SECONDS,
        cache_seconds: float = DEFAULT_CDP_CACHE_SECONDS,
        failure_cooldown_seconds: float = DEFAULT_CDP_FAILURE_COOLDOWN_SECONDS,
        enabled: bool | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        self.port = int(port or cdp_port_from_env(DEFAULT_CDP_PORT))
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.cache_seconds = max(0.0, float(cache_seconds))
        self.failure_cooldown_seconds = max(0.1, float(failure_cooldown_seconds))
        self.enabled = cdp_enabled_from_env() if enabled is None else bool(enabled)
        self.config_path = Path(config_path).expanduser() if config_path is not None else _default_codex_config_path()
        self.last_status = "idle" if self.enabled else "disabled"
        self.last_error = ""
        self._cache: CodexThemeSnapshot | None = None
        self._cache_at = 0.0
        self._failure_until = 0.0

    def snapshot(self, *, force: bool = False) -> CodexThemeSnapshot:
        now = time.monotonic()
        if not force and self._cache is not None and now - self._cache_at <= self.cache_seconds:
            self.last_status = "cache"
            return self._cache
        if not self.enabled:
            snapshot = _persisted_theme_snapshot(self.config_path)
            if snapshot is not None:
                self._cache = snapshot
                self._cache_at = now
                self.last_status = "persisted"
                return snapshot
            self.last_status = "disabled"
            return _fallback_snapshot()
        if not force and now < self._failure_until:
            self.last_status = "cooldown"
            if self._cache is not None:
                return self._cache
            snapshot = _persisted_theme_snapshot(self.config_path)
            if snapshot is not None:
                self._cache = snapshot
                self._cache_at = now
                self.last_status = "persisted"
                return snapshot
            return _fallback_snapshot()
        try:
            targets = list_targets(self.port, self.timeout_seconds)
            target = pick_page_target(targets)
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            result = send_cdp_command(
                websocket_url,
                "Runtime.evaluate",
                runtime_evaluate_params(THEME_PROBE_SCRIPT),
                self.timeout_seconds,
            )
            payload = result.get("result", {}).get("result", {}).get("value")
            snapshot = CodexThemeSnapshot.from_probe_result(payload, source="cdp")
            if snapshot is None:
                raise RuntimeError("Theme probe returned no value")
        except Exception as exc:
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._failure_until = now + self.failure_cooldown_seconds
            if self._cache is not None:
                return self._cache
            snapshot = _persisted_theme_snapshot(self.config_path)
            if snapshot is not None:
                self._cache = snapshot
                self._cache_at = time.monotonic()
                self.last_status = "persisted"
                return snapshot
            return _fallback_snapshot()
        self._cache = snapshot
        self._cache_at = time.monotonic()
        self._failure_until = 0.0
        self.last_status = "ok"
        self.last_error = ""
        return snapshot


__all__ = [
    "CODEX_THEME_SHARE_PREFIX",
    "CodexThemeConfig",
    "CodexThemeExport",
    "CodexThemeFonts",
    "CodexThemeProbe",
    "CodexThemeSemanticColors",
    "CodexThemeSnapshot",
    "HudThemeTokens",
    "THEME_PROBE_SCRIPT",
]
