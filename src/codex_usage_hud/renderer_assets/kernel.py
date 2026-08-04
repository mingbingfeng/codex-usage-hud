"""Renderer kernel and shared lifecycle capabilities."""

TEXT = r"""
  function createRendererContext() {
    const registeredDomains = new Map();
    const domainOrder = [];
    const teardownEntries = [];
    const scheduledFrames = new Map();
    const frameDisposers = new Map();
    const observerSlots = new Map();
    const scopeSlots = new Map();
    const timeoutDisposers = new Map();
    const intervalDisposers = new Map();
    let disposed = false;
    let context = null;

    function releaseEntry(entry, invoke = true) {
      if (!entry?.active) return false;
      entry.active = false;
      if (invoke) entry.dispose();
      return true;
    }

    function addTeardown(owner, dispose) {
      if (typeof dispose !== "function") throw new TypeError("teardown must be callable");
      const entry = {
        owner: String(owner || "shared"),
        dispose,
        active: true,
      };
      if (disposed) {
        releaseEntry(entry);
        return () => false;
      }
      teardownEntries.push(entry);
      return (invoke = true) => {
        const released = releaseEntry(entry, invoke);
        if (released && !disposed) {
          const index = teardownEntries.indexOf(entry);
          if (index >= 0) teardownEntries.splice(index, 1);
        }
        return released;
      };
    }

    function runTeardown() {
      if (disposed) return false;
      disposed = true;
      for (let index = teardownEntries.length - 1; index >= 0; index -= 1) {
        try {
          releaseEntry(teardownEntries[index]);
        } catch (_) {}
      }
      teardownEntries.length = 0;
      scheduledFrames.clear();
      frameDisposers.clear();
      observerSlots.clear();
      timeoutDisposers.clear();
      intervalDisposers.clear();
      scopeSlots.clear();
      return true;
    }

    function listen(owner, target, type, handler, options) {
      if (!target || typeof handler !== "function") return () => false;
      const modern = typeof target.addEventListener === "function"
        && typeof target.removeEventListener === "function";
      const add = modern
        ? target.addEventListener.bind(target)
        : (typeof target.addListener === "function" ? target.addListener.bind(target) : null);
      const remove = modern
        ? target.removeEventListener.bind(target)
        : (typeof target.removeListener === "function" ? target.removeListener.bind(target) : null);
      if (!add || !remove) return () => false;
      let release = () => false;
      const once = !!(options && typeof options === "object" && options.once);
      const installedHandler = once
        ? function rendererKernelOnceListener(...args) {
            release(false);
            return handler.apply(this, args);
          }
        : handler;
      if (modern) add(type, installedHandler, options);
      else add(installedHandler);
      release = addTeardown(owner, () => {
        if (modern) remove(type, installedHandler, options);
        else remove(installedHandler);
      });
      return release;
    }

    function scheduleFrame(concern, callback) {
      const key = String(concern || "shared");
      const previous = scheduledFrames.get(key);
      if (previous) previous.release();
      let release = () => false;
      const id = requestAnimationFrame((timestamp) => {
        scheduledFrames.delete(key);
        release(false);
        callback(timestamp);
      });
      release = addTeardown(`frame:${key}`, () => cancelAnimationFrame(id));
      scheduledFrames.set(key, { id, release });
      return id;
    }

    function cancelFrame(concern) {
      const key = String(concern || "shared");
      const scheduled = scheduledFrames.get(key);
      if (!scheduled) return false;
      scheduledFrames.delete(key);
      return scheduled.release();
    }

    function requestFrame(owner, callback) {
      let release = () => false;
      const id = requestAnimationFrame((timestamp) => {
        frameDisposers.delete(id);
        release(false);
        callback(timestamp);
      });
      release = addTeardown(`frame:${owner || "shared"}`, () => cancelAnimationFrame(id));
      frameDisposers.set(id, release);
      return id;
    }

    function cancelTrackedFrame(id) {
      const release = frameDisposers.get(id);
      if (!release) {
        cancelAnimationFrame(id || 0);
        return false;
      }
      frameDisposers.delete(id);
      return release();
    }

    function setObserver(concern, observer) {
      const key = String(concern || "shared");
      const previous = observerSlots.get(key);
      if (previous) previous.release();
      if (!observer?.disconnect) return observer;
      const release = addTeardown(`observer:${key}`, () => observer.disconnect());
      observerSlots.set(key, { observer, release });
      return observer;
    }

    function clearObserver(concern) {
      const key = String(concern || "shared");
      const tracked = observerSlots.get(key);
      if (!tracked) return false;
      observerSlots.delete(key);
      return tracked.release();
    }

    function createScope(owner) {
      const key = String(owner || "shared");
      scopeSlots.get(key)?.dispose?.();
      const releases = [];
      let releaseScope = () => false;
      const scope = {
        listen(target, type, handler, options) {
          const release = listen(key, target, type, handler, options);
          releases.push(release);
          return release;
        },
        dispose() {
          for (let index = releases.length - 1; index >= 0; index -= 1) {
            try {
              releases[index]();
            } catch (_) {}
          }
          releases.length = 0;
          if (scopeSlots.get(key) === scope) scopeSlots.delete(key);
          releaseScope(false);
        },
      };
      releaseScope = addTeardown(`scope:${key}`, () => scope.dispose());
      scopeSlots.set(key, scope);
      return scope;
    }

    function disposeScope(owner) {
      const key = String(owner || "shared");
      const scope = scopeSlots.get(key);
      if (!scope) return false;
      scope.dispose();
      return true;
    }

    function getScope(owner) {
      return scopeSlots.get(String(owner || "shared")) || null;
    }

    function scheduleTimeout(owner, callback, delay, ...args) {
      let release = () => false;
      const id = setTimeout(() => {
        timeoutDisposers.delete(id);
        release(false);
        callback(...args);
      }, delay);
      release = addTeardown(`timeout:${owner || "shared"}`, () => clearTimeout(id));
      timeoutDisposers.set(id, release);
      return id;
    }

    function cancelTimeout(id) {
      const release = timeoutDisposers.get(id);
      if (!release) {
        clearTimeout(id || 0);
        return false;
      }
      timeoutDisposers.delete(id);
      return release();
    }

    function scheduleInterval(owner, callback, delay, ...args) {
      const id = setInterval(callback, delay, ...args);
      const release = addTeardown(`interval:${owner || "shared"}`, () => clearInterval(id));
      intervalDisposers.set(id, release);
      return id;
    }

    function cancelInterval(id) {
      const release = intervalDisposers.get(id);
      if (!release) {
        clearInterval(id || 0);
        return false;
      }
      intervalDisposers.delete(id);
      return release();
    }

    function registerDomain(name, domain) {
      const key = String(name || "").trim();
      if (!key) throw new TypeError("domain name is required");
      if (!domain || typeof domain !== "object") throw new TypeError(`invalid domain: ${key}`);
      if (registeredDomains.has(key)) throw new Error(`duplicate renderer domain: ${key}`);
      registeredDomains.set(key, domain);
      domainOrder.push(key);
      if (typeof domain.dispose === "function") {
        addTeardown(`domain:${key}`, () => domain.dispose(context));
      }
      return domain;
    }

    function storageRead(area, key, fallback = null) {
      try {
        const value = area?.getItem?.(key);
        return value === null || value === undefined ? fallback : value;
      } catch (_) {
        return fallback;
      }
    }

    function storageWrite(area, key, value) {
      try {
        area?.setItem?.(key, value);
        return true;
      } catch (_) {
        return false;
      }
    }

    function sendBinding(name, payload) {
      const binding = window[name];
      if (typeof binding !== "function") return false;
      binding(JSON.stringify(payload));
      return true;
    }

    const retainedState = {
      read() {
        const state = window[stateName];
        return state && typeof state === "object" ? state : {};
      },
      payload() {
        const payload = retainedState.read().payload;
        return payload && typeof payload === "object" ? payload : {};
      },
      domains() {
        const domains = retainedState.read().domains;
        return domains && typeof domains === "object" ? domains : {};
      },
      write(state) {
        window[stateName] = state;
        return state;
      },
    };

    context = {
      domains: {
        register: registerDomain,
        get: (name) => registeredDomains.get(String(name || "")),
        entries: () => domainOrder.map((name) => [name, registeredDomains.get(name)]),
        names: () => [...domainOrder],
      },
      state: retainedState,
      storage: {
        read: storageRead,
        write: storageWrite,
      },
      bindings: {
        available: (name) => typeof window[name] === "function",
        send: sendBinding,
      },
      frames: {
        schedule: scheduleFrame,
        cancel: cancelFrame,
      },
      observers: {
        set: setObserver,
        clear: clearObserver,
      },
      lifecycle: {
        active: () => !disposed,
        listen,
        scope: createScope,
        disposeScope,
        getScope,
        frame: requestFrame,
        clearFrame: cancelTrackedFrame,
        timeout: scheduleTimeout,
        clearTimeout: cancelTimeout,
        interval: scheduleInterval,
        clearInterval: cancelInterval,
      },
      teardown: {
        add: addTeardown,
        run: runTeardown,
        owners: () => teardownEntries.filter((entry) => entry.active).map((entry) => entry.owner),
      },
    };
    return context;
  }

  const ctx = createRendererContext();
"""

__all__ = ["TEXT"]
