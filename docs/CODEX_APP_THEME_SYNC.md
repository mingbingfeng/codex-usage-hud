# Codex App Theme Sync

`codex-usage-hud` can follow the active Codex App theme without modifying the
Codex installation. The implementation uses two local signals:

- Runtime theme probing from the live Codex renderer when CDP is available
- Static export of built-in theme modules from the packaged `app.asar`

This document describes what is supported today and how to inspect the theme
data yourself.

## What Works

- Detect the current Codex appearance mode from the running app
- Distinguish light and dark variants
- Parse and emit `codex-theme-v1:` share strings
- Apply the active theme to the renderer HUD
- Reuse the same theme tokens in the work overlay
- Export the built-in Codex theme catalog from the locally installed app

## Runtime Theme Source

The runtime probe lives in `src/codex_usage_hud/platforms/codex_theme.py`.
When Codex is running with CDP enabled, the HUD reads:

- `appearanceTheme`
- `appearanceLightChromeTheme`
- `appearanceDarkChromeTheme`
- `appearanceLightCodeThemeId`
- `appearanceDarkCodeThemeId`

The probe also reads the current CSS variables from the live document so the
HUD can follow the theme that is actually rendered, not just the last saved
setting.

Live renderer updates are event-driven. Codex applies appearance changes to
the document root by changing its `electron-light` / `electron-dark` class and
rewriting the inline `--codex-base-*` / `--color-*` theme variables. The HUD
uses a targeted `MutationObserver` for those root attributes, plus a
`matchMedia("(prefers-color-scheme: dark)")` listener for system-mode changes,
and pushes the resulting snapshot through a CDP runtime binding. There is no
recurring theme poll while the page is unchanged. The same renderer path is
used on Windows and macOS.

The `appearance*` local-storage keys remain a compatibility source for Codex
versions that expose them. They are not the primary live signal because a
same-document storage write does not emit a `storage` event and current Codex
versions may keep those keys elsewhere.

Renderer HUD is the primary sync path because renderer mode already requires
CDP. When CDP is unavailable, the probe now falls back to the persisted Codex
desktop settings in `CODEX_HOME/config.toml` and reads the saved:

- `appearanceTheme`
- `appearanceLightChromeTheme`
- `appearanceDarkChromeTheme`
- `appearanceLightCodeThemeId`
- `appearanceDarkCodeThemeId`

This gives Tk HUD and work overlay a stable light/dark-aware theme snapshot
even when the current live renderer session is not debuggable. Renderer mode
is still the only path that can follow unsaved live CSS changes.

## Share String Format

Codex App exposes a `Copy theme` button in Appearance settings. The copied
payload uses this prefix:

```text
codex-theme-v1:
```

Example:

```json
{
  "codeThemeId": "codex",
  "theme": {
    "accent": "#339cff",
    "contrast": 60,
    "fonts": {
      "code": null,
      "ui": null
    },
    "ink": "#ffffff",
    "opaqueWindows": false,
    "semanticColors": {
      "diffAdded": "#40c977",
      "diffRemoved": "#fa423e",
      "skill": "#ad7bf9"
    },
    "surface": "#181818"
  },
  "variant": "dark"
}
```

The HUD parses this payload directly and converts it into renderer, Tk, and
overlay color tokens.

## Export All Built-In Themes

Use the helper script below to inspect the built-in Codex theme catalog from
your local installation:

```powershell
python tools/export_codex_themes.py --output codex-themes.json
```

You can also point it at a specific package:

```powershell
python tools/export_codex_themes.py `
  --app-asar "C:\Program Files\WindowsApps\OpenAI.Codex_26.616.3767.0_x64__2p2nqsd0c76g0\app\resources\app.asar" `
  --output codex-themes.json
```

The exported JSON contains:

- `themeCount`: total exported presets
- `familyCount`: total theme families
- `families`: grouped presets by `codeThemeId` and light/dark variant
- `themes`: the flattened list with module path, share string, raw theme data,
  and derived HUD-ready chrome theme tokens

On the Windows package used during development for this feature, the exporter
found 60 presets across 28 families.

## Current Limitations

- There is no documented public Codex theme API for plugins or external tools.
- Full live CSS sync still depends on renderer access. Persisted fallback uses
  the last saved theme in `config.toml`, so unsaved in-session theme tweaks are
  not visible until Codex writes them back.
- The static exporter is based on the current Codex package layout. If OpenAI
  changes the bundled asset format, the exporter may need updates.

## Related Files

- `src/codex_usage_hud/platforms/codex_theme.py`
- `src/codex_usage_hud/ui/renderer_hud.py`
- `src/codex_usage_hud/ui/work_overlay_qt.py`
- `tools/export_codex_themes.py`
