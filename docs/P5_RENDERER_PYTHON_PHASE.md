# P5 Renderer Python Phase

- Date: 2026-07-31
- Baseline HEAD: `ddfe29dc72aee19eeeb3231f118157ad557fe16b`
- Scope: renderer payload/client/CDP ownership moves only

## Ownership

- `renderer_catalog.py`: model catalog normalization and boot script assembly.
- `renderer_payloads.py`: full/partial payload schema and domain selection.
- `renderer_presenters/`: session, budget, request, activity, and common
  presentation helpers.
- `renderer_payload_builder.py`: snapshot-to-full/light payload projection.
- `renderer_client.py`: install, update, bootstrap, probe, binding lifecycle,
  close cleanup, and one-shot renderer attach.
- `renderer_cdp/target.py`: subscribed page target state.
- `renderer_cdp/connection.py`: local websocket connect/send/receive primitives.
- `renderer_cdp/bindings.py`: persistent Runtime binding and callback transport.
- `ui/renderer_domains.py`: 191-line compatibility facade for payload/client
  imports and legacy patch points.
- `ui/renderer_hud.py`: module identity compatibility alias.

The client resolves legacy facade callables at invocation time. Existing tests
that replace `renderer_hud.send_cdp_command`, script installation, target
listing, catalog construction, payload building, or `_RendererBinding` still
exercise the replacement object after client construction.

## Structural Proof

- `ui/renderer_domains.py`: 191 lines, below the 200-line facade guardrail.
- `renderer_cdp/__init__.py`: 6 lines, below the 100-line facade guardrail.
- `renderer_cdp.py`: removed; the package is now the only `renderer_cdp` path.
- CDP transport owners contain no imports of runtime coordinator, payload,
  settings, or overlay owners.
- Payload apply ordering and binding install/cleanup inventory remain covered by
  the frozen Renderer contract.

## Verification

Passed:

```text
python -m pytest tests/test_renderer_hud.py tests/test_renderer_presenters.py tests/test_renderer_client.py tests/test_renderer_cdp.py tests/test_renderer_catalog.py tests/test_architecture.py tests/test_cdp_probe.py tests/test_connection_health.py -q
python -m pytest tests/test_active_session.py tests/test_ui.py -q
python -m compileall -q src tests tools
rtk ruff check <scoped renderer/client/CDP/architecture files>
git diff --check
python tools/check_facade_patch_inventory.py
```

The wheel proof used `python -m pip wheel . --no-deps --wheel-dir
tmp/p5-wheel-20260731` with the full build log redirected to
`tmp/p5-wheel-20260731/build.log`.

- Wheel: `codex_usage_hud-1.0.5-py3-none-any.whl`
- SHA-256: `c72bd61ebe6a33a5dbafcfa5fa3f385cf71ef6013b4b6c79611730b8a2c1858c`
- Wheel import proof resolved `renderer_cdp.__file__` to
  `codex_usage_hud/renderer_cdp/__init__.py` and resolved the client and payload
  builder to their new owners.

The full P0-P8 task is not complete. P6 JavaScript/CSS extraction, P7 startup/
daemon/CLI extraction, and P8 compatibility removal plus Windows/macOS,
PyInstaller, live idle, screenshot, and latency acceptance remain.
