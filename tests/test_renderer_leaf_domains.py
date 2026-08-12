from __future__ import annotations

import subprocess
from pathlib import Path

from codex_usage_hud.renderer_assets import manifest
from codex_usage_hud.renderer_assets.budget import TEXT as BUDGET
from codex_usage_hud.renderer_assets.active_session import TEXT as ACTIVE_SESSION
from codex_usage_hud.renderer_assets.background_usage import TEXT as BACKGROUND_USAGE
from codex_usage_hud.renderer_assets.diagnostics import TEXT as DIAGNOSTICS
from codex_usage_hud.renderer_assets.kernel import TEXT as KERNEL
from codex_usage_hud.renderer_assets.layout import TEXT as LAYOUT
from codex_usage_hud.renderer_assets.model_picker import TEXT as MODEL_PICKER
from codex_usage_hud.renderer_assets.composer import TEXT as COMPOSER
from codex_usage_hud.renderer_assets.rest_reminder import TEXT as REST_REMINDER
from codex_usage_hud.renderer_assets.session_view import TEXT as SESSION_VIEW
from codex_usage_hud.renderer_assets.shared import TEXT as SHARED
from codex_usage_hud.renderer_assets.theme import TEXT as THEME


DOMAIN_SOURCES = (
    ("model_picker", "ModelPicker", MODEL_PICKER),
    ("theme", "Theme", THEME),
    ("diagnostics", "Diagnostics", DIAGNOSTICS),
    ("budget", "Budget", BUDGET),
    ("rest_reminder", "RestReminder", REST_REMINDER),
    ("session_view", "SessionView", SESSION_VIEW),
)

P6_5_DOMAIN_SOURCES = (
    ("layout", "Layout", LAYOUT),
    ("composer", "Composer", COMPOSER),
)


def test_leaf_domain_owners_are_registered_in_the_fixed_order() -> None:
    assert manifest.ASSET_ORDER[4:10] == (
        "02_model_picker",
        "03_theme",
        "04_diagnostics",
        "05_budget",
        "06_rest_reminder",
        "07_session_view",
    )

    for name, factory, source in DOMAIN_SOURCES:
        assert f"function create{factory}Domain(ctx, shared)" in source
        assert f'ctx.domains.register(\n    "{name}",' in source
        assert "function install()" in source
        assert "function apply(" in source
        assert "function dispose()" in source


def test_layout_and_composer_owners_are_registered_after_settings_shell() -> None:
    assert manifest.ASSET_ORDER[14:16] == ("12_layout", "13_composer")
    for name, factory, source in P6_5_DOMAIN_SOURCES:
        assert f"function create{factory}Domain(ctx, shared)" in source
        assert f'ctx.domains.register(\n    "{name}",' in source
        assert "function install()" in source
        assert "function apply(" in source
        assert "function dispose()" in source

    assert "function ensureStyle()" not in manifest.ROUTER
    assert "function beginGesture(" not in manifest.ROUTER
    assert "function composerElement()" not in manifest.ROUTER
    assert "function ensureComposerInputWatchers()" not in manifest.ROUTER


def test_active_session_owner_keeps_sequence_capture_before_reinjection_remove() -> None:
    assert manifest.ASSET_ORDER[16:17] == ("14_active_session",)
    assert "function createActiveSessionDomain(ctx, shared)" in ACTIVE_SESSION
    assert 'ctx.domains.register(\n    "active_session",' in ACTIVE_SESSION
    assert "function ensureActiveSessionWatchers()" in ACTIVE_SESSION
    assert "function cacheActiveSessionPayload(payload)" in ACTIVE_SESSION
    assert "window.__codexUsageHudReportActiveSession" in ACTIVE_SESSION
    assert "function ensureActiveSessionWatchers()" not in manifest.ROUTER
    assert "function cacheActiveSessionPayload(payload)" not in manifest.ROUTER


def test_active_session_sequence_uses_canonical_identity_for_live_updates() -> None:
    active_session_factory = ACTIVE_SESSION.split(
        "  const activeSessionDomain = ctx.domains.register(",
        1,
    )[0]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{}};
global.location = {{
  href: "app://-/index.html",
  pathname: "/",
  search: "",
  hash: "",
}};
const rootId = "codex-usage-hud-root";
const activeSessionCanonicalIdName = "__canonical";
const activeSessionCanonicalAtName = "__canonicalAt";
const activeSessionSettledTimerName = "__settled";
const activeSessionSelectionKeyName = "__selectionKey";
const activeSessionSelectionSeqName = "__selectionSeq";
const activeSessionAppliedSeqName = "__appliedSeq";
const activeSessionLastSignatureName = "__signature";
const activeSessionBindingName = "codexUsageHudActiveSession";
const normalize = (value) => String(value || "").replace(/\\s+/g, " ").trim();
const settingsBridgeUrl = () => "";
const sent = [];
const ctx = {{
  bindings: {{
    available: () => true,
    send: (_name, payload) => {{ sent.push(payload); return true; }},
  }},
  lifecycle: {{
    clearTimeout: () => {{}},
    timeout: () => 1,
    active: () => true,
  }},
}};
const shared = {{}};
{active_session_factory}
const domain = createActiveSessionDomain(ctx, shared);

function postAndAck(ref, reason = "event") {{
  const before = sent.length;
  domain.postActiveSession(reason, ref);
  assert.equal(sent.length, before + 1);
  const payload = sent[sent.length - 1];
  window[activeSessionAppliedSeqName] = payload.selectionSeq;
  return payload;
}}

const first = postAndAck({{
  sessionId: "thread-a",
  rendererSessionId: "local:thread-a",
  title: "First title",
  url: location.href,
  matchedBy: "sidebar-row",
}}, "click");
assert.equal(first.selectionSeq, 1);

const titleUpdate = postAndAck({{
  sessionId: "thread-a",
  rendererSessionId: "local:thread-a",
  title: "Updated title",
  url: location.href,
  matchedBy: "sidebar-row",
}});
assert.equal(titleUpdate.selectionSeq, 1);
assert.equal(titleUpdate.title, "Updated title");

const rawIdUpdate = postAndAck({{
  sessionId: "thread-a",
  rendererSessionId: "thread-a",
  title: "Updated title",
  url: location.href,
  matchedBy: "sidebar-row",
}});
assert.equal(rawIdUpdate.selectionSeq, 1);

const differentThread = postAndAck({{
  sessionId: "thread-b",
  rendererSessionId: "local:thread-b",
  title: "Other thread",
  url: location.href,
  matchedBy: "sidebar-row",
}});
assert.equal(differentThread.selectionSeq, 2);

const provisional = postAndAck({{
  sessionId: "",
  rendererSessionId: "local:client-new-thread:pending-a",
  title: "Pending thread",
  url: location.href,
  pendingSession: true,
  matchedBy: "sidebar-row",
}}, "click");
assert.equal(provisional.selectionSeq, 3);

const provisionalTitleUpdate = postAndAck({{
  sessionId: "",
  rendererSessionId: "local:client-new-thread:pending-a",
  title: "Pending thread renamed",
  url: location.href,
  pendingSession: true,
  matchedBy: "sidebar-row",
}});
assert.equal(provisionalTitleUpdate.selectionSeq, 4);

const titleOnly = postAndAck({{
  sessionId: "",
  rendererSessionId: "",
  title: "Header-only thread",
  url: location.href,
  matchedBy: "header-title",
}}, "click");
assert.equal(titleOnly.selectionSeq, 5);

const titleOnlyUpdate = postAndAck({{
  sessionId: "",
  rendererSessionId: "",
  title: "Header-only thread updated",
  url: location.href,
  matchedBy: "header-title",
}});
assert.equal(titleOnlyUpdate.selectionSeq, 6);

const newSession = postAndAck({{
  sessionId: "",
  rendererSessionId: "",
  title: "",
  url: location.href,
  newSession: true,
}}, "click");
assert.equal(newSession.selectionSeq, 7);

const nextCanonicalThread = postAndAck({{
  sessionId: "thread-c",
  rendererSessionId: "local:thread-c",
  title: "Canonical thread",
  url: location.href,
  matchedBy: "sidebar-row",
}});
assert.equal(nextCanonicalThread.selectionSeq, 8);
console.log("active-session-canonical-identity-ok");
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "active-session-canonical-identity-ok" in completed.stdout


def test_background_usage_range_change_bypasses_same_revision_cache() -> None:
    background_usage_factory = BACKGROUND_USAGE.split(
        "  const backgroundUsageDomain = ctx.domains.register(", 1
    )[0]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{}};
global.document = {{ getElementById: () => null }};
global.location = {{ href: "app://-/index.html" }};
const settingsCommandBindingName = "codexUsageHudSettings";
const settingsModalId = "codex-usage-hud-settings";
let settingsActiveTab = "backgroundUsage";
const currentPayload = () => ({{
  backgroundUsageRevision: 7,
  backgroundUsageBridgeUrl: "",
}});
let backgroundUsageFetchSeq = 0;
let backgroundUsageDetailSeq = 0;
let backgroundUsageQueryTimeoutId = 0;
let backgroundUsageDetailTimeoutId = 0;
const backgroundUsageRequestTimeoutMs = 5000;
const backgroundUsageBodyScrollTops = new Map();
const backgroundUsageHistoryScrollTops = new Map();
const backgroundUsageDetailScrollTops = new Map();
const backgroundUsageState = {{
  range: "7d",
  feature: "",
  model: "",
  selectedEventId: "",
  selectedSessionId: "",
  data: {{ summary: {{}} }},
  detail: null,
  loading: false,
  detailLoading: false,
  error: "",
  loadedRevision: 7,
  loadedFilterKey: JSON.stringify(["today", "", ""]),
  promptExpanded: false,
  queryRequestId: "",
  queryFilterKey: "",
  detailRequestId: "",
}};
const sent = [];
const ctx = {{
  bindings: {{
    available: () => true,
    send: (_name, payload) => {{ sent.push(payload); return true; }},
  }},
  lifecycle: {{
    active: () => true,
    clearTimeout: () => {{}},
    timeout: () => 1,
    frame: () => 1,
  }},
  teardown: {{ add: () => () => {{}} }},
}};
const shared = {{}};
{background_usage_factory}
const domain = createBackgroundUsageDomain(ctx, shared);

(async () => {{
  await domain.loadBackgroundUsage();
  assert.equal(sent.length, 1);
  assert.equal(backgroundUsageState.loading, true);
  assert.equal(
    backgroundUsageState.queryFilterKey,
    JSON.stringify(["7d", "", ""]),
  );
  assert.equal(sent[0].action, "backgroundUsageQuery");
  assert.deepEqual(sent[0].filters, {{
    range: "7d",
    feature: "",
    model: "",
    eventId: "",
  }});
  console.log("background-usage-filter-cache-ok");
}})().catch((error) => {{
  console.error(error.stack || error);
  process.exitCode = 1;
}});
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "background-usage-filter-cache-ok" in completed.stdout


def test_background_usage_policy_action_is_exported_and_sends_command() -> None:
    background_usage_factory = BACKGROUND_USAGE.split(
        "  const backgroundUsageDomain = ctx.domains.register(", 1
    )[0]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{ location: {{ href: "app://-/index.html" }} }};
global.document = {{ getElementById: () => null }};
global.location = window.location;
const settingsCommandBindingName = "codexUsageHudSettingsCommand";
const settingsModalId = "codex-usage-hud-settings";
const backgroundUsageRequestTimeoutMs = 5000;
const backgroundUsageState = {{
  policy: {{ desiredState: "enabled", policyRevision: 3 }},
  policyPending: false,
  policyError: "",
  detail: {{ featureKey: "memory_consolidation", eventId: "event-1" }},
}};
const sent = [];
const ctx = {{
  bindings: {{
    available: () => true,
    send: (name, payload) => sent.push({{ name, payload }}),
  }},
  lifecycle: {{ active: () => true, clearTimeout: () => {{}}, timeout: () => 1 }},
}};
const shared = {{}};
{background_usage_factory}
const domain = createBackgroundUsageDomain(ctx, shared);
assert.equal(typeof domain.backgroundUsagePolicyConfirm, "function");
assert.equal(typeof domain.applyBackgroundUsagePolicy, "function");
domain.applyBackgroundUsagePolicy(
  "memory_consolidation", "event-1", "disabled",
);
assert.equal(sent.length, 1);
assert.equal(sent[0].name, settingsCommandBindingName);
assert.equal(sent[0].payload.action, "backgroundUsagePolicySet");
assert.equal(sent[0].payload.featureKey, "memory_consolidation");
assert.equal(sent[0].payload.desiredState, "disabled");
assert.equal(sent[0].payload.expectedPolicyRevision, 3);
assert.ok(backgroundUsageState.policyRequestId);
console.log("background-usage-policy-command-ok");
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "background-usage-policy-command-ok" in completed.stdout


def test_background_usage_policy_target_and_copy_keep_enable_direction() -> None:
    background_usage_factory = BACKGROUND_USAGE.split(
        "  const backgroundUsageDomain = ctx.domains.register(", 1
    )[0]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{ location: {{ href: "app://-/index.html" }} }};
global.document = {{ getElementById: () => null }};
global.location = window.location;
const settingsCommandBindingName = "codexUsageHudSettingsCommand";
const settingsModalId = "codex-usage-hud-settings";
const backgroundUsageState = {{ policy: null, policyPending: false, policyError: "" }};
const ctx = {{
  bindings: {{ available: () => false, send: () => true }},
  lifecycle: {{ active: () => true }},
}};
const shared = {{}};
{background_usage_factory}
const domain = createBackgroundUsageDomain(ctx, shared);

const enablePolicy = {{
  desiredState: "enabled",
  effectiveState: "disabled",
  verificationState: "requires_user_action",
}};
assert.equal(domain.backgroundUsagePolicyTargetState(enablePolicy), "enabled");
assert.equal(
  domain.backgroundUsagePolicyCopy(
    {{ featureKey: "memory_consolidation", featureLabel: "记忆整理" }},
    domain.backgroundUsagePolicyTargetState(enablePolicy),
  ).title,
  "启用“记忆整理”",
);

const disablePolicy = {{
  desiredState: "disabled",
  effectiveState: "enabled",
  verificationState: "requires_user_action",
}};
assert.equal(domain.backgroundUsagePolicyTargetState(disablePolicy), "disabled");
assert.equal(
  domain.backgroundUsagePolicyCopy(
    {{ featureKey: "memory_consolidation", featureLabel: "记忆整理" }},
    domain.backgroundUsagePolicyTargetState(disablePolicy),
).title,
  "禁用“记忆整理”",
);

assert.equal(
  domain.backgroundUsagePolicyTargetState({{
    desiredState: "disabled",
    effectiveState: "enabled",
    verificationState: "failed",
  }}),
  "disabled",
);
assert.equal(
  domain.backgroundUsagePolicyTargetState({{
    desiredState: "enabled",
    effectiveState: "disabled",
    verificationState: "failed",
  }}),
  "enabled",
);
console.log("background-usage-policy-direction-ok");
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "background-usage-policy-direction-ok" in completed.stdout


def test_background_usage_restart_confirmation_is_explicit_and_unverified_is_not_disabled() -> None:
    assert "backgroundUsagePolicyRestartConfirm" in BACKGROUND_USAGE
    assert "重启 Codex 以完成禁用" in BACKGROUND_USAGE
    assert "重启 Codex 以完成启用" in BACKGROUND_USAGE
    assert ">立即重启</button>" in BACKGROUND_USAGE
    assert 'data-event-id="${escapeHtml(String(detail?.eventId || ""))}"' in BACKGROUND_USAGE
    assert 'data-event-id="${escapeHtml(String(policy?.desiredState || "disabled"))}"' not in BACKGROUND_USAGE
    assert 'disabled aria-disabled="true"' in BACKGROUND_USAGE
    assert ".codex-usage-hud-settings-confirm-card > .codex-usage-hud-background-policy-message" in LAYOUT
    assert "margin-inline: 18px;" in LAYOUT
    assert 'verification === "verified"' in BACKGROUND_USAGE
    assert '["verified", "configured_unverified"].includes(verification)' not in BACKGROUND_USAGE


def test_router_is_registry_first_and_no_continuous_legacy_asset_remains() -> None:
    assert manifest.ASSET_ORDER[-1] == "15_router"
    assert not Path(manifest.__file__).with_name("legacy.py").exists()
    for index in range(2, 9):
        assert not Path(manifest.__file__).with_name(
            f"fragment_{index:02d}.py"
        ).exists()
    assert not Path(manifest.__file__).with_name("shared_head.py").exists()
    router = manifest.ROUTER
    for name in (
        "usageInsightsDomain",
        "sessionCleanupDomain",
        "backgroundUsageDomain",
        "settingsShellDomain",
        "layoutDomain",
        "composerDomain",
        "activeSessionDomain",
    ):
        assert f"{name}.install();" in router
    assert "settingsShellDomain.apply(root" in router
    assert "backgroundUsageDomain.apply(root" in router
    assert "usageInsightsDomain.apply(root" in router
    assert "sessionCleanupDomain.apply(root" in router

def test_payload_router_keeps_the_frozen_domain_order() -> None:
    script = manifest.RENDERER_HUD_SCRIPT_TEMPLATE
    router = script.split("function applyPayloadDomains", 1)[1].split(
        "window.__codexUsageHudUpdate",
        1,
    )[0]
    expected = (
        'if ("currentSession" in domains)',
        'if ("sessionSwitch" in domains)',
        'if ("budget" in domains)',
        'if ("settings" in domains)',
        'if ("overlay" in domains)',
        'if ("backgroundUsage" in domains)',
        'if ("diagnostics" in domains)',
        'if ("usageInsights" in domains)',
        'if ("sessionCleanup" in domains)',
    )
    positions = [router.index(marker) for marker in expected]
    assert positions == sorted(positions)
    assert "sessionViewDomain.apply" in router
    assert "budgetDomain.apply" in router
    assert "diagnosticsDomain.apply" in router


def test_leaf_domain_registry_contract_executes_in_node() -> None:
    domain_sources = "\n".join(source for _, _, source in DOMAIN_SOURCES)
    expected_names = [name for name, _, _ in DOMAIN_SOURCES]
    script = f"""
const assert = require("node:assert/strict");
global.window = {{}};
const stateName = "__state";
const normalize = (value) => String(value || "").trim();
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const px = (value) => `${{Math.round(value)}}px`;
const visible = () => true;
const cssEscape = String;
const codexModelPickerCatalog = [];
{KERNEL}
{SHARED}
{domain_sources}
assert.deepEqual(ctx.domains.names(), {expected_names!r});
for (const name of ctx.domains.names()) {{
  const domain = ctx.domains.get(name);
  assert.equal(typeof domain.install, "function");
  assert.equal(typeof domain.apply, "function");
  assert.equal(typeof domain.dispose, "function");
}}
console.log("renderer-leaf-domains-ok");
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs"],
        input=script,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "renderer-leaf-domains-ok" in completed.stdout


def test_leaf_bundle_keeps_one_iife_and_one_boot_placeholder() -> None:
    script = manifest.RENDERER_HUD_SCRIPT_TEMPLATE
    assert script.lstrip().startswith("(() => {")
    assert script.count('const version = "52";') == 1
    assert script.count("__CODEX_MODEL_PICKER_CATALOG__") == 1
    assert script.rstrip().endswith("})()")
