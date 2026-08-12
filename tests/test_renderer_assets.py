from __future__ import annotations

import hashlib

from codex_usage_hud.renderer_assets import manifest
from codex_usage_hud.renderer_assets import layout
from codex_usage_hud.renderer_assets.layout_anchors import TEXT as LAYOUT_ANCHORS
from codex_usage_hud.renderer_assets.layout_gestures import TEXT as LAYOUT_GESTURES
from codex_usage_hud.renderer_assets.layout_markup import TEXT as LAYOUT_MARKUP
from codex_usage_hud.renderer_assets.layout_observers import TEXT as LAYOUT_OBSERVERS
from codex_usage_hud.renderer_assets.layout_style import TEXT as LAYOUT_STYLE
from codex_usage_hud.renderer_assets.settings_shell import TEXT as SETTINGS_SHELL
from codex_usage_hud.renderer_assets.settings_support_panels import (
    TEXT as SETTINGS_SUPPORT_PANELS,
)
from codex_usage_hud.ui import renderer_script


def test_renderer_asset_manifest_is_ordered_and_byte_identical() -> None:
    assert manifest.P6_1_TEMPLATE_BYTE_LENGTH == 539347
    assert manifest.P6_1_TEMPLATE_SHA256 == (
        "be4417fa105f6809200bf626835f86a35e9b3b7b247f8fca4df84660ad2afbdf"
    )


def test_renderer_kernel_manifest_and_shared_contract_are_explicit() -> None:
    assert manifest.ASSET_ORDER[:10] == (
        "00_bootstrap",
        "00_kernel",
        "01_shared_head",
        "01_shared",
        "02_model_picker",
        "03_theme",
        "04_diagnostics",
        "05_budget",
        "06_rest_reminder",
        "07_session_view",
    )
    assert manifest.ASSET_ORDER == (
        "00_bootstrap",
        "00_kernel",
        "01_shared_head",
        "01_shared",
        "02_model_picker",
        "03_theme",
        "04_diagnostics",
        "05_budget",
        "06_rest_reminder",
        "07_session_view",
        "08_usage_insights",
        "09_session_cleanup",
        "10_background_usage",
        "11_settings_shell",
        "12_layout",
        "13_composer",
        "14_active_session",
        "15_router",
    )
    assert len(manifest.ASSETS) == 18
    assert all(source for _, source in manifest.ASSETS)
    assert renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE == manifest.RENDERER_HUD_SCRIPT_TEMPLATE

    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    assert len(script.encode("utf-8")) == manifest.P6_7_TEMPLATE_BYTE_LENGTH
    assert hashlib.sha256(script.encode("utf-8")).hexdigest() == (
        manifest.P6_7_TEMPLATE_SHA256
    )
    assert script.count("function createRendererContext()") == 1
    assert script.count("const ctx = createRendererContext();") == 1
    assert "registeredDomains = new Map()" in script
    assert "for (let index = teardownEntries.length - 1; index >= 0" in script
    assert 'ctx.lifecycle.listen("layout", window, "resize"' in script
    assert "const previousState = ctx.state.read();" in script
    assert "ctx.state.write({" in script
    assert "ctx.teardown.run();" in script
    assert 'ctx.lifecycle.listen("model_picker", node' not in script
    assert "function createModelPickerDomain(ctx, shared)" in script
    assert 'ctx.domains.register(\n    "model_picker",' in script
    assert "return { install, apply, dispose };" in script
    assert "modelPickerDomain.install();" in script
    assert "modelPickerDomain.apply();" in script
    assert "node[syntheticSelect] = (event) =>" in script
    assert 'ctx.lifecycle.listen("model_picker", document' in script
    assert "removeSyntheticItems();" in script
    assert "const releaseAbort = ctx.teardown.add(" in script
    assert "if (!ctx.lifecycle.active()) return;" in script
    for domain in ("theme", "diagnostics", "budget", "rest_reminder", "session_view"):
        assert f'ctx.domains.register(\n    "{domain}",' in script
        assert f"function create{''.join(part.title() for part in domain.split('_'))}Domain(ctx, shared)" in script
    assert script.count("function install()") >= 6
    assert script.count("function apply(root, payload)") >= 4
    assert script.count("function dispose()") >= 6
    assert "themeDomain.apply(root, payload || {});" in script
    assert "budgetDomain.apply(root, { ...(payload || {}), ...(domains.budget || {}) });" in script
    assert "restReminderDomain.apply(root, payload || {});" in script
    assert "sessionViewDomain.apply(root" in script
    assert "diagnosticsDomain.apply(root" in script
    assert script.index('"model_picker"') < script.index('"theme"') < script.index('"diagnostics"') < script.index('"budget"') < script.index('"rest_reminder"') < script.index('"session_view"')
    for domain in ("usage_insights", "session_cleanup", "background_usage", "settings_shell"):
        assert f'ctx.domains.register(\n    "{domain}",' in script
    for domain in ("layout", "composer"):
        assert f'ctx.domains.register(\n    "{domain}",' in script
    assert 'ctx.domains.register(\n    "active_session",' in script


def test_layout_static_fragments_join_without_changing_manifest_asset() -> None:
    fragments = (
        LAYOUT_STYLE,
        LAYOUT_MARKUP,
        LAYOUT_GESTURES,
        LAYOUT_ANCHORS,
        LAYOUT_OBSERVERS,
    )

    assert layout.TEXT == "".join(fragments)
    assert manifest.ASSETS[14] == ("12_layout", layout.TEXT)
    assert LAYOUT_STYLE.startswith("\n  function createLayoutDomain(ctx, shared) {")
    assert LAYOUT_STYLE.rstrip().endswith(
        "document.documentElement.appendChild(style);\n      }"
    )
    assert LAYOUT_MARKUP.startswith("\n      function resizeEdgesMarkup()")
    assert LAYOUT_GESTURES.startswith("\n      function beginGesture(")
    assert LAYOUT_ANCHORS.startswith("\n      function minWidthFor(")
    assert LAYOUT_OBSERVERS.startswith("\n      function syncPosition(")
    assert LAYOUT_OBSERVERS.rstrip().endswith("} = layoutDomain;")


def test_settings_support_panels_are_a_static_subdomain_fragment() -> None:
    assert "function supportPanelHtml(settings, path)" in SETTINGS_SUPPORT_PANELS
    assert "function aboutPanelHtml(path)" in SETTINGS_SUPPORT_PANELS
    assert "ctx.domains.register" not in SETTINGS_SUPPORT_PANELS
    assert "ctx.bindings" not in SETTINGS_SUPPORT_PANELS
    assert "ctx.lifecycle" not in SETTINGS_SUPPORT_PANELS
    assert SETTINGS_SHELL.count("function supportPanelHtml(settings, path)") == 1
    assert SETTINGS_SHELL.count("function aboutPanelHtml(path)") == 1
    assert SETTINGS_SHELL.index("function supportPanelHtml") < SETTINGS_SHELL.index(
        "function setSettingsStatus"
    )


def test_codex_cli_dialog_is_compact_and_persists_profile_scoped_launches() -> None:
    form_start = SETTINGS_SHELL.index("function codexCliFormHtml()")
    form_end = SETTINGS_SHELL.index("function renderCodexCliDialog()", form_start)
    form = SETTINGS_SHELL[form_start:form_end]
    launch_start = SETTINGS_SHELL.index("function launchCodexCliFromDialog()")
    launch_end = SETTINGS_SHELL.index("function applyCodexCliCommandStatus", launch_start)
    launch = SETTINGS_SHELL[launch_start:launch_end]

    assert "codexCliLaunchStorageKey" in renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    assert "function codexCliLaunchStateKey()" in SETTINGS_SHELL
    assert "profile:" in SETTINGS_SHELL
    assert "|provider:" in SETTINGS_SHELL
    assert "applyCodexCliPersistedLaunchState(codexCliPersistedLaunchState());" in SETTINGS_SHELL
    assert "codexCliState.useProxy = false;" in SETTINGS_SHELL
    assert 'data-codex-cli-proxy-port="true"' in form
    assert 'proxyPort.hidden = codexCliState.useProxy !== true;' in SETTINGS_SHELL
    assert "function codexCliLaunchTitle" in SETTINGS_SHELL
    assert 'return ["启动 Codex", ...args].join(" ");' in SETTINGS_SHELL
    assert '<strong>${escapeHtml(codexCliLaunchTitle())}</strong>' in SETTINGS_SHELL
    assert 'data-codex-cli-field="resume"' in form
    assert form.index('data-codex-cli-field="resume"') < form.index("启动终端")
    assert "codex-usage-hud-codex-cli-check codex-usage-hud-codex-cli-wide" not in form
    assert "codexCliPersistLaunchState(command);" in launch
    assert launch.index("codexCliPersistLaunchState(command);") < launch.index(
        "submitSettingsCommand("
    )
    assert "codex-usage-hud-codex-cli-proxy {" in LAYOUT_STYLE
    assert "width: 76px;" in LAYOUT_STYLE


def test_renderer_reinject_captures_active_session_sequences_before_remove() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    selection_capture = script.index("const previousActiveSessionSelectionSeq")
    applied_capture = script.index("const previousActiveSessionAppliedSeq")
    previous_remove = script.index(
        'window.__codexUsageHudRemove({ preserveState: true });'
    )

    assert selection_capture < applied_capture < previous_remove
    assert "__codexUsageHudRemove" not in script[:selection_capture]


def test_active_session_container_prefers_thread_role_list() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    container_start = script.index("function activeSessionContainer()")
    container_end = script.index("function postActiveSession", container_start)
    container = script[container_start:container_end]

    assert 'row?.closest?.("[role=\'list\']")' in container
    assert 'document.querySelector("[role=\'list\']")' in container
    assert container.index('row?.closest?.("[role=\'list\']")') < container.index(
        'row?.closest?.("aside, nav'
    )
    assert container.index('document.querySelector("[role=\'list\']")') < container.index(
        'document.querySelector("aside, nav'
    )


def test_active_session_guards_header_title_transition_after_canonical_id() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    guard_start = script.index("const headerTitleTransition = (")
    guard_end = script.index("const selectionKey =", guard_start)
    guard = script[guard_start:guard_end]

    assert "!canonicalSessionId" in guard
    assert "!newSession" in guard
    assert "!pendingSession" in guard
    assert 'ref.matchedBy === "header-title"' in guard
    assert "reason !== \"click\"" in guard
    assert "lastCanonicalSessionId" in guard
    assert "Date.now() - lastCanonicalAt < 2500" in guard
    assert guard.index("headerTitleTransition") < guard.index(
        "const transientWithoutCanonicalId"
    )


def test_active_session_selection_identity_prefers_canonical_id() -> None:
    script = renderer_script._RENDERER_HUD_SCRIPT_TEMPLATE
    selection_start = script.index("const selectionKey =")
    selection_end = script.index("const selectionSeq =", selection_start)
    selection = script[selection_start:selection_end]

    assert "canonicalSessionId" in selection
    assert "JSON.stringify([canonicalSessionId, newSession, pendingSession])" in selection
    assert "rawRendererSessionId" in selection
    assert "ref.title" in selection


def test_renderer_script_is_now_only_a_small_facade() -> None:
    source_lines = open(renderer_script.__file__, encoding="utf-8").read().splitlines()
    assert len(source_lines) <= 20
