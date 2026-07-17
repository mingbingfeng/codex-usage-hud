# Provider Registry Contracts

## Scenario: Provider-aware session accounting and renderer settings

### 1. Scope / Trigger

- Trigger: HUD aggregates Codex App and CLI JSONL sessions that may use different `model_provider` values.

### 2. Signatures

- `discover_provider_registry(user_config, config_path=None, sessions_root=None) -> ProviderRegistry`
- `UserConfig.effective_provider_scope(app_provider) -> frozenset[str] | None`
- `ParsedSession.model_provider` and `ParsedSession.client_kind` are the shared JSONL projections.
- Renderer draft boundary: `ensureSettingsProviderDraft(settings)`, `captureSettingsProviderForm()`, and `collectSettingsForm()`.

### 3. Contracts

- Billing, filtering, and per-provider prices use normalized `model_provider`; profile names are aliases only.
- The registry reads the shared Codex TOML parser, saved HUD settings, and only the most recent 30 days of `session_meta` records.
- The base TOML `model_provider` is the App fallback. A real `client_kind="app"` session replaces it; `cli` never does.
- Renderer settings receive `provider_registry` and `app_provider`; custom scope always includes the App provider.
- Renderer price tabs select the provider being edited. The two checkboxes below the active tab control whether that provider is included in statistics or only produces notification bubbles. They are mutually exclusive; the App provider remains included in statistics and disabled.
- `notification_only_providers` stores providers that produce active-work bubbles without contributing to usage, budgets, or weekly adjustments. The effective notification scope is the union of the statistics scope and this list.
- Tab switches capture the current provider into a modal-local draft. Save merges every provider draft in one command, while close with dirty drafts requires explicit discard confirmation.
- `provider_scope_mode` is `all` only when every known provider is enabled; otherwise it is `custom` with the enabled provider keys in `selected_providers`.
- Loading normalizes overlaps in favor of `selected_providers`, because inclusion in statistics already includes notification bubbles.
- Provider-specific edits update `provider_settings` only. Top-level legacy `model_prices`, `pricing_url`, and `weekly_adjustment_usd` remain unchanged so the last viewed tab cannot rewrite compatibility data.
- Legacy row-level `provider` / `base_url` values remain hidden round-trip metadata. Provider tabs are the visible pricing scope, so those fields must not reopen redundant advanced columns.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Missing JSONL provider | Use visible `unknown`, never guess App/CLI provider. |
| Bad/missing TOML | Keep saved/history providers and no App fallback. |
| Old global config | Migrate once available providers are known; only App gets old weekly adjustment. |
| Price fetch failure | Preserve every provider's existing table and URL. |
| Switch tabs with unsaved inputs | Capture the current provider, retain its values, and show a dirty marker on its tab. |
| Close with dirty provider drafts | Show the discard confirmation; do not silently drop drafts. |
| Save while a non-App tab is active | Merge all drafts and preserve the top-level legacy fields. |
| Provider is notification-only | Show its active-work bubble, but exclude its usage and adjustment from aggregates. |
| Price rows contain provider/base URL metadata | Keep values through save, but render only the five approved price columns. |

### 5. Good/Base/Bad Cases

- Good: App `custom` and CLI `muyuan` have separate prices and one shared custom scope predicate.
- Base: no active App session uses the base TOML fallback.
- Bad: classifying by raw `source` in UI, treating a profile name as the pricing key, or saving only the currently visible provider tab.

### 6. Tests Required

- `tests/test_provider_registry.py`: TOML/profile/saved/history union and 30-day cutoff.
- `tests/test_parser.py`: App/CLI classification and provider-aware cost choice.
- `tests/test_config.py`, `tests/test_ui.py`, `tests/test_settings_bridge.py`: migration, forced App scope, settings save/fetch, and filtered summaries.
- `tests/test_renderer_hud.py`: tabs replace the selector/list, modal drafts merge on save, App is required, legacy top-level fields stay unchanged, and old advanced columns stay hidden.
- Live renderer acceptance: verify desktop/narrow screenshots, cross-tab draft retention, dirty/discard behavior, and zero legacy selector/scope nodes.

### 7. Wrong vs Correct

```python
# Wrong: profile name is not present in history and cannot be a billing key.
price_key = profile_name

# Correct: JSONL provider is the stable accounting key.
price_key = snapshot.model_provider
```

```javascript
// Wrong: the last viewed tab becomes the only saved provider and rewrites legacy fields.
providerSettings[activeProvider] = collectVisibleRows();
settings.model_prices = providerSettings[activeProvider].model_prices;

// Correct: capture the visible form, merge all drafts, and preserve legacy fields.
captureSettingsProviderForm();
for (const provider of draft.order) {
  providerSettings[provider] = draft.providers[provider].settings;
}
settings.model_prices = originalSettings.model_prices;
```
