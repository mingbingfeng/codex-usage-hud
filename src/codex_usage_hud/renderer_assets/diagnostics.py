"""Renderer diagnostics JavaScript domain."""

TEXT = r"""
  function createDiagnosticsDomain(ctx, shared) {
  function renderRuntimeErrors(root, payload) {
    const runtimeErrorsPanel = root.querySelector('[data-field="runtimeErrorsPanel"]');
    if (!runtimeErrorsPanel) return;
    const debug = !!payload?.debug;
    const items = Array.isArray(payload?.runtimeErrors) ? payload.runtimeErrors.filter(Boolean) : [];
    runtimeErrorsPanel.hidden = !debug;
    if (runtimeErrorsPanel.hidden) {
      runtimeErrorsPanel.replaceChildren();
      return;
    }
    const expanded = getRuntimeErrorsPanelState().expanded === true;
    runtimeErrorsPanel.dataset.expanded = String(expanded);
    runtimeErrorsPanel.replaceChildren();
    const title = document.createElement("div");
    title.className = "codex-usage-hud-runtime-errors-title";
    title.dataset.action = "runtime-errors-move";
    title.title = "拖动 Runtime errors 面板";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "codex-usage-hud-runtime-errors-toggle";
    toggle.dataset.action = "runtime-errors-toggle";
    toggle.setAttribute("aria-label", expanded ? "收缩 Runtime errors 面板" : "展开 Runtime errors 面板");
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.title = expanded ? "收缩 Runtime errors 面板" : "展开 Runtime errors 面板";
    toggle.textContent = expanded ? "v" : ">";
    const heading = document.createElement("span");
    heading.textContent = "errors";
    const count = document.createElement("span");
    count.className = "codex-usage-hud-runtime-errors-count";
    count.textContent = `${items.length}`;
    title.append(toggle, heading, count);
    runtimeErrorsPanel.appendChild(title);
    const body = document.createElement("div");
    body.className = "codex-usage-hud-runtime-errors-body";
    body.hidden = !expanded;
    runtimeErrorsPanel.appendChild(body);
    if (!items.length) {
      const debugStatusItem = document.createElement("div");
      debugStatusItem.className = "codex-usage-hud-runtime-error";
      debugStatusItem.dataset.severity = "info";
      const code = document.createElement("div");
      code.className = "codex-usage-hud-runtime-error-code";
      code.textContent = "debug.ready";
      const message = document.createElement("div");
      message.textContent = "DEBUG HUD active";
      const meta = document.createElement("div");
      meta.className = "codex-usage-hud-runtime-error-meta";
      meta.textContent = "info · renderer · 1x";
      debugStatusItem.append(code, message, meta);
      body.appendChild(debugStatusItem);
      applyRuntimeErrorsPanelState(runtimeErrorsPanel);
      return;
    }
    for (const item of items.slice(0, 6)) {
      const row = document.createElement("div");
      row.className = "codex-usage-hud-runtime-error";
      row.dataset.severity = String(item.severity || "error");
      const code = document.createElement("div");
      code.className = "codex-usage-hud-runtime-error-code";
      code.textContent = String(item.code || "runtime.unknown");
      const message = document.createElement("div");
      message.textContent = String(item.message || "");
      const meta = document.createElement("div");
      meta.className = "codex-usage-hud-runtime-error-meta";
      const source = String(item.source || "runtime");
      const severity = String(item.severity || "error");
      const occurrences = Number(item.count || 1);
      meta.textContent = `${severity} · ${source} · ${occurrences}x`;
      const context = document.createElement("div");
      context.className = "codex-usage-hud-runtime-error-context";
      try {
        const rawContext = item.context && typeof item.context === "object" ? item.context : {};
        context.textContent = JSON.stringify(rawContext, null, 2);
      } catch (_) {
        context.textContent = "";
      }
      row.append(code, message, meta);
      if (context.textContent && context.textContent !== "{}") row.appendChild(context);
      body.appendChild(row);
    }
    applyRuntimeErrorsPanelState(runtimeErrorsPanel);
  }


  function renderConnectionHealth(root, payload) {
    const health = payload?.connectionHealth;
    if (!health || typeof health !== "object") return;
    const rawState = String(health.state || "ok");
    const state = rawState === "recovering" || rawState === "failed" ? rawState : "ok";
    const detail = normalize(health.detail) || (
      state === "failed"
        ? "CDP 连接异常"
        : (state === "recovering" ? "连接异常，正在恢复" : "CDP 连接正常")
    );
    root.querySelectorAll('[data-field="connectionDot"]').forEach((node) => {
      node.dataset.state = state;
      node.setAttribute("title", detail);
      node.setAttribute("aria-label", detail);
    });
  }

    let installed = false;

    function install() {
      if (installed) return false;
      installed = true;
      return true;
    }

    function applyConnectionHealth(root, payload) {
      renderConnectionHealth(root, payload || {});
    }

    function apply(root, payload) {
      if (!installed) install();
      renderRuntimeErrors(root, payload || {});
      applyConnectionHealth(root, payload || {});
    }

    function dispose() {
      const wasInstalled = installed;
      installed = false;
      return wasInstalled;
    }

    return { install, apply, dispose, applyConnectionHealth };
  }

  const diagnosticsDomain = ctx.domains.register(
    "diagnostics",
    createDiagnosticsDomain(ctx, shared),
  );
"""

__all__ = ["TEXT"]
