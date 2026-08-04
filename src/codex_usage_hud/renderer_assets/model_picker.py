"""Renderer model-picker JavaScript domain."""

TEXT = r"""
  function createModelPickerDomain(ctx, shared) {
    const { normalize, visible, cssEscape } = shared;
    const models = Array.isArray(codexModelPickerCatalog)
      ? codexModelPickerCatalog.filter((model) => model && typeof model === "object" && model.model)
      : [];
    const syntheticSelect = Symbol("codexUsageHudModelPickerSelect");
    let installed = false;

    function reactFiberForNode(node) {
      if (!node) return null;
      const key = Object.getOwnPropertyNames(node).find((name) => name.startsWith("__reactFiber$"));
      return key ? node[key] : null;
    }

    function findFiber(node, predicate, limit = 36) {
      let fiber = reactFiberForNode(node);
      for (let depth = 0; fiber && depth < limit; depth += 1, fiber = fiber.return) {
        try {
          if (predicate(fiber)) return fiber;
        } catch (_) {}
      }
      return null;
    }

    function reasoningEffortLabel(value) {
      switch (String(value || "")) {
        case "minimal": return "极简";
        case "low": return "轻度";
        case "medium": return "中";
        case "high": return "高";
        case "xhigh": return "极高";
        case "max": return "最大";
        case "ultra": return "Ultra";
        default: return String(value || "");
      }
    }

    function normalizeReasoningEfforts(model) {
      const raw = Array.isArray(model?.supportedReasoningEfforts) ? model.supportedReasoningEfforts : [];
      return raw
        .map((item) => {
          const reasoningEffort = String(item?.reasoningEffort || "").trim();
          if (!reasoningEffort) return null;
          return {
            reasoningEffort,
            description: String(item?.description || reasoningEffortLabel(reasoningEffort)),
          };
        })
        .filter(Boolean);
    }

    function modelOptionFromCatalog(model, prototypeOption = null, forcedReasoningEffort = "") {
      const efforts = normalizeReasoningEfforts(model);
      const fallbackEffort = String(model.defaultReasoningEffort || efforts[0]?.reasoningEffort || "medium");
      const selectedEfforts = forcedReasoningEffort
        ? [{ reasoningEffort: forcedReasoningEffort, description: reasoningEffortLabel(forcedReasoningEffort) }]
        : efforts;
      return {
        id: String(model.model),
        model: String(model.model),
        upgrade: null,
        upgradeInfo: null,
        availabilityNux: null,
        displayName: String(model.displayName || model.model),
        description: String(model.description || ""),
        hidden: false,
        supportedReasoningEfforts: selectedEfforts.length ? selectedEfforts : [{ reasoningEffort: fallbackEffort, description: reasoningEffortLabel(fallbackEffort) }],
        defaultReasoningEffort: forcedReasoningEffort || fallbackEffort,
        inputModalities: Array.isArray(model.inputModalities) && model.inputModalities.length
          ? model.inputModalities.map(String)
          : (Array.isArray(prototypeOption?.inputModalities) ? prototypeOption.inputModalities : ["text"]),
        supportsPersonality: prototypeOption?.supportsPersonality ?? true,
        additionalSpeedTiers: Array.isArray(prototypeOption?.additionalSpeedTiers) ? prototypeOption.additionalSpeedTiers : [],
        serviceTiers: Array.isArray(prototypeOption?.serviceTiers) ? prototypeOption.serviceTiers : [],
        defaultServiceTier: prototypeOption?.defaultServiceTier ?? null,
        isDefault: false,
      };
    }

    function modelPickerLeafFiber(node) {
      return findFiber(node, (fiber) => !!fiber?.memoizedProps?.modelOption);
    }

    function modelPickerModelItems() {
      return Array.from(document.querySelectorAll('[role="menuitem"]'))
        .filter(visible)
        .map((node) => ({ node, fiber: modelPickerLeafFiber(node) }))
        .filter((item) => !!item.fiber?.memoizedProps?.modelOption);
    }

    function selectedCatalogModelFromMenu() {
      const selection = window[modelPickerSelectionName];
      const selectedModel = String(selection?.model || "");
      return models.find((model) => model.model === selectedModel) || null;
    }

    function insertSyntheticModelItem(container, referenceNode, model, modelProps) {
      if (!container || !referenceNode || !modelProps?.onSelect) return;
      if (container.querySelector(`[data-codex-usage-hud-model-option="${cssEscape(model.model)}"]`)) return;
      const prototypeOption = modelProps.modelOption || null;
      const option = modelOptionFromCatalog(model, prototypeOption);
      const node = referenceNode.cloneNode(true);
      node.textContent = option.displayName;
      node.title = option.description || option.displayName;
      node.setAttribute("role", "menuitem");
      node.setAttribute("tabindex", "-1");
      node.setAttribute("data-codex-usage-hud-model-option", option.model);
      node.removeAttribute("data-model-selected");
      if (modelProps.selectedModel === option.model) node.setAttribute("data-model-selected", "true");
      node[syntheticSelect] = (event) => {
        event.preventDefault();
        event.stopPropagation();
        window[modelPickerSelectionName] = {
          model: option.model,
          option,
          selectModel: modelProps.onSelect,
          serviceTier: option.defaultServiceTier ?? null,
        };
        modelProps.onSelect(option, option.defaultServiceTier ?? null);
        schedulePatch();
      };
      container.insertBefore(node, container.firstChild);
    }

    function insertSyntheticReasoningItem(container, referenceNode, model, effort) {
      const selection = window[modelPickerSelectionName];
      if (!container || !referenceNode || typeof selection?.selectModel !== "function") return;
      if (container.querySelector(`[data-codex-usage-hud-reasoning-option="${cssEscape(effort.reasoningEffort)}"]`)) return;
      const node = referenceNode.cloneNode(true);
      const label = reasoningEffortLabel(effort.reasoningEffort);
      node.textContent = label;
      node.title = effort.description || label;
      node.setAttribute("role", "menuitem");
      node.setAttribute("tabindex", "-1");
      node.setAttribute("data-codex-usage-hud-reasoning-option", effort.reasoningEffort);
      node.removeAttribute("data-reasoning-selected");
      node[syntheticSelect] = (event) => {
        event.preventDefault();
        event.stopPropagation();
        const option = modelOptionFromCatalog(model, selection.option, effort.reasoningEffort);
        window[modelPickerSelectionName] = {
          model: option.model,
          option,
          selectModel: selection.selectModel,
          serviceTier: option.defaultServiceTier ?? null,
        };
        selection.selectModel(option, option.defaultServiceTier ?? null);
        schedulePatch();
      };
      container.appendChild(node);
    }

    function handleSyntheticSelection(event) {
      if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
      const node = event.target?.closest?.(
        "[data-codex-usage-hud-model-option], [data-codex-usage-hud-reasoning-option]"
      );
      const select = node?.[syntheticSelect];
      if (typeof select === "function") select(event);
    }

    function removeSyntheticItems() {
      document.querySelectorAll(
        "[data-codex-usage-hud-model-option], [data-codex-usage-hud-reasoning-option]"
      ).forEach((node) => node.remove());
    }

    function patchCodexModelPicker() {
      if (!models.length) return;
      const modelItems = modelPickerModelItems();
      if (modelItems.length) {
        const first = modelItems[0];
        const container = first.node.parentElement;
        const existing = new Set(modelItems.map((item) => String(item.fiber.memoizedProps.modelOption?.model || "")));
        const modelProps = first.fiber.memoizedProps || {};
        for (const model of models) {
          if (!existing.has(String(model.model))) {
            insertSyntheticModelItem(container, first.node, model, modelProps);
          }
        }
      }
      const selectedModel = selectedCatalogModelFromMenu();
      if (!selectedModel) return;
      const reasoningItems = Array.from(document.querySelectorAll('[role="menuitem"]'))
        .filter(visible)
        .filter((node) => node.hasAttribute("data-reasoning-selected") || ["轻度", "中", "高", "极高"].includes(normalize(node.textContent)));
      if (!reasoningItems.length) return;
      const existingLabels = new Set(reasoningItems.map((node) => normalize(node.textContent)));
      const container = reasoningItems[0].parentElement;
      for (const effort of normalizeReasoningEfforts(selectedModel)) {
        if (!existingLabels.has(reasoningEffortLabel(effort.reasoningEffort))) {
          insertSyntheticReasoningItem(container, reasoningItems[0], selectedModel, effort);
        }
      }
    }

    function schedulePatch() {
      if (!installed || !models.length) return;
      ctx.frames.cancel("model_picker");
      for (const timer of (window[modelPickerPatchTimersName] || [])) {
        ctx.lifecycle.clearTimeout(timer);
      }
      window[modelPickerPatchRafName] = ctx.frames.schedule("model_picker", patchCodexModelPicker);
      window[modelPickerPatchTimersName] = [60, 180, 360].map((delay) => (
        ctx.lifecycle.timeout("model_picker", patchCodexModelPicker, delay)
      ));
    }

    function install() {
      if (installed) return false;
      installed = true;
      window[modelPickerPatchHandlerName] = schedulePatch;
      ctx.lifecycle.listen("model_picker", document, "pointerdown", schedulePatch, true);
      ctx.lifecycle.listen("model_picker", document, "pointerover", schedulePatch, true);
      ctx.lifecycle.listen("model_picker", document, "focusin", schedulePatch, true);
      ctx.lifecycle.listen("model_picker", document, "keydown", schedulePatch, true);
      ctx.lifecycle.listen("model_picker", document, "click", handleSyntheticSelection);
      ctx.lifecycle.listen("model_picker", document, "keydown", handleSyntheticSelection);
      return true;
    }

    function apply() {
      schedulePatch();
    }

    function dispose() {
      installed = false;
      ctx.frames.cancel("model_picker");
      for (const timer of (window[modelPickerPatchTimersName] || [])) {
        ctx.lifecycle.clearTimeout(timer);
      }
      removeSyntheticItems();
      delete window[modelPickerPatchHandlerName];
      delete window[modelPickerPatchRafName];
      delete window[modelPickerPatchTimersName];
      delete window[modelPickerSelectionName];
    }

    return { install, apply, dispose };
  }

  const modelPickerDomain = ctx.domains.register(
    "model_picker",
    createModelPickerDomain(ctx, shared),
  );
"""

__all__ = ["TEXT"]
