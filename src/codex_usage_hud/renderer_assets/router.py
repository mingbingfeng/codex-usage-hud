"""Remaining renderer asset during P6 domain migration."""

TEXT = r"""
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function currentPayload() {
    const payload = ctx.state.payload();
    if (Array.isArray(payload.supportImages) && payload.supportImages.length) {
      return payload;
    }
    const persistedSupportImages = loadPersistedSupportImages();
    if (!persistedSupportImages.length) return payload;
    return { ...payload, supportImages: persistedSupportImages };
  }

  function ensureRoot() {
    ensureStyle();
    let root = document.getElementById(rootId);
    if (root?.dataset.version === version) return root;
    if (!document.body) return null;
    root?.remove();
    root = document.createElement("div");
    root.id = rootId;
    root.dataset.version = version;
    root.dataset.hudReady = "false";
    root.innerHTML = `
      <div class="codex-usage-hud-startup-bubble" data-field="startupBubble" role="status" aria-live="polite" hidden>
        <div class="codex-usage-hud-startup-step" data-field="startupStep"></div>
        <div class="codex-usage-hud-startup-title" data-field="startupTitle"></div>
        <div class="codex-usage-hud-startup-detail" data-field="startupDetail"></div>
        <div class="codex-usage-hud-startup-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" data-field="startupProgressTrack">
          <div class="codex-usage-hud-startup-progress-fill" data-field="startupProgressFill"></div>
        </div>
        <div class="codex-usage-hud-startup-progress-label" data-field="startupProgressLabel"></div>
      </div>
    ` + panelMarkup("top", "", "展开顶部 HUD") + panelMarkup("request", "", "展开请求 HUD") + settingsChromeMarkup();
    document.body.appendChild(root);
    applyPanelStates(root);
    bindRoot(root);
    return root;
  }

  function loadStates() {
    try {
      const data = JSON.parse(ctx.storage.read(localStorage, storageKey, "{}"));
      return data && typeof data === "object" ? data : {};
    } catch (_) {
      return {};
    }
  }

  function saveStates(states) {
    try {
      ctx.storage.write(localStorage, storageKey, JSON.stringify(states));
    } catch (_) {}
  }

  function getPanelState(name) {
    return { ...(loadStates()[name] || {}) };
  }

  function setPanelState(name, patch) {
    const states = loadStates();
    states[name] = { ...(states[name] || {}), ...patch };
    saveStates(states);
    return states[name];
  }

  function getRuntimeErrorsPanelState() {
    return { expanded: false, ...(loadStates().runtimeErrors || {}) };
  }

  function setRuntimeErrorsPanelState(patch) {
    const states = loadStates();
    states.runtimeErrors = { ...(states.runtimeErrors || {}), ...patch };
    saveStates(states);
    return states.runtimeErrors;
  }

  function applyPanelStates(root) {
    for (const name of Object.keys(PANEL)) {
      const panel = root.querySelector(`[data-panel="${name}"]`);
      const expanded = !!getPanelState(name).expanded;
      if (panel) panel.dataset.expanded = String(expanded);
    }
  }

  function bindRoot(root) {
    if (root.dataset.bound === "true") return;
    root.dataset.bound = "true";
    const rootScope = ctx.lifecycle.scope("root");
    // 悬浮徽章 → 展开 A/B/C/D/F 明细；移开 → 收起（事件委托，徽章重建也生效）。
    rootScope.listen(root, "mouseover", (event) => {
      if (event.target?.closest?.(".codex-usage-hud-token-badge")) {
        showComposerBreakdown(root);
      }
    });
    rootScope.listen(root, "mouseout", (event) => {
      const from = event.target?.closest?.(".codex-usage-hud-token-badge");
      if (from && !from.contains(event.relatedTarget)) {
        hideComposerBreakdown(root);
      }
    });
    rootScope.listen(root, "wheel", (event) => {
      const select = event.target?.closest?.(`#${settingsModalId} select[data-setting-key]`);
      if (!select || !root.contains(select)) return;
      event.preventDefault();
      event.stopPropagation();
      select.closest(".codex-usage-hud-settings-dialog")?.scrollBy({
        left: event.deltaX,
        top: event.deltaY,
      });
    }, { capture: true, passive: false });
    const commitSessionCleanupSearch = (sessionSearch) => {
      if (!sessionSearch || !root.contains(sessionSearch)) return false;
      const search = String(sessionSearch.value || "");
      if (search === sessionCleanupState.search) return false;
      sessionCleanupState.search = search;
      sessionCleanupState.selectedIds.clear();
      persistSessionCleanupFilters();
      renderSettingsModal("storage");
      return true;
    };
    rootScope.listen(root, "keydown", (event) => {
      const sessionSearch = event.target?.closest?.('[data-session-cleanup-search="true"]');
      if (!sessionSearch || root.contains(sessionSearch) === false) return;
      if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
      event.preventDefault();
      commitSessionCleanupSearch(sessionSearch);
    });
    rootScope.listen(root, "focusout", (event) => {
      const sessionSearch = event.target?.closest?.('[data-session-cleanup-search="true"]');
      if (!sessionSearch || root.contains(sessionSearch) === false) return;
      commitSessionCleanupSearch(sessionSearch);
    });
    rootScope.listen(root, "input", (event) => {
      const codexCliField = event.target?.closest?.("[data-codex-cli-field]");
      if (codexCliField && root.contains(codexCliField)) {
        codexCliFieldInput(String(codexCliField.dataset.codexCliField || ""));
        return;
      }
      const restStartInput = event.target?.closest?.('[data-rest-reminder-start-time="true"]');
      if (restStartInput && root.contains(restStartInput)) {
        restStartInput.dataset.userEdited = "true";
        return;
      }
      const sessionDateStart = event.target?.closest?.('[data-session-cleanup-date-start="true"]');
      if (sessionDateStart && root.contains(sessionDateStart)) {
        sessionCleanupState.dateDraftStart = String(sessionDateStart.value || "");
        return;
      }
      const sessionDateEnd = event.target?.closest?.('[data-session-cleanup-date-end="true"]');
      if (sessionDateEnd && root.contains(sessionDateEnd)) {
        sessionCleanupState.dateDraftEnd = String(sessionDateEnd.value || "");
        return;
      }
      const sessionSearch = event.target?.closest?.('[data-session-cleanup-search="true"]');
      if (sessionSearch && root.contains(sessionSearch)) {
        // IME composition emits input events before the final text is committed.
        // Re-rendering here replaces the input and aborts Chinese composition.
        return;
      }
      const editor = event.target?.closest?.('[data-provider-editor="true"]');
      if (!editor || !root.contains(editor)) return;
      const scopeToggle = event.target?.closest?.(
        '[data-provider-enabled="true"], [data-provider-notification-only="true"]'
      );
      if (scopeToggle?.checked) {
        const counterpartSelector = scopeToggle.matches('[data-provider-enabled="true"]')
          ? '[data-provider-notification-only="true"]'
          : '[data-provider-enabled="true"]';
        const counterpart = editor.querySelector(counterpartSelector);
        if (counterpart) counterpart.checked = false;
      }
      markSettingsProviderDirty();
    });
    rootScope.listen(document, "paste", (event) => {
      const row = event.target?.closest?.('[data-price-row="true"]');
      if (!row || !root.contains(row)) return;
      const clipboard = event.clipboardData;
      const text = clipboard?.getData("text/plain") || clipboard?.getData("text") || "";
      if (!fillPriceRowFromClipboard(row, text)) return;
      event.preventDefault();
      event.stopPropagation();
      markSettingsProviderDirty();
    }, true);
    rootScope.listen(root, "change", (event) => {
      const codexCliField = event.target?.closest?.("[data-codex-cli-field]");
      if (codexCliField && root.contains(codexCliField)) {
        codexCliFieldChange(String(codexCliField.dataset.codexCliField || ""));
        return;
      }
      const restStartInput = event.target?.closest?.('[data-rest-reminder-start-time="true"]');
      if (restStartInput && root.contains(restStartInput)) {
        restStartInput.dataset.userEdited = "true";
        return;
      }
      const pricingImportFile = event.target?.closest?.('[data-pricing-import-file="true"]');
      if (pricingImportFile && root.contains(pricingImportFile)) {
        void readPricingImportFile(pricingImportFile);
        return;
      }
      const pricingApplyAll = event.target?.closest?.('[data-pricing-apply-all="true"]');
      if (pricingApplyAll && root.contains(pricingApplyAll)) {
        updatePricingApplyAllPreview(!!pricingApplyAll.checked);
        return;
      }
      const sessionFilter = event.target?.closest?.("[data-session-cleanup-filter]");
      if (sessionFilter && root.contains(sessionFilter)) {
        const key = String(sessionFilter.dataset.sessionCleanupFilter || "");
        if (!new Set(["archive", "availability", "clientKind", "modelProvider", "sort"]).has(key)) return;
        sessionCleanupState[key] = String(sessionFilter.value || "all");
        if (key !== "sort") sessionCleanupState.selectedIds.clear();
        persistSessionCleanupFilters();
        renderSettingsModal("storage");
        return;
      }

      const sessionSelectAll = event.target?.closest?.('[data-session-cleanup-select-all="true"]');
      if (sessionSelectAll && root.contains(sessionSelectAll)) {
        for (const item of sessionCleanupRows()) {
          const id = String(item?.id || "");
          if (!id || item?.selectable !== true) continue;
          if (sessionSelectAll.checked) sessionCleanupState.selectedIds.add(id);
          else sessionCleanupState.selectedIds.delete(id);
        }
        renderSettingsModal("storage");
        return;
      }
      const sessionItem = event.target?.closest?.('[data-session-cleanup-id]');
      if (sessionItem && root.contains(sessionItem)) {
        const id = String(sessionItem.dataset.sessionCleanupId || "");
        if (sessionItem.checked) sessionCleanupState.selectedIds.add(id);
        else sessionCleanupState.selectedIds.delete(id);
        renderSettingsModal("storage");
        return;
      }
      const filter = event.target?.closest?.("[data-background-usage-filter]");
      if (!filter || !root.contains(filter)) return;
      const key = String(filter.dataset.backgroundUsageFilter || "");
      if (key === "feature") backgroundUsageState.feature = String(filter.value || "");
      if (key === "model") backgroundUsageState.model = String(filter.value || "");
      if (backgroundUsageSessionRankingMode()) {
        backgroundUsageState.model = "";
        if (backgroundUsageState.range === "all") {
          backgroundUsageState.range = "7d";
        }
      }
      backgroundUsageState.selectedEventId = "";
      backgroundUsageState.selectedSessionId = "";
      backgroundUsageState.detail = null;
      backgroundUsageState.error = "";
      void loadBackgroundUsage();
    });
    rootScope.listen(root, "keydown", (event) => {
      if (event.key === "Escape") {
        const modal = document.getElementById(settingsModalId);
        if (modal && !modal.hidden) {
          const codexCli = modal.querySelector('[data-codex-cli-dialog="true"]');
          const confirm = modal.querySelector('[data-settings-confirm="true"]');
          event.preventDefault();
          event.stopPropagation();
          if (codexCli) closeCodexCliDialog();
          else if (confirm) closeSettingsConfirm();
          else closeSettingsModal();
          return;
        }
      }
      const workdirButton = event.target?.closest?.('[data-action="usage-insights-open-workdir"]');
      if (workdirButton && root.contains(workdirButton) && (event.key === "Enter" || event.key === " ")) {
        return;
      }
      const sessionRankingRow = event.target?.closest?.('[data-action="background-usage-session-select"]');
      if (sessionRankingRow && root.contains(sessionRankingRow) && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        sessionRankingRow.click();
        return;
      }
      const tab = event.target?.closest?.('[data-provider-tab="true"]');
      if (!tab || !root.contains(tab) || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      const providers = settingsProviderDraft?.order || [];
      if (!providers.length) return;
      event.preventDefault();
      event.stopPropagation();
      const currentIndex = Math.max(0, providers.indexOf(settingsProviderDraft.activeProvider));
      const offset = event.key === "ArrowRight" ? 1 : -1;
      const nextIndex = (currentIndex + offset + providers.length) % providers.length;
      switchSettingsProvider(providers[nextIndex], { focusTab: true });
    });
    rootScope.listen(root, "click", (event) => {
      // 设置弹窗只能通过右上角 × 或底部“关闭”按钮关闭，点击遮罩空白区域不应关闭。
      const copyNode = event.target?.closest?.("[data-copyable='true']");
      if (copyNode && root.contains(copyNode)) {
        event.preventDefault();
        event.stopPropagation();
        void copyHudText(copyNode.dataset.copyText || "").then((ok) => {
          flashCopyState(copyNode, ok);
        });
        return;
      }
      const sessionCleanupRow = event.target?.closest?.(".codex-usage-hud-session-row");
      if (sessionCleanupRow && root.contains(sessionCleanupRow)) {
        const interactiveTarget = event.target?.closest?.("input, button, select, textarea, a, label");
        if (!interactiveTarget) {
          const checkbox = sessionCleanupRow.querySelector('[data-session-cleanup-id]');
          if (checkbox && !checkbox.disabled) {
            event.preventDefault();
            checkbox.click();
          }
          return;
        }
      }
      const action = event.target?.closest?.("[data-action]");
      if (!action || !root.contains(action)) return;
      if (action.dataset.action === "active-session-candidate") {
        event.preventDefault();
        event.stopPropagation();
        const sessionId = String(action.dataset.sessionId || "").trim();
        if (!sessionId) return;
        submitSettingsCommand(
          {
            action: "resolveActiveSession",
            sessionId,
            selectionSeq: Number(action.dataset.selectionSeq || 0),
          },
          "正在按你的选择匹配未归档会话...",
          { preserveOverlay: true },
        );
        return;
      }
      if (action.dataset.action === "settings-codex-cli-open") {
        event.preventDefault();
        event.stopPropagation();
        openCodexCliDialog(action.dataset.provider || "");
        return;
      }
      if (action.dataset.action === "codex-cli-close") {
        event.preventDefault();
        event.stopPropagation();
        closeCodexCliDialog();
        return;
      }
      if (action.dataset.action === "codex-cli-refresh") {
        event.preventDefault();
        event.stopPropagation();
        refreshCodexCliDialog();
        return;
      }
      if (action.dataset.action === "codex-cli-copy") {
        event.preventDefault();
        event.stopPropagation();
        codexCliCopyCommand();
        return;
      }
      if (action.dataset.action === "codex-cli-launch") {
        event.preventDefault();
        event.stopPropagation();
        launchCodexCliFromDialog();
        return;
      }
      if (action.dataset.action === "codex-cli-chat-test") {
        event.preventDefault();
        event.stopPropagation();
        chatTestCodexCliFromDialog();
        return;
      }
      if (action.dataset.action === "runtime-errors-toggle") {
        event.preventDefault();
        event.stopPropagation();
        const runtimeErrorsPanel = action.closest('[data-field="runtimeErrorsPanel"]');
        if (!runtimeErrorsPanel) return;
        const expanded = runtimeErrorsPanel.dataset.expanded !== "true";
        runtimeErrorsPanel.dataset.expanded = String(expanded);
        const body = runtimeErrorsPanel.querySelector(".codex-usage-hud-runtime-errors-body");
        if (body) body.hidden = !expanded;
        action.setAttribute("aria-label", expanded ? "收缩 Runtime errors 面板" : "展开 Runtime errors 面板");
        action.setAttribute("aria-expanded", String(expanded));
        action.title = expanded ? "收缩 Runtime errors 面板" : "展开 Runtime errors 面板";
        action.textContent = expanded ? "v" : ">";
        setRuntimeErrorsPanelState({ expanded });
        applyRuntimeErrorsPanelState(runtimeErrorsPanel);
        return;
      }
      if (action.dataset.action === "activity-load-more") {
        event.preventDefault();
        event.stopPropagation();
        const list = root.querySelector('[data-field="topActivityTrail"]');
        if (list) {
          const current = Number(list.dataset.visibleCount || 4);
          list.dataset.visibleCount = String(current + 4);
        }
        renderActivityTimeline(root, currentPayload()?.topDetails || {});
        return;
      }
      if (action.dataset.action === "background-usage-open-notification") {
        event.preventDefault();
        event.stopPropagation();
        const eventId = String(action.dataset.eventId || "").trim();
        if (!eventId) return;
        const requestId = submitBackgroundUsageCommand(
          "openBackgroundUsage",
          { eventId },
        );
        if (!requestId) {
          backgroundUsageState.range = normalizeBackgroundUsageRange(
            action.dataset.backgroundRange,
          );
          backgroundUsageState.feature = "";
          backgroundUsageState.model = "";
          backgroundUsageState.selectedSessionId = "";
          backgroundUsageState.selectedEventId = eventId;
          backgroundUsageState.data = null;
          backgroundUsageState.detail = null;
          backgroundUsageState.loadedRevision = -1;
          renderSettingsModal("backgroundUsage");
          // No binding path: open + auto-locate still counts as viewing.
          void loadBackgroundUsageDetail(eventId, { markViewed: true });
        }
        return;
      }
      if (action.dataset.action === "background-usage-policy") {
        event.preventDefault(); event.stopPropagation();
        backgroundUsagePolicyConfirm(backgroundUsageState.detail);
        return;
      }
      if (action.dataset.action === "background-usage-policy-cancel") {
        event.preventDefault(); event.stopPropagation(); closeSettingsConfirm(); return;
      }
      if (action.dataset.action === "background-usage-policy-confirm") {
        event.preventDefault(); event.stopPropagation(); closeSettingsConfirm();
        applyBackgroundUsagePolicy(String(action.dataset.featureKey || ""), String(action.dataset.eventId || ""), String(action.dataset.desiredState || "disabled"));
        return;
      }
      if (action.dataset.action === "settings-open") {
        event.preventDefault();
        event.stopPropagation();
        renderSettingsModal("settings", "", { resetProviderDraft: true });
        return;
      }
      if (action.dataset.action === "settings-close") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsModal();
        return;
      }
      if (action.dataset.action === "settings-tab") {
        event.preventDefault();
        event.stopPropagation();
        const tab = action.dataset.tab || "settings";
        renderSettingsModal(tab);
        return;
      }
      if (action.dataset.action === "session-cleanup-date-toggle") {
        event.preventDefault();
        event.stopPropagation();
        const opening = !sessionCleanupState.datePickerOpen;
        sessionCleanupState.datePickerOpen = opening;
        if (opening) {
          sessionCleanupState.dateDraftStart = sessionCleanupState.dateStart;
          sessionCleanupState.dateDraftEnd = sessionCleanupState.dateEnd;
        }
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "session-cleanup-date-preset") {
        event.preventDefault();
        event.stopPropagation();
        const range = sessionCleanupDatePresetValues(action.dataset.sessionCleanupDatePreset);
        sessionCleanupState.dateDraftStart = range.start;
        sessionCleanupState.dateDraftEnd = range.end;
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "session-cleanup-date-reset") {
        event.preventDefault();
        event.stopPropagation();
        sessionCleanupState.dateStart = "";
        sessionCleanupState.dateEnd = "";
        sessionCleanupState.dateDraftStart = "";
        sessionCleanupState.dateDraftEnd = "";
        sessionCleanupState.datePickerOpen = false;
        sessionCleanupState.selectedIds.clear();
        persistSessionCleanupFilters();
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "session-cleanup-date-confirm") {
        event.preventDefault();
        event.stopPropagation();
        if (sessionCleanupDateRangeError(sessionCleanupState.dateDraftStart, sessionCleanupState.dateDraftEnd)) {
          renderSettingsModal("storage");
          return;
        }
        sessionCleanupState.dateStart = sessionCleanupState.dateDraftStart;
        sessionCleanupState.dateEnd = sessionCleanupState.dateDraftEnd;
        sessionCleanupState.datePickerOpen = false;
        sessionCleanupState.selectedIds.clear();
        persistSessionCleanupFilters();
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "session-cleanup-filters-clear") {
        event.preventDefault();
        event.stopPropagation();
        sessionCleanupState.search = "";
        sessionCleanupState.dateStart = "";
        sessionCleanupState.dateEnd = "";
        sessionCleanupState.dateDraftStart = "";
        sessionCleanupState.dateDraftEnd = "";
        sessionCleanupState.datePickerOpen = false;
        sessionCleanupState.archive = "all";
        sessionCleanupState.availability = "all";
        sessionCleanupState.clientKind = "all";
        sessionCleanupState.modelProvider = "all";
        sessionCleanupState.selectedIds.clear();
        persistSessionCleanupFilters();
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "background-usage-range") {
        event.preventDefault();
        event.stopPropagation();
        backgroundUsageState.range = normalizeBackgroundUsageRange(
          action.dataset.backgroundRange,
        );
        backgroundUsageState.selectedEventId = "";
        backgroundUsageState.selectedSessionId = "";
        backgroundUsageState.detail = null;
        void loadBackgroundUsage();
        return;
      }
      if (action.dataset.action === "background-usage-session-select") {
        event.preventDefault();
        event.stopPropagation();
        backgroundUsageState.selectedSessionId = String(action.dataset.usageSessionId || "").trim();
        syncBackgroundUsagePanel();
        return;
      }
      if (action.dataset.action === "background-usage-open-workdir") {
        event.preventDefault();
        event.stopPropagation();
        const eventId = String(action.dataset.eventId || "").trim();
        if (!eventId) return;
        submitSettingsCommand(
          { action: "openBackgroundUsageWorkdir", eventId },
          "正在打开工作目录...",
          { preserveOverlay: true },
        );
        return;
      }
      if (action.dataset.action === "background-usage-select") {
        event.preventDefault();
        event.stopPropagation();
        const eventId = String(action.dataset.eventId || "").trim();
        if (!eventId) return;
        backgroundUsageState.selectedEventId = eventId;
        backgroundUsageState.detail = null;
        backgroundUsageState.promptExpanded = false;
        syncBackgroundUsagePanel();
        void loadBackgroundUsageDetail(eventId, { markViewed: true });
        return;
      }
      if (action.dataset.action === "background-usage-toggle-prompt") {
        event.preventDefault();
        event.stopPropagation();
        backgroundUsageState.promptExpanded = !backgroundUsageState.promptExpanded;
        syncBackgroundUsagePanel();
        return;
      }
      if (action.dataset.action === "background-usage-copy-prompt") {
        event.preventDefault();
        event.stopPropagation();
        const rawPrompt = String(backgroundUsageState.detail?.prompt || "");
        void copyHudText(rawPrompt).then((ok) => {
          setSettingsStatus(ok ? "已复制请求原文。" : "请求原文复制失败。", ok ? "" : "error");
          flashCopyState(action, ok);
        });
        return;
      }
      if (action.dataset.action === "background-usage-refresh") {
        event.preventDefault();
        event.stopPropagation();
        void loadBackgroundUsage({ force: true });
        return;
      }
      if (action.dataset.action === "usage-insights-session") {
        event.preventDefault();
        event.stopPropagation();
        const sessionId = String(action.dataset.usageSessionId || "").trim();
        if (!sessionId) return;
        const submitted = submitSettingsCommand(
          {
            action: "openUsageInsightsSession",
            sessionId,
            targetTitle: String(action.dataset.targetTitle || "").trim(),
            workdir: String(action.dataset.workdir || "").trim(),
          },
          "正在打开所选会话...",
          { preserveOverlay: true },
        );
        if (submitted) closeSettingsModal();
        return;
      }
      if (action.dataset.action === "usage-insights-open-workdir") {
        event.preventDefault();
        event.stopPropagation();
        const sessionId = String(action.dataset.usageSessionId || "").trim();
        if (!sessionId) return;
        submitSettingsCommand(
          { action: "openUsageInsightsWorkdir", sessionId },
          "正在打开工作目录...",
          { preserveOverlay: true },
        );
        return;
      }
      if (action.dataset.action === "session-cleanup-open-workdir") {
        event.preventDefault();
        event.stopPropagation();
        const itemId = String(action.dataset.sessionCleanupWorkdirId || "").trim();
        const inventoryRevision = String(action.dataset.sessionCleanupInventoryRevision || "").trim();
        if (!itemId || !inventoryRevision) return;
        submitSettingsCommand(
          { action: "openSessionCleanupWorkdir", itemId, inventoryRevision },
          "正在打开工作目录...",
          { preserveOverlay: true },
        );
        return;
      }
      if (action.dataset.action === "session-cleanup-cancel") {
        event.preventDefault();
        event.stopPropagation();
        requestSessionCleanupCancel();
        return;
      }
      if (action.dataset.action === "session-cleanup-scan") {
        event.preventDefault();
        event.stopPropagation();
        requestSessionCleanupScan();
        return;
      }
      if (action.dataset.action === "session-cleanup-preview") {
        event.preventDefault();
        event.stopPropagation();
        requestSessionCleanupPreview();
        return;
      }
      if (action.dataset.action === "session-cleanup-confirm-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        submitSettingsCommand(
          { action: "sessionCleanupCancel", requestId: typedSettingsRequestId("session-cleanup-cancel") },
          "已取消永久删除确认。",
          { preserveOverlay: true },
        );
        return;
      }
      if (action.dataset.action === "session-cleanup-execute") {
        event.preventDefault();
        event.stopPropagation();
        executeSessionCleanup();
        return;
      }
      if (action.dataset.action === "settings-provider-tab") {
        event.preventDefault();
        event.stopPropagation();
        switchSettingsProvider(action.dataset.provider || "");
        return;
      }
      if (action.dataset.action === "settings-add-provider") {
        event.preventDefault();
        event.stopPropagation();
        openProviderConfigDialog("", { isNew: true });
        return;
      }
      if (action.dataset.action === "settings-edit-provider") {
        event.preventDefault();
        event.stopPropagation();
        openProviderConfigDialog(action.dataset.provider || "", { isNew: false });
        return;
      }
      if (action.dataset.action === "settings-delete-provider") {
        event.preventDefault();
        event.stopPropagation();
        openProviderDeleteDialog(action.dataset.provider || "");
        return;
      }
      if (action.dataset.action === "settings-provider-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        return;
      }
      if (action.dataset.action === "settings-provider-delete-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        return;
      }
      if (action.dataset.action === "settings-provider-delete-confirm") {
        event.preventDefault();
        event.stopPropagation();
        confirmProviderDeleteDialog();
        return;
      }
      if (action.dataset.action === "settings-provider-apply") {
        event.preventDefault();
        event.stopPropagation();
        applyProviderConfigDialog();
        return;
      }
      if (action.dataset.action === "settings-provider-toggle-api-key") {
        event.preventDefault();
        event.stopPropagation();
        toggleProviderApiKeyVisibility();
        return;
      }
      if (action.dataset.action === "settings-provider-test-connectivity") {
        event.preventDefault();
        event.stopPropagation();
        testProviderConnectivityFromDialog();
        return;
      }
      if (action.dataset.action === "settings-provider-chat-test") {
        event.preventDefault();
        event.stopPropagation();
        chatTestProviderFromDialog();
        return;
      }
      if (action.dataset.action === "settings-add-model") {
        event.preventDefault();
        event.stopPropagation();
        addModelPriceRow();
        return;
      }
      if (action.dataset.action === "settings-add-detected-model") {
        event.preventDefault();
        event.stopPropagation();
        addModelPriceRow(action.dataset.model || "");
        return;
      }
      if (action.dataset.action === "settings-save") {
        event.preventDefault();
        event.stopPropagation();
        void saveSettingsFromModal();
        return;
      }
      if (action.dataset.action === "rest-reminder-save") {
        event.preventDefault();
        event.stopPropagation();
        void saveSettingsFromModal({ section: "restReminder" });
        return;
      }
      if (action.dataset.action === "rest-reminder-ack") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand({ action: "restReminderAck" }, "正在关闭提醒预览...");
        const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
        if (toast) toast.dataset.visible = "false";
        const mask = document.querySelector(`#${rootId} [data-rest-reminder-mask="true"]`);
        if (mask) {
          mask.dataset.visible = "false";
          mask.setAttribute("aria-hidden", "true");
        }
        return;
      }
      if (action.dataset.action === "rest-reminder-start") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand({ action: "restReminderStart" }, "正在开始休息计时...");
        const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
        if (toast) toast.dataset.visible = "false";
        const mask = document.querySelector(`#${rootId} [data-rest-reminder-mask="true"]`);
        if (mask) {
          mask.dataset.visible = "false";
          mask.setAttribute("aria-hidden", "true");
        }
        return;
      }
      if (action.dataset.action === "rest-reminder-finish") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand({ action: "restReminderFinish" }, "正在结束本次休息...");
        return;
      }
      if (action.dataset.action === "rest-reminder-postpone") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand({ action: "restReminderPostpone" }, "正在安排稍后提醒...");
        const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
        if (toast) toast.dataset.visible = "false";
        const mask = document.querySelector(`#${rootId} [data-rest-reminder-mask="true"]`);
        if (mask) {
          mask.dataset.visible = "false";
          mask.setAttribute("aria-hidden", "true");
        }
        return;
      }
      if (action.dataset.action === "rest-reminder-test-notification") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand(
          { action: "restReminderTestNotification" },
          "正在发送系统通知并打开实际提醒预览..."
        );
        return;
      }
      if (action.dataset.action === "settings-exit") {
        event.preventDefault();
        event.stopPropagation();
        openSettingsExitConfirm();
        return;
      }
      if (action.dataset.action === "settings-exit-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        setSettingsStatus("已取消退出。");
        return;
      }
      if (action.dataset.action === "settings-discard-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        updateSettingsProviderDraftStatus();
        return;
      }
      if (action.dataset.action === "settings-discard-confirm") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsModal({ force: true });
        return;
      }
      if (action.dataset.action === "settings-exit-confirm") {
        event.preventDefault();
        event.stopPropagation();
        void exitHudFromModal();
        return;
      }
      if (action.dataset.action === "settings-restart") {
        event.preventDefault();
        event.stopPropagation();
        void restartHudFromModal();
        return;
      }
      if (action.dataset.action === "settings-install-desktop-overlay") {
        event.preventDefault();
        event.stopPropagation();
        void installDesktopOverlayFromModal();
        return;
      }
      if (action.dataset.action === "settings-enable-desktop-overlay") {
        event.preventDefault();
        event.stopPropagation();
        void enableDesktopOverlayFromModal();
        return;
      }
      if (action.dataset.action === "settings-fetch-prices") {
        event.preventDefault();
        event.stopPropagation();
        void fetchPricesFromModal();
        return;
      }
      if (action.dataset.action === "settings-sync-provider-prices") {
        event.preventDefault();
        event.stopPropagation();
        syncCurrentProviderPricesToOthers();
        return;
      }
      if (action.dataset.action === "pricing-export") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand(
          { action: "pricingExport" },
          "正在生成价格 JSON 到 HUD 程序根目录...",
        );
        return;
      }
      if (action.dataset.action === "pricing-open-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        setSettingsStatus("价格 JSON 已保留在 HUD 程序根目录。", "");
        return;
      }
      if (action.dataset.action === "pricing-open") {
        event.preventDefault();
        event.stopPropagation();
        const prompt = action.closest('[data-pricing-export-prompt="true"]');
        const filename = String(prompt?.dataset.pricingFilename || "").trim();
        submitSettingsCommand(
          { action: "pricingOpen", filename },
          "正在打开价格 JSON...",
        );
        return;
      }
      if (action.dataset.action === "pricing-copy-example") {
        event.preventDefault();
        event.stopPropagation();
        copyPricingExample();
        return;
      }
      if (action.dataset.action === "pricing-import-open") {
        event.preventDefault();
        event.stopPropagation();
        openPricingImportDialog();
        return;
      }
      if (action.dataset.action === "pricing-import-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        setSettingsStatus("已取消价格导入。");
        return;
      }
      if (action.dataset.action === "pricing-import-preview") {
        event.preventDefault();
        event.stopPropagation();
        previewPricingImport();
        return;
      }
      if (action.dataset.action === "pricing-import-commit") {
        event.preventDefault();
        event.stopPropagation();
        commitPricingImport();
        return;
      }
      if (action.dataset.action === "pricing-effective-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        setSettingsStatus("已取消价格保存。");
        return;
      }
      if (action.dataset.action === "pricing-effective-confirm") {
        event.preventDefault();
        event.stopPropagation();
        confirmPricingEffectiveAt();
        return;
      }
      if (action.dataset.action === "settings-check-update") {
        event.preventDefault();
        event.stopPropagation();
        void checkUpdateFromModal();
        return;
      }
      if (action.dataset.action === "settings-install-update") {
        event.preventDefault();
        event.stopPropagation();
        void installUpdateFromModal();
        return;
      }
      if (action.dataset.action === "update-action") {
        event.preventDefault();
        event.stopPropagation();
        void runUpdateAction();
        return;
      }
      if (action.dataset.action === "dismiss-warnings-today") {
        event.preventDefault();
        event.stopPropagation();
        dismissWarningsToday();
        return;
      }
      if (action.dataset.action === "settings-export") {
        event.preventDefault();
        event.stopPropagation();
        exportSettingsFromModal();
        return;
      }
      if (action.dataset.action !== "toggle") return;
      const panel = action.closest("[data-panel]");
      const name = panel?.dataset.panel;
      if (!name || !PANEL[name]) return;
      // A drag on a collapsed panel emits a trailing click; swallow it so the
      // panel does not toggle after being moved.
      if (window.__codexHudDragSuppressClick) {
        window.__codexHudDragSuppressClick = false;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      const expanded = panel.dataset.expanded !== "true";
      panel.dataset.expanded = String(expanded);
      setPanelState(name, { expanded });
      syncPosition();
      syncPositionSettled();
      scheduleLayoutReport(expanded ? "toggle-expand" : "toggle-collapse", name);
    });
    rootScope.listen(root, "pointerdown", (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      const settingsModal = event.target?.closest?.(`#${settingsModalId}`);
      if (settingsModal && root.contains(settingsModal)) {
        // Codex also listens for pointerdown outside its own surfaces. Do not
        // let interactions inside the HUD settings dialog trigger that path;
        // the delegated HUD click handler still owns buttons and backdrop.
        event.stopPropagation();
        return;
      }
      const runtimeToggle = event.target?.closest?.("[data-action='runtime-errors-toggle']");
      if (runtimeToggle && root.contains(runtimeToggle)) return;
      const runtimeMove = event.target?.closest?.("[data-action='runtime-errors-move']");
      if (runtimeMove && root.contains(runtimeMove)) {
        event.preventDefault();
        event.stopPropagation();
        beginRuntimeErrorsGesture(event);
        return;
      }
      const action = event.target?.closest?.("[data-action='move'], [data-action='resize'], [data-action='toggle']");
      if (!action || !root.contains(action)) return;
      const panel = action.closest("[data-panel]");
      const name = panel?.dataset.panel;
      if (!name || !PANEL[name]) return;
      const rawAction = action.dataset.action;
      if (rawAction === "resize") {
        event.preventDefault();
        event.stopPropagation();
        beginGesture(event, name, "resize", action.dataset.edge || "", false);
        return;
      }
      const collapsed = panel.dataset.expanded !== "true";
      // Collapsed panels drag from anywhere; a tap without movement still toggles.
      // Expanded panels keep dragging via the header handle only.
      if (rawAction === "toggle" && !collapsed) return;
      // Reset any stale suppress flag from a prior interrupted gesture.
      window.__codexHudDragSuppressClick = false;
      // For the tap-toggle target we must NOT preventDefault here — doing so
      // suppresses the compatibility click event and would break toggling.
      // A real drag is detected via the movement threshold in beginGesture.
      if (rawAction !== "toggle") {
        event.preventDefault();
        event.stopPropagation();
      }
      beginGesture(event, name, "move", "", rawAction === "toggle");
    });
  }

  function markHudStale() {
    const root = document.getElementById(rootId);
    const state = ctx.state.read();
    const payload = state.payload || {};
    const updatedAt = Number(state.updatedAt || 0);
    if (!root || !updatedAt || Date.now() - updatedAt < staleUpdateMs) return;
    if (!payloadNeedsStaleGuard(payload)) return;
    const ageSeconds = Math.max(10, Math.floor((Date.now() - updatedAt) / 1000));
    const existingWarning = String(payload?.topDetails?.warnings || "").trim();
    const staleWarning = `数据可能不是最新，已 ${ageSeconds}s 未同步`;
    setText(root, "topWarnings", existingWarning ? `${existingWarning}\n${staleWarning}` : staleWarning);
    root.querySelectorAll('[data-field-panel="topWarnings"]').forEach((node) => {
      node.hidden = false;
    });
    root.querySelectorAll('[data-field="topLine"]').forEach((node) => {
      node.classList.add(warningClass);
    });
  }

  function payloadNeedsStaleGuard(payload) {
    const requestStatus = String(payload?.requestStatus || "").toLowerCase();
    const updatePhase = String(payload?.updateState?.phase || "").toLowerCase();
    return requestStatus === "running" || updatePhase === "downloading" || updatePhase === "installing";
  }

  function scheduleStaleGuard(payload) {
    ctx.lifecycle.clearTimeout(window[staleTimerName] || 0);
    if (!payloadNeedsStaleGuard(payload)) return;
    window[staleTimerName] = ctx.lifecycle.timeout(
      "stale_guard",
      markHudStale,
      staleUpdateMs + 250,
    );
  }

  function normalizePayloadDomains(payload) {
    const provided = payload?.payloadDomains && typeof payload.payloadDomains === "object"
      ? payload.payloadDomains
      : {};
    const allDomains = ["startup", "currentSession", "sessionSwitch", "budget", "settings", "overlay", "backgroundUsage", "diagnostics", "usageInsights", "sessionCleanup"];
    const domains = {};
    if (Object.keys(provided).length > 0) {
      for (const name of allDomains) {
        if (provided[name] && typeof provided[name] === "object") domains[name] = provided[name];
      }
      return domains;
    }
    for (const name of allDomains) {
      domains[name] = payload || {};
    }
    return domains;
  }

  function renderStartupBubble(root, startup) {
    const bubble = root.querySelector('[data-field="startupBubble"]');
    if (!bubble) return;
    const active = !!startup && typeof startup === "object";
    bubble.hidden = !active;
    if (!active) return;
    const progress = clamp(Number(startup.progress ?? 0) || 0, 0, 100);
    setText(root, "startupStep", startup.step || "正在启动");
    setText(root, "startupTitle", startup.title || "正在打开 Codex HUD");
    setText(root, "startupDetail", startup.detail || "正在准备会话信息");
    setText(root, "startupProgressLabel", `${Math.round(progress)}%`);
    const track = root.querySelector('[data-field="startupProgressTrack"]');
    const fill = root.querySelector('[data-field="startupProgressFill"]');
    if (track) {
      track.setAttribute("aria-valuenow", String(Math.round(progress)));
      track.setAttribute("aria-label", `${startup.step || "启动进度"} ${Math.round(progress)}%`);
    }
    if (fill) fill.style.width = `${progress}%`;
    positionStartupBubble(root);
  }

  function positionStartupBubble(root = document.getElementById(rootId)) {
    const bubble = root?.querySelector?.('[data-field="startupBubble"]');
    if (!bubble || bubble.hidden) return;
    const header = activeSessionHeaderElement();
    const rect = visible(header) ? header.getBoundingClientRect() : null;
    const top = clamp((rect?.bottom || 62) + 10, 12, Math.max(12, innerHeight - 180));
    const right = rect
      ? clamp(innerWidth - rect.right + 14, 12, 28)
      : 16;
    bubble.style.top = px(top);
    bubble.style.right = px(right);
    bubble.style.bottom = "auto";
  }

  function applyOverlayPayload(_root, _payload) {
    // Overlay payload is currently consumed by Python/desktop IPC. Keeping this
    // domain explicit lets renderer updates skip unrelated DOM work.
  }

  function applyPayloadDomains(root, payload, domains) {
    if ("currentSession" in domains) {
      sessionViewDomain.apply(root, { ...(payload || {}), ...(domains.currentSession || {}) }, "currentSession");
    }
    if ("sessionSwitch" in domains) {
      sessionViewDomain.apply(root, { ...(payload || {}), ...(domains.sessionSwitch || {}) }, "sessionSwitch");
    }
    if ("budget" in domains) {
      budgetDomain.apply(root, { ...(payload || {}), ...(domains.budget || {}) });
    }
    if ("settings" in domains) {
      settingsShellDomain.apply(root, { ...(payload || {}), ...(domains.settings || {}) });
    }
    if ("overlay" in domains) {
      applyOverlayPayload(root, { ...(payload || {}), ...(domains.overlay || {}) });
    }
    if ("backgroundUsage" in domains) {
      backgroundUsageDomain.apply(root, { ...(payload || {}), ...(domains.backgroundUsage || {}) });
    }
    if ("diagnostics" in domains) {
      diagnosticsDomain.apply(root, { ...(payload || {}), ...(domains.diagnostics || {}) });
    }
    if ("usageInsights" in domains) {
      usageInsightsDomain.apply(root, { ...(payload || {}), ...(domains.usageInsights || {}) });
    }
    if ("sessionCleanup" in domains) {
      sessionCleanupDomain.apply(root, { ...(payload || {}), ...(domains.sessionCleanup || {}) });
    }
  }

  window.__codexUsageHudUpdate = (payload) => {
    const previousState = ctx.state.read();
    const previousPayload = currentPayload() || {};
    const nextPayload = { ...previousPayload, ...(payload || {}) };
    const domains = normalizePayloadDomains(nextPayload);
    const hasSessionPayload = "currentSession" in domains || "sessionSwitch" in domains;
    const hudHydrated = previousState.hydrated === true || (
      "currentSession" in domains && "budget" in domains
    );
    const previousDomains = previousState.domains && typeof previousState.domains === "object"
      ? previousState.domains
      : (Object.keys(previousPayload).length > 0
        ? normalizePayloadDomains(previousPayload)
        : {});
    const retainedDomains = { ...previousDomains, ...domains };
    if ("sessionSwitch" in domains) cacheActiveSessionPayload(domains.sessionSwitch);
    if ("currentSession" in domains) cacheActiveSessionPayload(domains.currentSession);
    if (
      (!payload?.supportImages || !payload.supportImages.length) &&
      previousPayload.supportImages?.length
    ) {
      nextPayload.supportImages = previousPayload.supportImages;
    }
    const persistedSupportImages = loadPersistedSupportImages();
    if (
      (!nextPayload.supportImages || !nextPayload.supportImages.length) &&
      persistedSupportImages.length
    ) {
      nextPayload.supportImages = persistedSupportImages;
    }
    for (const domainPayload of Object.values(domains)) {
      if (domainPayload && typeof domainPayload === "object") {
        Object.assign(nextPayload, domainPayload);
      }
    }
    if ("startup" in domains) nextPayload.startup = domains.startup;
    if (hasSessionPayload) {
      delete nextPayload.startup;
      delete retainedDomains.startup;
    }
    if (Array.isArray(nextPayload.supportImages) && nextPayload.supportImages.length) {
      persistSupportImages(nextPayload.supportImages);
    }
    ctx.state.write({
      payload: nextPayload,
      domains: retainedDomains,
      hydrated: hudHydrated,
      updatedAt: Date.now(),
    });
    try {
      ensureActiveSessionWatchers();
    } catch (_) {}
    const previousRoot = document.getElementById(rootId);
    const root = ensureRoot();
    if (!root) return false;
    // Codex can replace renderer DOM anchors while the page/JS realm remains
    // alive during cold startup. If our root was removed, hydrate the new root
    // from every retained domain before applying future lightweight updates;
    // otherwise a sessionSwitch-only update produces a text-only HUD with an
    // empty expanded panel.
    const renderedDomains = root === previousRoot ? domains : retainedDomains;
    const renderedSessionPayload = hudHydrated && (
      "currentSession" in renderedDomains || "sessionSwitch" in renderedDomains
    );
    applyPayloadDomains(root, nextPayload, renderedDomains);
    renderStartupBubble(root, nextPayload.startup);
    if (root !== previousRoot) restoreOpenSettingsModal();
    const wasReady = root.dataset.hudReady === "true";
    // A settings/theme/diagnostics partial update follows the first complete
    // payload during startup. It must preserve visible HUD panels rather than
    // treating the absence of a session domain as a new startup state.
    if (renderedSessionPayload) root.dataset.hudReady = "true";
    else if (!wasReady && "startup" in domains) root.dataset.hudReady = "false";
    // Session payloads only need a full anchor calculation when the HUD first
    // becomes visible. Subsequent session switches update text in place; the
    // targeted resize/mutation observers own later layout changes.
    if (renderedSessionPayload && !wasReady) {
      syncPosition();
      if (!cachedHeaderNode || !cachedComposerNode) syncPositionSettled();
    }
    scheduleStaleGuard(nextPayload);
    return true;
  };

  window.__codexUsageHudRemove = () => {
    const root = document.getElementById(rootId);
    root?.querySelectorAll(".codex-usage-hud-line").forEach(cancelNumericAnimation);
    ctx.teardown.run();
    root?.remove();
    document.getElementById(styleId)?.remove();
    observedHeaderNode = null;
    observedComposerNode = null;
    restReminderCountdownTimer = 0;
    sessionCleanupElapsedTimer = 0;
    storageRefreshRaf = 0;
    storageRefreshTimer = 0;
    delete window[mutationObserverName];
    delete window[resizeObserverName];
    delete window[bootstrapObserverName];
    delete window[bootstrapTimerName];
    delete window[activeSessionBootstrapTimerName];
    delete window[resizeHandlerName];
    delete window[scrollHandlerName];
    delete window[scheduleName];
    delete window[rafName];
    delete window[runningTimerName];
    delete window[staleTimerName];
    delete window[themeObserverName];
    delete window[themeMediaQueryName];
    delete window[themeMediaQueryHandlerName];
    delete window[themeStorageHandlerName];
    delete window[themeTimerName];
    delete window[themeSignatureName];
    delete window[composerSettleTimerName];
    delete window[settleTimerName];
    delete window[composerInputNodeName];
    delete window[composerInputHandlersName];
    delete window[composerFocusStateName];
    delete window[composerBadgeRafName];
    delete window[modelPickerPatchHandlerName];
    delete window[modelPickerPatchRafName];
    delete window[modelPickerPatchTimersName];
    delete window[modelPickerSelectionName];
    delete window.__codexUsageHudReportActiveSession;
    delete window.__codexUsageHudReportTheme;
    delete window.__codexUsageHudUpdate;
    delete window.__codexUsageHudRemove;
    return true;
  };

  window[scheduleName] = () => scheduleForPanels(Object.keys(PANEL), { invalidateTop: true });
  window[resizeHandlerName] = window[scheduleName];
  window[scrollHandlerName] = () => scheduleForPanels(["request"]);
  ctx.lifecycle.listen("layout", window, "resize", window[resizeHandlerName]);
  ctx.lifecycle.listen("layout", window, "scroll", window[scrollHandlerName], true);
  modelPickerDomain.install();
  themeDomain.install();
  diagnosticsDomain.install();
  budgetDomain.install();
  restReminderDomain.install();
  sessionViewDomain.install();
  usageInsightsDomain.install();
  sessionCleanupDomain.install();
  backgroundUsageDomain.install();
  settingsShellDomain.install();
  layoutDomain.install();
  composerDomain.install();
  activeSessionDomain.install();
  // Legacy contract marker: ctx.teardown.add("theme", stopRendererThemeObserver);
  ctx.teardown.add("active_session", () => {
    try {
      removeActiveSessionWatchers();
    } catch (_) {}
  });
  ctx.teardown.add("composer", detachComposerInputWatchers);
  ctx.teardown.add("layout", () => {
    stopBootstrapObserver();
  });
  const boot = () => {
    const state = ctx.state.read();
    if (state?.payload) {
      window.__codexUsageHudUpdate(state.payload);
    } else {
      // The new-document script can run before Python has a real session
      // payload. Do not create top/bottom panels here: a visible empty HUD is
      // both misleading and needlessly causes layout work while Codex loads.
      startBootstrapObserver();
    }
    restoreOpenSettingsModal();
    modelPickerDomain.apply();
  };
  if (document.body) {
    boot();
  } else {
    ctx.lifecycle.listen("bootstrap", document, "DOMContentLoaded", boot, { once: true });
  }
})()
"""

__all__ = ["TEXT"]
