# Provider Registry Contracts

## Scenario: Provider-aware session accounting and renderer settings

### 1. Scope / Trigger

- Trigger: HUD aggregates Codex App and CLI JSONL sessions that may use different `model_provider` values.

### 2. Signatures

- `discover_provider_registry(user_config, config_path=None, sessions_root=None) -> ProviderRegistry`
- `UserConfig.effective_provider_scope(app_provider) -> frozenset[str] | None`
- `ParsedSession.model_provider` and `ParsedSession.client_kind` are the shared JSONL projections.

### 3. Contracts

- Billing, filtering, and per-provider prices use normalized `model_provider`; profile names are aliases only.
- The registry reads the shared Codex TOML parser, saved HUD settings, and only the most recent 30 days of `session_meta` records.
- The base TOML `model_provider` is the App fallback. A real `client_kind="app"` session replaces it; `cli` never does.
- Renderer settings receive `provider_registry` and `app_provider`; custom scope always includes the App provider.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Missing JSONL provider | Use visible `unknown`, never guess App/CLI provider. |
| Bad/missing TOML | Keep saved/history providers and no App fallback. |
| Old global config | Migrate once available providers are known; only App gets old weekly adjustment. |
| Price fetch failure | Preserve every provider's existing table and URL. |

### 5. Good/Base/Bad Cases

- Good: App `custom` and CLI `muyuan` have separate prices and one shared custom scope predicate.
- Base: no active App session uses the base TOML fallback.
- Bad: classifying by raw `source` in UI or treating a profile name as the pricing key.

### 6. Tests Required

- `tests/test_provider_registry.py`: TOML/profile/saved/history union and 30-day cutoff.
- `tests/test_parser.py`: App/CLI classification and provider-aware cost choice.
- `tests/test_config.py`, `tests/test_ui.py`, `tests/test_settings_bridge.py`: migration, forced App scope, settings save/fetch, and filtered summaries.

### 7. Wrong vs Correct

```python
# Wrong: profile name is not present in history and cannot be a billing key.
price_key = profile_name

# Correct: JSONL provider is the stable accounting key.
price_key = snapshot.model_provider
```
