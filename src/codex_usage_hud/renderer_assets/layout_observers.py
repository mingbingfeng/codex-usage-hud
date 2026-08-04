"""Static raw Renderer layout asset fragment."""

TEXT = r"""
      function syncPosition(names = Object.keys(PANEL)) {
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
          const widthOverride = name === "request" && state.anchorSource === "footer-gap" && state.widthRatio
            ? null
            : state.width;
          const anchor = name === "top"
            ? topAnchor(height, widthOverride)
            : requestAnchor(height, widthOverride);
          let { left, top, width } = anchor;
          if (state.manual) {
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

      function syncPositionSettled(names = Object.keys(PANEL)) {
        for (const timer of (window[settleTimerName] || [])) ctx.lifecycle.clearTimeout(timer);
        window[settleTimerName] = [
          ctx.lifecycle.timeout("layout_settle", () => syncPosition(names), 50),
          ctx.lifecycle.timeout("layout_settle", () => syncPosition(names), 140),
          ctx.lifecycle.timeout("layout_settle", () => syncPosition(names), 260),
        ];
      }

      function scheduleForPanels(names = Object.keys(PANEL), { invalidateTop = false } = {}) {
        const panelNames = Array.isArray(names) ? names.filter((name) => PANEL[name]) : Object.keys(PANEL);
        if (invalidateTop || panelNames.includes("top")) topSlotCache = null;
        if (!pendingSyncPanels) pendingSyncPanels = new Set();
        for (const name of panelNames) pendingSyncPanels.add(name);
        ctx.frames.cancel("layout");
        window[rafName] = ctx.frames.schedule("layout", () => {
          const nextPanels = Array.from(pendingSyncPanels || Object.keys(PANEL));
          pendingSyncPanels = null;
          syncPosition(nextPanels);
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
        let touchesHeader = false;
        let touchesComposer = false;
        let touchesTextInput = false;
        for (const mutation of mutations) {
          const target = layoutMutationTarget(mutation, headerNode, composerNode);
          if (target === "header") touchesHeader = true;
          if (target === "composer") {
            touchesComposer = true;
            if (layoutMutationTouchesTextInput(mutation)) touchesTextInput = true;
          }
        }
        if (touchesHeader) {
          invalidateHeaderAnchor();
          scheduleForPanels(Object.keys(PANEL), { invalidateTop: true });
          return;
        }
        if (!touchesComposer) return;
        if (touchesTextInput) {
          scheduleRequestAfterComposerSettles();
          return;
        }
        invalidateComposerAnchor();
        scheduleForPanels(["request"]);
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
        if (typeof ResizeObserver === "function") {
          window[resizeObserverName] = ctx.observers.set("layout_resize", new ResizeObserver(() => scheduleForPanels(Object.keys(PANEL), { invalidateTop: true })));
          if (headerNode) window[resizeObserverName].observe(headerNode);
          if (composerNode && composerNode !== headerNode) window[resizeObserverName].observe(composerNode);
        } else {
          window[resizeObserverName] = { disconnect() {} };
        }
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
