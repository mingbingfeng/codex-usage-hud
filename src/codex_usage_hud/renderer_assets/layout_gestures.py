"""Static raw Renderer layout asset fragment."""

TEXT = r"""
      function beginGesture(event, name, action, edge = "", toggleOnTap = false) {
        const panel = document.querySelector(`#${rootId} [data-panel="${name}"]`);
        if (!panel) return;
        const rect = panel.getBoundingClientRect();
        const expanded = panel.dataset.expanded === "true";
        const startState = getPanelState(name);
        const startHeight = desiredHeight(name, startState, expanded, rect.height);
        const startAnchor = name === "top"
          ? topAnchor(startHeight, startState.width)
          : requestAnchor(startHeight, startState.width);
        const gestureScope = ctx.lifecycle.scope("layout_gesture");
        const DRAG_THRESHOLD = 4;
        const gesture = {
          action,
          edge: edge || "right",
          name,
          expanded,
          anchor: startAnchor,
          startX: event.clientX,
          startY: event.clientY,
          left: rect.left,
          top: rect.top,
          width: rect.width,
          height: rect.height,
          toggleOnTap,
          moved: !toggleOnTap,
        };
        const move = (nextEvent) => {
          const dx = nextEvent.clientX - gesture.startX;
          const dy = nextEvent.clientY - gesture.startY;
          // When the gesture started on a tap-toggle target, wait for real movement
          // before treating it as a drag, so a plain click still toggles.
          if (!gesture.moved) {
            if (Math.abs(dx) <= DRAG_THRESHOLD && Math.abs(dy) <= DRAG_THRESHOLD) return;
            gesture.moved = true;
            // Real drag started: swallow the trailing click so it doesn't toggle.
            if (gesture.toggleOnTap) window.__codexHudDragSuppressClick = true;
          }
          if (gesture.action === "move") {
            // The rect under the pointer is authoritative during a move. A
            // stale persisted width must not snap request back before the
            // user can drag it.
            const width = desiredWidth(name, {}, gesture.expanded, gesture.width, gesture.anchor.maxWidth);
            const height = desiredHeight(name, getPanelState(name), gesture.expanded, gesture.height);
            const left = clamp(gesture.left + dx, 8, Math.max(8, innerWidth - width - 8));
            const top = clamp(gesture.top + dy, 8, Math.max(8, innerHeight - height - 8));
            applyRect(panel, left, top, width, height);
            setPanelState(name, manualPatchFor(name, left, top, width, gesture.anchor, {
              manual: true,
              width: Math.round(width),
            }));
          } else {
            const minWidth = minWidthFor(name, gesture.expanded);
            const minHeight = minHeightFor(name, gesture.expanded);
            const resizeFromLeft = gesture.edge === "left" || gesture.edge.endsWith("-left");
            const maxWidthFromViewport = resizeFromLeft
              ? Math.max(minWidth, gesture.width + gesture.left - 8)
              : Math.max(minWidth, innerWidth - gesture.left - 8);
            const maxWidth = gesture.anchor.maxWidth || maxWidthFromViewport;
            const widthBase = resizeFromLeft ? (gesture.width - dx) : (gesture.width + dx);
            const width = clamp(widthBase, minWidth, Math.max(minWidth, maxWidth));
            const left = resizeFromLeft ? (gesture.left + gesture.width - width) : gesture.left;
            let height = gesture.height;
            let top = gesture.top;
            if (gesture.expanded) {
              if (name === "request" && gesture.edge.startsWith("top")) {
                const bottom = gesture.top + gesture.height;
                height = clamp(gesture.height - dy, minHeight, Math.max(minHeight, bottom - 8));
                top = bottom - height;
              } else if (name === "top" && gesture.edge.startsWith("bottom")) {
                height = clamp(gesture.height + dy, minHeight, Math.max(minHeight, innerHeight - gesture.top - 8));
              }
            }
            applyRect(panel, left, top, width, height);
            const current = getPanelState(name);
            const patch = manualPatchFor(name, left, top, width, gesture.anchor, {
              manual: true,
              width: Math.round(width),
              [gesture.expanded ? "expandedHeight" : "collapsedHeight"]: Math.round(height),
            });
            if (current.manual) patch.y = Math.round(top);
            setPanelState(name, patch);
          }
        };
        const done = () => {
          gestureScope.dispose();
          // A pure tap (no movement) falls through to the click handler, which
          // toggles the panel. Only a real drag persists a new position.
          // A manual gesture owns only its panel. Reflowing both panels here
          // can recalculate the unrelated top HUD width after moving request.
          syncPosition([gesture.name]);
          if (gesture.moved) {
            scheduleLayoutReport(
              gesture.action === "resize" ? "resize" : "move",
              gesture.name,
            );
          }
        };
        gestureScope.listen(document, "pointermove", move, true);
        gestureScope.listen(document, "pointerup", done, true);
        gestureScope.listen(document, "pointercancel", done, true);
      }

      function beginRuntimeErrorsGesture(event) {
        const panel = document.querySelector(`#${rootId} [data-field="runtimeErrorsPanel"]`);
        if (!panel || panel.hidden) return;
        const rect = panel.getBoundingClientRect();
        const gestureScope = ctx.lifecycle.scope("runtime_errors_gesture");
        const gesture = {
          startX: event.clientX,
          startY: event.clientY,
          left: rect.left,
          top: rect.top,
          width: rect.width,
          height: rect.height,
        };
        const move = (nextEvent) => {
          const left = clamp(
            gesture.left + nextEvent.clientX - gesture.startX,
            8,
            Math.max(8, innerWidth - gesture.width - 8),
          );
          const top = clamp(
            gesture.top + nextEvent.clientY - gesture.startY,
            8,
            Math.max(8, innerHeight - gesture.height - 8),
          );
          panel.style.left = px(left);
          panel.style.top = px(top);
          panel.style.right = "auto";
          panel.style.bottom = "auto";
          setRuntimeErrorsPanelState({ x: Math.round(left), y: Math.round(top) });
        };
        const done = () => {
          gestureScope.dispose();
          applyRuntimeErrorsPanelState(panel);
        };
        gestureScope.listen(document, "pointermove", move, true);
        gestureScope.listen(document, "pointerup", done, true);
        gestureScope.listen(document, "pointercancel", done, true);
      }
"""

__all__ = ["TEXT"]
