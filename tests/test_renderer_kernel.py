from __future__ import annotations

import subprocess

from codex_usage_hud.renderer_assets.kernel import TEXT


def test_renderer_kernel_tracks_shared_resources_and_tears_down_in_reverse_order() -> None:
    script = f"""
const assert = require("node:assert/strict");
global.window = {{}};
let nextId = 1;
const frames = new Map();
const timeouts = new Map();
const intervals = new Map();
global.requestAnimationFrame = (callback) => {{
  const id = nextId++;
  frames.set(id, callback);
  return id;
}};
global.cancelAnimationFrame = (id) => frames.delete(id);
global.setTimeout = (callback, _delay, ...args) => {{
  const id = nextId++;
  timeouts.set(id, () => callback(...args));
  return id;
}};
global.clearTimeout = (id) => timeouts.delete(id);
global.setInterval = (callback, _delay, ...args) => {{
  const id = nextId++;
  intervals.set(id, () => callback(...args));
  return id;
}};
global.clearInterval = (id) => intervals.delete(id);

class EventTargetProbe {{
  constructor() {{ this.listeners = new Map(); }}
  addEventListener(type, callback) {{
    const values = this.listeners.get(type) || [];
    values.push(callback);
    this.listeners.set(type, values);
  }}
  removeEventListener(type, callback) {{
    this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== callback));
  }}
  dispatch(type, value = {{}}) {{
    for (const callback of this.listeners.get(type) || []) callback(value);
  }}
  count(type) {{ return (this.listeners.get(type) || []).length; }}
}}

class LegacyTargetProbe {{
  constructor() {{ this.listeners = []; }}
  addListener(callback) {{ this.listeners.push(callback); }}
  removeListener(callback) {{ this.listeners = this.listeners.filter((item) => item !== callback); }}
  dispatch(value = {{}}) {{ for (const callback of this.listeners) callback(value); }}
}}

{TEXT}

const reverse = createRendererContext();
const order = [];
reverse.teardown.add("first", () => order.push("first"));
reverse.teardown.add("second", () => order.push("second"));
assert.equal(reverse.teardown.run(), true);
assert.deepEqual(order, ["second", "first"]);
assert.equal(reverse.teardown.run(), false);

const events = createRendererContext();
assert.equal(events.lifecycle.active(), true);
const modern = new EventTargetProbe();
const legacy = new LegacyTargetProbe();
let calls = 0;
const modernRelease = events.lifecycle.listen("modern", modern, "change", () => calls++);
const legacyRelease = events.lifecycle.listen("legacy", legacy, "change", () => calls++);
assert.equal(modern.count("change"), 1);
assert.equal(legacy.listeners.length, 1);
modern.dispatch("change");
legacy.dispatch();
assert.equal(calls, 2);
modernRelease();
legacyRelease();
assert.equal(modern.count("change"), 0);
assert.equal(legacy.listeners.length, 0);
events.teardown.run();
assert.equal(events.lifecycle.active(), false);

const scoped = createRendererContext();
    const firstScope = scoped.lifecycle.scope("replaceable");
    firstScope.listen(modern, "change", () => calls++);
    const secondScope = scoped.lifecycle.scope("replaceable");
    secondScope.listen(modern, "change", () => calls++);
    assert.equal(modern.count("change"), 1);
secondScope.dispose();
assert.equal(modern.count("change"), 0);

let firstObserverDisconnects = 0;
let secondObserverDisconnects = 0;
const observers = createRendererContext();
observers.observers.set("layout", {{ disconnect: () => firstObserverDisconnects++ }});
observers.observers.set("layout", {{ disconnect: () => secondObserverDisconnects++ }});
assert.equal(firstObserverDisconnects, 1);
observers.observers.clear("layout");
assert.equal(secondObserverDisconnects, 1);

const scheduled = createRendererContext();
scheduled.frames.schedule("layout", () => {{}});
scheduled.frames.schedule("layout", () => {{}});
assert.equal(frames.size, 1);
scheduled.frames.cancel("layout");
assert.equal(frames.size, 0);
scheduled.lifecycle.timeout("timeout-owner", () => {{}}, 10);
scheduled.lifecycle.interval("interval-owner", () => {{}}, 10);
assert.equal(timeouts.size, 1);
assert.equal(intervals.size, 1);
scheduled.teardown.run();
assert.equal(timeouts.size, 0);
assert.equal(intervals.size, 0);

const contracts = createRendererContext();
contracts.domains.register("settings", {{ apply: () => {{}} }});
assert.deepEqual(contracts.domains.names(), ["settings"]);
assert.equal(contracts.domains.get("settings").apply instanceof Function, true);
assert.throws(() => contracts.domains.register("settings", {{}}), /duplicate renderer domain/);
const area = new Map();
const storage = {{
  getItem: (key) => area.has(key) ? area.get(key) : null,
  setItem: (key, value) => area.set(key, value),
}};
assert.equal(contracts.storage.read(storage, "missing", "fallback"), "fallback");
assert.equal(contracts.storage.write(storage, "key", "value"), true);
assert.equal(contracts.storage.read(storage, "key"), "value");
let bindingPayload = null;
window.testBinding = (value) => {{ bindingPayload = value; }};
assert.equal(contracts.bindings.send("testBinding", {{ ok: true }}), true);
assert.deepEqual(JSON.parse(bindingPayload), {{ ok: true }});
console.log("renderer-kernel-ok");
"""
    completed = subprocess.run(
        ["node", "--input-type=commonjs", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "renderer-kernel-ok" in completed.stdout
