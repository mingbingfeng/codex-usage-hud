"""Static raw Renderer layout asset fragment."""

TEXT = r"""
      function minWidthFor(name, expanded) {
        return expanded ? PANEL[name].minExpandedWidth : PANEL[name].minCollapsedWidth;
      }

      function minHeightFor(name, expanded) {
        return expanded ? PANEL[name].minExpandedHeight : PANEL[name].collapsedHeight;
      }

      function manualPatchFor(name, left, top, width, anchor, base = {}) {
        const patch = {
          ...base,
          x: Math.round(left),
          y: Math.round(top),
        };
        if (name !== "request" || anchor?.source !== "footer-gap" || !anchor.area) {
          return patch;
        }
        const area = anchor.area;
        const free = Math.max(0, area.width - width);
        const patchHeight = Number(
          base.expandedHeight || base.collapsedHeight || anchor.height || 0
        );
        patch.anchorSource = anchor.source;
        patch.xRatio = free > 0 ? clamp((left - area.left) / free, 0, 1) : 0.5;
        patch.yOffset = Math.round(top - anchor.top);
        patch.bottomOffset = Number.isFinite(patchHeight) && patchHeight > 0
          ? Math.round((top + patchHeight) - (anchor.top + anchor.height))
          : 0;
        patch.widthRatio = clamp(width / Math.max(1, area.width), 0.1, 1);
        return patch;
      }

      function desiredWidth(name, state, expanded, fallback, maxWidthOverride) {
        const minWidth = minWidthFor(name, expanded);
        const base = Number(state.width || fallback || PANEL[name].fallbackWidth);
        const maxWidth = Number(maxWidthOverride || innerWidth - 16);
        return clamp(base, minWidth, Math.max(minWidth, maxWidth));
      }

      function desiredHeight(name, state, expanded, fallback) {
        const key = expanded ? "expandedHeight" : "collapsedHeight";
        const base = Number(state[key] || fallback || (expanded ? PANEL[name].expandedHeight : PANEL[name].collapsedHeight));
        return clamp(base, minHeightFor(name, expanded), Math.max(minHeightFor(name, expanded), innerHeight - 16));
      }

      function applyRect(panel, left, top, width, height) {
        panel.style.left = px(left);
        panel.style.top = px(top);
        panel.style.right = "auto";
        panel.style.bottom = "auto";
        panel.style.width = px(width);
        panel.style.height = px(height);
      }

      function applyRuntimeErrorsPanelState(panel = document.querySelector(`#${rootId} [data-field="runtimeErrorsPanel"]`)) {
        if (!panel || panel.hidden) return;
        const rect = panel.getBoundingClientRect();
        const state = getRuntimeErrorsPanelState();
        const hasManual = Number.isFinite(Number(state.x)) && Number.isFinite(Number(state.y));
        const width = Math.max(1, rect.width || 520);
        const height = Math.max(1, rect.height || 120);
        const left = hasManual
          ? clamp(Number(state.x), 8, Math.max(8, innerWidth - width - 8))
          : 16;
        const top = hasManual
          ? clamp(Number(state.y), 8, Math.max(8, innerHeight - height - 8))
          : clamp(innerHeight - height - 16, 8, Math.max(8, innerHeight - height - 8));
        panel.style.left = px(left);
        panel.style.top = px(top);
        panel.style.right = "auto";
        panel.style.bottom = "auto";
        if (hasManual) setRuntimeErrorsPanelState({ x: Math.round(left), y: Math.round(top) });
      }

      function anchorUsable(node) {
        return node instanceof HTMLElement && node.isConnected && !node.closest?.(`#${rootId}`) && visible(node);
      }

      function invalidateHeaderAnchor() {
        cachedHeaderNode = null;
        topSlotCache = null;
      }

      function invalidateComposerAnchor() {
        cachedComposerNode = null;
      }

      function candidateHeaders() {
        return Array.from(document.querySelectorAll([
          "header.app-header-tint",
          "[data-testid='app-shell-header']",
          "[data-testid='app-shell-header-context-menu-surface']",
          ".app-header-tint",
        ].join(", "))).filter(visible);
      }

      function scoreHeader(node) {
        const rect = node.getBoundingClientRect();
        const text = normalize(node.textContent);
        let score = 0;
        if (node.tagName === "HEADER") score += 80;
        if (node.classList.contains("app-header-tint")) score += 35;
        if (node.matches?.("[data-testid='app-shell-header-context-menu-surface']")) score += 140;
        if (node.closest?.("header.app-header-tint")) score += 120;
        if (String(node.className || "").includes("top-toolbar-sm")) score += 110;
        if (rect.top > 20) score += 95;
        if (rect.top <= 4) score -= 140;
        if (rect.width > 300) score += 25;
        if (rect.height >= 34 && rect.height <= 80) score += 30;
        if (/File\s*Edit\s*View\s*Window\s*Help/i.test(text) || text === "FileEditViewWindowHelp") score -= 300;
        if (text && !/File\s*Edit\s*View\s*Window\s*Help/i.test(text)) score += Math.min(20, text.length);
        return score;
      }

      function conversationHeaderElement() {
        if (anchorUsable(cachedHeaderNode)) return cachedHeaderNode;
        cachedHeaderNode = null;
        const surface = document.querySelector('[data-testid="app-shell-header-context-menu-surface"]');
        const surfaceHeader = surface?.closest?.("header.app-header-tint, header, .app-header-tint");
        if (anchorUsable(surfaceHeader)) {
          cachedHeaderNode = surfaceHeader;
          return cachedHeaderNode;
        }
        cachedHeaderNode = candidateHeaders()
          .map((node, index) => ({ node, index, score: scoreHeader(node) }))
          .sort((left, right) => (right.score - left.score) || (left.index - right.index))[0]?.node || null;
        return cachedHeaderNode;
      }

      function conversationHeaderRect() {
        const header = conversationHeaderElement();
        return visible(header) ? header.getBoundingClientRect() : null;
      }

      function hasAllClasses(node, classes) {
        const set = new Set(String(node?.className || "").split(/\s+/).filter(Boolean));
        return classes.every((name) => set.has(name));
      }

      function scoreComposer(node) {
        if (!visible(node)) return -Infinity;
        const rect = node.getBoundingClientRect();
        let score = 0;
        if (rect.bottom > innerHeight * 0.55) score += 80;
        if (rect.width >= 300 && rect.width <= Math.max(420, innerWidth * .82)) score += 36;
        if (node.querySelector?.(".composer-footer")) score += 32;
        if (node.querySelector?.("textarea, [contenteditable='true']")) score += 48;
        if (node.matches?.(".composer-footer")) score -= 20;
        if (rect.height >= 56 && rect.height <= 190) score += 30;
        score += Math.min(18, Array.from(node.querySelectorAll?.("button, [role='button']") || []).filter(visible).length * 2);
        score -= Math.max(0, (innerHeight * 0.45 - rect.top) / 10);
        score -= Math.max(0, (rect.height - 220) / 5);
        return score;
      }

      function headerLeftControlEdge(headerNode, header, controls = headerControlButtons(headerNode, header)) {
        const leftControls = controls
          .map((item) => item.rect)
          .filter((rect) => rect.left < header.left + (header.width * .55));
        if (!leftControls.length) return 0;
        return Math.max(...leftControls.map((rect) => rect.right - header.left)) + 14;
      }

      function headerTitleTextEdge(headerNode, header) {
        if (!headerNode) return 0;
        const maxTextWidth = Math.min(520, header.width * .55);
        const textRects = Array.from(headerNode.querySelectorAll("span, h1, h2, [data-thread-title]"))
          .filter((node) => visible(node) && !node.closest(`#${rootId}`))
          .filter((node) => normalize(node.textContent).length > 0)
          .map((node) => node.getBoundingClientRect())
          .filter((rect) => (
            rect.width > 0
            && rect.height > 0
            && rect.left >= header.left - 2
            && rect.right <= header.right + 2
            && rect.top >= header.top - 2
            && rect.bottom <= header.bottom + 2
            && rect.width <= maxTextWidth
            && rect.left < header.left + (header.width * .68)
          ));
        if (!textRects.length) return 0;
        return Math.max(...textRects.map((rect) => rect.right - header.left)) + 14;
      }

      function headerRightControlStart(headerNode, header, controls = headerControlButtons(headerNode, header)) {
        const rightControls = controls
          .map((item) => item.rect)
          .filter((rect) => rect.right > header.right - Math.min(260, Math.max(160, header.width * .24)));
        if (!rightControls.length) return header.right;
        return Math.min(...rightControls.map((rect) => rect.left));
      }

      function headerControlButtons(headerNode, header) {
        if (!headerNode || !header) return [];
        return Array.from(headerNode.querySelectorAll("button, [role='button'], a"))
          .filter((node) => visible(node) && !node.closest(`#${rootId}`))
          .map((node, index) => ({ node, index, rect: node.getBoundingClientRect(), label: normalize([
            node.getAttribute("aria-label"),
            node.getAttribute("title"),
            node.textContent,
          ].filter(Boolean).join(" ")) }))
          .filter((item) => (
            item.rect.width > 0
            && item.rect.height > 0
            && item.rect.left >= header.left - 2
            && item.rect.right <= header.right + 2
            && item.rect.top >= header.top - 2
            && item.rect.bottom <= header.bottom + 2
          ))
          .sort((left, right) => (left.rect.left - right.rect.left) || (left.index - right.index));
      }

      function headerLayoutSignature(headerNode, header, controls) {
        const textParts = Array.from(headerNode.querySelectorAll("span, h1, h2, [data-thread-title]"))
          .filter((node) => visible(node) && !node.closest(`#${rootId}`))
          .filter((node) => normalize(node.textContent).length > 0)
          .map((node) => {
            const rect = node.getBoundingClientRect();
            return [
              normalize(node.textContent).slice(0, 120),
              Math.round(rect.left - header.left),
              Math.round(rect.right - header.left),
            ].join("@");
          })
          .join("|");
        const controlParts = controls
          .map((item) => [
            item.label.slice(0, 80),
            Math.round(item.rect.left - header.left),
            Math.round(item.rect.right - header.left),
          ].join("@"))
          .join("|");
        return `${textParts}::${controlParts}`;
      }

      function topTitlebarSlot(headerNode, header) {
        if (!headerNode || !header) return null;
        const controls = headerControlButtons(headerNode, header);
        const cacheKey = [
          Math.round(innerWidth),
          Math.round(header.left),
          Math.round(header.right),
          Math.round(header.top),
          Math.round(header.bottom),
          headerLayoutSignature(headerNode, header, controls),
        ].join(":");
        if (topSlotCache?.key === cacheKey) return topSlotCache.slot;
        const chatActions = controls.find((item) => /chat actions/i.test(item.label));
        const openIn = controls.find((item) => /^open in\b/i.test(item.label));
        const titleEdge = headerTitleTextEdge(headerNode, header);
        const leftControlEdge = headerLeftControlEdge(headerNode, header, controls);
        const fallbackLeft = Math.max(160, Math.min(header.width * .14, 240));
        const left = clamp(
          (chatActions ? chatActions.rect.right + 10 : header.left + Math.max(fallbackLeft, titleEdge, leftControlEdge)),
          header.left + 8,
          header.right - 8
        );
        const rightMargin = Math.max(12, header.width * .04);
        const right = clamp(
          (openIn ? openIn.rect.left - 10 : Math.min(header.right - rightMargin, headerRightControlStart(headerNode, header, controls) - 10)),
          left,
          header.right - 8
        );
        if (right <= left) return null;
        const slot = { left, right, width: Math.max(1, right - left), cacheKey };
        topSlotCache = { key: cacheKey, slot };
        return slot;
      }

      function topHeaderSlot(headerNode, header) {
        const titlebarSlot = topTitlebarSlot(headerNode, header);
        if (titlebarSlot) return titlebarSlot;
        const controls = headerControlButtons(headerNode, header);
        const titleEdge = headerTitleTextEdge(headerNode, header);
        const leftControlEdge = headerLeftControlEdge(headerNode, header, controls);
        const fallbackLeft = Math.max(160, Math.min(header.width * .14, 240));
        const left = clamp(
          header.left + Math.max(fallbackLeft, titleEdge, leftControlEdge),
          header.left + 8,
          header.right - 8
        );
        const rightMargin = Math.max(12, header.width * .04);
        const right = clamp(
          Math.min(header.right - rightMargin, headerRightControlStart(headerNode, header, controls) - 10),
          left,
          header.right - 8
        );
        return { left, right, width: Math.max(1, right - left) };
      }

      function topAnchor(height, widthOverride) {
        const headerNode = conversationHeaderElement();
        const header = visible(headerNode) ? headerNode.getBoundingClientRect() : null;
        const minWidth = minWidthFor("top", height > PANEL.top.collapsedHeight);
        if (!header) {
          const width = clamp(widthOverride || PANEL.top.fallbackWidth, minWidth, Math.max(minWidth, innerWidth - 16));
          return { left: innerWidth - width - 12, top: 12, width, height };
        }
        const slot = topHeaderSlot(headerNode, header);
        const fitMinWidth = Math.min(minWidth, Math.max(1, slot.width));
        const defaultWidth = Math.min(PANEL.top.fallbackWidth, slot.width);
        const width = clamp(widthOverride || defaultWidth, fitMinWidth, Math.max(fitMinWidth, slot.width));
        const left = clamp(slot.left, 8, Math.max(8, slot.right - width));
        const top = clamp(header.top + Math.max(0, (header.height - PANEL.top.collapsedHeight) / 2), 8, Math.max(8, innerHeight - height - 8));
        return {
          left,
          top,
          width,
          height,
          source: "header-slot",
          maxWidth: slot.width,
          area: { left: slot.left, top, width: slot.width, height, right: slot.right },
        };
      }

      function footerControlRects(composerNode, composer) {
        if (!composerNode) return [];
        const candidates = Array.from(composerNode.querySelectorAll("button, [role='button']"))
          .filter((node) => visible(node) && !node.closest(`#${rootId}`))
          .map((node) => node.getBoundingClientRect())
          .filter((rect) => (
            rect.width > 0
            && rect.height > 0
            && rect.left >= composer.left - 2
            && rect.right <= composer.right + 2
            && rect.top >= composer.top - 2
            && rect.bottom <= Math.min(innerHeight, composer.bottom + 8)
          ));
        if (!candidates.length) return [];
        const lowestBottom = Math.max(...candidates.map((rect) => rect.bottom));
        return candidates
          .filter((rect) => (
            Math.abs(rect.bottom - lowestBottom) <= 14
            || rect.top >= lowestBottom - 40
          ))
          .sort((left, right) => (left.left - right.left) || (left.top - right.top));
      }

      function footerGapSlot(composerNode, composer, minWidth) {
        const controls = footerControlRects(composerNode, composer);
        if (!controls.length) return null;
        const rowTop = Math.min(...controls.map((rect) => rect.top));
        const rowBottom = Math.max(...controls.map((rect) => rect.bottom));
        const start = composer.left + 8;
        const end = composer.right - 8;
        const padding = 8;
        const blockers = [];
        for (const rect of controls) {
          const left = clamp(rect.left - padding, start, end);
          const right = clamp(rect.right + padding, start, end);
          if (right <= left) continue;
          const previous = blockers[blockers.length - 1];
          if (previous && left <= previous.right) {
            previous.right = Math.max(previous.right, right);
          } else {
            blockers.push({ left, right });
          }
        }
        const gaps = [];
        let cursor = start;
        for (const blocker of blockers) {
          if (blocker.left > cursor) gaps.push({ left: cursor, right: blocker.left });
          cursor = Math.max(cursor, blocker.right);
        }
        if (cursor < end) gaps.push({ left: cursor, right: end });
        const best = gaps
          .map((gap) => ({ ...gap, width: gap.right - gap.left }))
          .filter((gap) => gap.width >= minWidth)
          .sort((left, right) => right.width - left.width)[0];
        if (!best) return null;
        return {
          left: best.left,
          right: best.right,
          width: best.width,
          rowTop,
          rowBottom,
          rowHeight: Math.max(1, rowBottom - rowTop),
        };
      }

      function requestFallbackAnchor(composer, height, widthOverride, minWidth) {
        if (!composer) {
          const width = clamp(widthOverride || PANEL.request.fallbackWidth, minWidth, Math.max(minWidth, innerWidth - 16));
          return {
            left: innerWidth - width - 12,
            top: innerHeight - height - 28,
            width,
            height,
            source: "viewport-bottom",
            maxWidth: Math.max(minWidth, innerWidth - 16),
            area: { left: 8, top: 8, width: Math.max(1, innerWidth - 16), height: Math.max(1, innerHeight - 16) },
          };
        }
        const maxWidth = Math.max(minWidth, Math.min(composer.width, innerWidth - 16));
        const width = clamp(widthOverride || PANEL.request.fallbackWidth, minWidth, maxWidth);
        const left = clamp(composer.left + (composer.width - width) / 2, 8, Math.max(8, innerWidth - width - 8));
        const top = clamp(composer.top - height - 4, 8, Math.max(8, innerHeight - height - 8));
        return {
          left,
          top,
          width,
          height,
          source: "composer-above",
          maxWidth,
          area: { left: composer.left, top: composer.top, width: composer.width, height: composer.height },
        };
      }

      function requestAnchor(height, widthOverride) {
        const composerNode = composerElement();
        const composer = visible(composerNode) ? composerNode.getBoundingClientRect() : null;
        const minWidth = minWidthFor("request", height > PANEL.request.collapsedHeight);
        if (!composer) return requestFallbackAnchor(null, height, widthOverride, minWidth);
        const slot = footerGapSlot(composerNode, composer, minWidth);
        if (!slot) return requestFallbackAnchor(composer, height, widthOverride, minWidth);
        const maxWidth = Math.max(minWidth, slot.width);
        const width = clamp(widthOverride || slot.width, minWidth, maxWidth);
        const left = clamp(slot.left + (slot.width - width) / 2, slot.left, Math.max(slot.left, slot.right - width));
        const rowCenter = slot.rowTop + (slot.rowHeight / 2);
        const top = height > PANEL.request.collapsedHeight
          ? clamp(slot.rowBottom - height, 8, Math.max(8, innerHeight - height - 8))
          : clamp(rowCenter - (height / 2), 8, Math.max(8, innerHeight - height - 8));
        return {
          left,
          top,
          width,
          height,
          source: "footer-gap",
          maxWidth,
          area: { left: slot.left, top, width: slot.width, height: Math.max(1, slot.rowHeight) },
        };
      }

      function manualRequestRect(state, anchor, expanded, height) {
        if (anchor.source !== "footer-gap" || !anchor.area || state.anchorSource !== anchor.source) return null;
        const minWidth = minWidthFor("request", expanded);
        const maxWidth = anchor.maxWidth || Math.max(minWidth, innerWidth - 16);
        const ratio = Number(state.widthRatio);
        const baseWidth = Number.isFinite(ratio) && ratio > 0
          ? anchor.area.width * ratio
          : Number(state.width || anchor.width);
        const width = clamp(baseWidth, minWidth, Math.max(minWidth, maxWidth));
        const free = Math.max(0, anchor.area.width - width);
        const xRatio = clamp(Number(state.xRatio ?? 0.5), 0, 1);
        const left = clamp(anchor.area.left + (free * xRatio), 8, Math.max(8, innerWidth - width - 8));
        const bottomOffset = Number(state.bottomOffset);
        const top = Number.isFinite(bottomOffset)
          ? clamp(anchor.top + anchor.height + bottomOffset - height, 8, Math.max(8, innerHeight - height - 8))
          : clamp(anchor.top + Number(state.yOffset || 0), 8, Math.max(8, innerHeight - height - 8));
        return { left, top, width };
      }

      function manualTopRect(state, anchor, expanded, height) {
        if (anchor.source !== "header-slot" || !anchor.area) return null;
        const slotLeft = Number(anchor.area.left);
        const slotRight = Number(anchor.area.right ?? (slotLeft + anchor.area.width));
        const slotWidth = Math.max(1, slotRight - slotLeft);
        const minWidth = Math.min(minWidthFor("top", expanded), slotWidth);
        const baseWidth = Number(state.width || anchor.width);
        const width = clamp(baseWidth, minWidth, Math.max(minWidth, slotWidth));
        const left = clamp(Number(state.x ?? anchor.left), slotLeft, Math.max(slotLeft, slotRight - width));
        const top = clamp(Number(state.y ?? anchor.top), 8, Math.max(8, innerHeight - height - 8));
        return { left, top, width };
      }
"""

__all__ = ["TEXT"]
