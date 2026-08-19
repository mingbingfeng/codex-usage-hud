"""Static raw Renderer layout asset fragment."""

TEXT = r"""
      function syncPosition(names = Object.keys(PANEL), { forceAutoFit = false } = {}) {
        const root = document.getElementById(rootId);
        if (!root) return;
        positionStartupBubble(root);
        positionRestReminderBubble(root);
        applyPanelStates(root);
        const panelNames = Array.isArray(names) ? names.filter((name) => PANEL[name]) : Object.keys(PANEL);
        for (const name of panelNames) {
          const panel = root.querySelector(`[data-panel="${name}"]`);
          if (!panel) continue;
          const state = getPanelState(name);
          const expanded = panel.dataset.expanded === "true";
          const height = desiredHeight(name, state, expanded);
          const widthOverride = forceAutoFit
            ? null
            : name === "request" && state.anchorSource === "footer-gap" && state.widthRatio
            ? null
            : state.width;
          const anchor = name === "top"
            ? topAnchor(height, widthOverride)
            : requestAnchor(height, widthOverride);
          let { left, top, width } = anchor;
          if (state.manual && !forceAutoFit) {
            const relativeRequest = name === "request"
              ? manualRequestRect(state, anchor, expanded, height)
              : null;
            const relativeTop = name === "top"
              ? manualTopRect(state, anchor, expanded, height)
              : null;
            if (relativeRequest || relativeTop) {
              ({ left, top, width } = relativeRequest || relativeTop);
            } else {
              width = desiredWidth(name, state, expanded, anchor.width, anchor.maxWidth);
              left = clamp(Number(state.x ?? anchor.left), 8, Math.max(8, innerWidth - width - 8));
              top = clamp(Number(state.y ?? anchor.top), 8, Math.max(8, innerHeight - height - 8));
            }
          }
          applyRect(panel, left, top, width, height);
        }
        refreshAllMarquees(root);
        applyRuntimeErrorsPanelState();
        refreshLayoutObservers();
        startBootstrapObserver();
      }

      function syncPositionSettled(names = Object.keys(PANEL), options = {}) {
        for (const timer of (window[settleTimerName] || [])) ctx.lifecycle.clearTimeout(timer);
        window[settleTimerName] = [
          ctx.lifecycle.timeout("layout_settle", () => syncPosition(names, options), 50),
          ctx.lifecycle.timeout("layout_settle", () => syncPosition(names, options), 140),
          ctx.lifecycle.timeout("layout_settle", () => syncPosition(names, options), 260),
        ];
      }

      function scheduleForPanels(names = Object.keys(PANEL), { invalidateTop = false, forceAutoFit = false } = {}) {
        const panelNames = Array.isArray(names) ? names.filter((name) => PANEL[name]) : Object.keys(PANEL);
        if (invalidateTop || panelNames.includes("top")) topSlotCache = null;
        pendingForceAutoFit = pendingForceAutoFit || forceAutoFit;
        if (!pendingSyncPanels) pendingSyncPanels = new Set();
        for (const name of panelNames) pendingSyncPanels.add(name);
        ctx.frames.cancel("layout");
        window[rafName] = ctx.frames.schedule("layout", () => {
          const nextPanels = Array.from(pendingSyncPanels || Object.keys(PANEL));
          pendingSyncPanels = null;
          const nextForceAutoFit = pendingForceAutoFit;
          pendingForceAutoFit = false;
          syncPosition(nextPanels, { forceAutoFit: nextForceAutoFit });
        });
      }

      function scheduleRequestAfterComposerSettles() {
        ctx.lifecycle.clearTimeout(window[composerSettleTimerName] || 0);
        window[composerSettleTimerName] = ctx.lifecycle.timeout("composer_layout", () => {
          window[composerSettleTimerName] = 0;
          scheduleForPanels(["request"]);
        }, 180);
      }

      function layoutMutationTouchesTextInput(mutation) {
        const element = elementFromMutationNode(mutation.target);
        return !!element?.closest?.("textarea, [contenteditable='true'], [role='textbox']");
      }

      function headerTitleScopeSelector() {
        return [
          "[data-thread-title]",
          "[data-testid*='thread-title' i]",
          "[data-testid*='conversation-title' i]",
          // Codex Desktop's current title button is nested under the title
          // row. Keep this structural fallback narrow so arbitrary header
          // headings and truncation changes do not retrigger layout.
          ".text-md > .flex > .min-w-0.truncate > span > button",
        ].join(", ");
      }

      function nodeTouchesHeaderTitleScope(node) {
        const element = elementFromMutationNode(node);
        if (!element || element.closest?.(`#${rootId}`)) return false;
        const selector = headerTitleScopeSelector();
        const titleButton = element.closest?.("button");
        return !!(
          element.matches?.(selector)
          || element.closest?.(selector)
          // A characterData mutation can target a node inside the title
          // button, so test that button against the narrow title structure.
          || titleButton?.matches?.(selector)
        );
      }

      function mutationTouchesHeaderTitleScope(mutation) {
        if (nodeTouchesHeaderTitleScope(mutation.target)) return true;
        const selector = headerTitleScopeSelector();
        for (const node of [...(mutation.addedNodes || []), ...(mutation.removedNodes || [])]) {
          if (nodeTouchesHeaderTitleScope(node)) return true;
          const element = elementFromMutationNode(node);
          if (element?.querySelector?.(selector)) return true;
        }
        return false;
      }

      function isComposerModeControl(element) {
        const control = element?.closest?.("button, [role='button'], [role='radio'], input[type='radio']");
        if (!control || control.closest?.(`#${rootId}`)) return false;
        const identity = [
          control.getAttribute?.("data-testid"),
          control.getAttribute?.("aria-label"),
          control.getAttribute?.("title"),
          control.dataset?.mode,
          control.dataset?.composerMode,
          control.textContent,
        ].filter(Boolean).join(" ").trim();
        return /(?:^|[\\s_-])(plan|goal)(?:$|[\\s_-])|计划|目标/i.test(identity);
      }

      function mutationTouchesComposerModeControl(mutation) {
        if (isComposerModeControl(elementFromMutationNode(mutation.target))) return true;
        for (const node of [...(mutation.addedNodes || []), ...(mutation.removedNodes || [])]) {
          const element = elementFromMutationNode(node);
          if (isComposerModeControl(element)) return true;
          if (element?.querySelector?.("[data-testid*='plan' i], [data-testid*='goal' i], [data-mode*='plan' i], [data-mode*='goal' i], [data-composer-mode]")) return true;
        }
        return false;
      }

      function layoutMutationTarget(mutation, headerNode, composerNode) {
        const element = elementFromMutationNode(mutation.target);
        if (!element || element.closest?.(`#${rootId}`)) return "";
        if (headerNode && (element === headerNode || headerNode.contains(element))) return "header";
        if (composerNode && (element === composerNode || composerNode.contains(element))) return "composer";
        return "";
      }

      function handleLayoutMutations(mutations) {
        const headerNode = cachedHeaderNode;
        const composerNode = cachedComposerNode;
        let touchesHeaderTitle = false;
        let touchesComposerMode = false;
        for (const mutation of mutations) {
          const target = layoutMutationTarget(mutation, headerNode, composerNode);
          if (target === "header" && mutationTouchesHeaderTitleScope(mutation)) {
            touchesHeaderTitle = true;
          }
          if (target === "composer") {
            // Typing in the active conversation must not repeatedly measure
            // HUD geometry. Non-text composer mutations still cover mode
            // controls such as Plan/Goal, whose footer layout can change.
            if (!layoutMutationTouchesTextInput(mutation) && mutationTouchesComposerModeControl(mutation)) {
              touchesComposerMode = true;
            }
          }
        }
        if (touchesHeaderTitle) {
          invalidateHeaderAnchor();
          scheduleForPanels(["top"], { invalidateTop: true });
          syncPositionSettled(["top"]);
        }
        if (touchesComposerMode) {
          invalidateComposerAnchor();
          scheduleForPanels(["request"]);
        }
      }

      function refreshLayoutObservers() {
        const headerNode = conversationHeaderElement();
        const composerNode = composerElement();
        if (
          headerNode === observedHeaderNode
          && composerNode === observedComposerNode
          && window[mutationObserverName]
          && window[resizeObserverName]
        ) return;
        observedHeaderNode = headerNode;
        observedComposerNode = composerNode;
        ctx.observers.clear("layout");
        ctx.observers.clear("layout_resize");
        window[mutationObserverName] = ctx.observers.set("layout", new MutationObserver(handleLayoutMutations));
        const mutationOptions = {
          childList: true,
          subtree: true,
          characterData: true,
          attributes: true,
          attributeFilter: ["aria-label", "title", "data-thread-title", "class"],
        };
        if (headerNode) window[mutationObserverName].observe(headerNode, mutationOptions);
        if (composerNode && composerNode !== headerNode) window[mutationObserverName].observe(composerNode, mutationOptions);
        // Session switch, async title completion, and Plan/Goal selection
        // are handled by the targeted observers above. A generic resize
        // observer also fires while typing, which violates the per-session
        // no-relayout contract.
        window[resizeObserverName] = { disconnect() {} };
        ensureComposerInputWatchers();
      }

      function stopBootstrapObserver() {
        ctx.observers.clear("layout_bootstrap");
        ctx.lifecycle.clearTimeout(window[bootstrapTimerName] || 0);
        delete window[bootstrapObserverName];
        delete window[bootstrapTimerName];
      }

      function startBootstrapObserver() {
        if (cachedHeaderNode && cachedComposerNode) {
          stopBootstrapObserver();
          return;
        }
        if (window[bootstrapObserverName] || !document.body) return;
        window[bootstrapObserverName] = ctx.observers.set("layout_bootstrap", new MutationObserver(() => {
          invalidateHeaderAnchor();
          invalidateComposerAnchor();
          const headerNode = conversationHeaderElement();
          const composerNode = composerElement();
          if (!headerNode && !composerNode) return;
          scheduleForPanels(Object.keys(PANEL), { invalidateTop: true });
          if (headerNode && composerNode) stopBootstrapObserver();
        }));
        window[bootstrapObserverName].observe(document.body, { childList: true, subtree: true });
        window[bootstrapTimerName] = ctx.lifecycle.timeout(
          "layout_bootstrap",
          stopBootstrapObserver,
          5000,
        );
      }

      function headerScopeSelector() {
        return [
          "header.app-header-tint",
          "[data-testid='app-shell-header']",
          "[data-testid='app-shell-header-context-menu-surface']",
          ".app-header-tint",
        ].join(", ");
      }

      function elementFromMutationNode(node) {
        if (!node) return null;
        return node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
      }

      function nodeTouchesHeaderScope(node) {
        const element = elementFromMutationNode(node);
        if (!element || element.closest?.(`#${rootId}`)) return false;
        const selector = headerScopeSelector();
        return !!(
          element.closest?.(selector)
          || element.matches?.(selector)
          || element.querySelector?.(selector)
        );
      }

      function composerScopeSelector() {
        return [
          ".composer-footer",
          "textarea",
          "[contenteditable='true']",
          "[role='textbox']",
          ".bg-token-input-background\\/90",
        ].join(", ");
      }

      function nodeTouchesComposerScope(node) {
        const element = elementFromMutationNode(node);
        if (!element || element.closest?.(`#${rootId}`)) return false;
        const selector = composerScopeSelector();
        return !!(
          element.closest?.(selector)
          || element.matches?.(selector)
          || element.querySelector?.(selector)
        );
      }

      function mutationTouchesComposerScope(mutation) {
        if (nodeTouchesComposerScope(mutation.target)) return true;
        for (const node of mutation.addedNodes || []) {
          if (nodeTouchesComposerScope(node)) return true;
        }
        for (const node of mutation.removedNodes || []) {
          if (nodeTouchesComposerScope(node)) return true;
        }
        return false;
      }

      function mutationTouchesTextInput(mutation) {
        const element = elementFromMutationNode(mutation.target);
        return !!element?.closest?.("textarea, [contenteditable='true'], [role='textbox']");
      }

      function mutationTouchesHeaderScope(mutation) {
        if (nodeTouchesHeaderScope(mutation.target)) return true;
        for (const node of mutation.addedNodes || []) {
          if (nodeTouchesHeaderScope(node)) return true;
        }
        for (const node of mutation.removedNodes || []) {
          if (nodeTouchesHeaderScope(node)) return true;
        }
        return false;
      }

    function install() {
      return true;
    }

    function apply(_root, _payload) {
      return true;
    }

    function dispose() {
      return true;
    }

    return {
      install,
      apply,
      dispose,
      ensureStyle,
      resizeEdgesMarkup,
      topExpandedResizeMarkup,
      requestExpandedResizeMarkup,
      backgroundUsageNotificationMarkup,
      panelMarkup,
      topExpandedMarkup,
      requestExpandedMarkup,
      beginGesture,
      beginRuntimeErrorsGesture,
      minWidthFor,
      minHeightFor,
      manualPatchFor,
      desiredWidth,
      desiredHeight,
      applyRect,
      applyRuntimeErrorsPanelState,
      anchorUsable,
      invalidateHeaderAnchor,
      invalidateComposerAnchor,
      candidateHeaders,
      scoreHeader,
      conversationHeaderElement,
      conversationHeaderRect,
      hasAllClasses,
      scoreComposer,
      headerLeftControlEdge,
      headerTitleTextEdge,
      headerRightControlStart,
      headerControlButtons,
      headerLayoutSignature,
      topTitlebarSlot,
      topHeaderSlot,
      topAnchor,
      footerControlRects,
      footerGapSlot,
      requestFallbackAnchor,
      requestAnchor,
      manualRequestRect,
      manualTopRect,
      syncPosition,
      syncPositionSettled,
      scheduleForPanels,
      scheduleRequestAfterComposerSettles,
      layoutMutationTouchesTextInput,
      headerTitleScopeSelector,
      nodeTouchesHeaderTitleScope,
      mutationTouchesHeaderTitleScope,
      isComposerModeControl,
      mutationTouchesComposerModeControl,
      layoutMutationTarget,
      handleLayoutMutations,
      refreshLayoutObservers,
      stopBootstrapObserver,
      startBootstrapObserver,
      headerScopeSelector,
      elementFromMutationNode,
      nodeTouchesHeaderScope,
      composerScopeSelector,
      nodeTouchesComposerScope,
      mutationTouchesComposerScope,
      mutationTouchesTextInput,
      mutationTouchesHeaderScope,
    };
  }

  const layoutDomain = ctx.domains.register(
    "layout",
    createLayoutDomain(ctx, shared),
  );
  const {
    ensureStyle,
    resizeEdgesMarkup,
    topExpandedResizeMarkup,
    requestExpandedResizeMarkup,
    backgroundUsageNotificationMarkup,
    panelMarkup,
    topExpandedMarkup,
    requestExpandedMarkup,
    beginGesture,
    beginRuntimeErrorsGesture,
    minWidthFor,
    minHeightFor,
    manualPatchFor,
    desiredWidth,
    desiredHeight,
    applyRect,
    applyRuntimeErrorsPanelState,
    anchorUsable,
    invalidateHeaderAnchor,
    invalidateComposerAnchor,
    candidateHeaders,
    scoreHeader,
    conversationHeaderElement,
    conversationHeaderRect,
    hasAllClasses,
    scoreComposer,
    headerLeftControlEdge,
    headerTitleTextEdge,
    headerRightControlStart,
    headerControlButtons,
    headerLayoutSignature,
    topTitlebarSlot,
    topHeaderSlot,
    topAnchor,
    footerControlRects,
    footerGapSlot,
    requestFallbackAnchor,
    requestAnchor,
    manualRequestRect,
    manualTopRect,
    syncPosition,
    syncPositionSettled,
    scheduleForPanels,
    scheduleRequestAfterComposerSettles,
    layoutMutationTouchesTextInput,
    headerTitleScopeSelector,
    nodeTouchesHeaderTitleScope,
    mutationTouchesHeaderTitleScope,
    isComposerModeControl,
    mutationTouchesComposerModeControl,
    layoutMutationTarget,
    handleLayoutMutations,
    refreshLayoutObservers,
    stopBootstrapObserver,
    startBootstrapObserver,
    headerScopeSelector,
    elementFromMutationNode,
    nodeTouchesHeaderScope,
    composerScopeSelector,
    nodeTouchesComposerScope,
    mutationTouchesComposerScope,
    mutationTouchesTextInput,
    mutationTouchesHeaderScope,
  } = layoutDomain;
"""

__all__ = ["TEXT"]
