"""Export built-in Codex App theme modules from app.asar.

This script statically parses the packaged webview theme asset modules and
emits their chrome theme payloads. It is intended for local inspection and HUD
theme sync experiments when no public Codex theme API is available.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import struct
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms.codex_theme import CodexThemeConfig, CodexThemeExport

THEME_MODULE_ROOT = "webview/assets"
THEME_FILE_RE = re.compile(r"^(?P<name>.+)-[A-Za-z0-9_-]{6,}\.js$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
KNOWN_CODE_THEME_FILE = ".vite/build/src-UHYOvFd-.js"
FALLBACK_KNOWN_CODE_THEME_IDS = [
    "absolutely",
    "ayu",
    "catppuccin",
    "codex",
    "dracula",
    "everforest",
    "github",
    "gruvbox",
    "linear",
    "lobster",
    "material",
    "matrix",
    "monokai",
    "night-owl",
    "nord",
    "notion",
    "one",
    "oscurange",
    "proof",
    "raycast",
    "rose-pine",
    "sentry",
    "solarized",
    "temple",
    "tokyo-night",
    "vercel",
    "xcode",
]
CODE_THEME_ID_ALIASES: dict[str, tuple[str, ...]] = {
    "vscode-plus": ("dark-plus", "light-plus"),
}


@dataclass(frozen=True)
class AsarEntry:
    path: str
    size: int
    offset: int


class AsarReader:
    def __init__(self, path: Path) -> None:
        self.path = path
        with path.open("rb") as handle:
            handle.read(4)
            handle.read(4)
            handle.read(4)
            header_json_size = struct.unpack("<I", handle.read(4))[0]
            self._header = json.loads(handle.read(header_json_size).decode("utf-8"))
        self._base_offset = 16 + header_json_size

    def walk_files(self) -> list[AsarEntry]:
        entries: list[AsarEntry] = []

        def walk(node: dict[str, Any], prefix: str = "") -> None:
            for name, entry in node.items():
                rel = f"{prefix}/{name}" if prefix else name
                if "files" in entry:
                    walk(entry["files"], rel)
                    continue
                if "offset" not in entry:
                    continue
                entries.append(
                    AsarEntry(
                        path=rel,
                        size=int(entry.get("size") or 0),
                        offset=self._base_offset + int(entry["offset"]),
                    )
                )

        walk(self._header["files"])
        return entries

    def read_text(self, rel_path: str) -> str:
        entry = self._entry_for(rel_path)
        with self.path.open("rb") as handle:
            handle.seek(entry.offset)
            return handle.read(entry.size).decode("utf-8", "replace")

    def _entry_for(self, rel_path: str) -> AsarEntry:
        node: dict[str, Any] = self._header["files"]
        for part in rel_path.split("/"):
            child = node[part]
            if "files" in child:
                node = child["files"]
                continue
            return AsarEntry(
                path=rel_path,
                size=int(child.get("size") or 0),
                offset=self._base_offset + int(child["offset"]),
            )
        raise FileNotFoundError(rel_path)


def _find_default_app_asar() -> Path:
    configured = [
        Path(r"C:\Program Files\WindowsApps"),
        Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps",
    ]
    candidates: list[Path] = []
    for root in configured:
        if not root.exists():
            continue
        candidates.extend(root.glob("OpenAI.Codex_*__2p2nqsd0c76g0/app/resources/app.asar"))
    if not candidates:
        raise FileNotFoundError("Unable to locate Codex app.asar automatically")
    return sorted(candidates)[-1]


def _extract_js_string(text: str, start: int) -> tuple[str, int]:
    quote = text[start]
    if quote not in {"`", "'", '"'}:
        raise ValueError("Expected string literal")
    index = start + 1
    result: list[str] = []
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            result.append(text[index + 1])
            index += 2
            continue
        if char == quote:
            return "".join(result), index + 1
        result.append(char)
        index += 1
    raise ValueError("Unterminated string literal")


def _extract_balanced_sequence(
    text: str,
    start: int,
    *,
    opening: str,
    closing: str,
) -> tuple[str, int]:
    if text[start] != opening:
        raise ValueError(f"Expected {opening} literal")
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char in {"`", "'", '"'}:
            _, index = _extract_js_string(text, index)
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start : index + 1], index + 1
        index += 1
    raise ValueError("Unterminated literal")


def _js_literal_to_python(js_literal: str) -> Any:
    normalized = js_literal
    normalized = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', normalized)
    normalized = normalized.replace("!0", "true").replace("!1", "false")
    normalized = normalized.replace("void 0", "null")

    def replace_backticks(match: re.Match[str]) -> str:
        body = match.group(1)
        return json.dumps(body)

    normalized = re.sub(r"`([^`\\]*(?:\\.[^`\\]*)*)`", replace_backticks, normalized)
    normalized = re.sub(r",\s*([}\]])", r"\1", normalized)
    return json.loads(normalized)


def _extract_export_map(module_text: str) -> dict[str, str]:
    matches = list(re.finditer(r"export\s*\{([^}]*)\};?", module_text))
    if not matches:
        return {}
    block = matches[-1].group(1)
    export_map: dict[str, str] = {}
    for variable_name, export_name in re.findall(
        r"([A-Za-z_$][\w$]*)\s+as\s+([A-Za-z_$][\w$]*)",
        block,
    ):
        export_map[export_name] = variable_name
    return export_map


def _extract_variable_assignment(module_text: str, variable_name: str) -> tuple[str, int] | None:
    match = re.search(rf"\b{re.escape(variable_name)}\s*=", module_text)
    if match is None:
        return None
    index = match.end()
    while index < len(module_text) and module_text[index].isspace():
        index += 1
    if index >= len(module_text):
        return None
    return module_text[index:], index


def _extract_parsed_assignment(module_text: str, variable_name: str) -> Any | None:
    assignment = _extract_variable_assignment(module_text, variable_name)
    if assignment is None:
        return None
    tail, index = assignment
    try:
        if tail.startswith("Object.freeze(JSON.parse(") or tail.startswith("JSON.parse("):
            string_start = module_text.find("`", index)
            if string_start < 0:
                string_start = module_text.find('"', index)
            if string_start < 0:
                string_start = module_text.find("'", index)
            if string_start < 0:
                return None
            raw_json, _ = _extract_js_string(module_text, string_start)
            return json.loads(raw_json)
        if tail.startswith("{"):
            object_text, _ = _extract_balanced_sequence(
                module_text,
                index,
                opening="{",
                closing="}",
            )
            return _js_literal_to_python(object_text)
        if tail.startswith("["):
            array_text, _ = _extract_balanced_sequence(
                module_text,
                index,
                opening="[",
                closing="]",
            )
            return _js_literal_to_python(array_text)
        if tail[0] in {"`", "'", '"'}:
            value, _ = _extract_js_string(module_text, index)
            return value
        if module_text[index] == "{":
            object_text, _ = _extract_balanced_sequence(
                module_text,
                index,
                opening="{",
                closing="}",
            )
            return _js_literal_to_python(object_text)
    except (ValueError, json.JSONDecodeError):
        return None
    return None


def _infer_variant(theme_payload: dict[str, Any], module_name: str) -> str:
    lowered = module_name.lower()
    if "-light" in lowered:
        return "light"
    if "-dark" in lowered:
        return "dark"
    surface = str(theme_payload.get("surface") or "").strip()
    ink = str(theme_payload.get("ink") or "").strip()
    if HEX_RE.fullmatch(surface) and HEX_RE.fullmatch(ink):
        surface_luma = _relative_luma(surface)
        ink_luma = _relative_luma(ink)
        return "dark" if surface_luma < ink_luma else "light"
    return "dark"


def _relative_luma(hex_color: str) -> float:
    def channel(value: str) -> float:
        normalized = int(value, 16) / 255.0
        if normalized <= 0.03928:
            return normalized / 12.92
        return ((normalized + 0.055) / 1.055) ** 2.4

    red = channel(hex_color[1:3])
    green = channel(hex_color[3:5])
    blue = channel(hex_color[5:7])
    return (red * 0.2126) + (green * 0.7152) + (blue * 0.0722)


def _logical_module_name(path: str) -> str:
    file_name = path.rsplit("/", 1)[-1]
    match = THEME_FILE_RE.match(file_name)
    return match.group("name") if match is not None else Path(file_name).stem


def _extract_known_code_theme_ids(reader: AsarReader) -> list[str]:
    try:
        text = reader.read_text(KNOWN_CODE_THEME_FILE)
    except FileNotFoundError:
        return list(FALLBACK_KNOWN_CODE_THEME_IDS)
    match = re.search(r"LS=\{([^}]+)\}", text)
    if match is None:
        return list(FALLBACK_KNOWN_CODE_THEME_IDS)
    values = re.findall(r"`([^`]+)`", match.group(1))
    return values or list(FALLBACK_KNOWN_CODE_THEME_IDS)


def _family_code_theme_id(module_name: str, known_ids: list[str]) -> str:
    lowered = module_name.lower()
    for candidate in sorted(known_ids, key=len, reverse=True):
        aliases = CODE_THEME_ID_ALIASES.get(candidate, ())
        prefixes = (candidate, *aliases)
        if any(lowered == prefix or lowered.startswith(f"{prefix}-") for prefix in prefixes):
            return candidate
    return "codex"


def _display_name_from_module(module_text: str, module_name: str) -> str:
    export_map = _extract_export_map(module_text)
    name_var = export_map.get("name") or export_map.get("displayName")
    if name_var:
        value = _extract_parsed_assignment(module_text, name_var)
        if isinstance(value, str) and value:
            return value
    prettified = module_name.replace("-", " ").strip()
    return " ".join(part.capitalize() for part in prettified.split())


def _is_theme_candidate(module_name: str, known_ids: list[str]) -> bool:
    lowered = module_name.lower()
    for candidate in known_ids:
        aliases = CODE_THEME_ID_ALIASES.get(candidate, ())
        prefixes = (candidate, *aliases)
        if any(lowered == prefix or lowered.startswith(f"{prefix}-") for prefix in prefixes):
            return True
    return False


def _raw_theme_from_exports(module_text: str) -> dict[str, Any]:
    export_map = _extract_export_map(module_text)
    if not export_map:
        return {}
    default_var = export_map.get("default")
    if default_var:
        default_value = _extract_parsed_assignment(module_text, default_var)
        if isinstance(default_value, dict):
            return default_value
    raw_theme: dict[str, Any] = {}
    for export_name in (
        "bg",
        "chromeTheme",
        "colors",
        "displayName",
        "fg",
        "name",
        "semanticTokenColors",
        "settings",
        "tokenColors",
        "type",
    ):
        variable_name = export_map.get(export_name)
        if not variable_name:
            continue
        value = _extract_parsed_assignment(module_text, variable_name)
        if value is None:
            continue
        raw_theme[export_name] = value
    return raw_theme


def _normalize_hex6(value: object) -> str:
    text = str(value or "").strip()
    if not text.startswith("#"):
        return ""
    lowered = text.lower()
    if len(lowered) == 4:
        return f"#{lowered[1] * 2}{lowered[2] * 2}{lowered[3] * 2}"
    if len(lowered) == 7:
        return lowered
    if len(lowered) == 9:
        return lowered[:7]
    return ""


def _pick_color(colors: dict[str, Any], *keys: str, fallback: str) -> str:
    for key in keys:
        candidate = _normalize_hex6(colors.get(key))
        if candidate:
            return candidate
    return fallback


def _derive_chrome_theme(
    raw_theme: dict[str, Any],
    module_name: str,
) -> tuple[CodexThemeConfig, str]:
    chrome_theme = raw_theme.get("chromeTheme")
    theme_type = str(raw_theme.get("type") or "").strip().lower()
    variant = theme_type if theme_type in {"light", "dark"} else _infer_variant(raw_theme, module_name)
    if isinstance(chrome_theme, dict):
        return CodexThemeConfig.from_dict(chrome_theme), variant

    colors = raw_theme.get("colors") if isinstance(raw_theme.get("colors"), dict) else {}
    accent = _pick_color(
        colors,
        "focusBorder",
        "button.background",
        "textLink.foreground",
        "activityBar.activeBorder",
        "activityBarBadge.background",
        fallback="#339cff",
    )
    surface = _pick_color(
        colors,
        "editor.background",
        "sideBar.background",
        "panel.background",
        "activityBar.background",
        fallback="#181818" if variant == "dark" else "#f7f8fb",
    )
    ink = _pick_color(
        colors,
        "editor.foreground",
        "foreground",
        "sideBarTitle.foreground",
        "sideBar.foreground",
        fallback="#ffffff" if variant == "dark" else "#171717",
    )
    diff_added = _pick_color(
        colors,
        "gitDecoration.addedResourceForeground",
        "gitDecoration.untrackedResourceForeground",
        "terminal.ansiGreen",
        "terminal.ansiBrightGreen",
        fallback="#40c977",
    )
    diff_removed = _pick_color(
        colors,
        "gitDecoration.deletedResourceForeground",
        "terminal.ansiRed",
        "terminal.ansiBrightRed",
        fallback="#fa423e",
    )
    skill = _pick_color(
        colors,
        "terminal.ansiMagenta",
        "terminal.ansiBrightMagenta",
        "textLink.foreground",
        fallback=accent,
    )
    return (
        CodexThemeConfig.from_dict(
            {
                "accent": accent,
                "contrast": 60 if variant == "dark" else 40,
                "fonts": {"code": None, "ui": None},
                "ink": ink,
                "opaqueWindows": False,
                "semanticColors": {
                    "diffAdded": diff_added,
                    "diffRemoved": diff_removed,
                    "skill": skill,
                },
                "surface": surface,
            }
        ),
        variant,
    )


def _serializable_raw_theme(raw_theme: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in (
        "bg",
        "chromeTheme",
        "colors",
        "displayName",
        "fg",
        "name",
        "semanticTokenColors",
        "settings",
        "tokenColors",
        "type",
    ):
        if key in raw_theme:
            payload[key] = raw_theme[key]
    return payload


def _extract_theme_record(
    *,
    path: str,
    module_text: str,
    known_ids: list[str],
) -> dict[str, Any] | None:
    logical_name = _logical_module_name(path)
    if not _is_theme_candidate(logical_name, known_ids):
        return None
    raw_theme = _raw_theme_from_exports(module_text)
    if not raw_theme:
        return None
    if not isinstance(raw_theme.get("colors"), dict) and not isinstance(raw_theme.get("chromeTheme"), dict):
        return None

    theme_config, variant = _derive_chrome_theme(raw_theme, logical_name)
    display_name = str(raw_theme.get("displayName") or raw_theme.get("name") or _display_name_from_module(module_text, logical_name))
    code_theme_id = _family_code_theme_id(logical_name, known_ids)
    export_payload = CodexThemeExport(
        code_theme_id=code_theme_id,
        theme=theme_config,
        variant=variant,
    )
    raw_theme_payload = _serializable_raw_theme(raw_theme)
    return {
        "modulePath": path,
        "moduleName": logical_name,
        "displayName": display_name,
        "variant": variant,
        "codeThemeId": code_theme_id,
        "sourceKind": "chromeTheme" if "chromeTheme" in raw_theme_payload else "vscodeTheme",
        "rawTheme": raw_theme_payload,
        "theme": export_payload.theme.to_dict(),
        "shareString": export_payload.to_share_string(),
    }


def _build_theme_families(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    families: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for item in records:
        family = str(item["codeThemeId"])
        variant = str(item["variant"])
        families.setdefault(family, {}).setdefault(variant, []).append({
            "displayName": item["displayName"],
            "moduleName": item["moduleName"],
            "modulePath": item["modulePath"],
            "sourceKind": item["sourceKind"],
            "theme": item["theme"],
            "shareString": item["shareString"],
        })

    payload: dict[str, dict[str, Any]] = {}
    for family, variants in sorted(families.items()):
        variant_payload: dict[str, list[dict[str, Any]]] = {}
        theme_count = 0
        for variant, items in sorted(variants.items()):
            ordered = sorted(
                items,
                key=lambda entry: (
                    str(entry["displayName"]).lower(),
                    str(entry["moduleName"]).lower(),
                ),
            )
            variant_payload[variant] = ordered
            theme_count += len(ordered)
        payload[family] = {
            "themeCount": theme_count,
            "variants": variant_payload,
        }
    return payload


def export_built_in_themes(app_asar: Path) -> dict[str, Any]:
    reader = AsarReader(app_asar)
    known_ids = _extract_known_code_theme_ids(reader)
    records: list[dict[str, Any]] = []
    for entry in reader.walk_files():
        if not entry.path.startswith(f"{THEME_MODULE_ROOT}/") or not entry.path.endswith(".js"):
            continue
        module_text = reader.read_text(entry.path)
        if "export{" not in module_text:
            continue
        record = _extract_theme_record(
            path=entry.path,
            module_text=module_text,
            known_ids=known_ids,
        )
        if record is not None:
            records.append(record)
    records.sort(key=lambda item: (str(item["moduleName"]), str(item["variant"])))
    families = _build_theme_families(records)
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "appAsar": str(app_asar),
        "familyCount": len(families),
        "themeCount": len(records),
        "families": families,
        "themes": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export built-in Codex App theme modules from app.asar.",
    )
    parser.add_argument(
        "--app-asar",
        default="",
        help="Optional explicit path to Codex app.asar.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON output path. Prints to stdout when omitted.",
    )
    args = parser.parse_args(argv)

    app_asar = Path(args.app_asar).expanduser() if args.app_asar else _find_default_app_asar()
    payload = export_built_in_themes(app_asar)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
