"""Renderer-injected Codex HUD driven through local Chrome DevTools Protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import socket
import threading
import time
from typing import Any, NamedTuple
from urllib.parse import urlparse

from .. import __version__
from ..config import UserConfig, warning_dismissed_today
from ..config import DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED
from ..core.parser import CostEstimator, ParsedSession, RequestRound, ToolCallTiming, seconds_between
from ..core.runtime_errors import RuntimeErrorEvent
from ..platforms.cdp_probe import (
    _receive_text_message,
    _send_text_frame,
    _websocket_handshake,
    cdp_port_from_env,
    install_new_document_script,
    list_targets,
    pick_page_target,
    remove_new_document_script,
    send_cdp_command,
)
from ..platforms.active_session import (
    is_new_session_source,
    is_pending_session_source,
)
from ..platforms.codex_theme import CodexThemeProbe, CodexThemeSnapshot
from ..support_assets import support_qr_payload

RENDERER_HUD_ENV = "CODEX_USAGE_HUD_RENDERER"
RENDERER_HUD_VERSION = "20"
DEFAULT_RENDERER_TIMEOUT_SECONDS = 0.45
DEFAULT_RENDERER_TARGET_CACHE_SECONDS = 2.0
SLOW_RENDERER_UPDATE_LOG_MS = 250.0
ACTIVE_SESSION_BINDING_NAME = "codexUsageHudActiveSession"
SETTINGS_COMMAND_BINDING_NAME = "codexUsageHudSettingsCommand"
COMPOSER_ATTACHMENTS_BINDING_NAME = "codexUsageHudComposerAttachments"
LAYOUT_BINDING_NAME = "codexUsageHudLayout"
THEME_BINDING_NAME = "codexUsageHudTheme"
MODEL_CATALOG_JSON_ENV = "CODEX_USAGE_HUD_MODEL_CATALOG_JSON"
TOKEN_LEGEND_TEXT = "↑ 输入  ↻ 缓存  ↓ 输出\n◇ 推理  ∑ 合计  $ 金额\n◎ 缓存率  ~ 估算"
TOP_EXPANDED_HEADER_FALLBACK = "Codex 会话 / 预算"
_LOGGER = logging.getLogger("codex_usage_hud.ui.renderer_hud")
REMOVE_RENDERER_HUD_SCRIPT = (
    "(() => {"
    "let existed = false;"
    "try {"
    "const remove = window.__codexUsageHudRemove;"
    "existed = typeof remove === 'function' || !!document.getElementById('codex-usage-hud-root');"
    "if (typeof remove === 'function') remove();"
    "else {"
    "document.getElementById('codex-usage-hud-root')?.remove();"
    "document.getElementById('codex-usage-hud-style')?.remove();"
    "}"
    "} catch (_) {"
    "document.getElementById('codex-usage-hud-root')?.remove();"
    "document.getElementById('codex-usage-hud-style')?.remove();"
    "existed = true;"
    "}"
    "return existed;"
    "})()"
)

_COST_ESTIMATOR = CostEstimator()
COMPOSER_TIKTOKEN_BADGE_ENABLED = DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED


def set_cost_estimator(estimator: CostEstimator) -> None:
    """Use the current user-configured price table for renderer formatting."""
    global _COST_ESTIMATOR
    _COST_ESTIMATOR = estimator


def _renderer_theme_payload(snapshot: CodexThemeSnapshot | None) -> dict[str, object]:
    if snapshot is None or snapshot.source not in {"cdp", "persisted"}:
        return {}
    return {
        "variant": snapshot.effective_variant,
        "source": snapshot.source,
        "tokens": snapshot.hud_tokens.to_dict(),
        "effectiveTheme": snapshot.effective_theme.to_dict(),
    }

_RENDERER_HUD_SCRIPT_TEMPLATE = r"""
(() => {
  const version = "35";
  const rootId = "codex-usage-hud-root";
  const styleId = "codex-usage-hud-style";
  const topClass = "codex-usage-hud-top";
  const requestClass = "codex-usage-hud-request";
  const warningClass = "codex-usage-hud-warning";
  const errorClass = "codex-usage-hud-error";
  const resizeHandlerName = "__codexUsageHudResize";
  const scrollHandlerName = "__codexUsageHudScroll";
  const mutationObserverName = "__codexUsageHudObserver";
  const resizeObserverName = "__codexUsageHudResizeObserver";
  const bootstrapObserverName = "__codexUsageHudBootstrapObserver";
  const bootstrapTimerName = "__codexUsageHudBootstrapTimer";
  const scheduleName = "__codexUsageHudSchedule";
  const stateName = "__codexUsageHudState";
  const rafName = "__codexUsageHudRaf";
  const settleTimerName = "__codexUsageHudSettleTimers";
  const composerSettleTimerName = "__codexUsageHudComposerSettleTimer";
  const composerInputNodeName = "__codexUsageHudComposerInputNode";
  const composerInputHandlersName = "__codexUsageHudComposerInputHandlers";
  const composerFocusStateName = "__codexUsageHudComposerFocused";
  const composerBadgeRafName = "__codexUsageHudComposerBadgeRaf";
  const composerAttachmentsTimerName = "__codexUsageHudComposerAttachmentsTimer";
  const composerAttachmentsSignatureName = "__codexUsageHudComposerAttachmentsSignature";
  const composerAttachmentsObserverName = "__codexUsageHudComposerAttachmentsObserver";
  const themeObserverName = "__codexUsageHudThemeObserver";
  const themeMediaQueryName = "__codexUsageHudThemeMediaQuery";
  const themeMediaQueryHandlerName = "__codexUsageHudThemeMediaQueryHandler";
  const themeStorageHandlerName = "__codexUsageHudThemeStorageHandler";
  const themeTimerName = "__codexUsageHudThemeTimer";
  const themeSignatureName = "__codexUsageHudThemeSignature";
  const runningTimerName = "__codexUsageHudRunningTimer";
  const staleTimerName = "__codexUsageHudStaleTimer";
  const storageKey = "codexUsageHudPanelState:v5";
  const supportImagesStorageKey = "codexUsageHudSupportImages:v1";
const settingsModalId = "codex-usage-hud-settings-modal";
const settingsProviderName = "__codexUsageHudSettingsProvider";
  const activeSessionObserverName = "__codexUsageHudActiveSessionObserver";
  const activeSessionBootstrapObserverName = "__codexUsageHudActiveSessionBootstrapObserver";
  const activeSessionTimerName = "__codexUsageHudActiveSessionTimer";
  const activeSessionSendFollowupTimersName = "__codexUsageHudActiveSessionSendTimers";
  const activeSessionComposerHandlerName = "__codexUsageHudActiveSessionComposerHandler";
  const activeSessionClickHandlerName = "__codexUsageHudActiveSessionClick";
  const activeSessionHistoryPatchName = "__codexUsageHudActiveSessionHistoryPatch";
  const activeSessionCanonicalIdName = "__codexUsageHudActiveSessionCanonicalId";
  const activeSessionCanonicalAtName = "__codexUsageHudActiveSessionCanonicalAt";
  const activeSessionSettledTimerName = "__codexUsageHudActiveSessionSettledTimer";
  const activeSessionSelectionKeyName = "__codexUsageHudActiveSessionSelectionKey";
  const activeSessionSelectionSeqName = "__codexUsageHudActiveSessionSelectionSeq";
  const activeSessionAppliedSeqName = "__codexUsageHudActiveSessionAppliedSeq";
  const activeSessionPayloadCacheName = "__codexUsageHudActiveSessionPayloadCache";
    const activeSessionLastSignatureName = "__codexUsageHudActiveSessionLastSignature";
    const activeSessionBindingName = "codexUsageHudActiveSession";
    const settingsCommandBindingName = "codexUsageHudSettingsCommand";
    const composerAttachmentsBindingName = "codexUsageHudComposerAttachments";
    const layoutBindingName = "codexUsageHudLayout";
    const themeBindingName = "codexUsageHudTheme";
  const layoutReportTimerName = "__codexUsageHudLayoutTimer";
  const layoutReportSignatureName = "__codexUsageHudLayoutSignature";
  const staleUpdateMs = 10000;
  const composerAttachmentsDebounceMs = 80;
  const composerBadgeEnabled = __COMPOSER_TIKTOKEN_BADGE_ENABLED__;
  const codexModelPickerCatalog = __CODEX_MODEL_PICKER_CATALOG__;
  const modelPickerPatchHandlerName = "__codexUsageHudModelPickerPatchHandler";
  const modelPickerPatchRafName = "__codexUsageHudModelPickerPatchRaf";
  const modelPickerPatchTimersName = "__codexUsageHudModelPickerPatchTimers";
  const modelPickerSelectionName = "__codexUsageHudModelPickerSelection";
  let topSlotCache = null;
  let pendingSyncPanels = null;
  let settingsActiveTab = "settings";
  let storageFilter = "all";
  let storagePreviewHidden = false;
  let storageBodyScrollTop = 0;
  let cleanupContentScrollTop = 0;
  let sessionTableScrollTop = 0;
  const usageInsightsState = {
    data: null,
    refreshRequestId: "",
    error: "",
  };
  const safeCleanupState = {
    data: null,
    stableData: null,
    inventoryRevision: "",
    selectedIds: new Set(),
    expandedGroupIds: new Set(),
    pendingRequestId: "",
    executeStartedAt: 0,
    includeConsent: false,
    backupDirectory: "",
    backupDirectoryDirty: false,
    previewBackupDirectory: "",
    autoCloseConfirmed: false,
    previewHidden: false,
    lastBackupPickerRequestId: "",
    scanStartedAt: 0,
  };
  let cleanupActiveSection = "junk";
  let safeCleanupPreviewTimer = 0;
  let safeCleanupLiveTimer = 0;
  let restReminderCountdownTimer = 0;
  const sessionCleanupState = {
    data: null,
    pendingRequestId: "",
    selectedIds: new Set(),
    search: "",
    status: "all",
    time: "all",
    previewTokenShown: "",
    scanStartedAt: 0,
  };
  let backgroundUsageFetchSeq = 0;
  let backgroundUsageDetailSeq = 0;
  const backgroundUsageRequestTimeoutMs = 5000;
  let backgroundUsageQueryTimeoutId = 0;
  let backgroundUsageDetailTimeoutId = 0;
  const backgroundUsageBodyScrollTops = new Map();
  const backgroundUsageHistoryScrollTops = new Map();
  const backgroundUsageDetailScrollTops = new Map();
  const backgroundUsageState = {
    range: "today",
    feature: "",
    model: "",
    selectedEventId: "",
    selectedSessionId: "",
    data: null,
    detail: null,
    loading: false,
    detailLoading: false,
    error: "",
    loadedRevision: -1,
    promptExpanded: false,
    queryRequestId: "",
    detailRequestId: "",
  };
  let cachedHeaderNode = null;
  let cachedComposerNode = null;
  let observedHeaderNode = null;
  let observedComposerNode = null;
  let settingsProviderDraft = null;
  const settingsDirtyProviders = new Set();
  const numericTokenRe = /\$?\d+(?:,\d{3})*(?:\.\d+)?(?:[kM%])?/g;
  const numericAnimations = new WeakMap();
  const previousActiveSessionSelectionSeq = Number(
    window[activeSessionSelectionSeqName] || 0
  );
  const previousActiveSessionAppliedSeq = Number(
    window[activeSessionAppliedSeqName] || 0
  );

  try {
    if (typeof window.__codexUsageHudRemove === "function") {
      window.__codexUsageHudRemove({ preserveState: true });
    }
  } catch (_) {}
  const restoredActiveSessionSelectionSeq = Math.max(
    previousActiveSessionSelectionSeq,
    previousActiveSessionAppliedSeq
  );
  if (restoredActiveSessionSelectionSeq > 0) {
    window[activeSessionSelectionSeqName] = restoredActiveSessionSelectionSeq;
  }
  if (previousActiveSessionAppliedSeq > 0) {
    window[activeSessionAppliedSeqName] = previousActiveSessionAppliedSeq;
  }

  const PANEL = {
    top: {
      className: topClass,
      collapsedHeight: 36,
      expandedHeight: 390,
      minCollapsedWidth: 120,
      minExpandedWidth: 220,
      minExpandedHeight: 240,
      fallbackWidth: 520,
    },
    request: {
      className: requestClass,
      collapsedHeight: 32,
      expandedHeight: 180,
      minCollapsedWidth: 120,
      minExpandedWidth: 220,
      minExpandedHeight: 120,
      fallbackWidth: 380,
    },
  };

  const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const px = (value) => `${Math.round(value)}px`;
  const visible = (node) => {
    if (!(node instanceof HTMLElement) || !node.isConnected) return false;
    const style = getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) === 0) return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const cssEscape = (value) => {
    if (window.CSS && typeof window.CSS.escape === "function") {
      return window.CSS.escape(String(value));
    }
    return String(value).replace(/["\\]/g, "\\$&");
  };
  const codexModelPickerModels = Array.isArray(codexModelPickerCatalog)
    ? codexModelPickerCatalog.filter((model) => model && typeof model === "object" && model.model)
    : [];

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
    return codexModelPickerModels.find((model) => model.model === selectedModel) || null;
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
    const select = (event) => {
      event.preventDefault();
      event.stopPropagation();
      window[modelPickerSelectionName] = {
        model: option.model,
        option,
        selectModel: modelProps.onSelect,
        serviceTier: option.defaultServiceTier ?? null,
      };
      modelProps.onSelect(option, option.defaultServiceTier ?? null);
      scheduleCodexModelPickerPatch();
    };
    node.addEventListener("click", select);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") select(event);
    });
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
    const select = (event) => {
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
      scheduleCodexModelPickerPatch();
    };
    node.addEventListener("click", select);
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") select(event);
    });
    container.appendChild(node);
  }

  function patchCodexModelPicker() {
    if (!codexModelPickerModels.length) return;
    const modelItems = modelPickerModelItems();
    if (modelItems.length) {
      const first = modelItems[0];
      const container = first.node.parentElement;
      const existing = new Set(modelItems.map((item) => String(item.fiber.memoizedProps.modelOption?.model || "")));
      const modelProps = first.fiber.memoizedProps || {};
      for (const model of codexModelPickerModels) {
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

  function scheduleCodexModelPickerPatch() {
    if (!codexModelPickerModels.length) return;
    cancelAnimationFrame(window[modelPickerPatchRafName] || 0);
    for (const timer of (window[modelPickerPatchTimersName] || [])) clearTimeout(timer);
    window[modelPickerPatchRafName] = requestAnimationFrame(patchCodexModelPicker);
    window[modelPickerPatchTimersName] = [60, 180, 360].map((delay) => setTimeout(patchCodexModelPicker, delay));
  }

  function ensureStyle() {
    const existing = document.getElementById(styleId);
    if (existing?.dataset.version === version) return;
    existing?.remove();
    const style = document.createElement("style");
    style.id = styleId;
    style.dataset.version = version;
    style.textContent = `
      #${rootId} {
        --codex-usage-hud-surface: #10161d;
        --codex-usage-hud-panel-surface: #141b24;
        --codex-usage-hud-panel-border: #3a485a;
        --codex-usage-hud-header-surface: #202833;
        --codex-usage-hud-divider: #273241;
        --codex-usage-hud-text: #e8eef7;
        --codex-usage-hud-muted: #8492a6;
        --codex-usage-hud-accent: #f3d27a;
        --codex-usage-hud-info: #9ccbff;
        --codex-usage-hud-warning: #ffb86b;
        --codex-usage-hud-error: #ff6b6b;
        --codex-usage-hud-success: #8fe3a1;
        --codex-usage-hud-request-surface: #0b1016;
        --codex-usage-hud-request-header-surface: #151d27;
        --codex-usage-hud-request-panel-surface: #101821;
        --codex-usage-hud-request-text: #dce7f2;
        --codex-usage-hud-request-muted: #718095;
        --codex-usage-hud-progress-track: #262c33;
        --codex-usage-hud-progress-track-border: #3b4149;
        --codex-usage-hud-progress-track-text: #c1c7d0;
        --codex-usage-hud-progress-cache: #9ccbff;
        --codex-usage-hud-progress-cache-end: #5ea7ff;
        --codex-usage-hud-progress-cache-text: #07131f;
        --codex-usage-hud-progress-day: #f3d27a;
        --codex-usage-hud-progress-day-end: #f3d37f;
        --codex-usage-hud-progress-day-text: #111111;
        --codex-usage-hud-progress-week: #b5dd92;
        --codex-usage-hud-progress-week-end: #aede95;
        --codex-usage-hud-progress-week-text: #111111;
        --codex-usage-hud-progress-overflow: #ff875a;
        --codex-usage-hud-progress-overflow-highlight: #ffd8bd;
        --codex-usage-hud-progress-overflow-anchor: #ff6b64;
        --codex-usage-hud-progress-overflow-anchor-edge: #ffc3a4;
        --codex-usage-hud-progress-overflow-badge: #7f3e3a;
        --codex-usage-hud-progress-overflow-badge-edge: #ff875a;
        --codex-usage-hud-progress-overflow-badge-text: #ffd7ca;
        position: fixed;
        inset: 0;
        z-index: 2147482600;
        pointer-events: none;
        user-select: none;
        color-scheme: dark;
        font-family: "Microsoft YaHei UI", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      #${rootId}[data-hud-ready="false"] .codex-usage-hud-panel {
        display: none !important;
      }
      #${rootId} .codex-usage-hud-startup-bubble {
        position: fixed;
        top: 72px;
        right: 18px;
        bottom: auto;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        width: 196px;
        min-height: 146px;
        padding: 15px 16px 14px;
        border: 1px solid rgba(243, 210, 122, .52);
        border-radius: 12px;
        background: linear-gradient(145deg, rgba(32, 40, 51, .96), rgba(16, 22, 29, .96));
        box-shadow: 0 16px 38px rgba(0, 0, 0, .34), inset 0 1px 0 rgba(255,255,255,.06);
        color: var(--codex-usage-hud-text, #e8eef7);
        text-align: center;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-startup-bubble[hidden] {
        display: none !important;
      }
      #${rootId} .codex-usage-hud-startup-bubble::before {
        content: "";
        width: 30px;
        height: 30px;
        margin-bottom: 8px;
        border: 3px solid rgba(243, 210, 122, .24);
        border-top-color: var(--codex-usage-hud-accent, #f3d27a);
        border-radius: 50%;
        animation: codex-usage-hud-startup-spin .85s linear infinite;
      }
      #${rootId} .codex-usage-hud-startup-step {
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        font-weight: 700;
        letter-spacing: .04em;
      }
      #${rootId} .codex-usage-hud-startup-title {
        color: var(--codex-usage-hud-accent, #f3d27a);
        font-size: 12px;
        font-weight: 800;
        line-height: 1.25;
      }
      #${rootId} .codex-usage-hud-startup-detail {
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        line-height: 1.35;
      }
      #${rootId} .codex-usage-hud-startup-progress-track {
        width: 100%;
        height: 6px;
        margin-top: 11px;
        overflow: hidden;
        border-radius: 999px;
        background: rgba(255,255,255,.10);
      }
      #${rootId} .codex-usage-hud-startup-progress-fill {
        width: 0%;
        height: 100%;
        border-radius: inherit;
        background: linear-gradient(90deg, var(--codex-usage-hud-accent, #f3d27a), #ffe7a0);
        transition: width .22s ease;
      }
      #${rootId} .codex-usage-hud-startup-progress-label {
        align-self: flex-end;
        margin-top: 5px;
        color: var(--codex-usage-hud-muted, #8492a6);
        font: 700 10px Consolas, "Cascadia Mono", ui-monospace, monospace;
      }
      @keyframes codex-usage-hud-startup-spin {
        to { transform: rotate(360deg); }
      }
      #${rootId} .codex-usage-hud-panel {
        position: fixed;
        box-sizing: border-box;
        border: 1px solid rgba(140, 153, 174, .24);
        border-radius: 7px;
        color: #e8eef7;
        box-shadow: 0 10px 28px rgba(0, 0, 0, .24);
        backdrop-filter: blur(12px);
        pointer-events: none;
        overflow: hidden;
        letter-spacing: 0;
        -webkit-app-region: no-drag;
      }
      #${rootId} .codex-usage-hud-panel * {
        -webkit-app-region: no-drag;
      }
      #${rootId} .codex-usage-hud-handle,
      #${rootId} .codex-usage-hud-update-button,
      #${rootId} .codex-usage-hud-settings-button,
      #${rootId} .codex-usage-hud-background-notification,
      #${rootId} .codex-usage-hud-resize-zone,
      #${rootId} .codex-usage-hud-main,
      #${rootId} .codex-usage-hud-panel-header,
      #${rootId} .codex-usage-hud-top-body,
      #${rootId} .codex-usage-hud-request-list,
      #${rootId} .codex-usage-hud-settings-modal {
        pointer-events: auto;
      }
      #${rootId} .${topClass} {
        background: rgba(16, 22, 29, .94);
      }
      #${rootId} .${requestClass} {
        background: rgba(11, 16, 22, .92);
      }
      #${rootId} .codex-usage-hud-collapsed,
      #${rootId} .codex-usage-hud-expanded-shell {
        box-sizing: border-box;
        width: 100%;
        height: 100%;
      }
      #${rootId} .codex-usage-hud-collapsed {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        align-items: center;
        gap: 6px;
        padding: 4px 7px 4px 8px;
      }
      #${rootId} .codex-usage-hud-collapsed[data-has-settings="true"] {
        grid-template-columns: auto minmax(0, 1fr) 22px;
      }
      #${rootId} .codex-usage-hud-expanded-shell {
        display: none;
        position: relative;
        grid-template-rows: auto minmax(0, 1fr);
        padding: 5px 8px 8px;
      }
      #${rootId} .codex-usage-hud-panel[data-expanded="true"] .codex-usage-hud-collapsed {
        display: none;
      }
      #${rootId} .codex-usage-hud-panel[data-expanded="true"] .codex-usage-hud-expanded-shell {
        display: grid;
      }
      #${rootId} .${requestClass} .codex-usage-hud-expanded-shell {
        grid-template-rows: auto minmax(0, 1fr) auto;
      }
      #${rootId} button {
        font: inherit;
      }
      #${rootId} .codex-usage-hud-left-controls {
        min-width: 0;
        display: inline-flex;
        align-items: center;
        gap: 4px;
      }
      #${rootId} .codex-usage-hud-collapsed[data-has-settings="false"] {
        grid-template-columns: minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-collapsed[data-has-badge="true"] {
        grid-template-columns: minmax(0, 1fr) auto;
      }
      #${rootId} .${requestClass} .codex-usage-hud-collapsed {
        grid-template-columns: minmax(0, 1fr) 22px;
      }
      #${rootId} .${requestClass} .codex-usage-hud-collapsed[data-has-badge="true"] {
        grid-template-columns: minmax(0, 1fr) auto 22px;
      }
      #${rootId} .codex-usage-hud-token-badge {
        position: relative;
        display: none;
        align-items: center;
        box-sizing: border-box;
        max-width: 160px;
        height: 20px;
        padding: 0 8px;
        border: 1px solid rgba(156, 203, 255, .28);
        border-radius: 999px;
        background: rgba(156, 203, 255, .12);
        color: #9ccbff;
        font: 700 11px/1 Consolas, "Cascadia Mono", ui-monospace, monospace;
        white-space: nowrap;
        cursor: default;
        pointer-events: auto;
      }
      #${rootId} .codex-usage-hud-token-badge[data-composer-badge="active"] {
        display: inline-flex;
      }
      #${rootId} .codex-usage-hud-token-badge[data-badge-state="warning"] {
        display: inline-flex;
        max-width: 240px;
        border-color: rgba(255, 196, 84, .55);
        background: rgba(255, 196, 84, .16);
        color: #ffca54;
        animation: codex-usage-hud-badge-pulse 1.4s ease-in-out infinite;
      }
      @keyframes codex-usage-hud-badge-pulse {
        0%, 100% { opacity: .62; }
        50% { opacity: 1; }
      }
      #${rootId} .codex-usage-hud-token-badge-text {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-token-breakdown {
        position: fixed !important;
        z-index: 2147483647 !important;
        display: block !important;
        width: max-content !important;
        min-width: 208px !important;
        max-width: 340px !important;
        box-sizing: border-box !important;
        margin: 0 !important;
        padding: 8px 11px !important;
        border: 1px solid rgba(156, 203, 255, .35) !important;
        border-radius: 9px !important;
        background: rgba(20, 26, 34, .98) !important;
        box-shadow: 0 8px 24px rgba(0, 0, 0, .55) !important;
        color: #c7d4e4 !important;
        font: 600 10.5px/1.55 Consolas, "Cascadia Mono", ui-monospace, monospace !important;
        white-space: nowrap !important;
        pointer-events: none !important;
        opacity: 0;
        transform: translateY(4px);
        transition: opacity .12s ease, transform .12s ease;
      }
      #${rootId} .codex-usage-hud-token-breakdown[hidden] {
        display: none !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown[data-open="true"] {
        opacity: 1;
        transform: translateY(0);
      }
      /* 全量样式重置 + !important，彻底隔离 Codex 页面样式污染 */
      #${rootId} .codex-usage-hud-token-breakdown > * {
        box-sizing: border-box !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        min-width: 0 !important;
        max-width: none !important;
        float: none !important;
        position: static !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-title {
        display: flex !important;
        flex-direction: row !important;
        align-items: baseline !important;
        justify-content: space-between !important;
        gap: 18px !important;
        width: 100% !important;
        color: #9ccbff !important;
        font: 700 11px/1.35 Consolas, "Cascadia Mono", ui-monospace, monospace !important;
        margin: 0 0 6px !important;
        padding: 0 0 5px !important;
        border-bottom: 1px solid rgba(156, 203, 255, .22) !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-title-cost {
        color: #7fd7a6 !important;
        font-weight: 700 !important;
        font-variant-numeric: tabular-nums !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-row {
        display: flex !important;
        flex-direction: row !important;
        align-items: baseline !important;
        column-gap: 12px !important;
        width: 100% !important;
        line-height: 1.6 !important;
        text-align: left !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-row[data-total="true"] {
        margin-top: 4px !important;
        padding-top: 5px !important;
        border-top: 1px solid rgba(156, 203, 255, .25) !important;
        color: #9ccbff !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-name {
        flex: 1 1 auto !important;
        color: #b8c4d4 !important;
        font-weight: 600 !important;
        line-height: inherit !important;
        text-align: left !important;
        white-space: nowrap !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-note {
        margin-left: 6px !important;
        color: #6f7d90 !important;
        font-size: 9.5px !important;
        font-weight: 600 !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-row[data-total="true"] .codex-usage-hud-token-breakdown-name {
        color: #9ccbff !important;
        font-weight: 700 !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-tok {
        flex: 0 0 auto !important;
        margin-left: auto !important;
        color: #8492a6 !important;
        font-variant-numeric: tabular-nums !important;
        line-height: inherit !important;
        text-align: right !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-cost {
        flex: 0 0 auto !important;
        min-width: 54px !important;
        color: #7fd7a6 !important;
        font-variant-numeric: tabular-nums !important;
        line-height: inherit !important;
        text-align: right !important;
      }
      #${rootId} .codex-usage-hud-token-breakdown-row[data-total="true"] .codex-usage-hud-token-breakdown-cost {
        font-weight: 700 !important;
      }
      #${rootId} .codex-usage-hud-handle,
      #${rootId} .codex-usage-hud-update-button,
      #${rootId} .codex-usage-hud-settings-button,
      #${rootId} .codex-usage-hud-background-notification {
        display: inline-grid;
        place-items: center;
        box-sizing: border-box;
        width: 18px;
        height: 18px;
        border: 0;
        border-radius: 4px;
        padding: 0;
        background: rgba(46, 56, 70, .78);
        color: #c7d4e4;
        font: 700 11px/1 "Segoe UI Symbol", "Microsoft YaHei UI", sans-serif;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-background-notification {
        position: relative;
        width: 22px;
        height: 22px;
        color: var(--codex-usage-hud-info, #9ccbff);
        font-size: 13px;
        visibility: hidden;
        opacity: 0;
        pointer-events: none;
        transition: opacity .12s ease;
      }
      #${rootId} .codex-usage-hud-background-notification[data-visible="true"] {
        visibility: visible;
        opacity: 1;
        pointer-events: auto;
      }
      #${rootId} .codex-usage-hud-background-notification:hover {
        background: rgba(156, 203, 255, .16);
        color: #c8e3ff;
      }
      #${rootId} .codex-usage-hud-background-notification-count {
        position: absolute;
        top: -5px;
        right: -5px;
        min-width: 13px;
        height: 13px;
        display: inline-grid;
        place-items: center;
        box-sizing: border-box;
        padding: 0 3px;
        border: 1px solid var(--codex-usage-hud-request-surface, #0b1016);
        border-radius: 7px;
        background: var(--codex-usage-hud-error, #ff6b6b);
        color: #fff;
        font: 700 8px/1 Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-variant-numeric: tabular-nums;
      }
      #${rootId} .codex-usage-hud-background-notification-count[hidden] {
        display: none;
      }
      #${rootId} .codex-usage-hud-handle {
        cursor: grab;
        color: #8492a6;
      }
      #${rootId} .codex-usage-hud-handle:active {
        cursor: grabbing;
      }
      #${rootId} .codex-usage-hud-resize-zone {
        position: absolute;
        z-index: 5;
        background: transparent;
      }
      #${rootId} .codex-usage-hud-resize-edge-left {
        top: 0;
        left: 0;
        bottom: 0;
        width: 6px;
        cursor: ew-resize;
      }
      #${rootId} .codex-usage-hud-resize-edge-right {
        top: 0;
        right: 0;
        bottom: 0;
        width: 6px;
        cursor: ew-resize;
      }
      #${rootId} .codex-usage-hud-resize-corner {
        width: 12px;
        height: 12px;
      }
      #${rootId} .codex-usage-hud-resize-corner-top-left {
        top: 0;
        left: 0;
        cursor: nwse-resize;
      }
      #${rootId} .codex-usage-hud-resize-corner-top-right {
        top: 0;
        right: 0;
        cursor: nesw-resize;
      }
      #${rootId} .codex-usage-hud-resize-corner-bottom-left {
        left: 0;
        bottom: 0;
        cursor: nesw-resize;
      }
      #${rootId} .codex-usage-hud-resize-corner-bottom-right {
        right: 0;
        bottom: 0;
        cursor: nwse-resize;
      }
      #${rootId} .codex-usage-hud-update-button {
        background: rgba(46, 56, 70, .82);
        color: #9ccbff;
      }
      #${rootId} .codex-usage-hud-update-button[data-state="paused"],
      #${rootId} .codex-usage-hud-update-button[data-state="error"] {
        color: #ffb86b;
      }
      #${rootId} .codex-usage-hud-update-button[data-icon="install"] {
        color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-settings-button {
        width: 22px;
        height: 22px;
        border-radius: 5px;
        background: transparent;
        color: #a9bcd2;
        font-size: 13px;
      }
      #${rootId} .codex-usage-hud-handle:hover {
        background: rgba(62, 74, 92, .92);
        color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-update-button:hover,
      #${rootId} .codex-usage-hud-settings-button:hover {
        background: rgba(62, 74, 92, .92);
        color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-main {
        min-width: 0;
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        align-items: center;
        gap: 8px;
        height: 100%;
        padding: 0;
        border: 0;
        background: transparent;
        color: inherit;
        text-align: left;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-main[data-has-glyph="false"] {
        grid-template-columns: minmax(0, 1fr);
        gap: 0;
      }
      #${rootId} .${topClass} .codex-usage-hud-main[data-progress="true"] {
        grid-template-columns: minmax(0, 1fr);
      }
      #${rootId} .${topClass} .codex-usage-hud-main[data-progress="true"] .codex-usage-hud-glyph,
      #${rootId} .${topClass} .codex-usage-hud-main[data-progress="true"] .codex-usage-hud-line {
        display: none;
      }
      #${rootId} .codex-usage-hud-progress-strip-viewport {
        min-width: 0;
        width: 100%;
        display: none;
        overflow: hidden;
      }
      #${rootId} .codex-usage-hud-progress-strip {
        min-width: 0;
        width: 100%;
        height: 26px;
        display: none;
        grid-template-columns: max-content minmax(0, 1fr) minmax(0, 1fr);
        gap: 7px;
        align-items: center;
        transform: translateX(0);
        will-change: transform;
      }
      #${rootId} .codex-usage-hud-main[data-progress="true"] .codex-usage-hud-progress-strip-viewport,
      #${rootId} .codex-usage-hud-main[data-progress="true"] .codex-usage-hud-progress-strip {
        display: block;
      }
      #${rootId} .codex-usage-hud-main[data-progress="true"] .codex-usage-hud-progress-strip {
        display: grid;
      }
      #${rootId} .codex-usage-hud-progress-strip[data-count="1"] {
        grid-template-columns: minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-progress-strip[data-overflow="true"] {
        width: max-content;
        min-width: max-content;
        animation-name: codex-usage-hud-progress-strip-marquee;
        animation-duration: var(--codex-usage-hud-progress-strip-duration, 6000ms);
        animation-timing-function: linear;
        animation-iteration-count: infinite;
      }
      @keyframes codex-usage-hud-progress-strip-marquee {
        0%, 22% {
          transform: translateX(0);
        }
        48%, 70% {
          transform: translateX(calc(var(--codex-usage-hud-progress-strip-distance, 0px) * -1));
        }
        100% {
          transform: translateX(0);
        }
      }
      #${rootId} .codex-usage-hud-progress-strip:not([data-count="1"]) .codex-usage-hud-progress-rail:first-child {
        justify-self: start;
        width: max-content;
        max-width: 100%;
      }
      #${rootId} .codex-usage-hud-progress-rail {
        position: relative;
        display: block;
        width: 100%;
        min-width: 0;
        height: 100%;
        border-radius: 999px;
        border: 1px solid rgba(255,255,255,.06);
        background: linear-gradient(180deg, rgba(255,255,255,.032), rgba(255,255,255,0)), #111822;
        overflow: hidden;
        box-shadow: inset 0 1px 0 rgba(255,255,255,.04), inset 0 -8px 16px rgba(0,0,0,.16);
      }
      #${rootId} .codex-usage-hud-progress-track-text {
        position: absolute;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        min-width: 0;
        padding: 0 11px;
        overflow: hidden;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0;
      }
      #${rootId} .codex-usage-hud-progress-text {
        min-width: 0;
        flex: 1 1 auto;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-progress-right-text {
        flex: 0 0 auto;
        max-width: 42%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-progress-probe-text {
        flex: 0 0 auto;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-progress-size-probe {
        box-sizing: border-box;
        display: flex;
        align-items: center;
        height: 0;
        overflow: hidden;
        padding: 0 11px;
        visibility: hidden;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-strip .codex-usage-hud-progress-track-text {
        padding: 0 10px;
        font-size: 11px;
      }
      #${rootId} .codex-usage-hud-progress-strip .codex-usage-hud-progress-size-probe {
        padding: 0 10px;
        font-size: 11px;
      }
      #${rootId} .codex-usage-hud-progress-track-text {
        z-index: 3;
        color: #ffffff;
        mix-blend-mode: difference;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-fill {
        position: absolute;
        inset: 0 auto 0 0;
        min-width: 0;
        max-width: 100%;
        z-index: 2;
        border-radius: inherit;
        overflow: hidden;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-fill::before {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(180deg, rgba(255,255,255,.26), rgba(255,255,255,.07) 28%, rgba(255,255,255,0) 52%);
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-fill::after {
        content: "";
        position: absolute;
        top: 4px;
        right: 7px;
        width: min(64px, 42%);
        height: calc(100% - 8px);
        border-radius: inherit;
        background: linear-gradient(90deg, rgba(255,255,255,0), rgba(255,255,255,.18));
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-rail[data-overflow="true"] {
        border-color: rgba(255,136,92,.18);
      }
      #${rootId} .codex-usage-hud-progress-rail[data-overflow="true"] .codex-usage-hud-progress-track-text,
      #${rootId} .codex-usage-hud-progress-rail[data-overflow="true"] .codex-usage-hud-progress-size-probe {
        padding-right: 28px;
      }
      #${rootId} .codex-usage-hud-progress-rail[data-badge="true"] .codex-usage-hud-progress-track-text,
      #${rootId} .codex-usage-hud-progress-rail[data-badge="true"] .codex-usage-hud-progress-size-probe {
        padding-right: 108px;
      }
      #${rootId} .codex-usage-hud-progress-overflow {
        position: absolute;
        top: 5px;
        right: 6px;
        height: 8px;
        border-radius: 999px;
        background: linear-gradient(90deg, #ffcfaa, #ff875a 60%, #ff5b64);
        box-shadow: 0 10px 22px rgba(255,91,100,.18);
        z-index: 3;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-overflow::before {
        content: "";
        position: absolute;
        inset: 0;
        border-radius: inherit;
        background: linear-gradient(180deg, rgba(255,255,255,.28), rgba(255,255,255,0) 70%);
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-overflow-anchor {
        position: absolute;
        top: 3px;
        right: 5px;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background: radial-gradient(circle at 35% 35%, #fff4d9 0%, #ff8e61 58%, #ff5b64 100%);
        box-shadow: 0 0 0 2px rgba(255,107,99,.12), 0 0 14px rgba(255,107,99,.32);
        z-index: 4;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-badge {
        position: absolute;
        top: 50%;
        right: 10px;
        transform: translateY(-50%);
        display: inline-flex;
        align-items: center;
        gap: 6px;
        min-height: 22px;
        padding: 0 10px;
        border-radius: 999px;
        border: 1px solid rgba(255,132,88,.24);
        background: rgba(255,95,92,.12);
        color: #ffd7ca;
        font-size: 10.5px;
        font-weight: 800;
        box-shadow: 0 8px 18px rgba(255,91,100,.12);
        backdrop-filter: blur(10px);
        z-index: 5;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-progress-badge::before {
        content: "";
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: linear-gradient(180deg, #ffcfaa, #ff5b64);
        box-shadow: 0 0 10px rgba(255,91,100,.32);
      }
      #${rootId} .codex-usage-hud-progress-strip .codex-usage-hud-progress-badge {
        display: none;
      }
      #${rootId} .codex-usage-hud-budget-rails .codex-usage-hud-progress-overflow {
        top: 7px;
        right: 8px;
        height: 12px;
      }
      #${rootId} .codex-usage-hud-budget-rails .codex-usage-hud-progress-overflow-anchor {
        top: 6px;
        right: 7px;
        width: 16px;
        height: 16px;
      }
      #${rootId} .codex-usage-hud-budget-rails .codex-usage-hud-progress-badge {
        min-height: 24px;
        padding: 0 11px;
        font-size: 11px;
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="cache"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, #9ccbff, #5ea7ff);
        box-shadow: 0 10px 22px rgba(94,167,255,.18);
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="session"] {
        border-color: rgba(156,203,255,.14);
        background: linear-gradient(180deg, rgba(156,203,255,.07), rgba(255,255,255,0)), #111822;
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="day"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, var(--codex-usage-hud-progress-day), var(--codex-usage-hud-progress-day-end));
        box-shadow: 0 10px 22px color-mix(in srgb, var(--codex-usage-hud-progress-day-end) 18%, transparent);
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="week"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, var(--codex-usage-hud-progress-week), var(--codex-usage-hud-progress-week-end));
        box-shadow: 0 10px 22px color-mix(in srgb, var(--codex-usage-hud-progress-week-end) 18%, transparent);
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="error"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, #ff8a8a, #ff6b6b);
      }
      #${rootId} .codex-usage-hud-glyph {
        display: inline-grid;
        place-items: center;
        width: 18px;
        height: 18px;
        border-radius: 999px;
        background: rgba(243, 210, 122, .16);
        color: #f3d27a;
        font: 700 12px ui-monospace, "Cascadia Mono", Consolas, monospace;
      }
      #${rootId} .codex-usage-hud-line {
        position: relative;
        min-width: 0;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0;
      }
      #${rootId} .codex-usage-hud-line-inner {
        display: inline-block;
        max-width: none;
        white-space: nowrap;
        transform: translateX(0);
        will-change: transform;
      }
      #${rootId} .codex-usage-hud-line[data-marquee="true"] .codex-usage-hud-line-inner {
        animation-name: codex-usage-hud-marquee;
        animation-duration: var(--codex-usage-hud-marquee-duration, 6000ms);
        animation-timing-function: linear;
        animation-iteration-count: infinite;
      }
      @keyframes codex-usage-hud-marquee {
        0%, 22% {
          transform: translateX(0);
        }
        48%, 70% {
          transform: translateX(calc(var(--codex-usage-hud-marquee-distance, 0px) * -1));
        }
        100% {
          transform: translateX(0);
        }
      }
      #${rootId} .${requestClass} .codex-usage-hud-line {
        color: #f3d27a;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 13px;
      }
      #${rootId} .codex-usage-hud-panel-header {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        align-items: center;
        gap: 6px;
        min-height: 27px;
        margin-bottom: 7px;
        padding: 2px 5px;
        border-radius: 5px;
        background: #202833;
      }
      #${rootId} .codex-usage-hud-panel-header[data-action="toggle"] {
        cursor: pointer;
      }
      #${rootId} .${topClass} .codex-usage-hud-panel-header {
        grid-template-columns: auto minmax(0, auto) minmax(0, 1fr) minmax(90px, 150px) 22px;
      }
      #${rootId} .${requestClass} .codex-usage-hud-panel-header {
        grid-template-columns: auto minmax(0, 1fr) 22px;
        background: #151d27;
        margin-top: 4px;
        margin-bottom: 0;
      }
      #${rootId} .codex-usage-hud-title {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #e8eef7;
        font-size: 12px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-session-meta {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #8492a6;
        text-align: right;
        font-size: 10.5px;
      }
      #${rootId} .codex-usage-hud-top-body {
        min-height: 0;
        overflow: auto;
        scrollbar-width: thin;
        scrollbar-color: #273241 #10161d;
      }
      #${rootId} .codex-usage-hud-top-grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        gap: 12px;
        min-height: 100%;
        align-items: stretch;
      }
      #${rootId} .${topClass} {
        container-type: inline-size;
      }
      #${rootId} .codex-usage-hud-top-column {
        min-width: 0;
        min-height: 0;
        height: 100%;
        display: grid;
        gap: 12px;
        align-content: stretch;
      }
      #${rootId} .codex-usage-hud-top-column-left {
        grid-template-rows: auto auto minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-top-column-right {
        grid-template-rows: minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-top-card,
      #${rootId} .codex-usage-hud-alert {
        box-sizing: border-box;
        min-width: 0;
        border: 1px solid rgba(132, 146, 166, .16);
        border-radius: 7px;
        background: #141b24;
        padding: 10px 12px;
      }
      #${rootId} .codex-usage-hud-card-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        min-height: 18px;
        margin-bottom: 9px;
      }
      #${rootId} .codex-usage-hud-card-title {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #dce7f2;
        font-size: 12px;
        font-weight: 800;
      }
      #${rootId} .codex-usage-hud-card-actions {
        min-width: 0;
        display: inline-flex;
        align-items: center;
        justify-content: flex-end;
        gap: 6px;
        overflow: hidden;
      }
      #${rootId} .codex-usage-hud-chip {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 0;
        min-height: 20px;
        padding: 2px 9px;
        border-radius: 999px;
        border: 1px solid rgba(132, 146, 166, .20);
        background: #1c2330;
        color: #dce7f2;
        font-size: 10.5px;
        font-weight: 800;
        line-height: 1.2;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-chip:empty {
        display: none;
      }
      #${rootId} .codex-usage-hud-copy-chip {
        max-width: 116px;
        overflow: hidden;
        text-overflow: ellipsis;
        cursor: default;
      }
      #${rootId} .codex-usage-hud-copy-chip[data-copyable="true"] {
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-copy-chip[data-copyable="true"]:hover {
        border-color: rgba(243, 210, 122, .34);
        color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-chip[data-tone="warning"] {
        border-color: rgba(255, 184, 107, .34);
        background: rgba(36, 27, 16, .88);
        color: #ffb86b;
      }
      #${rootId} .codex-usage-hud-session-stats {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
        align-items: stretch;
      }
      #${rootId} .codex-usage-hud-session-stat {
        min-width: 0;
        display: grid;
        align-content: center;
        min-height: 48px;
      }
      #${rootId} .codex-usage-hud-stat-value {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #e8eef7;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 24px;
        font-weight: 800;
        line-height: 1.08;
      }
      #${rootId} .codex-usage-hud-stat-value.info {
        color: #9ccbff;
      }
      #${rootId} .codex-usage-hud-stat-value.cache {
        color: #8fe3a1;
      }
      #${rootId} .codex-usage-hud-stat-label {
        margin-top: 3px;
        color: #8492a6;
        font-size: 10px;
      }
      #${rootId} .codex-usage-hud-session-insight {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        align-items: center;
        gap: 10px;
        min-height: 32px;
        margin-top: 11px;
        padding: 5px 10px;
        border: 1px solid rgba(132, 146, 166, .16);
        border-radius: 6px;
        background: #101821;
      }
      #${rootId} .codex-usage-hud-session-insight .codex-usage-hud-value:last-child {
        text-align: right;
      }
      #${rootId} .codex-usage-hud-token-breakdown {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 5px;
        margin-top: 7px;
      }
      #${rootId} .codex-usage-hud-session-composition {
        min-width: 0;
        display: block;
        margin-top: 7px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #b8c6d8;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 10.5px;
        line-height: 1.35;
      }
      #${rootId} .codex-usage-hud-session-composition:empty {
        display: none;
      }
      #${rootId} .codex-usage-hud-heavy-rounds-card {
        min-height: 150px;
        display: grid;
        grid-template-rows: auto minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-heavy-rounds {
        min-width: 0;
        min-height: 0;
        display: grid;
        grid-template-rows: repeat(3, minmax(32px, 1fr));
        align-content: start;
        gap: 6px;
        overflow: hidden;
      }
      #${rootId} .codex-usage-hud-heavy-rounds[data-empty="true"] {
        align-content: stretch;
      }
      #${rootId} .codex-usage-hud-heavy-round {
        min-width: 0;
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        align-items: center;
        gap: 8px;
        padding: 6px 8px;
        border: 1px solid rgba(132, 146, 166, .16);
        border-radius: 5px;
        background: #101821;
      }
      #${rootId} .codex-usage-hud-heavy-round[data-placeholder="true"] {
        grid-template-columns: minmax(0, 1fr);
        align-content: center;
        opacity: .72;
      }
      #${rootId} .codex-usage-hud-heavy-round[data-copyable="true"] {
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-heavy-round[data-copyable="true"]:hover {
        border-color: rgba(243, 210, 122, .30);
        background: rgba(243, 210, 122, .06);
      }
      #${rootId} .codex-usage-hud-heavy-round-title,
      #${rootId} .codex-usage-hud-heavy-round-detail {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-heavy-round-title {
        color: #f3d27a;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 10.5px;
        font-weight: 800;
      }
      #${rootId} .codex-usage-hud-heavy-round-detail {
        color: #8492a6;
        font-size: 10px;
      }
      #${rootId} .codex-usage-hud-token-chip {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        padding: 5px 7px;
        border: 1px solid rgba(132, 146, 166, .16);
        border-radius: 5px;
        background: #101821;
        color: #b8c6d8;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 10.5px;
      }
      #${rootId} .codex-usage-hud-token-chip span:first-child {
        margin-right: 5px;
        color: #718095;
        font-family: "Microsoft YaHei UI", system-ui, sans-serif;
      }
      #${rootId} .codex-usage-hud-alert {
        display: grid;
        grid-template-columns: auto auto minmax(0, 1fr) auto;
        gap: 8px;
        align-items: center;
        margin-bottom: 12px;
        padding: 8px 10px;
        border-color: rgba(255, 135, 90, .46);
        background: rgba(36, 24, 16, .90);
      }
      #${rootId} .codex-usage-hud-alert[hidden] {
        display: none !important;
      }
      #${rootId} .codex-usage-hud-alert-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #ffb86b;
      }
      #${rootId} .codex-usage-hud-alert-title {
        color: #ffb86b;
        font-size: 11px;
        font-weight: 800;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-alert [data-field="topWarnings"] {
        min-width: 0;
        line-height: 1.35;
      }
      #${rootId} .codex-usage-hud-alert-close {
        width: 22px;
        height: 22px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border: 0;
        border-radius: 5px;
        background: transparent;
        color: #ffb86b;
        font-family: "Microsoft YaHei UI", system-ui, sans-serif;
        font-size: 16px;
        font-weight: 800;
        line-height: 1;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-alert-close:hover {
        background: rgba(255, 184, 107, .12);
      }
      #${rootId} .codex-usage-hud-cache-pill {
        min-width: 0;
        height: 24px;
      }
      #${rootId} .codex-usage-hud-cache-pill:empty {
        display: none;
      }
      #${rootId} .codex-usage-hud-budget-rails {
        display: grid;
        gap: 7px;
      }
      #${rootId} .codex-usage-hud-budget-rails:empty {
        display: none;
      }
      #${rootId} .codex-usage-hud-budget-rails .codex-usage-hud-progress-rail {
        height: 34px;
      }
      #${rootId} .codex-usage-hud-budget-rails .codex-usage-hud-progress-track-text {
        padding: 0 13px;
        font-size: 12.5px;
      }
      #${rootId} .codex-usage-hud-activity-card {
        display: grid;
        grid-template-rows: auto auto auto auto minmax(0, 1fr);
        height: 100%;
        min-height: 0;
      }
      #${rootId} .codex-usage-hud-activity-main {
        box-sizing: border-box;
        min-width: 0;
        display: grid;
        gap: 4px;
        margin-bottom: 8px;
        padding: 10px 12px;
        border: 1px solid rgba(132, 146, 166, .16);
        border-radius: 6px;
        background: #101821;
      }
      #${rootId} .codex-usage-hud-activity-main strong {
        margin-right: 10px;
        color: #9ccbff;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 12.5px;
      }
      #${rootId} .codex-usage-hud-activity-step {
        box-sizing: border-box;
        min-width: 0;
        display: grid;
        gap: 4px;
        margin-bottom: 12px;
        padding: 8px 10px;
        border: 1px solid rgba(132, 146, 166, .16);
        border-radius: 6px;
        background: #101821;
      }
      #${rootId} .codex-usage-hud-activity-metrics {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 8px;
        margin-bottom: 12px;
      }
      #${rootId} .codex-usage-hud-activity-metric {
        box-sizing: border-box;
        min-width: 0;
        padding: 8px 10px;
        border: 1px solid rgba(132, 146, 166, .16);
        border-radius: 6px;
        background: #101821;
      }
      #${rootId} .codex-usage-hud-activity-metric .codex-usage-hud-value {
        color: #f3d27a;
        font-size: 15px;
        font-weight: 800;
      }
      #${rootId} .codex-usage-hud-activity-trail {
        display: grid;
        grid-template-rows: auto auto auto;
        gap: 8px;
        min-width: 0;
        min-height: 0;
      }
      #${rootId} .codex-usage-hud-activity-trail-head {
        min-width: 0;
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: center;
        gap: 8px;
      }
      #${rootId} .codex-usage-hud-activity-trail .codex-usage-hud-value {
        font-size: 10.5px;
      }
      #${rootId} .codex-usage-hud-activity-timeline {
        min-width: 0;
        min-height: calc(34px * 4 + 7px * 3);
        height: calc(34px * 4 + 7px * 3);
        max-height: calc(34px * 4 + 7px * 3);
        display: grid;
        grid-auto-rows: minmax(34px, auto);
        align-content: start;
        gap: 7px;
        overflow-y: auto;
        padding-right: 3px;
        scrollbar-width: thin;
        scrollbar-color: #273241 #101821;
      }
      #${rootId} .codex-usage-hud-activity-timeline[data-fill="spread"] {
        align-content: start;
      }
      #${rootId} .codex-usage-hud-activity-load-more {
        width: 100%;
        height: 22px;
        border: 1px solid rgba(132, 146, 166, .18);
        border-radius: 5px;
        background: rgba(156, 203, 255, .06);
        color: #9ccbff;
        font-size: 10.5px;
        font-weight: 800;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-activity-load-more:disabled {
        opacity: .58;
        cursor: default;
      }
      #${rootId} .codex-usage-hud-activity-load-more:hover {
        border-color: rgba(156, 203, 255, .36);
        background: rgba(156, 203, 255, .10);
      }
      #${rootId} .codex-usage-hud-activity-load-more:disabled:hover {
        border-color: rgba(132, 146, 166, .18);
        background: rgba(156, 203, 255, .06);
      }
      #${rootId} .codex-usage-hud-activity-node {
        position: relative;
        display: grid;
        grid-template-columns: 48px 10px minmax(0, 1fr);
        gap: 8px;
        align-items: start;
        min-width: 0;
        min-height: 34px;
      }
      #${rootId} .codex-usage-hud-activity-node > span:last-child {
        min-width: 0;
        display: grid;
        gap: 1px;
      }
      #${rootId} .codex-usage-hud-activity-node-time {
        color: #718095;
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
        font-size: 10px;
        line-height: 1.4;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-activity-node-dot {
        position: relative;
        width: 7px;
        height: 7px;
        margin-top: 4px;
        border-radius: 50%;
        background: #9ccbff;
        box-shadow: 0 0 0 3px rgba(156,203,255,.10);
      }
      #${rootId} .codex-usage-hud-activity-node:not(:last-child)::after {
        content: "";
        position: absolute;
        top: 11px;
        bottom: -15px;
        left: 55px;
        width: 1px;
        background: rgba(132, 146, 166, .24);
      }
      #${rootId} .codex-usage-hud-activity-node[data-active="true"] .codex-usage-hud-activity-node-dot {
        background: #f3d27a;
        box-shadow: 0 0 0 3px rgba(243,210,122,.12);
      }
      #${rootId} .codex-usage-hud-activity-node-title {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #dce7f2;
        font-size: 10.5px;
        font-weight: 800;
        line-height: 1.35;
      }
      #${rootId} .codex-usage-hud-activity-node-detail {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: #8492a6;
        font-size: 10px;
        line-height: 1.35;
      }
      #${rootId} .codex-usage-hud-section {
        margin: 0 0 6px;
      }
      #${rootId} .codex-usage-hud-section-title {
        margin-bottom: 1px;
        color: #8492a6;
        font-size: 10px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-value {
        white-space: pre-wrap;
        overflow-wrap: anywhere;
        color: #dde7f2;
        font-size: 11px;
        line-height: 1.45;
      }
      #${rootId} .codex-usage-hud-activity-main .codex-usage-hud-value,
      #${rootId} .codex-usage-hud-activity-step .codex-usage-hud-value,
      #${rootId} .codex-usage-hud-activity-metric .codex-usage-hud-value {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-value.mono {
        font-family: Consolas, "Cascadia Mono", ui-monospace, monospace;
      }
      #${rootId} .codex-usage-hud-value.accent {
        color: #f3d27a;
        font-size: 13px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-value.blue {
        color: #9ccbff;
      }
      #${rootId} .codex-usage-hud-value.warn {
        color: #ffb86b;
      }
      #${rootId} .codex-usage-hud-value.muted {
        color: #a9bcd2;
      }
      #${rootId} .codex-usage-hud-value[data-copyable="true"] {
        cursor: pointer;
        border-radius: 4px;
        transition: color .12s ease, background .12s ease;
      }
      #${rootId} .codex-usage-hud-value[data-copyable="true"][data-copy-field="slow"] {
        color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-value[data-copyable="true"][data-copy-field="gap"] {
        color: #bcd7ff;
      }
      #${rootId} .codex-usage-hud-value[data-copyable="true"]:hover {
        background: rgba(243, 210, 122, .08);
      }
      #${rootId} .codex-usage-hud-value[data-copied="true"] {
        color: #8fe3a1 !important;
      }
      #${rootId} .codex-usage-hud-divider {
        height: 1px;
        margin: 5px 0 6px;
        background: #273241;
      }
      #${rootId} .codex-usage-hud-request-subhead {
        display: flex;
        align-items: center;
        justify-content: space-between;
        min-height: 14px;
        margin-bottom: 2px;
        padding: 0 2px;
        color: #718095;
        font-size: 10px;
        font-weight: 700;
        line-height: 14px;
      }
      #${rootId} .codex-usage-hud-request-subhead span:last-child {
        color: #566477;
        font-weight: 500;
      }
      #${rootId} .codex-usage-hud-request-list {
        min-height: 0;
        max-height: 100%;
        overflow: auto;
        border-radius: 4px;
        background: #101821;
        padding: 4px;
        scrollbar-width: thin;
        scrollbar-color: #273241 #101821;
      }
      #${rootId} .codex-usage-hud-row {
        white-space: pre;
        color: #dce7f2;
        font: 11px/1.45 Consolas, "Cascadia Mono", ui-monospace, monospace;
      }
      #${rootId} .codex-usage-hud-row:first-child {
        color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-row-time[data-running="true"] {
        color: #9ccbff;
      }
      #${rootId} .${warningClass} {
        color: #ffb86b !important;
      }
      #${rootId} .${errorClass} {
        color: #ff6b6b !important;
      }
      #${rootId} .codex-usage-hud-runtime-errors[hidden] {
        display: none !important;
      }
      #${rootId} .codex-usage-hud-runtime-errors {
        position: fixed;
        left: 16px;
        bottom: 16px;
        z-index: 2147482760;
        max-height: min(360px, calc(100vh - 32px));
        overflow: hidden;
        box-sizing: border-box;
        border: 1px solid rgba(255, 107, 107, .45);
        border-radius: 7px;
        background: rgba(16, 22, 29, .97);
        color: #e8eef7;
        box-shadow: 0 18px 48px rgba(0, 0, 0, .46);
        pointer-events: auto;
        padding: 0;
        user-select: text;
        font: 11px/1.45 Consolas, "Cascadia Mono", ui-monospace, monospace;
      }
      #${rootId} .codex-usage-hud-runtime-errors[data-expanded="false"] {
        width: max-content;
        min-width: 88px;
        max-width: calc(100vw - 32px);
      }
      #${rootId} .codex-usage-hud-runtime-errors[data-expanded="true"] {
        width: min(520px, calc(100vw - 32px));
      }
      #${rootId} .codex-usage-hud-runtime-errors-title {
        display: flex;
        align-items: center;
        justify-content: flex-start;
        gap: 7px;
        padding: 7px 9px;
        border-bottom: 1px solid rgba(255, 107, 107, .18);
        background: rgba(32, 40, 51, .82);
        color: #ffb3b3;
        font-weight: 700;
        cursor: grab;
        user-select: none;
        touch-action: none;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-runtime-errors-title:active {
        cursor: grabbing;
      }
      #${rootId} .codex-usage-hud-runtime-errors-toggle {
        display: inline-grid;
        place-items: center;
        flex: 0 0 auto;
        width: 17px;
        height: 17px;
        box-sizing: border-box;
        border: 1px solid rgba(255, 179, 179, .26);
        border-radius: 4px;
        padding: 0;
        background: rgba(255, 107, 107, .10);
        color: #ffd2d2;
        font: 700 10px/1 Consolas, "Cascadia Mono", ui-monospace, monospace;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-runtime-errors-toggle:hover {
        border-color: rgba(255, 179, 179, .46);
        background: rgba(255, 107, 107, .18);
      }
      #${rootId} .codex-usage-hud-runtime-errors-count {
        color: #ffd2d2;
        font-variant-numeric: tabular-nums;
      }
      #${rootId} .codex-usage-hud-runtime-errors[data-expanded="false"] .codex-usage-hud-runtime-errors-title {
        border-bottom: 0;
      }
      #${rootId} .codex-usage-hud-runtime-errors-body {
        max-height: calc(min(360px, calc(100vh - 32px)) - 34px);
        overflow: auto;
        padding: 0 10px 8px;
        scrollbar-width: thin;
        scrollbar-color: #3b4149 #101821;
        user-select: text;
      }
      #${rootId} .codex-usage-hud-runtime-error {
        display: grid;
        gap: 3px;
        padding: 7px 0;
        border-top: 1px solid rgba(255, 107, 107, .18);
      }
      #${rootId} .codex-usage-hud-runtime-error-code {
        color: #ff8f8f;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-runtime-error-meta {
        color: #8492a6;
      }
      #${rootId} .codex-usage-hud-runtime-error-context {
        color: #b8c6d8;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }
      #${rootId} .codex-usage-hud-settings-modal[hidden] {
        display: none !important;
      }
      #${rootId} .codex-usage-hud-settings-modal {
        position: fixed;
        inset: 0;
        z-index: 2147482750;
        display: grid;
        place-items: center;
        padding: 24px;
        background: color-mix(in srgb, var(--codex-usage-hud-surface, #10161d) 52%, transparent);
        pointer-events: auto;
      }
      #${rootId} .codex-usage-hud-settings-dialog {
        position: relative;
        width: min(760px, calc(100vw - 48px));
        max-height: min(720px, calc(100vh - 48px));
        display: grid;
        grid-template-rows: auto auto minmax(0, 1fr) auto;
        border: 1px solid var(--codex-usage-hud-panel-border, #3a485a);
        border-radius: 8px;
        background: var(--codex-usage-hud-surface, #10161d);
        color: var(--codex-usage-hud-text, #e8eef7);
        box-shadow: 0 24px 70px rgba(0, 0, 0, .44);
        overflow: hidden;
        font: 12px "Microsoft YaHei UI", system-ui, sans-serif;
      }
      #${rootId} .codex-usage-hud-settings-head,
      #${rootId} .codex-usage-hud-settings-actions {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 10px 12px;
        background: var(--codex-usage-hud-header-surface, #151d27);
      }
      #${rootId} .codex-usage-hud-settings-title {
        font-size: 13px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-settings-close,
      #${rootId} .codex-usage-hud-settings-action {
        border: 0;
        border-radius: 5px;
        background: var(--codex-usage-hud-panel-border, #2e3846);
        color: var(--codex-usage-hud-text, #dde7f2);
        min-height: 28px;
        padding: 4px 9px;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-settings-action[data-primary="true"] {
        background: var(--codex-usage-hud-accent, #f3d27a);
        color: var(--codex-usage-hud-progress-day-text, #10161d);
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-settings-action[data-danger="true"] {
        background: var(--codex-usage-hud-error, #ff6b6b);
        color: #160707;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-settings-action:disabled,
      #${rootId} .codex-usage-hud-settings-link:disabled {
        cursor: not-allowed;
        opacity: .52;
      }
      #${rootId} .codex-usage-hud-settings-link {
        min-height: 0;
        border: 0;
        border-radius: 0;
        background: transparent;
        color: var(--codex-usage-hud-accent, #f3d27a);
        padding: 0;
        cursor: pointer;
        font: inherit;
        font-size: 11px;
        font-weight: 700;
        text-decoration: underline;
        text-underline-offset: 2px;
      }
      #${rootId} .codex-usage-hud-settings-tabs {
        display: flex;
        gap: 6px;
        padding: 8px 12px;
        border-top: 1px solid var(--codex-usage-hud-divider, #202833);
        border-bottom: 1px solid var(--codex-usage-hud-divider, #202833);
        background: var(--codex-usage-hud-surface, #10161d);
      }
      #${rootId} .codex-usage-hud-settings-tab {
        border: 0;
        border-radius: 5px;
        background: transparent;
        color: var(--codex-usage-hud-muted, #a9bcd2);
        padding: 5px 9px;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-settings-tab[data-active="true"] {
        background: var(--codex-usage-hud-header-surface, #202833);
        color: var(--codex-usage-hud-accent, #f3d27a);
      }
      #${rootId} .codex-usage-hud-settings-body {
        min-height: 0;
        overflow: auto;
        padding: 12px;
        scrollbar-width: thin;
        scrollbar-color: var(--codex-usage-hud-divider, #273241) var(--codex-usage-hud-surface, #10161d);
      }
      #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="backgroundUsage"] {
        width: min(1060px, calc(100vw - 48px));
        height: min(760px, calc(100vh - 48px));
      }
      #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="backgroundUsage"] .codex-usage-hud-settings-body {
        overflow: hidden;
        padding: 0;
      }
      #${rootId} .codex-usage-hud-background {
        min-height: 0;
        height: 100%;
        display: grid;
        grid-template-rows: auto auto minmax(0, 1fr);
        color: var(--codex-usage-hud-text, #e8eef7);
      }
      #${rootId} .codex-usage-hud-background-metrics {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 8px;
        padding: 12px;
        border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-background-metrics > div {
        min-width: 0;
        display: grid;
        gap: 2px;
        padding: 9px 10px;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 6px;
        background: var(--codex-usage-hud-request-panel-surface, #121a23);
      }
      #${rootId} .codex-usage-hud-background-metrics span,
      #${rootId} .codex-usage-hud-background-metrics small {
        min-width: 0;
        overflow: hidden;
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-background-metrics strong {
        min-width: 0;
        overflow: hidden;
        color: var(--codex-usage-hud-warning, #ffb86b);
        font-size: 17px;
        line-height: 1.25;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-background-metrics > div:nth-child(2) strong {
        color: var(--codex-usage-hud-info, #9ccbff);
      }
      #${rootId} .codex-usage-hud-background-metrics > div:nth-child(3) strong {
        color: var(--codex-usage-hud-text, #e8eef7);
      }
      #${rootId} .codex-usage-hud-background-metrics > div:nth-child(4) strong {
        color: var(--codex-usage-hud-success, #8fe3a1);
        font-size: 13px;
      }
      #${rootId} .codex-usage-hud-background-toolbar {
        display: grid;
        grid-template-columns: auto minmax(140px, 1fr) minmax(140px, 1fr);
        gap: 8px;
        align-items: center;
        padding: 8px 12px;
        border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-background-range {
        display: inline-flex;
        min-width: 0;
        padding-right: 8px;
        border-right: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-background-range button,
      #${rootId} .codex-usage-hud-background-toolbar select {
        min-height: 30px;
        box-sizing: border-box;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        background: var(--codex-usage-hud-panel-surface, #141b24);
        color: var(--codex-usage-hud-muted, #a9bcd2);
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-background-range button {
        padding: 4px 10px;
        border-right: 0;
      }
      #${rootId} .codex-usage-hud-background-range button:first-child {
        border-radius: 5px 0 0 5px;
      }
      #${rootId} .codex-usage-hud-background-range button:last-child {
        border-right: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 0 5px 5px 0;
      }
      #${rootId} .codex-usage-hud-background-range button[data-active="true"] {
        border-color: var(--codex-usage-hud-warning, #ffb86b);
        background: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 14%, var(--codex-usage-hud-panel-surface, #141b24));
        color: var(--codex-usage-hud-warning, #ffb86b);
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-background-toolbar select {
        min-width: 0;
        width: 100%;
        border-radius: 5px;
        padding: 4px 8px;
      }
      #${rootId} .codex-usage-hud-background-master-detail {
        min-height: 0;
        display: grid;
        grid-template-columns: minmax(280px, 340px) minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-background-history,
      #${rootId} .codex-usage-hud-background-detail {
        min-width: 0;
        min-height: 0;
        overflow: auto;
        padding: 12px;
        scrollbar-width: thin;
      }
      #${rootId} .codex-usage-hud-background-history {
        border-right: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-session-ranking-note {
        padding: 0 12px 8px;
      }
      #${rootId} .codex-usage-hud-background-section-title {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        min-height: 24px;
        color: var(--codex-usage-hud-text, #e8eef7);
        font-size: 11px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-background-section-title > span:last-child {
        color: var(--codex-usage-hud-muted, #8492a6);
        font-weight: 600;
      }
      #${rootId} .codex-usage-hud-background-event-list {
        display: grid;
        gap: 7px;
        margin-top: 7px;
      }
      #${rootId} .codex-usage-hud-background-event {
        position: relative;
        min-width: 0;
        width: 100%;
        display: grid;
        gap: 5px;
        padding: 9px 10px;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 6px;
        background: transparent;
        color: inherit;
        text-align: left;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-background-unread-dot {
        position: absolute;
        top: 5px;
        right: 5px;
        width: 7px;
        height: 7px;
        border: 1px solid var(--codex-usage-hud-panel-surface, #141b24);
        border-radius: 50%;
        background: var(--codex-usage-hud-error, #ff6b6b);
        box-shadow: 0 0 0 1px color-mix(in srgb, var(--codex-usage-hud-error, #ff6b6b) 22%, transparent);
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-background-event[data-unread="true"] .codex-usage-hud-background-event-head {
        padding-right: 8px;
      }
      #${rootId} .codex-usage-hud-background-event:hover,
      #${rootId} .codex-usage-hud-background-event[data-selected="true"] {
        border-color: var(--codex-usage-hud-warning, #ffb86b);
        background: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 8%, var(--codex-usage-hud-panel-surface, #141b24));
      }
      #${rootId} .codex-usage-hud-background-event-head,
      #${rootId} .codex-usage-hud-background-event-totals {
        min-width: 0;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 7px;
      }
      #${rootId} .codex-usage-hud-background-event-title {
        min-width: 0;
        overflow: hidden;
        color: var(--codex-usage-hud-text, #e8eef7);
        font-weight: 700;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-background-event-meta {
        min-width: 0;
        overflow: hidden;
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-background-event-totals {
        justify-content: flex-start;
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
      }
      #${rootId} .codex-usage-hud-background-event-totals strong {
        color: var(--codex-usage-hud-text, #e8eef7);
      }
      #${rootId} .codex-usage-hud-background-event-totals span:last-child {
        margin-left: auto;
        color: var(--codex-usage-hud-warning, #ffb86b);
      }
      #${rootId} .codex-usage-hud-session-ranking-row .codex-usage-hud-background-event-totals > * {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-background-status {
        flex: 0 0 auto;
        padding: 2px 7px;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 4px;
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 9px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-background-detail-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 10px;
        padding-bottom: 10px;
        border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-background-detail-head > div {
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-background-detail-head h3 {
        min-width: 0;
        margin: 0;
        overflow: hidden;
        font-size: 15px;
        line-height: 1.35;
        letter-spacing: 0;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-background-detail-head > .codex-usage-hud-settings-action {
        flex: 0 0 auto;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-background-detail-sub {
        color: var(--codex-usage-hud-info, #9ccbff);
        font: 10px Consolas, "Cascadia Mono", ui-monospace, monospace;
      }
      #${rootId} .codex-usage-hud-background-detail-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 8px 12px;
        padding: 12px 0;
        border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-background-detail-grid > div {
        min-width: 0;
        display: grid;
        gap: 2px;
      }
      #${rootId} .codex-usage-hud-background-detail-grid span {
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 9px;
      }
      #${rootId} .codex-usage-hud-background-detail-grid strong {
        min-width: 0;
        overflow: hidden;
        font-size: 10px;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-background-detail-grid .codex-usage-hud-background-detail-wide {
        grid-column: span 2;
      }
      #${rootId} .codex-usage-hud-background-detail-grid .codex-usage-hud-background-detail-full {
        grid-column: 1 / -1;
      }
      #${rootId} .codex-usage-hud-background-requests,
      #${rootId} .codex-usage-hud-background-prompt {
        padding-top: 10px;
      }
      #${rootId} .codex-usage-hud-background-request-list {
        display: grid;
        gap: 4px;
        margin-top: 6px;
      }
      #${rootId} .codex-usage-hud-background-request {
        min-width: 0;
        display: grid;
        grid-template-columns: 74px minmax(100px, 1.2fr) minmax(96px, 1fr) 58px 82px 28px;
        gap: 7px;
        align-items: center;
        min-height: 28px;
        padding: 0 8px;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 4px;
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 9px;
      }
      #${rootId} .codex-usage-hud-background-request > * {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-background-request strong,
      #${rootId} .codex-usage-hud-background-request-endpoint {
        color: var(--codex-usage-hud-text, #e8eef7);
      }
      #${rootId} .codex-usage-hud-background-request > span:nth-child(5) {
        color: var(--codex-usage-hud-warning, #ffb86b);
        text-align: right;
      }
      #${rootId} .codex-usage-hud-background-request-index {
        text-align: right;
      }
      #${rootId} .codex-usage-hud-background-prompt pre {
        max-height: 150px;
        box-sizing: border-box;
        margin: 6px 0 0;
        overflow: auto;
        padding: 9px 10px;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 5px;
        background: var(--codex-usage-hud-request-panel-surface, #121a23);
        color: var(--codex-usage-hud-request-text, #d8e2ef);
        font: 9px/1.55 Consolas, "Cascadia Mono", ui-monospace, monospace;
        white-space: pre-wrap;
        overflow-wrap: anywhere;
        cursor: text;
        user-select: text;
        -webkit-user-select: text;
      }
      #${rootId} .codex-usage-hud-background-prompt pre[data-expanded="true"] {
        max-height: 320px;
      }
      #${rootId} .codex-usage-hud-background-empty,
      #${rootId} .codex-usage-hud-background-error {
        padding: 16px;
        color: var(--codex-usage-hud-muted, #8492a6);
        text-align: center;
      }
      #${rootId} .codex-usage-hud-background-error {
        padding: 7px 12px;
        background: color-mix(in srgb, var(--codex-usage-hud-error, #ff7b86) 8%, transparent);
        color: var(--codex-usage-hud-error, #ff7b86);
        text-align: left;
      }
      #${rootId} .codex-usage-hud-settings-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
      }
      #${rootId} .codex-usage-hud-settings-compact-row {
        grid-column: 1 / -1;
        min-width: 0;
        display: grid;
        grid-template-columns: minmax(112px, 0.9fr) minmax(88px, 0.72fr) minmax(0, 1.45fr);
        gap: 8px;
        align-items: end;
      }
      #${rootId} .codex-usage-hud-settings-compact-row > .codex-usage-hud-settings-field {
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-settings-compact-row [data-setting-key="work_overlay_max_items"],
      #${rootId} .codex-usage-hud-settings-compact-row [data-setting-key="budget_thresholds"] {
        max-width: 100%;
      }
      #${rootId} .codex-usage-hud-settings-field,
      #${rootId} .codex-usage-hud-price-table {
        min-width: 0;
        display: grid;
        gap: 4px;
      }
      #${rootId} .codex-usage-hud-settings-field label,
      #${rootId} .codex-usage-hud-price-title {
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-settings-field input,
      #${rootId} .codex-usage-hud-settings-field select,
      #${rootId} .codex-usage-hud-price-row input {
        min-width: 0;
        box-sizing: border-box;
        width: 100%;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 5px;
        background: var(--codex-usage-hud-panel-surface, #141b24);
        color: var(--codex-usage-hud-text, #e8eef7);
        min-height: 30px;
        padding: 5px 7px;
        outline: none;
      }
      #${rootId} .codex-usage-hud-settings-field input:focus,
      #${rootId} .codex-usage-hud-settings-field select:focus,
      #${rootId} .codex-usage-hud-price-row input:focus {
        border-color: var(--codex-usage-hud-accent, #f3d27a);
      }
      #${rootId} .codex-usage-hud-overlay-dependency {
        min-height: 30px;
        box-sizing: border-box;
        display: flex;
        flex-wrap: nowrap;
        align-items: center;
        gap: 8px;
        padding: 5px 8px;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 5px;
        background: var(--codex-usage-hud-panel-surface, #141b24);
        overflow: hidden;
      }
      #${rootId} .codex-usage-hud-overlay-dependency-state {
        color: var(--codex-usage-hud-text, #e8eef7);
        font-size: 11px;
        font-weight: 700;
        white-space: nowrap;
        flex: 0 0 auto;
      }
      #${rootId} .codex-usage-hud-overlay-dependency-version,
      #${rootId} .codex-usage-hud-overlay-dependency-note {
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 11px;
        line-height: 1.2;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        min-width: 0;
        flex: 1 1 auto;
      }
      #${rootId} .codex-usage-hud-overlay-dependency-version {
        color: var(--codex-usage-hud-success, #8fe3a1);
        font: 700 11px Consolas, "Cascadia Mono", ui-monospace, monospace;
        flex: 0 1 auto;
      }
      #${rootId} .codex-usage-hud-overlay-dependency-actions {
        display: inline-flex;
        flex-wrap: nowrap;
        gap: 8px;
        flex: 0 0 auto;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-settings-inline {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 8px;
        align-items: center;
      }
      #${rootId} .codex-usage-hud-settings-inline .codex-usage-hud-settings-action {
        min-height: 30px;
        padding-inline: 12px;
      }
      #${rootId} .codex-usage-hud-settings-visually-hidden {
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        white-space: nowrap;
        border: 0;
      }
      #${rootId} .codex-usage-hud-provider-editor {
        min-width: 0;
        grid-column: 1 / -1;
        display: grid;
        gap: 8px;
        margin-top: 4px;
        border-top: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-provider-editor-head {
        min-width: 0;
        min-height: 42px;
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        align-items: center;
        gap: 10px;
        padding-top: 5px;
        border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-provider-tabs {
        min-width: 0;
        display: flex;
        gap: 2px;
        overflow-x: auto;
        overscroll-behavior-inline: contain;
        scrollbar-width: thin;
        scrollbar-color: var(--codex-usage-hud-divider, #273241) transparent;
      }
      #${rootId} .codex-usage-hud-provider-tab {
        position: relative;
        flex: 0 0 auto;
        min-width: 88px;
        min-height: 34px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
        border: 0;
        border-bottom: 2px solid transparent;
        background: transparent;
        color: var(--codex-usage-hud-muted, #8492a6);
        padding: 5px 10px 4px;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-provider-tab:hover {
        background: var(--codex-usage-hud-header-surface, #202833);
        color: var(--codex-usage-hud-text, #e8eef7);
      }
      #${rootId} .codex-usage-hud-provider-tab[aria-selected="true"] {
        border-bottom-color: var(--codex-usage-hud-accent, #f3d27a);
        color: var(--codex-usage-hud-text, #e8eef7);
      }
      #${rootId} .codex-usage-hud-provider-tab-badge {
        border: 1px solid var(--codex-usage-hud-panel-border, #3a485a);
        border-radius: 4px;
        color: var(--codex-usage-hud-muted, #8492a6);
        padding: 1px 4px;
        font-size: 9px;
        line-height: 1.4;
      }
      #${rootId} .codex-usage-hud-provider-tab[aria-selected="true"] .codex-usage-hud-provider-tab-badge {
        border-color: color-mix(in srgb, var(--codex-usage-hud-accent, #f3d27a) 48%, transparent);
        color: var(--codex-usage-hud-accent, #f3d27a);
      }
      #${rootId} .codex-usage-hud-provider-dirty-dot {
        width: 6px;
        height: 6px;
        flex: 0 0 6px;
        border-radius: 50%;
        background: var(--codex-usage-hud-warning, #ffb86b);
      }
      #${rootId} .codex-usage-hud-provider-context {
        min-width: 0;
        min-height: 28px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        flex-wrap: nowrap;
      }
      #${rootId} .codex-usage-hud-provider-scope {
        min-width: 0;
        display: inline-flex;
        align-items: center;
        gap: 7px;
        color: var(--codex-usage-hud-text, #e8eef7);
        cursor: pointer;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-provider-scope-options {
        min-width: 0;
        display: inline-flex;
        align-items: center;
        flex-wrap: nowrap;
        gap: 8px 14px;
        flex: 1 1 auto;
      }
      #${rootId} .codex-usage-hud-provider-scope input {
        width: 15px;
        height: 15px;
        margin: 0;
        accent-color: var(--codex-usage-hud-accent, #f3d27a);
      }
      #${rootId} .codex-usage-hud-provider-scope input:disabled {
        opacity: .78;
        cursor: not-allowed;
      }
      #${rootId} .codex-usage-hud-provider-meta {
        min-width: 0;
        color: var(--codex-usage-hud-muted, #8492a6);
        text-align: right;
        overflow-wrap: anywhere;
      }
      #${rootId} .codex-usage-hud-provider-meta[data-tone="required"] {
        color: var(--codex-usage-hud-success, #8fe3a1);
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-provider-meta[data-tone="historical"] {
        color: var(--codex-usage-hud-warning, #ffb86b);
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-provider-context-adjustment {
        min-width: 140px;
        width: 160px;
        flex: 0 0 160px;
      }
      #${rootId} .codex-usage-hud-provider-context-adjustment input {
        min-width: 0;
        box-sizing: border-box;
        width: 100%;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 5px;
        background: var(--codex-usage-hud-panel-surface, #141b24);
        color: var(--codex-usage-hud-text, #e8eef7);
        min-height: 28px;
        padding: 4px 7px;
        outline: none;
      }
      #${rootId} .codex-usage-hud-provider-context-adjustment input:focus {
        border-color: var(--codex-usage-hud-accent, #f3d27a);
      }
      #${rootId} .codex-usage-hud-price-actions {
        min-width: 0;
        display: grid;
        grid-template-columns: auto minmax(0, 1fr) auto;
        gap: 8px;
        align-items: center;
        margin-top: 6px;
      }
      #${rootId} .codex-usage-hud-price-actions > .codex-usage-hud-settings-action {
        justify-self: start;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-price-actions input[data-setting-key="pricing_url"] {
        min-width: 0;
        box-sizing: border-box;
        width: 100%;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 5px;
        background: var(--codex-usage-hud-panel-surface, #141b24);
        color: var(--codex-usage-hud-text, #e8eef7);
        min-height: 30px;
        padding: 5px 7px;
        outline: none;
      }
      #${rootId} .codex-usage-hud-price-actions input[data-setting-key="pricing_url"]:focus {
        border-color: var(--codex-usage-hud-accent, #f3d27a);
      }
      #${rootId} .codex-usage-hud-provider-empty {
        color: var(--codex-usage-hud-muted, #8492a6);
        padding: 8px 0 4px;
      }
      #${rootId} .codex-usage-hud-price-unit {
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-price-table {
        grid-column: 1 / -1;
        margin-top: 4px;
        overflow-x: auto;
        scrollbar-width: thin;
        scrollbar-color: var(--codex-usage-hud-divider, #273241) transparent;
      }
      #${rootId} .codex-usage-hud-price-row,
      #${rootId} .codex-usage-hud-price-header {
        display: grid;
        grid-template-columns: minmax(130px, 1.4fr) repeat(4, minmax(72px, 1fr));
        gap: 6px;
        align-items: center;
        min-width: 610px;
      }
      #${rootId} .codex-usage-hud-price-advanced {
        display: none;
      }
      #${rootId} .codex-usage-hud-price-header {
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-price-detected {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: center;
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 11px;
      }
      #${rootId} .codex-usage-hud-settings-status {
        min-width: 0;
        color: var(--codex-usage-hud-request-muted, #a9bcd2);
        font-size: 11px;
        overflow-wrap: anywhere;
      }
      #${rootId} .codex-usage-hud-settings-footnote {
        grid-column: 1 / -1;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        margin-top: 2px;
      }
      #${rootId} .codex-usage-hud-settings-footnote .codex-usage-hud-settings-status {
        text-align: right;
      }
      #${rootId} .codex-usage-hud-settings-status[data-kind="error"] {
        color: var(--codex-usage-hud-warning, #ffb86b);
      }
      #${rootId} .codex-usage-hud-storage {
        display: grid;
        gap: 12px;
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-storage-pathbar,
      #${rootId} .codex-usage-hud-storage-summary,
      #${rootId} .codex-usage-hud-storage-preview-head,
      #${rootId} .codex-usage-hud-storage-preview-foot {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-storage-path,
      #${rootId} .codex-usage-hud-storage-item-path {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font: 11px Consolas, "Cascadia Mono", ui-monospace, monospace;
      }
      #${rootId} .codex-usage-hud-storage-muted {
        color: var(--codex-usage-hud-muted, #8492a6);
      }
      #${rootId} .codex-usage-hud-storage-status,
      #${rootId} .codex-usage-hud-storage-policy {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        flex: 0 0 auto;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 4px;
        padding: 3px 6px;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-storage-status[data-state="running"],
      #${rootId} .codex-usage-hud-storage-status[data-state="queued_exit"] {
        color: var(--codex-usage-hud-warning, #ffb86b);
        border-color: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 55%, transparent);
      }
      #${rootId} .codex-usage-hud-storage-status[data-state="completed"],
      #${rootId} .codex-usage-hud-storage-policy[data-policy="candidate"] {
        color: var(--codex-usage-hud-success, #8fe3a1);
        border-color: color-mix(in srgb, var(--codex-usage-hud-success, #8fe3a1) 55%, transparent);
      }
      #${rootId} .codex-usage-hud-storage-policy[data-policy="managed"] {
        color: var(--codex-usage-hud-info, #9ccbff);
        border-color: color-mix(in srgb, var(--codex-usage-hud-info, #9ccbff) 55%, transparent);
      }
      #${rootId} .codex-usage-hud-storage-policy[data-policy="blocked"],
      #${rootId} .codex-usage-hud-storage-policy[data-policy="unknown"] {
        color: var(--codex-usage-hud-warning, #ffb86b);
        border-color: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 55%, transparent);
      }
      #${rootId} .codex-usage-hud-storage-summary {
        display: grid;
        grid-template-columns: minmax(0, 1.3fr) repeat(2, minmax(90px, .7fr));
        align-items: stretch;
        gap: 8px;
        padding: 10px 0;
        border-top: 1px solid var(--codex-usage-hud-divider, #273241);
        border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-storage-summary-value,
      #${rootId} .codex-usage-hud-storage-summary-label {
        margin: 0;
      }
      #${rootId} .codex-usage-hud-storage-summary-value {
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-storage-summary-main .codex-usage-hud-storage-summary-value {
        color: var(--codex-usage-hud-accent, #f3d27a);
      }
      #${rootId} .codex-usage-hud-storage-summary-label {
        margin-top: 3px;
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
      }
      #${rootId} .codex-usage-hud-storage-filters {
        display: flex;
        gap: 6px;
        overflow-x: auto;
        padding-bottom: 2px;
        scrollbar-width: thin;
      }
      #${rootId} .codex-usage-hud-storage-filter {
        flex: 0 0 auto;
        border: 0;
        border-radius: 5px;
        background: transparent;
        color: var(--codex-usage-hud-muted, #8492a6);
        padding: 5px 8px;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-storage-filter[data-active="true"] {
        background: var(--codex-usage-hud-header-surface, #202833);
        color: var(--codex-usage-hud-accent, #f3d27a);
      }
      #${rootId} .codex-usage-hud-storage-categories,
      #${rootId} .codex-usage-hud-storage-items {
        display: grid;
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-storage-category,
      #${rootId} .codex-usage-hud-storage-item {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto auto;
        align-items: center;
        gap: 8px;
        min-width: 0;
        padding: 8px 0;
        border-top: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-storage-item {
        grid-template-columns: auto minmax(0, 1fr) auto auto auto;
      }
      #${rootId} .codex-usage-hud-storage-category-main,
      #${rootId} .codex-usage-hud-storage-item-main {
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-storage-category-title {
        display: flex;
        align-items: center;
        gap: 7px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-storage-marker {
        width: 7px;
        height: 7px;
        flex: 0 0 7px;
        border-radius: 50%;
        background: var(--codex-usage-hud-warning, #ffb86b);
      }
      #${rootId} .codex-usage-hud-storage-marker[data-policy="candidate"] { background: var(--codex-usage-hud-success, #8fe3a1); }
      #${rootId} .codex-usage-hud-storage-marker[data-policy="managed"] { background: var(--codex-usage-hud-info, #9ccbff); }
      #${rootId} .codex-usage-hud-storage-marker[data-policy="blocked"] { background: var(--codex-usage-hud-error, #ff6b6b); }
      #${rootId} .codex-usage-hud-storage-meta {
        margin-top: 3px;
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        overflow-wrap: anywhere;
      }
      #${rootId} .codex-usage-hud-storage-size {
        color: var(--codex-usage-hud-text, #e8eef7);
        white-space: nowrap;
        font: 11px Consolas, "Cascadia Mono", ui-monospace, monospace;
      }
      #${rootId} .codex-usage-hud-storage-item input[type="checkbox"] {
        width: 15px;
        height: 15px;
        accent-color: var(--codex-usage-hud-accent, #f3d27a);
      }
      #${rootId} .codex-usage-hud-storage-item-action {
        min-height: 26px;
        border: 0;
        border-radius: 4px;
        background: var(--codex-usage-hud-panel-border, #2e3846);
        color: var(--codex-usage-hud-text, #e8eef7);
        padding: 4px 7px;
        cursor: pointer;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-storage-item-action:disabled { opacity: .55; cursor: not-allowed; }
      #${rootId} .codex-usage-hud-storage-item-actions {
        display: flex;
        flex-wrap: wrap;
        justify-content: flex-end;
        gap: 5px;
        grid-column: 4;
      }
      #${rootId} .codex-usage-hud-storage-preview {
        display: grid;
        gap: 8px;
        padding: 10px;
        border: 1px solid color-mix(in srgb, var(--codex-usage-hud-accent, #f3d27a) 45%, transparent);
        border-radius: 6px;
        background: color-mix(in srgb, var(--codex-usage-hud-accent, #f3d27a) 7%, var(--codex-usage-hud-panel-surface, #141b24));
      }
      #${rootId} .codex-usage-hud-storage-preview-list {
        display: grid;
        gap: 5px;
        max-height: 150px;
        overflow: auto;
      }
      #${rootId} .codex-usage-hud-storage-preview-row {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        min-width: 0;
        padding-top: 5px;
        border-top: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 72%, transparent);
      }
      #${rootId} .codex-usage-hud-storage-note {
        margin: 0;
        color: var(--codex-usage-hud-muted, #8492a6);
        line-height: 1.5;
      }
      #${rootId} .codex-usage-hud-storage-empty {
        padding: 14px 0;
        color: var(--codex-usage-hud-muted, #8492a6);
        text-align: center;
      }
      #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="storage"] {
        width: min(980px, calc(100vw - 48px));
        height: min(572px, calc(100vh - 48px));
        max-height: min(572px, calc(100vh - 48px));
        overflow: hidden;
      }
      #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="storage"] .codex-usage-hud-settings-body {
        padding: 0;
        overflow: hidden;
        min-height: 0;
        height: 100%;
        display: flex;
        flex-direction: column;
      }
      #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="storage"] .codex-usage-hud-settings-actions {
        display: none;
      }
      #${rootId} .codex-usage-hud-cleanup-workspace {
        min-width: 0;
        min-height: 0;
        height: 100%;
        flex: 1 1 auto;
        display: grid;
        grid-template-rows: auto minmax(0, 1fr) auto;
        overflow: hidden;
      }
      #${rootId} .codex-usage-hud-cleanup-page-head {
        min-height: 52px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 9px 14px;
        border-bottom: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 88%, transparent);
        background: color-mix(in srgb, var(--codex-usage-hud-surface, #10161d) 86%, #191a1c);
      }
      #${rootId} .codex-usage-hud-cleanup-segments {
        display: inline-grid;
        grid-template-columns: repeat(2, minmax(96px, 1fr));
        min-width: 0;
        border: 1px solid #414349;
        border-radius: 6px;
        overflow: hidden;
        background: #151618;
      }
      #${rootId} .codex-usage-hud-cleanup-segments button {
        min-width: 96px;
        min-height: 31px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 7px;
        border: 0;
        border-right: 1px solid #414349;
        border-radius: 0;
        background: transparent;
        color: var(--codex-usage-hud-muted, #9da1a8);
        padding: 0 12px;
        cursor: pointer;
        font: inherit;
        font-size: 12px;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-cleanup-segments button:last-child { border-right: 0; }
      #${rootId} .codex-usage-hud-cleanup-segments button[data-active="true"] {
        background: #2b2d31;
        color: var(--codex-usage-hud-text, #f1f3f5);
        font-weight: 650;
      }
      #${rootId} .codex-usage-hud-cleanup-head-meta {
        color: #73777f;
        font-size: 11px;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-cleanup-content {
        min-width: 0;
        min-height: 0;
        overflow-x: hidden;
        overflow-y: auto;
        position: relative;
        scrollbar-width: thin;
      }
      #${rootId} .codex-usage-hud-cleanup-footer {
        min-height: 50px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 8px 13px;
        border-top: 1px solid var(--codex-usage-hud-divider, #393b40);
        background: #2c2d30;
      }
      #${rootId} .codex-usage-hud-cleanup-footer-meta {
        min-width: 0;
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 11px;
        overflow-wrap: anywhere;
      }
      #${rootId} .codex-usage-hud-cleanup-footer-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        flex: 0 0 auto;
      }
      #${rootId} .codex-usage-hud-cleanup-workspace .codex-usage-hud-settings-action {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        border: 1px solid #4a4d53;
        background: #2b2d31;
        color: var(--codex-usage-hud-text, #f1f3f5);
        font-size: 12px;
        font-weight: 600;
      }
      #${rootId} .codex-usage-hud-cleanup-workspace .codex-usage-hud-settings-action[data-primary="true"],
      #${rootId} .codex-usage-hud-settings-confirm-card .codex-usage-hud-settings-action[data-primary="true"] {
        background: #3b8eea;
        border-color: #3b8eea;
        color: #ffffff;
      }
      #${rootId} .codex-usage-hud-cleanup-workspace .codex-usage-hud-settings-action[data-danger="true"],
      #${rootId} .codex-usage-hud-settings-confirm-card[data-tone="danger"] .codex-usage-hud-settings-action[data-danger="true"] {
        background: #c43e45;
        border-color: #d84a51;
        color: #ffffff;
      }
      #${rootId} .codex-usage-hud-cleanup-icon {
        width: 16px;
        height: 16px;
        stroke: currentColor;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
        fill: none;
        flex: 0 0 auto;
      }
      #${rootId} .codex-usage-hud-cleanup-icon-lg {
        width: 30px;
        height: 30px;
      }
      #${rootId} .codex-usage-hud-cleanup-icon-button {
        width: 30px;
        height: 30px;
        display: inline-grid;
        place-items: center;
        border: 1px solid transparent;
        border-radius: 5px;
        background: transparent;
        color: var(--codex-usage-hud-muted, #9da1a8);
        cursor: pointer;
        padding: 0;
      }
      #${rootId} .codex-usage-hud-cleanup-empty-state {
        min-width: 0;
        height: 100%;
        min-height: 220px;
        display: grid;
        place-content: center;
        justify-items: center;
        gap: 12px;
        text-align: center;
        padding: 18px 16px 28px;
      }
      #${rootId} .codex-usage-hud-cleanup-scan-mark {
        width: 62px;
        height: 62px;
        display: grid;
        place-items: center;
        border: 1px solid #3f5f82;
        border-radius: 50%;
        background: #17202a;
        color: #7db9f7;
      }
      #${rootId} .codex-usage-hud-cleanup-empty-title {
        margin: 0;
        color: var(--codex-usage-hud-text, #f1f3f5);
        font-size: 17px;
        font-weight: 700;
        line-height: 1.25;
      }
      #${rootId} .codex-usage-hud-cleanup-empty-meta {
        margin: 0;
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 12px;
        line-height: 1.5;
      }
      #${rootId} .codex-usage-hud-cleanup-scan-mark[data-live="true"] {
        position: relative;
      }
      #${rootId} .codex-usage-hud-cleanup-scan-mark[data-live="true"]::before {
        content: "";
        position: absolute;
        inset: -5px;
        border-radius: 50%;
        border: 2px solid transparent;
        border-top-color: var(--codex-usage-hud-accent-blue, #3b8eea);
        border-right-color: rgba(59, 142, 234, .35);
        animation: codex-usage-hud-cleanup-spin .9s linear infinite;
      }
      @keyframes codex-usage-hud-cleanup-spin { to { transform: rotate(360deg); } }
      #${rootId} .codex-usage-hud-cleanup-scan-strip {
        display: grid;
        gap: 8px;
        padding: 11px 15px 12px;
        border-bottom: 1px solid var(--codex-usage-hud-border, #393b40);
        background: linear-gradient(180deg, #1a2430 0%, #151a20 100%);
      }
      #${rootId} .codex-usage-hud-cleanup-scan-strip-top {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
      }
      #${rootId} .codex-usage-hud-cleanup-scan-strip-title {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        color: #7db9f7;
        font-size: 12px;
        font-weight: 650;
      }
      #${rootId} .codex-usage-hud-cleanup-scan-strip-meta {
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 11px;
        font-variant-numeric: tabular-nums;
        text-align: right;
      }
      #${rootId} .codex-usage-hud-cleanup-scan-track {
        position: relative;
        height: 6px;
        border-radius: 999px;
        overflow: hidden;
        background: rgba(255, 255, 255, .08);
      }
      #${rootId} .codex-usage-hud-cleanup-scan-fill {
        height: 100%;
        border-radius: inherit;
        background: linear-gradient(90deg, #5ea7ff, #9ccbff);
        box-shadow: 0 0 12px rgba(94, 167, 255, .35);
        transition: width .28s ease;
      }
      #${rootId} .codex-usage-hud-cleanup-scan-fill[data-indeterminate="true"] {
        width: 38% !important;
        animation: codex-usage-hud-cleanup-indet 1.35s ease-in-out infinite;
      }
      @keyframes codex-usage-hud-cleanup-indet {
        0% { transform: translateX(-120%); }
        100% { transform: translateX(320%); }
      }
      #${rootId} .codex-usage-hud-cleanup-scan-stage {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 11px;
      }
      #${rootId} .codex-usage-hud-cleanup-scan-stage strong {
        color: #cfe4ff;
        font-weight: 600;
      }
      #${rootId} .codex-usage-hud-cleanup-mini-spinner {
        width: 12px;
        height: 12px;
        border: 1.5px solid rgba(125, 185, 247, .25);
        border-top-color: #7db9f7;
        border-radius: 50%;
        animation: codex-usage-hud-cleanup-spin .75s linear infinite;
        flex: 0 0 auto;
      }
      #${rootId} .codex-usage-hud-cleanup-summary-band[data-scanning="true"] {
        background: linear-gradient(180deg, #1a2430, #16202a);
        border-bottom-color: #2d3d50;
      }
      #${rootId} .codex-usage-hud-cleanup-summary-band[data-scanning="true"] .codex-usage-hud-cleanup-summary-label {
        color: #7db9f7;
      }
      #${rootId} .codex-usage-hud-cleanup-summary-band[data-scanning="true"] .codex-usage-hud-cleanup-summary-value {
        color: #d7ebff;
      }
      #${rootId} .codex-usage-hud-cleanup-summary-band[data-scanning="true"] .codex-usage-hud-cleanup-summary-side {
        color: #8eb6de;
      }
      #${rootId} .codex-usage-hud-cleanup-row[data-scan-state="pending"] { opacity: .72; }
      #${rootId} .codex-usage-hud-cleanup-row[data-scan-state="current"] {
        background: rgba(59, 142, 234, .06);
      }
      #${rootId} .codex-usage-hud-cleanup-row[data-scan-state="found"] {
        background: linear-gradient(90deg, rgba(59, 142, 234, .14), transparent 72%);
      }
      #${rootId} .codex-usage-hud-cleanup-row-status {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        color: #7db9f7;
        font-size: 10px;
        font-weight: 600;
      }
      #${rootId} .codex-usage-hud-cleanup-check[data-skeleton="true"] {
        border-style: dashed;
        border-color: #3a3d43;
        background: #1a1b1e;
      }
      #${rootId} .codex-usage-hud-cleanup-rescan-shell {
        position: relative;
        min-height: 0;
        flex: 1;
        display: flex;
        flex-direction: column;
      }
      #${rootId} .codex-usage-hud-cleanup-rescan-dim {
        filter: saturate(.85) brightness(.88);
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-cleanup-rescan-chip {
        position: absolute;
        top: 14px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 2;
        display: inline-flex;
        align-items: center;
        gap: 8px;
        min-height: 34px;
        padding: 0 14px;
        border: 1px solid #3f5f82;
        border-radius: 999px;
        background: rgba(23, 32, 42, .96);
        color: #cfe4ff;
        font-size: 12px;
        font-weight: 650;
        box-shadow: 0 10px 28px rgba(0, 0, 0, .35);
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-cleanup-head-meta[data-live="true"] { color: #7db9f7; }
      #${rootId} .codex-usage-hud-cleanup-pulse-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: #3b8eea;
        box-shadow: 0 0 0 0 rgba(59, 142, 234, .55);
        animation: codex-usage-hud-cleanup-pulse 1.4s ease-out infinite;
      }
      @keyframes codex-usage-hud-cleanup-pulse {
        0% { box-shadow: 0 0 0 0 rgba(59, 142, 234, .55); }
        70% { box-shadow: 0 0 0 8px rgba(59, 142, 234, 0); }
        100% { box-shadow: 0 0 0 0 rgba(59, 142, 234, 0); }
      }
      #${rootId} .codex-usage-hud-cleanup {
        display: flex;
        flex-direction: column;
        min-height: 0;
        height: 100%;
      }
      #${rootId} .codex-usage-hud-settings-action[data-size="large"] {
        min-height: 38px;
        padding: 0 17px;
        font-size: 13px;
      }
      #${rootId} .codex-usage-hud-cleanup {
        display: grid;
        min-width: 0;
        min-height: 100%;
        height: 100%;
        align-content: start;
      }
      #${rootId} .codex-usage-hud-cleanup:has(.codex-usage-hud-cleanup-empty-state) {
        grid-template-rows: minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-cleanup-summary-band {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 18px;
        align-items: center;
        padding: 13px 15px;
        border-bottom: 1px solid var(--codex-usage-hud-divider, #393b40);
        background: #16261a;
      }
      #${rootId} .codex-usage-hud-cleanup-summary-label {
        display: flex;
        align-items: center;
        gap: 9px;
        color: #9be5ad;
        font-size: 12px;
      }
      #${rootId} .codex-usage-hud-cleanup-summary-value {
        margin-top: 3px;
        color: #c9f4d2;
        font-size: 24px;
        line-height: 1;
        font-weight: 750;
      }
      #${rootId} .codex-usage-hud-cleanup-summary-side {
        color: #9be5ad;
        font-size: 11px;
        text-align: right;
        line-height: 1.45;
      }
      #${rootId} .codex-usage-hud-cleanup-list {
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-cleanup-row {
        min-width: 0;
        min-height: 54px;
        display: grid;
        grid-template-columns: 24px minmax(0, 1fr) minmax(150px, .7fr) 76px 20px;
        gap: 9px;
        align-items: center;
        padding: 7px 14px;
        border-bottom: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 80%, transparent);
      }
      #${rootId} .codex-usage-hud-cleanup-row[data-tier="consent"],
      #${rootId} .codex-usage-hud-cleanup-row[data-kind="deep"] {
        background: #2a2418;
        border-top: 1px solid #554525;
        border-bottom-color: #554525;
      }
      #${rootId} .codex-usage-hud-cleanup-check {
        width: 16px;
        height: 16px;
        min-width: 16px;
        padding: 0;
        border: 1px solid #5a5d63;
        border-radius: 3px;
        background: #111214;
        display: grid;
        place-items: center;
        color: #fff;
        flex: 0 0 auto;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-cleanup-check[data-checked="true"] {
        background: #3b8eea;
        border-color: #3b8eea;
      }
      #${rootId} .codex-usage-hud-cleanup-check[data-checked="true"]::after {
        content: "";
        width: 7px;
        height: 4px;
        border-left: 2px solid currentColor;
        border-bottom: 2px solid currentColor;
        transform: translateY(-1px) rotate(-45deg);
      }
      #${rootId} .codex-usage-hud-cleanup-check[data-partial="true"]::after {
        content: "";
        width: 7px;
        height: 2px;
        background: currentColor;
      }
      #${rootId} .codex-usage-hud-cleanup-check[data-disabled="true"] {
        border-color: #3b3d42;
        background: #202124;
        cursor: default;
      }
      #${rootId} .codex-usage-hud-cleanup-row-main { min-width: 0; }
      #${rootId} .codex-usage-hud-cleanup-row-title {
        font-size: 12px;
        font-weight: 650;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-cleanup-row-meta {
        margin-top: 3px;
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 10px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-cleanup-row-impact {
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 10px;
        overflow-wrap: anywhere;
      }
      #${rootId} .codex-usage-hud-cleanup-row-size {
        text-align: right;
        font-size: 12px;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
      }
      #${rootId} .codex-usage-hud-cleanup-row-chevron {
        width: 24px;
        height: 24px;
        padding: 0;
        border: 0;
        border-radius: 4px;
        background: transparent;
        color: #73777f;
        display: inline-grid;
        place-items: center;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-cleanup-row-chevron:hover {
        background: rgba(255, 255, 255, .06);
        color: var(--codex-usage-hud-text, #e8eef7);
      }
      #${rootId} .codex-usage-hud-cleanup-row-chevron .codex-usage-hud-cleanup-icon {
        transition: transform .16s ease;
      }
      #${rootId} .codex-usage-hud-cleanup-row-chevron[aria-expanded="true"] .codex-usage-hud-cleanup-icon {
        transform: rotate(90deg);
      }
      #${rootId} .codex-usage-hud-cleanup-result-mark {
        width: 16px;
        height: 16px;
        display: inline-grid;
        place-items: center;
        color: #8fcfa0;
        font-size: 11px;
        font-weight: 750;
      }
      #${rootId} .codex-usage-hud-cleanup-result-mark[data-state="failed"],
      #${rootId} .codex-usage-hud-cleanup-result-mark[data-state="partial"] {
        color: #f0b66a;
      }
      #${rootId} .codex-usage-hud-cleanup-details {
        min-width: 0;
        display: grid;
        margin-left: 38px;
        padding: 0 14px 8px 0;
        border-bottom: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 80%, transparent);
        background: rgba(255, 255, 255, .012);
      }
      #${rootId} .codex-usage-hud-cleanup-target {
        min-width: 0;
        display: grid;
        gap: 4px;
        padding: 9px 0;
        border-top: 1px solid rgba(255, 255, 255, .045);
      }
      #${rootId} .codex-usage-hud-cleanup-target:first-child { border-top: 0; }
      #${rootId} .codex-usage-hud-cleanup-target-head {
        min-width: 0;
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: start;
        gap: 8px;
      }
      #${rootId} .codex-usage-hud-cleanup-target-path {
        min-width: 0;
        color: #d5d9df;
        font: 10.5px/1.45 Consolas, "Cascadia Mono", ui-monospace, monospace;
        letter-spacing: 0;
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-word;
        user-select: text;
        -webkit-user-select: text;
      }
      #${rootId} .codex-usage-hud-cleanup-target-actions {
        display: inline-flex;
        gap: 3px;
      }
      #${rootId} .codex-usage-hud-cleanup-target-action {
        width: 26px;
        height: 26px;
        padding: 0;
        border: 1px solid transparent;
        border-radius: 4px;
        background: transparent;
        color: #8c929b;
        display: inline-grid;
        place-items: center;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-cleanup-target-action:hover {
        border-color: #474b52;
        background: rgba(255, 255, 255, .055);
        color: #e3e6eb;
      }
      #${rootId} .codex-usage-hud-cleanup-target-meta,
      #${rootId} .codex-usage-hud-cleanup-target-note {
        min-width: 0;
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 9.5px;
        line-height: 1.45;
        overflow-wrap: anywhere;
      }
      #${rootId} .codex-usage-hud-cleanup-protected-note {
        min-height: 39px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 7px 14px;
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 10px;
        border-bottom: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 80%, transparent);
      }
      #${rootId} .codex-usage-hud-cleanup-protected-note > button {
        width: 100%;
        min-width: 0;
        padding: 0;
        border: 0;
        background: transparent;
        color: inherit;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        font: inherit;
        text-align: left;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-cleanup-protected-note span {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-cleanup-controls {
        display: grid;
        gap: 8px;
        padding: 10px 14px 12px;
        border-bottom: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 80%, transparent);
        background: color-mix(in srgb, #2a2418 55%, transparent);
      }
      #${rootId} .codex-usage-hud-cleanup-control {
        min-width: 0;
        display: grid;
        grid-template-columns: 18px minmax(0, 1fr);
        gap: 7px;
        align-items: start;
      }
      #${rootId} .codex-usage-hud-cleanup-control input[type="checkbox"] {
        width: 15px;
        height: 15px;
        margin-top: 1px;
        accent-color: #3b8eea;
      }
      #${rootId} .codex-usage-hud-cleanup-backup {
        min-width: 0;
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 6px;
      }
      #${rootId} .codex-usage-hud-cleanup-backup input {
        min-width: 0;
        border: 1px solid #44464c;
        border-radius: 4px;
        background: #111214;
        color: var(--codex-usage-hud-text, #e8eef7);
        padding: 6px 7px;
        font: 11px Consolas, "Cascadia Mono", ui-monospace, monospace;
      }
      #${rootId} .codex-usage-hud-cleanup-meta {
        min-width: 0;
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        overflow-wrap: anywhere;
      }
      #${rootId} .codex-usage-hud-cleanup-preview {
        display: grid;
        gap: 8px;
        padding: 10px 14px 12px;
        border-top: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-cleanup-preview-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-cleanup-results {
        max-height: 130px;
        overflow: auto;
      }
      #${rootId} .codex-usage-hud-cleanup-result {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        min-width: 0;
        padding: 5px 0;
        border-top: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 72%, transparent);
      }
      #${rootId} .codex-usage-hud-cleanup-result span:first-child { min-width: 0; overflow-wrap: anywhere; }
      #${rootId} .codex-usage-hud-cleanup-empty {
        min-height: 96px;
        display: grid;
        place-items: center;
        padding: 18px;
        color: var(--codex-usage-hud-muted, #8492a6);
        text-align: center;
      }
      #${rootId} .codex-usage-hud-cleanup-empty[data-kind="error"] { color: var(--codex-usage-hud-warning, #ffb86b); }
      #${rootId} .codex-usage-hud-session-cleanup {
        min-width: 0;
        min-height: 100%;
        height: 100%;
        display: grid;
        grid-template-rows: auto minmax(0, 1fr);
        align-content: start;
      }
      #${rootId} .codex-usage-hud-session-cleanup:has(.codex-usage-hud-cleanup-empty-state) {
        grid-template-rows: minmax(0, 1fr);
      }
      #${rootId} .codex-usage-hud-session-tools {
        min-width: 0;
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 9px;
        align-items: center;
        padding: 10px 13px;
        border-bottom: 1px solid var(--codex-usage-hud-divider, #393b40);
      }
      #${rootId} .codex-usage-hud-session-search {
        min-width: 0;
        min-height: 32px;
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 0 10px;
        border: 1px solid #44464c;
        border-radius: 5px;
        color: var(--codex-usage-hud-muted, #9da1a8);
        background: #111214;
      }
      #${rootId} .codex-usage-hud-session-search input[type="search"] {
        min-width: 0;
        width: 100%;
        border: 0;
        outline: 0;
        background: transparent;
        color: var(--codex-usage-hud-text, #e8eef7);
        font: inherit;
        font-size: 11px;
        padding: 0;
      }
      #${rootId} .codex-usage-hud-session-filters {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        min-width: 0;
        flex-wrap: wrap;
      }
      #${rootId} .codex-usage-hud-session-filter {
        min-width: 0;
        min-height: 32px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border: 1px solid #44464c;
        border-radius: 5px;
        background: #24262a;
        color: var(--codex-usage-hud-muted, #9da1a8);
        padding: 0 9px;
        font: inherit;
        font-size: 10px;
        white-space: nowrap;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-session-filter[data-active="true"] {
        border-color: #4c78a9;
        color: #a9d1ff;
        background: #1b2632;
      }
      #${rootId} .codex-usage-hud-session-table {
        min-width: 0;
        min-height: 0;
        overflow: auto;
        scrollbar-width: thin;
      }
      #${rootId} .codex-usage-hud-session-head,
      #${rootId} .codex-usage-hud-session-row {
        display: grid;
        grid-template-columns: 28px minmax(0, 1.35fr) minmax(0, .9fr) 88px 72px 82px;
        grid-template-areas: "check title workdir time status size";
        gap: 9px;
        align-items: center;
        padding: 0 13px;
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-session-head {
        min-height: 31px;
        color: #73777f;
        font-size: 10px;
        border-bottom: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 80%, transparent);
        background: #1c1d1f;
        position: sticky;
        top: 0;
        z-index: 1;
      }
      #${rootId} .codex-usage-hud-session-head > :nth-child(1),
      #${rootId} .codex-usage-hud-session-row > :nth-child(1) { grid-area: check; }
      #${rootId} .codex-usage-hud-session-head > :nth-child(2),
      #${rootId} .codex-usage-hud-session-row > :nth-child(2) { grid-area: title; }
      #${rootId} .codex-usage-hud-session-head > :nth-child(3),
      #${rootId} .codex-usage-hud-session-row > :nth-child(3) { grid-area: workdir; }
      #${rootId} .codex-usage-hud-session-head > :nth-child(4),
      #${rootId} .codex-usage-hud-session-row > :nth-child(4) { grid-area: time; }
      #${rootId} .codex-usage-hud-session-head > :nth-child(5),
      #${rootId} .codex-usage-hud-session-row > :nth-child(5) { grid-area: status; }
      #${rootId} .codex-usage-hud-session-head > :nth-child(6),
      #${rootId} .codex-usage-hud-session-row > :nth-child(6) { grid-area: size; }
      #${rootId} .codex-usage-hud-session-head span:last-child,
      #${rootId} .codex-usage-hud-session-size { text-align: right; }
      #${rootId} .codex-usage-hud-session-row {
        min-height: 54px;
        border-bottom: 1px solid color-mix(in srgb, var(--codex-usage-hud-divider, #273241) 80%, transparent);
      }
      #${rootId} .codex-usage-hud-session-row[data-selected="true"] { background: #172331; }
      #${rootId} .codex-usage-hud-session-row[data-selectable="false"] { opacity: .55; }
      #${rootId} .codex-usage-hud-session-row input[type="checkbox"] {
        width: 15px;
        height: 15px;
        margin: 0;
        accent-color: #3b8eea;
      }
      #${rootId} .codex-usage-hud-session-title {
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-session-title strong,
      #${rootId} .codex-usage-hud-session-workdir,
      #${rootId} .codex-usage-hud-session-title span {
        display: block;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-session-title strong {
        font-size: 11px;
        font-weight: 650;
      }
      #${rootId} .codex-usage-hud-session-title span {
        margin-top: 3px;
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 9px;
      }
      #${rootId} .codex-usage-hud-session-title span[data-secondary="true"] {
        display: none;
      }
      #${rootId} .codex-usage-hud-session-title span[data-kind="warning"] {
        color: var(--codex-usage-hud-warning, #ffb86b);
      }
      #${rootId} .codex-usage-hud-session-workdir {
        color: #c4c7cc;
        font-size: 10px;
      }
      #${rootId} .codex-usage-hud-session-cell {
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 10px;
      }
      #${rootId} .codex-usage-hud-session-size {
        text-align: right;
        color: #c7cad0;
        font-size: 10px;
        font-variant-numeric: tabular-nums;
      }
      #${rootId} .codex-usage-hud-session-badge {
        min-height: 22px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 0 7px;
        border: 1px solid #4a4d53;
        border-radius: 999px;
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 9px;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-session-badge[data-state="current"],
      #${rootId} .codex-usage-hud-session-badge[data-state="running"] {
        border-color: #356894;
        color: #8dc7ff;
        background: #172431;
      }
      #${rootId} .codex-usage-hud-session-badge[data-state="archived"] {
        border-color: #5b4b2b;
        color: #e2bd6c;
        background: #262116;
      }
      #${rootId} .codex-usage-hud-session-capability {
        margin: 8px 13px 0;
        padding: 8px 10px;
        border-left: 3px solid var(--codex-usage-hud-warning, #ffb86b);
        background: color-mix(in srgb, var(--codex-usage-hud-warning, #ffb86b) 8%, transparent);
        color: var(--codex-usage-hud-warning, #ffb86b);
        overflow-wrap: anywhere;
      }
      #${rootId} .codex-usage-hud-session-results {
        min-width: 0;
        display: grid;
        gap: 5px;
        padding: 9px 13px;
        border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
      }
      #${rootId} .codex-usage-hud-session-results > div {
        min-width: 0;
        display: flex;
        justify-content: space-between;
        gap: 10px;
      }
      #${rootId} .codex-usage-hud-session-results > div span:first-child {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-session-results [data-kind="success"] { color: var(--codex-usage-hud-success, #8fe3a1); }
      #${rootId} .codex-usage-hud-session-results [data-kind="error"] { color: var(--codex-usage-hud-warning, #ffb86b); }
      #${rootId} .codex-usage-hud-settings-confirm-layer {
        position: absolute;
        inset: 0;
        z-index: 3;
        display: grid;
        place-items: center;
        padding: 24px;
        background: rgba(7, 8, 9, .72);
        backdrop-filter: blur(4px);
      }
      #${rootId} .codex-usage-hud-settings-confirm-card {
        width: min(500px, calc(100% - 36px));
        display: grid;
        gap: 12px;
        padding: 18px 0 0;
        border: 1px solid color-mix(in srgb, var(--codex-usage-hud-accent, #f3d27a) 34%, transparent);
        border-radius: 8px;
        background: #1d1e20;
        box-shadow: 0 22px 58px rgba(0, 0, 0, .52);
        overflow: hidden;
      }
      #${rootId} .codex-usage-hud-settings-confirm-card:has(.codex-usage-hud-settings-confirm-main),
      #${rootId} .codex-usage-hud-settings-confirm-card[data-tone="danger"] {
        gap: 0;
        padding: 0;
      }
      #${rootId} .codex-usage-hud-settings-confirm-card[data-tone="danger"] {
        border-color: #5e3a3d;
      }
      #${rootId} .codex-usage-hud-settings-confirm-main {
        padding: 18px 19px 16px;
      }
      #${rootId} .codex-usage-hud-settings-confirm-card > .codex-usage-hud-settings-confirm-kicker,
      #${rootId} .codex-usage-hud-settings-confirm-card > .codex-usage-hud-settings-confirm-title,
      #${rootId} .codex-usage-hud-settings-confirm-card > .codex-usage-hud-settings-confirm-body {
        padding-inline: 18px;
      }
      #${rootId} .codex-usage-hud-settings-confirm-kicker {
        color: var(--codex-usage-hud-accent, #f3d27a);
        font-size: 11px;
        font-weight: 800;
        letter-spacing: .08em;
        text-transform: uppercase;
      }
      #${rootId} .codex-usage-hud-settings-confirm-title {
        margin: 0;
        color: var(--codex-usage-hud-text, #f6f9fc);
        font-size: 17px;
        font-weight: 720;
        line-height: 1.25;
      }
      #${rootId} .codex-usage-hud-settings-confirm-body {
        margin: 0;
        color: #c9cbd0;
        font-size: 11px;
        line-height: 1.6;
        white-space: pre-wrap;
      }
      #${rootId} .codex-usage-hud-settings-confirm-main .codex-usage-hud-settings-confirm-body {
        margin: 8px 0 0;
      }
      #${rootId} .codex-usage-hud-settings-confirm-danger-mark {
        width: 42px;
        height: 42px;
        display: grid;
        place-items: center;
        border: 1px solid #754247;
        border-radius: 50%;
        background: #2b191b;
        color: #ff858a;
        margin-bottom: 12px;
      }
      #${rootId} .codex-usage-hud-settings-confirm-summary {
        margin-top: 13px;
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        border: 1px solid #47494f;
        border-radius: 6px;
        overflow: hidden;
      }
      #${rootId} .codex-usage-hud-settings-confirm-summary div {
        padding: 10px 11px;
        border-right: 1px solid #47494f;
      }
      #${rootId} .codex-usage-hud-settings-confirm-summary div:last-child { border-right: 0; }
      #${rootId} .codex-usage-hud-settings-confirm-summary span {
        display: block;
        color: var(--codex-usage-hud-muted, #9da1a8);
        font-size: 9px;
      }
      #${rootId} .codex-usage-hud-settings-confirm-summary strong {
        display: block;
        margin-top: 3px;
        font-size: 13px;
      }
      #${rootId} .codex-usage-hud-settings-confirm-note {
        margin-top: 12px;
        display: flex;
        align-items: flex-start;
        gap: 8px;
        color: #e6bd69;
        font-size: 10px;
        line-height: 1.5;
      }
      #${rootId} .codex-usage-hud-settings-confirm-actions {
        min-height: 52px;
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 8px;
        padding: 9px 12px;
        border-top: 1px solid var(--codex-usage-hud-divider, #393b40);
        background: #292a2d;
      }
      #${rootId} .codex-usage-hud-settings-confirm-actions .codex-usage-hud-settings-action {
        min-height: 34px;
        padding-inline: 12px;
      }
      #${rootId} .codex-usage-hud-settings-loading-track {
        position: relative;
        overflow: hidden;
        height: 10px;
        border-radius: 999px;
        background: var(--codex-usage-hud-progress-track, #1A2430);
      }
      #${rootId} .codex-usage-hud-settings-loading-bar,
      #${rootId} .codex-usage-hud-settings-loading-glow {
        position: absolute;
        top: 0;
        bottom: 0;
        border-radius: 999px;
        animation: codex-usage-hud-loading-slide 1.2s ease-in-out infinite alternate;
      }
      #${rootId} .codex-usage-hud-settings-loading-bar {
        left: 0;
        width: 34%;
        background: var(--codex-usage-hud-accent, #F3D27A);
      }
      #${rootId} .codex-usage-hud-settings-loading-glow {
        left: 8%;
        width: 16%;
        background: color-mix(in srgb, var(--codex-usage-hud-accent, #FFE7A0) 55%, white);
        animation-duration: 1.2s;
      }
      @keyframes codex-usage-hud-loading-slide {
        from {
          transform: translateX(0);
        }
        to {
          transform: translateX(190%);
        }
      }
      #${rootId} .codex-usage-hud-settings-action[data-variant="subtle"] {
        background: var(--codex-usage-hud-header-surface, #202833);
        color: var(--codex-usage-hud-accent, #f3d27a);
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-settings-action[data-variant="ghost"] {
        background: transparent;
        border: 1px solid var(--codex-usage-hud-panel-border, #2e3846);
        color: var(--codex-usage-hud-request-muted, #a9bcd2);
      }
      #${rootId} .codex-usage-hud-support {
        display: grid;
        gap: 12px;
        color: var(--codex-usage-hud-text, #dde7f2);
        line-height: 1.55;
      }
      #${rootId} .codex-usage-hud-rest-reminder-card {
        display: grid;
        gap: 10px;
        padding: 12px;
        border-radius: 12px;
        border: 1px solid var(--codex-usage-hud-panel-border, #2e3846);
        background: color-mix(in srgb, var(--codex-usage-hud-panel-surface, #141b24) 92%, #f3d27a 8%);
      }
      #${rootId} .codex-usage-hud-rest-reminder-title {
        color: var(--codex-usage-hud-accent, #f3d27a);
        font-weight: 700;
        font-size: 13px;
      }
      #${rootId} .codex-usage-hud-rest-reminder-toggle {
        display: flex;
        gap: 8px;
        align-items: center;
        font-size: 12px;
      }
      #${rootId} .codex-usage-hud-rest-reminder-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 8px;
      }
      #${rootId} .codex-usage-hud-rest-reminder-grid label {
        display: grid;
        gap: 4px;
        font-size: 11px;
        color: var(--codex-usage-hud-request-muted, #a9bcd2);
      }
      #${rootId} .codex-usage-hud-rest-reminder-grid input {
        width: 100%;
        box-sizing: border-box;
        border-radius: 8px;
        border: 1px solid var(--codex-usage-hud-panel-border, #2e3846);
        background: var(--codex-usage-hud-surface, #10161d);
        color: var(--codex-usage-hud-text, #dde7f2);
        padding: 6px 8px;
      }
      #${rootId} .codex-usage-hud-rest-reminder-status {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        padding: 9px 10px;
        border-radius: 9px;
        background: color-mix(in srgb, var(--codex-usage-hud-surface, #10161d) 82%, #f3d27a 18%);
      }
      #${rootId} .codex-usage-hud-rest-reminder-status-item {
        display: grid;
        gap: 2px;
        min-width: 0;
      }
      #${rootId} .codex-usage-hud-rest-reminder-status-label {
        color: var(--codex-usage-hud-request-muted, #a9bcd2);
        font-size: 10px;
      }
      #${rootId} .codex-usage-hud-rest-reminder-status-value {
        color: var(--codex-usage-hud-text, #dde7f2);
        font-size: 13px;
        font-variant-numeric: tabular-nums;
      }
      #${rootId} .codex-usage-hud-rest-toast {
        position: fixed;
        left: 50%;
        top: 18%;
        transform: translateX(-50%);
        z-index: 2147483000;
        width: min(420px, calc(100vw - 32px));
        padding: 14px 16px;
        border-radius: 14px;
        border: 1px solid var(--codex-usage-hud-panel-border, #3a4a5c);
        background: var(--codex-usage-hud-panel-surface, #141b24);
        color: var(--codex-usage-hud-text, #dce7f2);
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);
        display: none;
        gap: 10px;
      }
      #${rootId} .codex-usage-hud-rest-toast[data-visible="true"] {
        display: grid;
      }
      #${rootId} .codex-usage-hud-rest-toast-title {
        color: var(--codex-usage-hud-accent, #f3d27a);
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-rest-toast-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
      }
      #${rootId} .codex-usage-hud-support a {
        color: var(--codex-usage-hud-info, #9ccbff);
      }
      #${rootId} .codex-usage-hud-support-note {
        color: var(--codex-usage-hud-request-muted, #a9bcd2);
        font-size: 12px;
      }
      #${rootId} .codex-usage-hud-support-qr-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
        align-items: start;
      }
      #${rootId} .codex-usage-hud-support-qr {
        min-width: 0;
        display: grid;
        gap: 8px;
        justify-items: center;
        padding: 10px;
        border: 1px solid var(--codex-usage-hud-divider, #273241);
        border-radius: 8px;
        background: var(--codex-usage-hud-panel-surface, #141b24);
      }
      #${rootId} .codex-usage-hud-support-qr-title {
        width: 100%;
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 8px;
        color: var(--codex-usage-hud-text, #e8eef7);
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-support-qr-title span:last-child {
        color: var(--codex-usage-hud-muted, #8492a6);
        font-size: 10px;
        font-weight: 600;
      }
      #${rootId} .codex-usage-hud-support-qr img {
        display: block;
        width: 100%;
        max-width: 260px;
        max-height: 360px;
        object-fit: contain;
        border-radius: 6px;
        background: #ffffff;
      }
      #${rootId}[data-theme-variant="light"] {
        color-scheme: light;
      }
      #${rootId}[data-theme-variant="dark"] {
        color-scheme: dark;
      }
      #${rootId} .codex-usage-hud-panel {
        border-color: var(--codex-usage-hud-panel-border);
        color: var(--codex-usage-hud-text);
      }
      #${rootId} .${topClass} {
        background: var(--codex-usage-hud-surface);
      }
      #${rootId} .${requestClass} {
        background: var(--codex-usage-hud-request-surface);
      }
      #${rootId} .codex-usage-hud-handle,
      #${rootId} .codex-usage-hud-update-button,
      #${rootId} .codex-usage-hud-settings-button {
        background: var(--codex-usage-hud-header-surface);
      }
      #${rootId} .codex-usage-hud-handle {
        color: var(--codex-usage-hud-muted);
      }
      #${rootId} .codex-usage-hud-update-button {
        color: var(--codex-usage-hud-info);
      }
      #${rootId} .codex-usage-hud-update-button[data-state="paused"],
      #${rootId} .codex-usage-hud-update-button[data-state="error"],
      #${rootId} .codex-usage-hud-update-button[data-icon="install"] {
        color: var(--codex-usage-hud-warning);
      }
      #${rootId} .codex-usage-hud-settings-button {
        color: var(--codex-usage-hud-muted);
      }
      #${rootId} .codex-usage-hud-handle:hover,
      #${rootId} .codex-usage-hud-update-button:hover,
      #${rootId} .codex-usage-hud-settings-button:hover {
        background: var(--codex-usage-hud-panel-border);
        color: var(--codex-usage-hud-accent);
      }
      #${rootId} .codex-usage-hud-progress-rail {
        border-color: var(--codex-usage-hud-progress-track-border);
        background: linear-gradient(180deg, rgba(255,255,255,.032), rgba(255,255,255,0)), var(--codex-usage-hud-progress-track);
        isolation: isolate;
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="session"] .codex-usage-hud-progress-fill,
      #${rootId} .codex-usage-hud-progress-rail[data-tone="cache"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, var(--codex-usage-hud-progress-cache), var(--codex-usage-hud-progress-cache-end));
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="day"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, var(--codex-usage-hud-progress-day), var(--codex-usage-hud-progress-day-end));
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="week"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, var(--codex-usage-hud-progress-week), var(--codex-usage-hud-progress-week-end));
      }
      #${rootId} .codex-usage-hud-progress-rail[data-tone="error"] .codex-usage-hud-progress-fill {
        background: linear-gradient(90deg, var(--codex-usage-hud-progress-overflow), var(--codex-usage-hud-error));
      }
      #${rootId} .codex-usage-hud-progress-rail[data-overflow="true"] {
        border-color: var(--codex-usage-hud-progress-overflow-badge-edge);
      }
      #${rootId} .codex-usage-hud-progress-overflow {
        background: linear-gradient(90deg, var(--codex-usage-hud-progress-overflow-highlight), var(--codex-usage-hud-progress-overflow) 60%, var(--codex-usage-hud-error));
      }
      #${rootId} .codex-usage-hud-progress-overflow-anchor {
        background: radial-gradient(circle at 35% 35%, var(--codex-usage-hud-progress-overflow-highlight) 0%, var(--codex-usage-hud-progress-overflow) 58%, var(--codex-usage-hud-error) 100%);
        box-shadow: 0 0 0 2px rgba(255,255,255,.06), 0 0 14px rgba(0,0,0,.18);
      }
      #${rootId} .codex-usage-hud-progress-badge {
        border-color: var(--codex-usage-hud-progress-overflow-badge-edge);
        background: var(--codex-usage-hud-progress-overflow-badge);
        color: var(--codex-usage-hud-progress-overflow-badge-text);
      }
      #${rootId} .codex-usage-hud-progress-badge::before {
        background: linear-gradient(180deg, var(--codex-usage-hud-progress-overflow-highlight), var(--codex-usage-hud-error));
      }
      #${rootId} .codex-usage-hud-panel-header,
      #${rootId} .codex-usage-hud-settings-tab.is-active,
      #${rootId} .codex-usage-hud-settings-loading-kicker {
        background: var(--codex-usage-hud-header-surface);
      }
      #${rootId} .codex-usage-hud-top-body {
        scrollbar-color: var(--codex-usage-hud-divider) var(--codex-usage-hud-surface);
      }
      #${rootId} .codex-usage-hud-top-card,
      #${rootId} .codex-usage-hud-settings-modal-shell,
      #${rootId} .codex-usage-hud-settings-shell {
        background: var(--codex-usage-hud-panel-surface);
      }
      #${rootId} .codex-usage-hud-top-card,
      #${rootId} .codex-usage-hud-alert,
      #${rootId} .codex-usage-hud-activity-main,
      #${rootId} .codex-usage-hud-activity-step,
      #${rootId} .codex-usage-hud-activity-metric,
      #${rootId} .codex-usage-hud-session-insight,
      #${rootId} .codex-usage-hud-token-chip,
      #${rootId} .codex-usage-hud-heavy-round,
      #${rootId} .codex-usage-hud-chip,
      #${rootId} .codex-usage-hud-settings-modal-shell,
      #${rootId} .codex-usage-hud-settings-shell,
      #${rootId} .codex-usage-hud-input,
      #${rootId} .codex-usage-hud-select,
      #${rootId} .codex-usage-hud-textarea {
        border-color: var(--codex-usage-hud-divider);
      }
      #${rootId} .codex-usage-hud-activity-main,
      #${rootId} .codex-usage-hud-activity-step,
      #${rootId} .codex-usage-hud-activity-metric,
      #${rootId} .codex-usage-hud-session-insight,
      #${rootId} .codex-usage-hud-heavy-round,
      #${rootId} .codex-usage-hud-token-chip {
        background: var(--codex-usage-hud-request-panel-surface);
      }
      #${rootId} .codex-usage-hud-card-title,
      #${rootId} .codex-usage-hud-title,
      #${rootId} .codex-usage-hud-stat-value,
      #${rootId} .codex-usage-hud-activity-node-title,
      #${rootId} .codex-usage-hud-value,
      #${rootId} .codex-usage-hud-token-chip,
      #${rootId} .codex-usage-hud-support,
      #${rootId} .codex-usage-hud-support-qr-title {
        color: var(--codex-usage-hud-text);
      }
      #${rootId} .codex-usage-hud-card-title {
        color: var(--codex-usage-hud-request-text);
      }
      #${rootId} .codex-usage-hud-chip {
        background: var(--codex-usage-hud-header-surface);
        color: var(--codex-usage-hud-text);
      }
      #${rootId} .codex-usage-hud-chip[data-tone="warning"] {
        border-color: var(--codex-usage-hud-warning);
        background: color-mix(in srgb, var(--codex-usage-hud-warning) 14%, var(--codex-usage-hud-panel-surface));
        color: var(--codex-usage-hud-warning);
      }
      #${rootId} .codex-usage-hud-copy-chip[data-copyable="true"]:hover,
      #${rootId} .codex-usage-hud-heavy-round[data-copyable="true"]:hover,
      #${rootId} .codex-usage-hud-value[data-copyable="true"]:hover {
        border-color: var(--codex-usage-hud-accent);
        background: color-mix(in srgb, var(--codex-usage-hud-accent) 10%, var(--codex-usage-hud-panel-surface));
        color: var(--codex-usage-hud-accent);
      }
      #${rootId} .codex-usage-hud-stat-value.info,
      #${rootId} .codex-usage-hud-value.blue,
      #${rootId} .codex-usage-hud-activity-main strong,
      #${rootId} .codex-usage-hud-activity-load-more {
        color: var(--codex-usage-hud-info);
      }
      #${rootId} .codex-usage-hud-stat-value.cache,
      #${rootId} .codex-usage-hud-value[data-copied="true"] {
        color: var(--codex-usage-hud-success) !important;
      }
      #${rootId} .codex-usage-hud-stat-label,
      #${rootId} .codex-usage-hud-section-title,
      #${rootId} .codex-usage-hud-token-chip span:first-child,
      #${rootId} .codex-usage-hud-heavy-round-detail,
      #${rootId} .codex-usage-hud-activity-node-time,
      #${rootId} .codex-usage-hud-activity-node-detail,
      #${rootId} .codex-usage-hud-session-composition,
      #${rootId} .codex-usage-hud-value.muted {
        color: var(--codex-usage-hud-muted);
      }
      #${rootId} .codex-usage-hud-session-composition {
        color: var(--codex-usage-hud-request-muted);
      }
      #${rootId} .codex-usage-hud-heavy-round-title,
      #${rootId} .codex-usage-hud-activity-metric .codex-usage-hud-value,
      #${rootId} .codex-usage-hud-value.accent,
      #${rootId} .codex-usage-hud-value[data-copyable="true"][data-copy-field="slow"] {
        color: var(--codex-usage-hud-accent);
      }
      #${rootId} .codex-usage-hud-value[data-copyable="true"][data-copy-field="gap"] {
        color: var(--codex-usage-hud-info);
      }
      #${rootId} .codex-usage-hud-value.warn,
      #${rootId} .codex-usage-hud-alert-title,
      #${rootId} .codex-usage-hud-alert-close {
        color: var(--codex-usage-hud-warning);
      }
      #${rootId} .codex-usage-hud-alert {
        border-color: var(--codex-usage-hud-warning);
        background: color-mix(in srgb, var(--codex-usage-hud-warning) 13%, var(--codex-usage-hud-panel-surface));
      }
      #${rootId} .codex-usage-hud-alert-dot {
        background: var(--codex-usage-hud-warning);
      }
      #${rootId} .codex-usage-hud-alert-close:hover {
        background: color-mix(in srgb, var(--codex-usage-hud-warning) 12%, transparent);
      }
      #${rootId} .codex-usage-hud-activity-timeline {
        scrollbar-color: var(--codex-usage-hud-divider) var(--codex-usage-hud-request-panel-surface);
      }
      #${rootId} .codex-usage-hud-activity-load-more {
        border-color: var(--codex-usage-hud-divider);
        background: color-mix(in srgb, var(--codex-usage-hud-info) 8%, var(--codex-usage-hud-panel-surface));
      }
      #${rootId} .codex-usage-hud-activity-load-more:hover {
        border-color: var(--codex-usage-hud-info);
        background: color-mix(in srgb, var(--codex-usage-hud-info) 12%, var(--codex-usage-hud-panel-surface));
      }
      #${rootId} .codex-usage-hud-activity-load-more:disabled:hover {
        border-color: var(--codex-usage-hud-divider);
        background: color-mix(in srgb, var(--codex-usage-hud-info) 8%, var(--codex-usage-hud-panel-surface));
      }
      #${rootId} .codex-usage-hud-activity-node-dot {
        background: var(--codex-usage-hud-info);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--codex-usage-hud-info) 14%, transparent);
      }
      #${rootId} .codex-usage-hud-activity-node:not(:last-child)::after {
        background: var(--codex-usage-hud-divider);
      }
      #${rootId} .codex-usage-hud-activity-node[data-active="true"] .codex-usage-hud-activity-node-dot {
        background: var(--codex-usage-hud-accent);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--codex-usage-hud-accent) 16%, transparent);
      }
      #${rootId} .codex-usage-hud-divider {
        background: var(--codex-usage-hud-divider);
      }
      #${rootId} .codex-usage-hud-request-list {
        background: var(--codex-usage-hud-request-panel-surface);
        scrollbar-color: var(--codex-usage-hud-divider) var(--codex-usage-hud-request-panel-surface);
      }
      #${rootId} .codex-usage-hud-row {
        color: var(--codex-usage-hud-request-text);
      }
      #${rootId} .codex-usage-hud-row[data-latest="true"] {
        color: var(--codex-usage-hud-accent);
      }
      #${rootId} .codex-usage-hud-row-time,
      #${rootId} .codex-usage-hud-session-meta,
      #${rootId} .codex-usage-hud-support-qr-title span:last-child {
        color: var(--codex-usage-hud-request-muted);
      }
      #${rootId} .codex-usage-hud-warning,
      #${rootId} .codex-usage-hud-line-warning,
      #${rootId} [data-field="topWarnings"] {
        color: var(--codex-usage-hud-warning) !important;
      }
      #${rootId} .codex-usage-hud-error {
        color: var(--codex-usage-hud-error) !important;
      }
      #${rootId} .codex-usage-hud-line-accent,
      #${rootId} .codex-usage-hud-support-qr-title span:first-child {
        color: var(--codex-usage-hud-accent);
      }
      #${rootId} .codex-usage-hud-line-info {
        color: var(--codex-usage-hud-info);
      }
      #${rootId} .codex-usage-hud-line-muted,
      #${rootId} .codex-usage-hud-label,
      #${rootId} .codex-usage-hud-field-caption {
        color: var(--codex-usage-hud-muted);
      }
      @container (max-width: 560px) {
        #${rootId} .codex-usage-hud-top-grid {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-top-column {
          height: auto;
        }
        #${rootId} .codex-usage-hud-top-column-left,
        #${rootId} .codex-usage-hud-top-column-right {
          grid-template-rows: auto;
        }
        #${rootId} .codex-usage-hud-activity-card {
          min-height: auto;
        }
      }
      @container (max-width: 440px) {
        #${rootId} .codex-usage-hud-session-stats {
          grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-session-stats .codex-usage-hud-chip {
          display: none;
        }
        #${rootId} .codex-usage-hud-token-breakdown {
          display: none;
        }
        #${rootId} .codex-usage-hud-session-insight {
          grid-template-columns: auto minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-session-insight .codex-usage-hud-value:last-child,
        #${rootId} .codex-usage-hud-card-actions .codex-usage-hud-copy-chip {
          display: none;
        }
        #${rootId} .codex-usage-hud-activity-metrics {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        #${rootId} .codex-usage-hud-activity-metrics .codex-usage-hud-activity-metric:last-child {
          display: none;
        }
      }
      @media (max-width: 760px) {
        #${rootId} .codex-usage-hud-session-meta {
          display: none;
        }
        #${rootId} .codex-usage-hud-cache-pill {
          display: none;
        }
        #${rootId} .${topClass} .codex-usage-hud-panel-header {
          grid-template-columns: auto minmax(0, 1fr) 22px;
        }
        #${rootId} .codex-usage-hud-settings-grid {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-settings-compact-row {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-provider-editor {
          grid-column: 1;
        }
        #${rootId} .codex-usage-hud-provider-editor-head {
          grid-template-columns: auto minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-provider-editor-head .codex-usage-hud-price-unit {
          display: none;
        }
        #${rootId} .codex-usage-hud-price-actions {
          grid-template-columns: auto minmax(0, 1fr) auto;
        }
        #${rootId} .codex-usage-hud-provider-context {
          align-items: center;
          flex-wrap: wrap;
        }
        #${rootId} .codex-usage-hud-provider-scope-options {
          flex-wrap: wrap;
        }
        #${rootId} .codex-usage-hud-provider-meta {
          max-width: 58%;
        }
        #${rootId} .codex-usage-hud-support-qr-grid {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-storage-summary {
          grid-template-columns: 1fr 1fr;
        }
        #${rootId} .codex-usage-hud-storage-summary-main {
          grid-column: 1 / -1;
        }
        #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="storage"] {
          width: min(980px, calc(100vw - 48px));
          height: min(572px, calc(100vh - 48px));
        }
        #${rootId} .codex-usage-hud-session-tools {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-session-filters { width: 100%; }
        #${rootId} .codex-usage-hud-session-head,
        #${rootId} .codex-usage-hud-session-row {
          grid-template-columns: 28px minmax(0, 1.2fr) minmax(0, .9fr) 70px 68px;
          grid-template-areas:
            "check title title status size"
            "check workdir time status size";
        }
        #${rootId} .codex-usage-hud-session-row {
          min-height: 54px;
          padding-top: 8px;
          padding-bottom: 8px;
        }
        #${rootId} .codex-usage-hud-session-head > :nth-child(3),
        #${rootId} .codex-usage-hud-session-head > :nth-child(4) {
          display: none;
        }
        #${rootId} .codex-usage-hud-insights-summary {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        #${rootId} .codex-usage-hud-insights-metric:nth-child(2) { border-right: 0; }
        #${rootId} .codex-usage-hud-insights-metric:nth-child(-n + 2) { border-bottom: 1px solid var(--codex-usage-hud-divider, #273241); }
        #${rootId} .codex-usage-hud-insights-rankings {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-insights-ranking {
          border-right: 0;
          border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
        }
        #${rootId} .codex-usage-hud-insights-ranking:last-child { border-bottom: 0; }
        #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="backgroundUsage"] {
          height: calc(100vh - 24px);
        }
        #${rootId} .codex-usage-hud-settings-dialog[data-active-tab="backgroundUsage"] .codex-usage-hud-settings-body {
          overflow: auto;
        }
        #${rootId} .codex-usage-hud-background {
          display: block;
          height: auto;
          min-height: 100%;
        }
        #${rootId} .codex-usage-hud-background-metrics {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        #${rootId} .codex-usage-hud-background-toolbar {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        #${rootId} .codex-usage-hud-background-range {
          grid-column: 1 / -1;
          border-right: 0;
          padding-right: 0;
        }
        #${rootId} .codex-usage-hud-background-master-detail {
          display: block;
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-background-history,
        #${rootId} .codex-usage-hud-background-detail {
          overflow: visible;
        }
        #${rootId} .codex-usage-hud-background-history {
          border-right: 0;
          border-bottom: 1px solid var(--codex-usage-hud-divider, #273241);
        }
        #${rootId} .codex-usage-hud-background-request {
          grid-template-columns: 70px minmax(92px, 1fr) 56px 78px;
        }
        #${rootId} .codex-usage-hud-background-request > span:nth-child(3),
        #${rootId} .codex-usage-hud-background-request-index {
          display: none;
        }
      }
      @media (max-width: 520px) {
        #${rootId} .codex-usage-hud-settings-tabs {
          overflow-x: auto;
        }
        #${rootId} .codex-usage-hud-settings-tab {
          flex: 0 0 auto;
        }
        #${rootId} .codex-usage-hud-settings-actions {
          align-items: flex-start;
          flex-wrap: wrap;
        }
        #${rootId} .codex-usage-hud-settings-actions > div:last-child {
          display: flex;
          width: 100%;
          gap: 6px;
        }
        #${rootId} .codex-usage-hud-settings-actions > div:last-child .codex-usage-hud-settings-action {
          min-width: 0;
          flex: 1 1 0;
        }
        #${rootId} .codex-usage-hud-price-actions {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-provider-context {
          display: grid;
        }
        #${rootId} .codex-usage-hud-provider-context-adjustment {
          width: 100%;
          flex: 1 1 auto;
        }
        #${rootId} .codex-usage-hud-provider-meta {
          max-width: none;
          text-align: left;
        }
        #${rootId} .codex-usage-hud-storage-pathbar,
        #${rootId} .codex-usage-hud-storage-preview-head,
        #${rootId} .codex-usage-hud-storage-preview-foot {
          align-items: flex-start;
          flex-direction: column;
        }
        #${rootId} .codex-usage-hud-storage-category {
          grid-template-columns: minmax(0, 1fr) auto;
        }
        #${rootId} .codex-usage-hud-storage-category .codex-usage-hud-storage-policy {
          grid-column: 1;
          justify-self: start;
          margin-left: 14px;
        }
        #${rootId} .codex-usage-hud-storage-category .codex-usage-hud-storage-size {
          grid-column: 2;
          grid-row: 1 / span 2;
        }
        #${rootId} .codex-usage-hud-storage-item {
          grid-template-columns: auto minmax(0, 1fr) auto;
        }
        #${rootId} .codex-usage-hud-storage-item .codex-usage-hud-storage-policy {
          grid-column: 2;
          justify-self: start;
        }
        #${rootId} .codex-usage-hud-storage-item .codex-usage-hud-storage-size {
          grid-column: 3;
          grid-row: 1 / span 2;
        }
        #${rootId} .codex-usage-hud-storage-item-action {
          grid-column: 2;
          justify-self: start;
        }
        #${rootId} .codex-usage-hud-storage-item-actions {
          grid-column: 2;
          justify-self: start;
        }
        #${rootId} .codex-usage-hud-insights-toolbar,
        #${rootId} .codex-usage-hud-insights-background,
        #${rootId} .codex-usage-hud-cleanup-preview-head {
          align-items: flex-start;
          flex-direction: column;
        }
        #${rootId} .codex-usage-hud-insights-range { width: 100%; }
        #${rootId} .codex-usage-hud-cleanup-page-head {
          align-items: flex-start;
          flex-direction: column;
        }
        #${rootId} .codex-usage-hud-cleanup-row {
          grid-template-columns: 24px minmax(0, 1fr) 64px 24px;
        }
        #${rootId} .codex-usage-hud-cleanup-row-impact { grid-column: 2 / -1; }
        #${rootId} .codex-usage-hud-cleanup-details {
          margin-left: 14px;
          padding-right: 14px;
        }
        #${rootId} .codex-usage-hud-cleanup-protected-note > button {
          align-items: flex-start;
          flex-direction: column;
          gap: 4px;
        }
        #${rootId} .codex-usage-hud-cleanup-backup {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-cleanup-footer {
          align-items: flex-start;
          flex-direction: column;
        }
        #${rootId} .codex-usage-hud-session-tools {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-session-filters {
          width: 100%;
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        #${rootId} .codex-usage-hud-session-head { display: none; }
        #${rootId} .codex-usage-hud-session-row {
          grid-template-columns: 28px minmax(0, 1fr) auto;
          grid-template-areas:
            "check title size"
            "check status size";
          min-height: 58px;
          padding-top: 8px;
          padding-bottom: 8px;
        }
        #${rootId} .codex-usage-hud-session-workdir,
        #${rootId} .codex-usage-hud-session-cell {
          display: none;
        }
        #${rootId} .codex-usage-hud-session-title span[data-secondary="true"] {
          display: block;
        }
        #${rootId} .codex-usage-hud-session-badge {
          justify-self: start;
        }
        #${rootId} .codex-usage-hud-session-results > div {
          align-items: flex-start;
          flex-direction: column;
          gap: 2px;
        }
        #${rootId} .codex-usage-hud-background-toolbar {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-background-range {
          grid-column: 1;
        }
        #${rootId} .codex-usage-hud-background-range button {
          min-width: 0;
          flex: 1 1 0;
          padding-inline: 6px;
        }
        #${rootId} .codex-usage-hud-background-detail-grid {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        #${rootId} .codex-usage-hud-background-detail-grid .codex-usage-hud-background-detail-wide {
          grid-column: 1 / -1;
        }
        #${rootId} .codex-usage-hud-background-request {
          grid-template-columns: minmax(92px, 1fr) 52px 74px;
        }
        #${rootId} .codex-usage-hud-background-request > span:first-child {
          display: none;
        }
      }
    `;
    document.documentElement.appendChild(style);
  }

  function applyTheme(root, payload) {
    if (!root) return;
    const themePayload = payload?.theme;
    if (!themePayload || typeof themePayload !== "object" || Object.keys(themePayload).length === 0) {
      return;
    }
    const tokens = themePayload.tokens || {};
    const variant = String(themePayload.variant || "dark").toLowerCase() === "light" ? "light" : "dark";
    const defaults = {
      surface: "#10161d",
      panelSurface: "#141b24",
      panelBorder: "#3a485a",
      headerSurface: "#202833",
      divider: "#273241",
      text: "#e8eef7",
      muted: "#8492a6",
      accent: "#f3d27a",
      info: "#9ccbff",
      warning: "#ffb86b",
      error: "#ff6b6b",
      success: "#8fe3a1",
      requestSurface: "#0b1016",
      requestHeaderSurface: "#151d27",
      requestPanelSurface: "#101821",
      requestText: "#dce7f2",
      requestMuted: "#718095",
      progressTrack: "#262c33",
      progressTrackBorder: "#3b4149",
      progressTrackText: "#c1c7d0",
      progressCache: "#9ccbff",
      progressCacheEnd: "#5ea7ff",
      progressCacheText: "#07131f",
      progressDay: "#f3d27a",
      progressDayEnd: "#f3d37f",
      progressDayText: "#111111",
      progressWeek: "#b5dd92",
      progressWeekEnd: "#aede95",
      progressWeekText: "#111111",
      progressOverflow: "#ff875a",
      progressOverflowHighlight: "#ffd8bd",
      progressOverflowAnchor: "#ff6b64",
      progressOverflowAnchorEdge: "#ffc3a4",
      progressOverflowBadge: "#7f3e3a",
      progressOverflowBadgeEdge: "#ff875a",
      progressOverflowBadgeText: "#ffd7ca",
    };
    const resolved = { ...defaults, ...(tokens || {}) };
    const variableEntries = [
      ["--codex-usage-hud-surface", resolved.surface],
      ["--codex-usage-hud-panel-surface", resolved.panelSurface],
      ["--codex-usage-hud-panel-border", resolved.panelBorder],
      ["--codex-usage-hud-header-surface", resolved.headerSurface],
      ["--codex-usage-hud-divider", resolved.divider],
      ["--codex-usage-hud-text", resolved.text],
      ["--codex-usage-hud-muted", resolved.muted],
      ["--codex-usage-hud-accent", resolved.accent],
      ["--codex-usage-hud-info", resolved.info],
      ["--codex-usage-hud-warning", resolved.warning],
      ["--codex-usage-hud-error", resolved.error],
      ["--codex-usage-hud-success", resolved.success],
      ["--codex-usage-hud-request-surface", resolved.requestSurface],
      ["--codex-usage-hud-request-header-surface", resolved.requestHeaderSurface],
      ["--codex-usage-hud-request-panel-surface", resolved.requestPanelSurface],
      ["--codex-usage-hud-request-text", resolved.requestText],
      ["--codex-usage-hud-request-muted", resolved.requestMuted],
      ["--codex-usage-hud-progress-track", resolved.progressTrack],
      ["--codex-usage-hud-progress-track-border", resolved.progressTrackBorder],
      ["--codex-usage-hud-progress-track-text", resolved.progressTrackText],
      ["--codex-usage-hud-progress-cache", resolved.progressCache],
      ["--codex-usage-hud-progress-cache-end", resolved.progressCacheEnd],
      ["--codex-usage-hud-progress-cache-text", resolved.progressCacheText],
      ["--codex-usage-hud-progress-day", resolved.progressDay],
      ["--codex-usage-hud-progress-day-end", resolved.progressDayEnd],
      ["--codex-usage-hud-progress-day-text", resolved.progressDayText],
      ["--codex-usage-hud-progress-week", resolved.progressWeek],
      ["--codex-usage-hud-progress-week-end", resolved.progressWeekEnd],
      ["--codex-usage-hud-progress-week-text", resolved.progressWeekText],
      ["--codex-usage-hud-progress-overflow", resolved.progressOverflow],
      ["--codex-usage-hud-progress-overflow-highlight", resolved.progressOverflowHighlight],
      ["--codex-usage-hud-progress-overflow-anchor", resolved.progressOverflowAnchor],
      ["--codex-usage-hud-progress-overflow-anchor-edge", resolved.progressOverflowAnchorEdge],
      ["--codex-usage-hud-progress-overflow-badge", resolved.progressOverflowBadge],
      ["--codex-usage-hud-progress-overflow-badge-edge", resolved.progressOverflowBadgeEdge],
      ["--codex-usage-hud-progress-overflow-badge-text", resolved.progressOverflowBadgeText],
    ];
    root.dataset.themeVariant = variant;
    for (const [name, value] of variableEntries) {
      root.style.setProperty(name, String(value || ""));
    }
  }

  function resizeEdgesMarkup() {
    return `
      <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-edge-left" data-action="resize" data-edge="left" aria-hidden="true"></div>
      <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-edge-right" data-action="resize" data-edge="right" aria-hidden="true"></div>
    `;
  }

  function topExpandedResizeMarkup() {
    return `
      <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-corner codex-usage-hud-resize-corner-bottom-left" data-action="resize" data-edge="bottom-left" aria-hidden="true"></div>
      <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-corner codex-usage-hud-resize-corner-bottom-right" data-action="resize" data-edge="bottom-right" aria-hidden="true"></div>
    `;
  }

  function requestExpandedResizeMarkup() {
    return `
      <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-corner codex-usage-hud-resize-corner-top-left" data-action="resize" data-edge="top-left" aria-hidden="true"></div>
      <div class="codex-usage-hud-resize-zone codex-usage-hud-resize-corner codex-usage-hud-resize-corner-top-right" data-action="resize" data-edge="top-right" aria-hidden="true"></div>
    `;
  }

  function backgroundUsageNotificationMarkup() {
    return `
      <button type="button" class="codex-usage-hud-background-notification"
        data-action="background-usage-open-notification" data-visible="false"
        title="后台用量提醒" aria-label="后台用量提醒" aria-hidden="true" tabindex="-1">
        <span aria-hidden="true">▥</span>
        <span class="codex-usage-hud-background-notification-count"
          data-field="backgroundUsageNotificationCount" hidden></span>
      </button>
    `;
  }

  function restReminderToastMarkup() {
    return `
      <div class="codex-usage-hud-rest-toast" data-rest-reminder-toast="true" data-visible="false" role="dialog" aria-live="polite">
        <div class="codex-usage-hud-rest-toast-title">☕ 该休息一下了</div>
        <div data-rest-reminder-message="true">站起来走走，让眼睛放松片刻。</div>
        <div class="codex-usage-hud-rest-toast-actions">
          <button type="button" class="codex-usage-hud-settings-action" data-action="rest-reminder-postpone" hidden>稍后提醒</button>
          <button type="button" class="codex-usage-hud-settings-action" data-action="rest-reminder-ack" data-primary="true">我休息好了</button>
        </div>
      </div>
    `;
  }

  function panelMarkup(name, glyph, ariaLabel) {
    const glyphMarkup = glyph ? `<span class="codex-usage-hud-glyph">${glyph}</span>` : "";
    const settingsButtonMarkup = name === "top"
      ? `<button class="codex-usage-hud-settings-button" data-action="settings-open" title="设置" aria-label="设置">⚙</button>`
      : "";
    const tokenBadgeMarkup = name === "request"
      ? (composerBadgeEnabled
        ? `<span class="codex-usage-hud-token-badge" data-composer-badge="idle"><span class="codex-usage-hud-token-badge-text" data-field="requestComposerTokens">TikToken:0 Ts</span></span>`
        : "")
      : "";
    const updateButtonMarkup = name === "top"
      ? `<button class="codex-usage-hud-update-button" data-action="update-action" title="" aria-label="" hidden>↓</button>`
      : "";
    const leftControlsMarkup = name === "top"
      ? `<div class="codex-usage-hud-left-controls">${updateButtonMarkup}</div>`
      : "";
    const backgroundNotificationMarkup = name === "request"
      ? backgroundUsageNotificationMarkup()
      : "";
    return `
      <div class="codex-usage-hud-panel ${PANEL[name].className}" data-panel="${name}" data-expanded="false" role="status" aria-live="polite">
        ${resizeEdgesMarkup()}
        <div class="codex-usage-hud-collapsed" data-has-settings="${name === "top" ? "true" : "false"}" data-has-badge="${name === "request" && composerBadgeEnabled ? "true" : "false"}">
          ${leftControlsMarkup}
          <button class="codex-usage-hud-main" data-action="toggle" data-has-glyph="${glyph ? "true" : "false"}" aria-label="${ariaLabel}">
            ${glyphMarkup}
            ${name === "top" ? `<span class="codex-usage-hud-progress-strip-viewport"><span class="codex-usage-hud-progress-strip" data-field="topCollapsedProgress"></span></span>` : ""}
            <span class="codex-usage-hud-line" data-field="${name}Line"></span>
          </button>
          ${settingsButtonMarkup}
          ${tokenBadgeMarkup}
          ${backgroundNotificationMarkup}
        </div>
        ${name === "top" ? topExpandedMarkup() : requestExpandedMarkup()}
      </div>
    `;
  }

  function topExpandedMarkup() {
    return `
      <div class="codex-usage-hud-expanded-shell">
        <div class="codex-usage-hud-panel-header" data-action="toggle">
          <div class="codex-usage-hud-left-controls">
            <button class="codex-usage-hud-handle" data-action="move" title="移动" aria-label="移动">⋮⋮</button>
            <button class="codex-usage-hud-update-button" data-action="update-action" title="" aria-label="" hidden>↓</button>
          </div>
          <div class="codex-usage-hud-title" data-action="toggle" data-field="topTitle"></div>
          <div class="codex-usage-hud-session-meta" data-field="topSession"></div>
          <div class="codex-usage-hud-cache-pill" data-field="topCacheProgress"></div>
          <button class="codex-usage-hud-settings-button" data-action="settings-open" title="设置" aria-label="设置">⚙</button>
        </div>
        <div class="codex-usage-hud-top-body">
          <div class="codex-usage-hud-alert" data-field-panel="topWarnings" hidden>
            <span class="codex-usage-hud-alert-dot"></span>
            <span class="codex-usage-hud-alert-title">预警</span>
            <span class="codex-usage-hud-value warn" data-field="topWarnings"></span>
            <button class="codex-usage-hud-alert-close" data-action="dismiss-warnings-today" type="button" title="今天不再显示" aria-label="今天不再显示预警">×</button>
          </div>
          <div class="codex-usage-hud-top-grid">
            <div class="codex-usage-hud-top-column codex-usage-hud-top-column-left">
              <section class="codex-usage-hud-top-card">
                <div class="codex-usage-hud-card-head">
                  <div class="codex-usage-hud-card-title">本会话用量</div>
                  <div class="codex-usage-hud-card-actions">
                    <div class="codex-usage-hud-chip" data-field="topSessionRounds"></div>
                    <div class="codex-usage-hud-chip" data-field="topTaskOrdinalSession"></div>
                  </div>
                </div>
                <div class="codex-usage-hud-session-stats">
                  <div class="codex-usage-hud-session-stat">
                    <div class="codex-usage-hud-stat-value" data-field="topSessionCost"></div>
                    <div class="codex-usage-hud-stat-label">会话金额</div>
                  </div>
                  <div class="codex-usage-hud-session-stat">
                    <div class="codex-usage-hud-stat-value info" data-field="topSessionTokens"></div>
                    <div class="codex-usage-hud-stat-label">累计 tokens</div>
                  </div>
                </div>
                <div class="codex-usage-hud-session-insight">
                  <div class="codex-usage-hud-label">会话构成</div>
                  <div class="codex-usage-hud-value mono blue" data-field="topSessionMix"></div>
                  <div class="codex-usage-hud-value mono accent" data-field="topSessionAverage"></div>
                </div>
                <div class="codex-usage-hud-session-composition" data-field="topSessionComposition"></div>
                <div class="codex-usage-hud-token-breakdown">
                  <div class="codex-usage-hud-token-chip"><span>输入</span><b data-field="topSessionInputTokens"></b></div>
                  <div class="codex-usage-hud-token-chip"><span>缓存</span><b data-field="topSessionCachedTokens"></b></div>
                  <div class="codex-usage-hud-token-chip"><span>输出</span><b data-field="topSessionOutputTokens"></b></div>
                  <div class="codex-usage-hud-token-chip"><span>推理</span><b data-field="topSessionReasoningTokens"></b></div>
                </div>
              </section>
              <section class="codex-usage-hud-top-card">
                <div class="codex-usage-hud-card-head">
                  <div class="codex-usage-hud-card-title">额度进度</div>
                </div>
                <div class="codex-usage-hud-budget-rails" data-field="topBudgetProgress"></div>
              </section>
              <section class="codex-usage-hud-top-card codex-usage-hud-heavy-rounds-card">
                <div class="codex-usage-hud-card-head">
                  <div class="codex-usage-hud-card-title">高消耗轮次</div>
                  <div class="codex-usage-hud-chip" data-field="topHeavyRoundsSummary"></div>
                </div>
                <div class="codex-usage-hud-heavy-rounds" data-field="topHeavyRounds"></div>
              </section>
            </div>
            <div class="codex-usage-hud-top-column codex-usage-hud-top-column-right">
              <section class="codex-usage-hud-top-card codex-usage-hud-activity-card">
                <div class="codex-usage-hud-card-head">
                  <div class="codex-usage-hud-card-title">当前活动</div>
                  <div class="codex-usage-hud-card-actions">
                    <div class="codex-usage-hud-chip" data-field="topTaskOrdinalActivity"></div>
                    <div class="codex-usage-hud-chip" data-tone="warning" data-field="topActivityState"></div>
                  </div>
                </div>
                <div class="codex-usage-hud-activity-step">
                  <div class="codex-usage-hud-section-title" data-field="topCurrentTaskLabel">当前需求</div>
                  <div class="codex-usage-hud-value" data-field="topCurrentTask"></div>
                </div>
                <div class="codex-usage-hud-activity-main">
                  <div class="codex-usage-hud-section-title" data-field="topExecutingLabel">正在执行</div>
                  <div class="codex-usage-hud-value blue" data-field="topExecuting"></div>
                </div>
                <div class="codex-usage-hud-activity-metrics">
                  <div class="codex-usage-hud-activity-metric">
                    <div class="codex-usage-hud-section-title" data-field="topActivityElapsedLabel">已运行</div>
                    <div class="codex-usage-hud-value mono" data-field="topActivityElapsed"></div>
                  </div>
                  <div class="codex-usage-hud-activity-metric">
                    <div class="codex-usage-hud-section-title" data-field="topActivityGapLabel">当前等待</div>
                    <div class="codex-usage-hud-value mono" data-field="topActivityGap"></div>
                  </div>
                  <div class="codex-usage-hud-activity-metric">
                    <div class="codex-usage-hud-section-title" data-field="topActivityLastLabel">需求轮次</div>
                    <div class="codex-usage-hud-value mono" data-field="topActivityLast"></div>
                  </div>
                </div>
                <div class="codex-usage-hud-activity-trail">
                  <div class="codex-usage-hud-activity-trail-head">
                    <div class="codex-usage-hud-section-title">活动轨迹</div>
                    <div class="codex-usage-hud-card-actions">
                      <div class="codex-usage-hud-chip codex-usage-hud-copy-chip" data-field="topSlow"></div>
                      <div class="codex-usage-hud-chip codex-usage-hud-copy-chip" data-field="topGap"></div>
                    </div>
                  </div>
                  <div class="codex-usage-hud-activity-timeline" data-field="topActivityTrail"></div>
                  <button class="codex-usage-hud-activity-load-more" data-field="topActivityLoadMore" data-action="activity-load-more" type="button">查看更多</button>
                </div>
              </section>
            </div>
          </div>
        </div>
        ${topExpandedResizeMarkup()}
      </div>
    `;
  }

  function requestExpandedMarkup() {
    return `
      <div class="codex-usage-hud-expanded-shell">
        <div class="codex-usage-hud-request-subhead"><span>轮次流水</span><span>最新在上</span></div>
        <div class="codex-usage-hud-request-list" data-field="requestRows"></div>
        <div class="codex-usage-hud-panel-header" data-action="toggle">
          <button class="codex-usage-hud-handle" data-action="move" title="移动" aria-label="移动">⋮⋮</button>
          <div class="codex-usage-hud-title codex-usage-hud-line" data-action="toggle" data-field="requestLineExpanded"></div>
          ${backgroundUsageNotificationMarkup()}
        </div>
        ${requestExpandedResizeMarkup()}
      </div>
    `;
  }

  function settingsChromeMarkup() {
    return `
      <div id="${settingsModalId}" class="codex-usage-hud-settings-modal" hidden></div>
      ${composerBadgeEnabled
        ? `<div class="codex-usage-hud-token-breakdown" data-field="requestComposerBreakdown" role="tooltip" hidden></div>`
        : ""}
      <div class="codex-usage-hud-runtime-errors" data-field="runtimeErrorsPanel" hidden></div>
      ${restReminderToastMarkup()}
    `;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function loadPersistedSupportImages() {
    try {
      const raw = JSON.parse(localStorage.getItem(supportImagesStorageKey) || "[]");
      if (!Array.isArray(raw)) return [];
      return raw.filter((item) => (
        item
        && typeof item === "object"
        && typeof item.src === "string"
        && item.src.startsWith("data:image/")
      ));
    } catch (_) {
      return [];
    }
  }

  function persistSupportImages(images) {
    try {
      const items = Array.isArray(images) ? images.filter(Boolean) : [];
      if (!items.length) return;
      localStorage.setItem(supportImagesStorageKey, JSON.stringify(items));
    } catch (_) {}
  }

  function currentPayload() {
    const payload = window[stateName]?.payload || {};
    if (Array.isArray(payload.supportImages) && payload.supportImages.length) {
      return payload;
    }
    const persistedSupportImages = loadPersistedSupportImages();
    if (!persistedSupportImages.length) return payload;
    return { ...payload, supportImages: persistedSupportImages };
  }

  function defaultHudSettings() {
    return {
      daily_budget_usd: 100,
      weekly_budget_usd: 400,
      daily_reset_time: "10:00",
      weekly_reset_weekday: 3,
      weekly_reset_time: "10:00",
      display_mode: "renderer",
      work_overlay_max_items: 6,
      pricing_url: "",
      budget_thresholds: [0.5, 0.8, 0.9, 1.0],
      weekly_adjustment_usd: 0,
      provider_settings: {},
      provider_scope_mode: "all",
      selected_providers: [],
      notification_only_providers: [],
      provider_registry: {},
      app_provider: "",
      support_url: "https://github.com/mingbingfeng/codex-usage-hud",
      rest_reminder_enabled: false,
      rest_reminder_interval_minutes: 45,
      rest_reminder_postpone_minutes: 10,
      rest_reminder_idle_reset_minutes: 5,
      model_prices: {},
    };
  }

  function hudSettingsFromPayload() {
    const raw = currentPayload()?.settings || {};
    return { ...defaultHudSettings(), ...(raw && typeof raw === "object" ? raw : {}) };
  }

  function normalizePriceModel(value) {
    return String(value || "").trim().toLowerCase().replace(/-\\d{4}-\\d{2}-\\d{2}$/, "");
  }

  function priceModelPatternMatches(pattern, model) {
    const normalizedPattern = normalizePriceModel(pattern);
    const normalizedModel = normalizePriceModel(model);
    if (!normalizedPattern || !normalizedModel) return false;
    if (normalizedPattern.includes("*") || normalizedPattern.includes("?")) {
      const regexText = Array.from(normalizedPattern).map((char) => {
        if (char === "*") return ".*";
        if (char === "?") return ".";
        return char.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&");
      }).join("");
      const regex = new RegExp(`^${regexText}$`);
      return regex.test(normalizedModel);
    }
    return normalizedModel === normalizedPattern || normalizedModel.startsWith(`${normalizedPattern}-`);
  }

  function configuredPriceModels(settings) {
    const prices = settings?.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {};
    return Object.entries(prices).map(([key, price]) => String(price?.model || key || "").trim()).filter(Boolean);
  }

  function observedPriceModels() {
    const payload = currentPayload() || {};
    const values = [];
    if (payload.model) values.push(payload.model);
    if (Array.isArray(payload.observedModels)) values.push(...payload.observedModels);
    const seen = new Set();
    return values.map((item) => String(item || "").trim()).filter((item) => {
      if (!item || item === "n/a") return false;
      const key = normalizePriceModel(item);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function unknownPriceModels(settings) {
    const configured = configuredPriceModels(settings);
    return observedPriceModels().filter(
      (model) => !configured.some((pattern) => priceModelPatternMatches(pattern, model))
    );
  }

  function settingsBridgeUrl() {
    return String(currentPayload()?.settingsBridgeUrl || "").replace(/\/+$/, "");
  }

  function backgroundUsageBridgeUrl() {
    return String(currentPayload()?.backgroundUsageBridgeUrl || "").trim();
  }

  function backgroundUsageEndpoint(suffix = "") {
    const bridge = backgroundUsageBridgeUrl();
    if (!bridge) return null;
    try {
      const url = new URL(bridge, window.location.href);
      url.pathname = url.pathname.replace(
        /\/background-usage\/?$/,
        `/background-usage${suffix}`,
      );
      return url;
    } catch (_) {
      return null;
    }
  }

  function submitBackgroundUsageCommand(action, payload = {}) {
    const binding = window[settingsCommandBindingName];
    if (typeof binding !== "function") return "";
    const requestId = `background-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    try {
      binding(JSON.stringify({
        id: requestId,
        createdAt: Date.now(),
        action,
        ...payload,
        requestId,
      }));
      return requestId;
    } catch (error) {
      backgroundUsageState.error = `用量总览命令提交失败：${error?.message || error}`;
      return "";
    }
  }

  function readThemeStorage(key) {
    try {
      return localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  }

  function parseThemeStorageJson(value) {
    const text = String(value || "").trim();
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch (_) {
      return null;
    }
  }

  function normalizeThemeHex(value) {
    const text = String(value || "").trim().toLowerCase();
    if (!text) return "";
    const shortHex = text.match(/^#([0-9a-f]{3})$/i);
    if (shortHex) {
      return `#${shortHex[1][0]}${shortHex[1][0]}${shortHex[1][1]}${shortHex[1][1]}${shortHex[1][2]}${shortHex[1][2]}`;
    }
    const longHex = text.match(/^#([0-9a-f]{6})$/i);
    if (longHex) return `#${longHex[1]}`;
    const rgbMatch = text.match(/^rgba?\(([^)]+)\)$/i);
    if (!rgbMatch) return "";
    const parts = rgbMatch[1].split(",").map((item) => item.trim());
    if (parts.length < 3) return "";
    const channels = parts.slice(0, 3).map((item) => {
      if (item.endsWith("%")) {
        const numeric = Number.parseFloat(item.slice(0, -1));
        if (!Number.isFinite(numeric)) return null;
        return Math.max(0, Math.min(255, Math.round((numeric / 100) * 255)));
      }
      const numeric = Number.parseFloat(item);
      if (!Number.isFinite(numeric)) return null;
      return Math.max(0, Math.min(255, Math.round(numeric)));
    });
    if (channels.some((channel) => channel == null)) return "";
    return `#${channels.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
  }

  function rendererThemeSnapshot() {
    const root = document.documentElement;
    if (!root) return null;
    const css = getComputedStyle(root);
    const cssValue = (...names) => {
      for (const name of names) {
        const value = String(css.getPropertyValue(name) || "").trim();
        if (value) return value;
      }
      return "";
    };
    const colorValue = (...names) => normalizeThemeHex(cssValue(...names));
    const rawMode = String(readThemeStorage("appearanceTheme") || "").trim().toLowerCase();
    const mode = ["system", "light", "dark"].includes(rawMode) ? rawMode : "system";
    const classList = Array.from(root.classList || []);
    const classText = classList.join(" ").toLowerCase();
    const colorScheme = String(css.colorScheme || "").trim().toLowerCase();
    const prefersDark = !!window.matchMedia?.("(prefers-color-scheme: dark)")?.matches;
    let effectiveVariant = prefersDark ? "dark" : "light";
    if (colorScheme.includes("dark") || classText.includes("dark")) effectiveVariant = "dark";
    else if (colorScheme.includes("light") || classText.includes("light")) effectiveVariant = "light";
    return {
      mode,
      lightCodeThemeId: String(readThemeStorage("appearanceLightCodeThemeId") || "").trim(),
      darkCodeThemeId: String(readThemeStorage("appearanceDarkCodeThemeId") || "").trim(),
      lightTheme: parseThemeStorageJson(readThemeStorage("appearanceLightChromeTheme")),
      darkTheme: parseThemeStorageJson(readThemeStorage("appearanceDarkChromeTheme")),
      effectiveVariant,
      classList,
      colorScheme,
      cssTheme: {
        accent: colorValue("--codex-base-accent", "--color-text-accent", "--vscode-focusBorder", "--vscode-button-background", "--vscode-textLink-foreground"),
        surface: colorValue("--codex-base-surface", "--color-background-surface", "--vscode-editor-background", "--vscode-sideBar-background", "--vscode-panel-background", "--vscode-activityBar-background"),
        ink: colorValue("--codex-base-ink", "--color-text-foreground", "--vscode-editor-foreground", "--vscode-foreground", "--vscode-sideBarTitle-foreground"),
        diffAdded: colorValue("--color-decoration-added", "--vscode-gitDecoration-addedResourceForeground", "--vscode-terminal-ansiGreen"),
        diffRemoved: colorValue("--color-decoration-deleted", "--vscode-gitDecoration-deletedResourceForeground", "--vscode-terminal-ansiRed"),
        skill: colorValue("--color-accent-purple", "--vscode-terminal-ansiMagenta", "--vscode-textLink-foreground", "--vscode-terminal-ansiBlue"),
      },
    };
  }

  function reportRendererTheme(reason = "event") {
    const snapshot = rendererThemeSnapshot();
    const binding = window[themeBindingName];
    if (!snapshot || typeof binding !== "function") return false;
    const signature = JSON.stringify(snapshot);
    if (window[themeSignatureName] === signature) return false;
    try {
      binding(JSON.stringify({ ...snapshot, reason: String(reason || "event"), observedAt: Date.now() }));
      window[themeSignatureName] = signature;
      return true;
    } catch (_) {
      return false;
    }
  }

  function scheduleRendererThemeReport(reason = "event") {
    clearTimeout(window[themeTimerName] || 0);
    window[themeTimerName] = setTimeout(() => {
      reportRendererTheme(reason);
    }, 0);
  }

  function stopRendererThemeObserver() {
    window[themeObserverName]?.disconnect?.();
    const mediaQuery = window[themeMediaQueryName];
    const mediaQueryHandler = window[themeMediaQueryHandlerName];
    if (mediaQuery && mediaQueryHandler) {
      if (typeof mediaQuery.removeEventListener === "function") mediaQuery.removeEventListener("change", mediaQueryHandler);
      else mediaQuery.removeListener?.(mediaQueryHandler);
    }
    const storageHandler = window[themeStorageHandlerName];
    if (storageHandler) window.removeEventListener("storage", storageHandler);
    clearTimeout(window[themeTimerName] || 0);
    delete window[themeObserverName];
    delete window[themeMediaQueryName];
    delete window[themeMediaQueryHandlerName];
    delete window[themeStorageHandlerName];
    delete window[themeTimerName];
  }

  function startRendererThemeObserver() {
    const root = document.documentElement;
    if (!root) return false;
    stopRendererThemeObserver();
    window[themeObserverName] = new MutationObserver(() => {
      scheduleRendererThemeReport("dom-theme-change");
    });
    window[themeObserverName].observe(root, {
      attributes: true,
      attributeFilter: ["class", "style", "data-theme", "data-color-scheme"],
    });
    if (document.body) {
      window[themeObserverName].observe(document.body, {
        attributes: true,
        attributeFilter: ["class", "style", "data-theme", "data-color-scheme"],
      });
    }
    const mediaQuery = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (mediaQuery) {
      const handler = () => scheduleRendererThemeReport("system-theme-change");
      window[themeMediaQueryName] = mediaQuery;
      window[themeMediaQueryHandlerName] = handler;
      if (typeof mediaQuery.addEventListener === "function") mediaQuery.addEventListener("change", handler);
      else mediaQuery.addListener?.(handler);
    }
    const storageHandler = (event) => {
      if (!event?.key || [
        "appearanceTheme",
        "appearanceLightChromeTheme",
        "appearanceDarkChromeTheme",
        "appearanceLightCodeThemeId",
        "appearanceDarkCodeThemeId",
      ].includes(String(event.key))) {
        scheduleRendererThemeReport("storage-theme-change");
      }
    };
    window[themeStorageHandlerName] = storageHandler;
    window.addEventListener("storage", storageHandler);
    scheduleRendererThemeReport("bootstrap");
    return true;
  }

  window.__codexUsageHudReportTheme = (reason = "manual") => {
    startRendererThemeObserver();
    return reportRendererTheme(String(reason || "manual"));
  };

  function normalizeThreadId(value) {
    const text = normalize(value);
    const match = text.match(/^(?:[a-z0-9_.-]+:)(.+)$/i);
    return match ? normalize(match[1]) : text;
  }

  function activeSessionIdIsProvisional(value) {
    const text = normalize(value);
    const match = text.match(/^(?:[a-z0-9_.-]+:)(.+)$/i);
    const normalized = (match ? match[1] : text).toLowerCase();
    return normalized.startsWith("client-new-thread:");
  }

  const activeSessionIdentitySelector = [
    "[data-app-action-sidebar-thread-id]",
    "[data-session-id]",
    "a[href*='thread']",
    "a[href*='conversation']",
    "a[href*='session']",
  ].join(",");
  const activeSessionTitleSelector = [
    "[data-thread-title]",
    ".truncate.select-none",
    ".truncate.text-base",
  ].join(",");
  const activeSessionRowSelector = [
    activeSessionIdentitySelector,
    "[role='link']",
    "[role='button']",
  ].join(",");

  function activeSessionLocationId() {
    const source = `${location.pathname}${location.search}${location.hash}`;
    const match = source.match(/(?:session|conversation|thread)(?:\/|=|:|-)([A-Za-z0-9_.-]+)/i)
      || source.match(/\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:[/?#]|$)/)
      || source.match(/\/([A-Za-z0-9_-]{24,})(?:[/?#]|$)/);
    return match ? normalizeThreadId(decodeURIComponent(match[1])) : "";
  }

  function activeSessionRowHref(row) {
    return row?.getAttribute?.("href") || row?.querySelector?.("a")?.getAttribute?.("href") || "";
  }

  function activeSessionRowUrl(row) {
    const href = activeSessionRowHref(row);
    if (!href) return location.href;
    try {
      return new URL(href, location.href).href;
    } catch (_) {
      return location.href;
    }
  }

  function activeSessionIdentityRow(row) {
    if (!row) return row;
    if (row.matches?.(activeSessionIdentitySelector)) return row;
    return row.closest?.(activeSessionIdentitySelector) || row;
  }

  function activeSessionRowLooksThread(row) {
    const sourceRow = activeSessionIdentityRow(row);
    if (!sourceRow) return false;
    if (sourceRow.matches?.(activeSessionIdentitySelector)) return true;
    if (sourceRow?.querySelector?.(activeSessionTitleSelector)) return true;
    const href = activeSessionRowHref(sourceRow);
    return !!href && /(?:session|conversation|thread)/i.test(href);
  }

  function cleanActiveSessionTitle(value) {
    return normalize(String(value || "").replace(
      /\s*\d+\s*(秒|分|分钟|小时|天|周|个月|月|年|sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\s*$/i,
      ""
    ));
  }

  function activeSessionTitleIsNewSession(value) {
    return /^(新对话|新会话|新聊天|new chat|new conversation|new session)$/i.test(cleanActiveSessionTitle(value));
  }

  function activeSessionHeaderElement() {
    try {
      const header = conversationHeaderElement();
      if (visible(header)) return header;
    } catch (_) {}
    const surface = document.querySelector('[data-testid="app-shell-header-context-menu-surface"]');
    const surfaceHeader = surface?.closest?.("header.app-header-tint, header, .app-header-tint");
    if (visible(surfaceHeader)) return surfaceHeader;
    return Array.from(document.querySelectorAll([
      "header.app-header-tint",
      "[data-testid='app-shell-header']",
      "[data-testid='app-shell-header-context-menu-surface']",
      ".app-header-tint",
    ].join(","))).filter(visible)[0] || null;
  }

  function activeSessionHeaderTitleIgnored(value) {
    const text = cleanActiveSessionTitle(value);
    if (!text) return true;
    return /^(File\s*Edit\s*View\s*Window\s*Help|FileEditViewWindowHelp|文件|编辑|视图|帮助|切换底部面板显示|显示\/隐藏侧边栏|chat actions|open in)$/i.test(text);
  }

  function activeSessionHeaderTitleText() {
    const header = activeSessionHeaderElement();
    if (!visible(header)) return "";
    const candidates = Array.from(header.querySelectorAll([
      "[data-thread-title]",
      "h1",
      "h2",
      ".truncate",
      "[class*='truncate']",
    ].join(","))).filter((node) => (
      visible(node)
      && !node.closest?.(`#${rootId}`)
      && !node.closest?.("button, [role='button'], a")
    ));
    for (const node of candidates) {
      const text = cleanActiveSessionTitle(node.textContent || "").slice(0, 160);
      if (!activeSessionHeaderTitleIgnored(text)) return text;
    }
    const clone = header.cloneNode(true);
    clone.querySelectorAll([
      "button",
      "[role='button']",
      "a",
      "svg",
      `#${rootId}`,
      ".codex-usage-hud-panel",
    ].join(",")).forEach((node) => node.remove());
    const fallback = cleanActiveSessionTitle(clone.textContent || "").slice(0, 160);
    return activeSessionHeaderTitleIgnored(fallback) ? "" : fallback;
  }

  function activeSessionComposerVisible() {
    try {
      const composer = composerElement();
      if (visible(composer)) {
        const rect = composer.getBoundingClientRect();
        if (
          rect.width >= 240
          && rect.height >= 20
          && rect.left > 200
          && rect.top > 80
          && rect.bottom <= innerHeight + 4
        ) return true;
      }
    } catch (_) {}
    return Array.from(document.querySelectorAll([
      "textarea",
      "[contenteditable='true']",
      "[role='textbox']",
      ".ProseMirror",
    ].join(","))).some((node) => {
      if (!visible(node)) return false;
      const rect = node.getBoundingClientRect();
      return (
        rect.width >= 120
        && rect.height >= 18
        && rect.left > 200
        && rect.top > 80
        && rect.bottom <= innerHeight + 4
      );
    });
  }

  function activeSessionHeaderLooksNewSession(rows) {
    const header = activeSessionHeaderElement();
    if (!visible(header)) return false;
    if (activeSessionHeaderTitleText()) return false;
    if (activeSessionLocationId()) return false;
    const activeRows = Array.isArray(rows) ? rows : activeSessionRows();
    if (activeRows.some(activeSessionRowSelected)) return false;
    return activeSessionComposerVisible();
  }

  function activeSessionRefFromRow(row) {
    const sourceRow = activeSessionIdentityRow(row);
    const href = activeSessionRowHref(sourceRow);
    const idMatch = href.match(/(?:session|conversation|thread)[=/:-]([A-Za-z0-9_.-]+)/i)
      || href.match(/([A-Za-z0-9_-]{8,})$/);
    const rawSessionId = sourceRow?.getAttribute?.("data-app-action-sidebar-thread-id")
      || (idMatch && idMatch[1])
      || sourceRow?.getAttribute?.("data-session-id")
      || sourceRow?.getAttribute?.("data-testid")
      || "";
    const sessionId = normalizeThreadId(rawSessionId);
    const titleNode = sourceRow?.querySelector?.(activeSessionTitleSelector);
    const rawTitle = titleNode?.textContent
      || (titleNode ? "" : (activeSessionRowLooksThread(sourceRow) ? (sourceRow?.textContent || row?.textContent || "") : ""));
    const title = cleanActiveSessionTitle(titleNode ? rawTitle : rawTitle.replace(/\s*(Export|Delete|Move|Remove from project|导出|删除|移动|移出项目)+$/g, "")).slice(0, 160);
    if (activeSessionTitleIsNewSession(title)) {
      return { rawSessionId: "", sessionId: "", title };
    }
    if (activeSessionIdIsProvisional(rawSessionId)) {
      return {
        rawSessionId: normalize(rawSessionId),
        rendererSessionId: normalize(rawSessionId),
        sessionId: "",
        title,
        pendingSession: true,
      };
    }
    return { rawSessionId: normalize(rawSessionId), sessionId, title };
  }

  function activeSessionRowSelected(row) {
    if (row?.getAttribute?.("data-app-action-sidebar-thread-active") === "true") return true;
    if (row?.getAttribute?.("aria-current") === "page" || row?.getAttribute?.("aria-current") === "true") return true;
    if (row?.getAttribute?.("aria-selected") === "true") return true;
    if (row?.getAttribute?.("data-active") === "true" || row?.getAttribute?.("data-selected") === "true") return true;
    if (row?.matches?.("[data-state='active'], [data-state='selected'], .active, .selected")) return true;
    return false;
  }

  function activeSessionRowMatchesLocation(row) {
    const href = activeSessionRowHref(row);
    if (href) {
      try {
        const url = new URL(href, location.href);
        if (url.href === location.href) return true;
      } catch (_) {
        if (location.href.includes(href)) return true;
      }
    }
    const ref = activeSessionRefFromRow(row);
    return (
      (!!ref.rawSessionId && location.href.includes(ref.rawSessionId))
      || (!!ref.sessionId && location.href.includes(ref.sessionId))
    );
  }

  function activeSessionRows() {
    const container = activeSessionContainer();
    const root = container || document;
    return Array.from(root.querySelectorAll(activeSessionRowSelector))
      .filter((row) => activeSessionRowLooksThread(row))
      .filter((row) => {
        const ref = activeSessionRefFromRow(row);
        return !!(ref.sessionId || ref.title);
      });
  }

  function readActiveSessionRef() {
    const rows = activeSessionRows();
    if (activeSessionHeaderLooksNewSession(rows)) {
      return {
        sessionId: "",
        title: "",
        url: location.href,
        newSession: true,
        matchedBy: "header-empty",
      };
    }
    const row = rows.find(activeSessionRowSelected) || rows.find(activeSessionRowMatchesLocation) || null;
    const ref = row ? activeSessionRefFromRow(row) : { sessionId: activeSessionLocationId(), title: "" };
    const pendingSession = !!ref.pendingSession;
    const newSession = !pendingSession && !ref.sessionId && activeSessionTitleIsNewSession(ref.title);
    return {
      sessionId: (newSession || pendingSession) ? "" : (ref.sessionId || ""),
      rendererSessionId: ref.rendererSessionId || ref.rawSessionId || "",
      title: newSession ? "" : (ref.title || ""),
      url: location.href,
      newSession,
      pendingSession,
    };
  }

  function activeSessionContainer() {
    const row = document.querySelector(activeSessionIdentitySelector)
      || document.querySelector(activeSessionTitleSelector)?.closest?.(activeSessionRowSelector)
      || document.querySelector(activeSessionRowSelector);
    return row?.closest?.("aside, nav, [role='navigation'], [data-testid*='sidebar' i], [class*='sidebar' i]")
      || row?.parentElement
      || document.querySelector("aside, nav, [role='navigation'], [data-testid*='sidebar' i], [class*='sidebar' i]")
      || null;
  }

  function postActiveSession(reason = "event", overrideRef = null) {
    const bridge = settingsBridgeUrl();
    const ref = overrideRef || readActiveSessionRef();
    const newSession = !!ref.newSession || reason === "new-session";
    const pendingSession = !!ref.pendingSession;
    if (!ref.sessionId && !ref.title && !newSession && !pendingSession) return;
    const rawRendererSessionId = normalize(
      ref.rendererSessionId || ref.rawSessionId || ref.sessionId || "",
    );
    const rendererSessionId = normalizeThreadId(rawRendererSessionId);
    const canonicalSessionId = activeSessionIdIsProvisional(rawRendererSessionId)
      ? ""
      : rendererSessionId;
    const lastCanonicalSessionId = normalizeThreadId(
      window[activeSessionCanonicalIdName] || "",
    );
    const lastCanonicalAt = Number(window[activeSessionCanonicalAtName] || 0);
    const transientWithoutCanonicalId = !canonicalSessionId && (newSession || pendingSession);
    if (
      transientWithoutCanonicalId
      && reason !== "click"
      && lastCanonicalSessionId
      && Date.now() - lastCanonicalAt < 2500
    ) {
      // 会话切换期间 Codex 会短暂清空选中行；不能让这个瞬态覆盖刚确认的会话。
      clearTimeout(window[activeSessionSettledTimerName] || 0);
      window[activeSessionSettledTimerName] = setTimeout(() => {
        postActiveSession("settled");
      }, 320);
      return;
    }
    const selectionKey = JSON.stringify([
      rawRendererSessionId,
      ref.title,
      newSession,
      pendingSession,
    ]);
    if (window[activeSessionSelectionKeyName] !== selectionKey) {
      window[activeSessionSelectionKeyName] = selectionKey;
      window[activeSessionSelectionSeqName] = Number(window[activeSessionSelectionSeqName] || 0) + 1;
    }
    const selectionSeq = Math.max(1, Number(window[activeSessionSelectionSeqName] || 1));
    const appliedSeq = Number(window[activeSessionAppliedSeqName] || 0);
    const signature = JSON.stringify([
      selectionSeq,
      rawRendererSessionId,
      ref.title,
      ref.url || location.href,
      newSession,
      pendingSession,
    ]);
    if (window[activeSessionLastSignatureName] === signature && appliedSeq >= selectionSeq) return;
    const payload = {
      sessionId: canonicalSessionId,
      rendererSessionId: rawRendererSessionId,
      selectionSeq,
      title: ref.title,
      url: ref.url || location.href,
      reason: newSession ? "new-session" : (pendingSession ? "pending-session" : reason),
      newSession,
      pendingSession,
      matchedBy: ref.matchedBy || "",
      observedAt: Number(ref.observedAt || 0) || Date.now(),
    };
    const binding = window[activeSessionBindingName];
    if (typeof binding === "function") {
      try {
        binding(JSON.stringify(payload));
        // Mark an observation as delivered only after the transport accepts
        // it. The first sidebar click can race binding setup on a cold start;
        // recording the signature before delivery suppressed every follow-up
        // until the user clicked a second conversation.
        window[activeSessionLastSignatureName] = signature;
        if (canonicalSessionId) {
          window[activeSessionCanonicalIdName] = canonicalSessionId;
          window[activeSessionCanonicalAtName] = Date.now();
        } else {
          window[activeSessionCanonicalIdName] = "";
          window[activeSessionCanonicalAtName] = 0;
        }
        return;
      } catch (_) {}
    }
    if (!bridge) return;
    fetch(`${bridge}/active-session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).then(() => {
      window[activeSessionLastSignatureName] = signature;
      if (canonicalSessionId) {
        window[activeSessionCanonicalIdName] = canonicalSessionId;
        window[activeSessionCanonicalAtName] = Date.now();
      } else {
        window[activeSessionCanonicalIdName] = "";
        window[activeSessionCanonicalAtName] = 0;
      }
    }).catch(() => {});
  }

  function scheduleActiveSessionReport(reason = "event") {
    clearTimeout(window[activeSessionTimerName] || 0);
    window[activeSessionTimerName] = setTimeout(() => {
      postActiveSession(reason);
      refreshActiveSessionObserver();
    }, 0);
  }

  function clearActiveSessionSendFollowup() {
    for (const timer of (window[activeSessionSendFollowupTimersName] || [])) {
      clearTimeout(timer);
    }
    window[activeSessionSendFollowupTimersName] = [];
    clearTimeout(window[activeSessionSettledTimerName] || 0);
    window[activeSessionSettledTimerName] = 0;
  }

  function showActiveSessionFollowFeedback(reason = "reading-session-data") {
    const root = document.getElementById(rootId);
    if (!root) return;
    const message = reason === "renderer-channel-unavailable"
      ? "会话切换暂停：renderer 事件通道不可用"
      : "会话切换中：正在读取会话数据";
    setText(root, "requestLine", message);
    setText(root, "requestLineExpanded", message);
  }

  function activeSessionPayloadCache() {
    const existing = window[activeSessionPayloadCacheName];
    if (existing instanceof Map) return existing;
    const cache = new Map();
    window[activeSessionPayloadCacheName] = cache;
    return cache;
  }

  function activeSessionPayloadKeys(value) {
    const raw = normalize(value?.rendererSessionId || value?.rawSessionId || "");
    const canonical = normalize(value?.sessionId || "");
    return [...new Set([raw, canonical].filter(Boolean))];
  }

  function cacheActiveSessionPayload(payload) {
    if (
      !payload
      || payload.cachedPreview
      || payload.newSession
      || payload.pendingSession
      || payload.requestStatus !== "confirmed"
    ) return;
    const keys = activeSessionPayloadKeys(payload);
    if (!keys.length) return;
    const cache = activeSessionPayloadCache();
    const cached = { ...payload, cachedPreview: false };
    delete cached.payloadDomains;
    for (const key of keys) cache.set(key, cached);
    while (cache.size > 48) cache.delete(cache.keys().next().value);
  }

  function applyCachedActiveSessionPayload(ref, observedAt = 0) {
    const cache = activeSessionPayloadCache();
    const cached = activeSessionPayloadKeys(ref).map((key) => cache.get(key)).find(Boolean);
    if (!cached || typeof window.__codexUsageHudUpdate !== "function") return false;
    const domain = {
      ...cached,
      selectionSeq: Math.max(1, Number(window[activeSessionSelectionSeqName] || 1)),
      selectionObservedAt: Number(observedAt || 0) || Date.now(),
      followState: "cached",
      followReason: "renderer-cached-preview",
      cachedPreview: true,
    };
    window.__codexUsageHudUpdate({
      ...domain,
      payloadDomains: { sessionSwitch: domain },
    });
    window.__codexUsageHudCachedPreview = {
      selectionSeq: domain.selectionSeq,
      observedAt: domain.selectionObservedAt,
      appliedAt: Date.now(),
      rendererSessionId: normalize(ref?.rendererSessionId || ref?.rawSessionId || ""),
    };
    return true;
  }

  function activeSessionComposerTarget(target) {
    if (!(target instanceof Node)) return false;
    const composer = composerElement();
    if (composer?.contains?.(target)) return true;
    return !!target.closest?.("textarea, [contenteditable='true'], [role='textbox'], .ProseMirror");
  }

  function scheduleActiveSessionSendFollowup(reason = "composer-send", expectedSessionId = "") {
    clearActiveSessionSendFollowup();
    const expected = normalize(expectedSessionId);
    const report = () => {
      const ref = readActiveSessionRef();
      const current = normalize(ref.rendererSessionId || ref.rawSessionId || ref.sessionId || "");
      if (expected && current !== expected) return;
      postActiveSession(reason, ref);
      refreshActiveSessionObserver();
    };
    window[activeSessionSendFollowupTimersName] = [
      setTimeout(report, 32),
      setTimeout(report, 120),
      setTimeout(report, 320),
      setTimeout(report, 800),
      setTimeout(report, 1600),
    ];
  }

  function activeSessionComposerSubmitButton(button) {
    if (!(button instanceof HTMLElement) || button.hasAttribute("disabled")) return false;
    const label = cleanActiveSessionTitle(
      button.getAttribute("aria-label")
      || button.getAttribute("title")
      || button.textContent
      || ""
    );
    return /^(发送|提交|send|submit)$/i.test(label);
  }

  function refreshActiveSessionObserver() {
    const container = activeSessionContainer();
    const header = activeSessionHeaderElement();
    window[activeSessionObserverName]?.disconnect?.();
    if (!container && !header) return false;
    window[activeSessionObserverName] = new MutationObserver(() => {
      scheduleActiveSessionReport("active-session-dom");
    });
    if (container) window[activeSessionObserverName].observe(container, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: [
        "aria-selected",
        "aria-current",
        "data-active",
        "data-selected",
        "data-state",
        "data-app-action-sidebar-thread-active",
        "data-app-action-sidebar-thread-id",
        "href",
      ],
    });
    if (header && header !== container && !container?.contains?.(header)) {
      window[activeSessionObserverName].observe(header, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
        attributeFilter: [
          "aria-label",
          "class",
          "data-thread-title",
          "data-testid",
          "title",
        ],
      });
    }
    return true;
  }

  function startActiveSessionBootstrapObserver() {
    if (window[activeSessionBootstrapObserverName] || refreshActiveSessionObserver()) return;
    if (!document.body) return;
    window[activeSessionBootstrapObserverName] = new MutationObserver(() => {
      if (refreshActiveSessionObserver()) {
        window[activeSessionBootstrapObserverName]?.disconnect?.();
        delete window[activeSessionBootstrapObserverName];
        scheduleActiveSessionReport("sidebar-ready");
      }
    });
    window[activeSessionBootstrapObserverName].observe(document.body, {
      subtree: true,
      childList: true,
    });
    setTimeout(() => {
      window[activeSessionBootstrapObserverName]?.disconnect?.();
      delete window[activeSessionBootstrapObserverName];
    }, 5000);
  }

  function installActiveSessionHistoryPatch() {
    if (window[activeSessionHistoryPatchName]) return;
    const originalPushState = history.pushState;
    const originalReplaceState = history.replaceState;
    const patch = {
      originalPushState,
      originalReplaceState,
      pushState: function(...args) {
        const result = originalPushState.apply(this, args);
        scheduleActiveSessionSendFollowup("history");
        return result;
      },
      replaceState: function(...args) {
        const result = originalReplaceState.apply(this, args);
        scheduleActiveSessionSendFollowup("history");
        return result;
      },
      popstate: () => scheduleActiveSessionSendFollowup("popstate"),
    };
    try {
      history.pushState = patch.pushState;
      history.replaceState = patch.replaceState;
      window.addEventListener("popstate", patch.popstate);
      window[activeSessionHistoryPatchName] = patch;
    } catch (_) {
      try {
        history.pushState = originalPushState;
        history.replaceState = originalReplaceState;
      } catch (_) {}
    }
  }

  function removeActiveSessionWatchers() {
    clearTimeout(window[activeSessionTimerName] || 0);
    clearActiveSessionSendFollowup();
    document.removeEventListener("click", window[activeSessionClickHandlerName], true);
    const composerHandler = window[activeSessionComposerHandlerName];
    if (composerHandler) {
      document.removeEventListener("submit", composerHandler.submit, true);
      document.removeEventListener("keydown", composerHandler.keydown, true);
    }
    window[activeSessionObserverName]?.disconnect?.();
    window[activeSessionBootstrapObserverName]?.disconnect?.();
    const patch = window[activeSessionHistoryPatchName];
    if (patch) {
      if (history.pushState === patch.pushState) history.pushState = patch.originalPushState;
      if (history.replaceState === patch.replaceState) history.replaceState = patch.originalReplaceState;
      window.removeEventListener("popstate", patch.popstate);
    }
    delete window[activeSessionObserverName];
    delete window[activeSessionBootstrapObserverName];
    delete window[activeSessionTimerName];
    delete window[activeSessionSendFollowupTimersName];
    delete window[activeSessionComposerHandlerName];
    delete window[activeSessionClickHandlerName];
    delete window[activeSessionHistoryPatchName];
    delete window[activeSessionLastSignatureName];
    delete window[activeSessionCanonicalIdName];
    delete window[activeSessionCanonicalAtName];
    delete window[activeSessionSettledTimerName];
    delete window[activeSessionSelectionKeyName];
    delete window[activeSessionSelectionSeqName];
    delete window[activeSessionAppliedSeqName];
  }

  function ensureActiveSessionWatchers() {
    if (!window[activeSessionClickHandlerName]) {
      window[activeSessionClickHandlerName] = (event) => {
        const submitButton = event.target?.closest?.("button, [role='button']");
        if (
          !composerBadgeEnabled
          && submitButton
          && activeSessionComposerTarget(submitButton)
          && activeSessionComposerSubmitButton(submitButton)
        ) {
          scheduleActiveSessionSendFollowup("composer-send");
          return;
        }
        const container = event.target?.closest?.("aside, nav, [role='navigation'], [data-testid*='sidebar' i], [class*='sidebar' i]");
        const identityRow = event.target?.closest?.(activeSessionIdentitySelector);
        const row = identityRow || event.target?.closest?.(activeSessionRowSelector);
        const explicitRow = !!identityRow || row?.matches?.("[role='link']");
        if (row && !container && !explicitRow) return;
        if (row && (!container || container.contains(row))) {
          const ref = activeSessionRefFromRow(row);
          const pendingSession = !!ref.pendingSession;
          const newSession = !pendingSession && !ref.sessionId && activeSessionTitleIsNewSession(ref.title);
          const observedAt = Date.now();
          showActiveSessionFollowFeedback(
            typeof window[activeSessionBindingName] === "function"
              ? "reading-session-data"
              : "renderer-channel-unavailable"
          );
          postActiveSession("click", {
            sessionId: (newSession || pendingSession) ? "" : (ref.sessionId || ""),
            rendererSessionId: ref.rendererSessionId || ref.rawSessionId || "",
            title: newSession ? "" : (ref.title || ""),
            url: activeSessionRowUrl(row),
            newSession,
            pendingSession,
            observedAt,
          });
          applyCachedActiveSessionPayload({
            sessionId: ref.sessionId || "",
            rendererSessionId: ref.rendererSessionId || ref.rawSessionId || "",
          }, observedAt);
          scheduleActiveSessionSendFollowup(
            "click",
            ref.rendererSessionId || ref.rawSessionId || ref.sessionId || "",
          );
        }
      };
      document.addEventListener("click", window[activeSessionClickHandlerName], true);
    }
    if (!window[activeSessionComposerHandlerName]) {
      const submit = (event) => {
        if (activeSessionComposerTarget(event.target)) {
          scheduleActiveSessionSendFollowup("composer-submit");
        }
      };
      const keydown = (event) => {
        if (
          event.key === "Enter"
          && !event.shiftKey
          && !event.isComposing
          && activeSessionComposerTarget(event.target)
        ) {
          scheduleActiveSessionSendFollowup("composer-enter");
        }
      };
      window[activeSessionComposerHandlerName] = { submit, keydown };
      document.addEventListener("submit", submit, true);
      if (!composerBadgeEnabled) {
        document.addEventListener("keydown", keydown, true);
      }
    }
    installActiveSessionHistoryPatch();
    startActiveSessionBootstrapObserver();
    scheduleActiveSessionReport("payload");
  }

  window.__codexUsageHudReportActiveSession = (reason = "manual") => {
    ensureActiveSessionWatchers();
    const reportReason = String(reason || "manual");
    const ref = readActiveSessionRef();
    postActiveSession(reportReason, ref);
    // Return the same renderer-observed reference through Runtime.evaluate.
    // CDP bindings still keep it live afterwards, but this synchronous result
    // guarantees that the initial HUD snapshot follows the page already open
    // when the HUD starts (including the blank-title new-session page).
    return {
      sessionId: ref.sessionId || ref.rendererSessionId || "",
      rendererSessionId: ref.rendererSessionId || "",
      title: ref.title || "",
      newSession: !!ref.newSession,
      pendingSession: !!ref.pendingSession,
      matchedBy: ref.matchedBy || "",
      reason: reportReason,
      observedAt: Date.now(),
    };
  };

  function settingsPathLabel() {
    return String(currentPayload()?.settingsPath || "");
  }

  function appVersion() {
    return String(currentPayload()?.appVersion || "unknown");
  }

  function currentUpdateState() {
    const raw = currentPayload()?.updateState || {};
    return raw && typeof raw === "object" ? raw : {};
  }

  function workOverlaySelectableMax() {
    const value = Number(currentPayload()?.workOverlaySelectableMax ?? 6);
    return Number.isFinite(value) && value >= 1 ? Math.round(value) : 6;
  }

  function desktopOverlayDependency() {
    const raw = currentPayload()?.desktopOverlayDependency || {};
    return raw && typeof raw === "object" ? raw : {};
  }

  function desktopOverlayDependencyHtml() {
    const dependency = desktopOverlayDependency();
    const installed = !!dependency.installed;
    const installing = !!dependency.installing;
    const requiresRestart = !!dependency.requiresRestart;
    const canInstall = !!dependency.canInstall;
    const version = String(dependency.version || "").trim();
    if (installed) {
      return `
        <div class="codex-usage-hud-overlay-dependency" data-installed="true" title="保存后显示方形进度气泡；会话完成后收起为圆形总结。">
          <span class="codex-usage-hud-overlay-dependency-state">已安装</span>
          <span class="codex-usage-hud-overlay-dependency-version">${escapeHtml(version ? `PySide6 ${version}` : "PySide6 可用")}</span>
        </div>
      `;
    }
    const actions = [];
    if (canInstall && !installing) {
      actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-install-desktop-overlay">立即安装</button>');
    }
    if (!requiresRestart) {
      actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-enable-desktop-overlay">启用气泡</button>');
    } else {
      actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-restart">立即重启</button>');
    }
    const stateText = installing ? "正在安装" : (requiresRestart ? "需要重启" : "未安装");
    const noteText = installing
      ? "后台安装中"
      : (requiresRestart ? "重启后生效" : "需 PySide6");
    return `
      <div class="codex-usage-hud-overlay-dependency" data-installed="false" title="${escapeHtml(
        installing
          ? "气泡组件正在后台安装；完成后可启用。"
          : (requiresRestart
            ? "安装完成后重启 HUD，才能显示会话进度气泡。"
            : "会话进度气泡需要 PySide6 桌面组件。")
      )}">
        <span class="codex-usage-hud-overlay-dependency-state">${stateText}</span>
        <span class="codex-usage-hud-overlay-dependency-note">${escapeHtml(noteText)}</span>
        <div class="codex-usage-hud-overlay-dependency-actions">${actions.join("")}</div>
      </div>
    `;
  }

  function syncDesktopOverlayDependency() {
    const node = document.querySelector(`#${settingsModalId} [data-desktop-overlay-dependency="true"]`);
    if (node) node.innerHTML = desktopOverlayDependencyHtml();
  }

  function updateStateFromPayload(payload) {
    const raw = payload?.updateState || {};
    return raw && typeof raw === "object" ? raw : {};
  }

  function updateActionGlyph(state) {
    return String(state?.icon || "download") === "install" ? "⇪" : "↓";
  }

  function renderUpdateButtons(root, payload) {
    const state = updateStateFromPayload(payload);
    const visible = !!state?.visible;
    root.querySelectorAll('[data-action="update-action"]').forEach((node) => {
      if (!(node instanceof HTMLButtonElement)) return;
      node.hidden = !visible;
      if (!visible) {
        node.removeAttribute("title");
        node.removeAttribute("aria-label");
        node.dataset.state = "";
        node.dataset.icon = "";
        return;
      }
      const title = String(state?.title || state?.message || "发现新版本");
      node.textContent = updateActionGlyph(state);
      node.title = title;
      node.setAttribute("aria-label", title);
      node.dataset.state = String(state?.phase || "");
      node.dataset.icon = String(state?.icon || "download");
    });
  }

  function thresholdText(settings) {
    const items = Array.isArray(settings.budget_thresholds) ? settings.budget_thresholds : [];
    return items.map((value) => Number(value || 0)).filter((value) => value > 0).join(",");
  }

  function priceRowsHtml(settings) {
    const prices = settings.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {};
    const entries = Object.entries(prices);
    if (!entries.length) entries.push(["gpt-5.6-sol", { input: 5, output: 30, cached_input: 0.5, cache_write: 6.25 }]);
    return entries.map(([key, price]) => {
      const model = String(price?.model || key || "");
      const provider = String(price?.provider || "");
      const baseUrl = String(price?.base_url || price?.baseUrl || "");
      return `
      <div class="codex-usage-hud-price-row" data-price-row="true" data-price-key="${escapeHtml(key)}">
        <input data-price-field="model" value="${escapeHtml(model)}" aria-label="模型">
        <input data-price-field="input" type="number" min="0" step="0.000001" value="${escapeHtml(price?.input ?? 0)}" aria-label="输入单价">
        <input data-price-field="output" type="number" min="0" step="0.000001" value="${escapeHtml(price?.output ?? 0)}" aria-label="输出单价">
        <input data-price-field="cached_input" type="number" min="0" step="0.000001" value="${escapeHtml(price?.cached_input ?? 0)}" aria-label="缓存读取单价">
        <input data-price-field="cache_write" type="number" min="0" step="0.000001" value="${escapeHtml(price?.cache_write ?? 0)}" aria-label="缓存写入单价">
        <input class="codex-usage-hud-price-advanced" data-price-field="provider" value="${escapeHtml(provider)}" aria-label="渠道">
        <input class="codex-usage-hud-price-advanced" data-price-field="base_url" value="${escapeHtml(baseUrl)}" aria-label="Base URL">
      </div>
    `;
    }).join("");
  }

  function detectedPriceModelsHtml(settings) {
    const models = unknownPriceModels(settings);
    if (!models.length) return "";
    return `
      <div class="codex-usage-hud-price-detected">
        <span>检测到未计价模型</span>
        ${models.slice(0, 4).map((model) => `<button type="button" class="codex-usage-hud-settings-action" data-action="settings-add-detected-model" data-model="${escapeHtml(model)}">${escapeHtml(model)}</button>`).join("")}
      </div>
    `;
  }

  function settingsProviderNames(settings) {
    const registry = settings.provider_registry && typeof settings.provider_registry === "object" ? settings.provider_registry : {};
    const providerSettings = settings.provider_settings && typeof settings.provider_settings === "object" ? settings.provider_settings : {};
    const appProvider = String(settings.app_provider || "").trim().toLowerCase();
    const names = new Set([...Object.keys(registry), ...Object.keys(providerSettings)]);
    if (appProvider) names.add(appProvider);
    return Array.from(names).map((provider) => String(provider || "").trim().toLowerCase()).filter(Boolean).sort();
  }

  function cloneSettingsPriceTable(value) {
    const prices = value && typeof value === "object" ? value : {};
    return Object.fromEntries(Object.entries(prices).map(([key, price]) => [
      key,
      price && typeof price === "object" ? { ...price } : {},
    ]));
  }

  function providerDraftFromSettings(settings, provider, enabled, notificationOnly) {
    const source = settings.provider_settings?.[provider] || {};
    const modelPrices = source.model_prices && typeof source.model_prices === "object"
      ? source.model_prices
      : settings.model_prices;
    return {
      enabled: !!enabled,
      notificationOnly: !!notificationOnly && !enabled,
      settings: {
        ...source,
        model_prices: cloneSettingsPriceTable(modelPrices),
        pricing_url: String(source.pricing_url ?? settings.pricing_url ?? ""),
        weekly_adjustment_usd: Number(source.weekly_adjustment_usd ?? settings.weekly_adjustment_usd ?? 0),
      },
    };
  }

  function ensureSettingsProviderDraft(settings, reset = false) {
    if (settingsProviderDraft && !reset) return settingsProviderDraft;
    const order = settingsProviderNames(settings);
    const appProvider = String(settings.app_provider || "").trim().toLowerCase();
    const selected = settings.provider_scope_mode === "custom"
      ? new Set((settings.selected_providers || []).map((provider) => String(provider || "").trim().toLowerCase()).filter(Boolean))
      : new Set(order);
    const notificationOnly = new Set(
      (settings.notification_only_providers || []).map((provider) => String(provider || "").trim().toLowerCase()).filter(Boolean)
    );
    if (appProvider) selected.add(appProvider);
    const requestedProvider = String(window[settingsProviderName] || "").trim().toLowerCase();
    const activeProvider = order.includes(requestedProvider)
      ? requestedProvider
      : (order.includes(appProvider) ? appProvider : (order[0] || ""));
    settingsProviderDraft = {
      activeProvider,
      appProvider,
      order,
      providers: Object.fromEntries(order.map((provider) => [
        provider,
        providerDraftFromSettings(
          settings,
          provider,
          selected.has(provider) || provider === appProvider,
          notificationOnly.has(provider) && !selected.has(provider) && provider !== appProvider,
        ),
      ])),
    };
    settingsDirtyProviders.clear();
    window[settingsProviderName] = activeProvider;
    return settingsProviderDraft;
  }

  function settingsProviderTabBadge(settings, provider) {
    const detail = settings.provider_registry?.[provider] || {};
    if (provider === settingsProviderDraft?.appProvider) return "App";
    if (detail.historicalOnly) return "历史";
    if (provider === "unknown") return "未知";
    return "";
  }

  function settingsProviderMeta(settings, provider) {
    const detail = settings.provider_registry?.[provider] || {};
    const profiles = Array.isArray(detail.profiles) ? detail.profiles.map((profile) => String(profile || "").trim()).filter(Boolean) : [];
    const parts = [];
    let tone = "";
    if (provider === settingsProviderDraft?.appProvider) {
      parts.push("Codex App · 必选");
      tone = "required";
    } else if (detail.historicalOnly) {
      parts.push("历史通道");
      tone = "historical";
    } else if (provider === "unknown") {
      parts.push("未知通道");
    }
    if (profiles.length) {
      parts.push(`${profiles.length > 1 ? "Profiles" : "Profile"}: ${profiles.join(", ")}`);
    }
    return { text: parts.join(" · "), tone };
  }

  function settingsProviderTabsHtml(settings) {
    const draft = ensureSettingsProviderDraft(settings);
    return draft.order.map((provider) => {
      const badge = settingsProviderTabBadge(settings, provider);
      const dirty = settingsDirtyProviders.has(provider);
      return `
        <button type="button" class="codex-usage-hud-provider-tab" role="tab"
          data-action="settings-provider-tab" data-provider-tab="true" data-provider="${escapeHtml(provider)}"
          aria-selected="${provider === draft.activeProvider}">
          <span>${escapeHtml(provider)}</span>
          ${badge ? `<span class="codex-usage-hud-provider-tab-badge">${escapeHtml(badge)}</span>` : ""}
          ${dirty ? '<span class="codex-usage-hud-provider-dirty-dot" aria-hidden="true"></span><span class="codex-usage-hud-settings-visually-hidden">有未保存修改</span>' : ""}
        </button>
      `;
    }).join("");
  }

  function settingsProviderEditorHtml(settings) {
    const draft = ensureSettingsProviderDraft(settings);
    const activeProvider = draft.activeProvider;
    const head = `
      <div class="codex-usage-hud-provider-editor-head">
        <div class="codex-usage-hud-price-title">模型单价</div>
        <div class="codex-usage-hud-provider-tabs" data-provider-tabs="true" role="tablist" aria-label="Provider">
          ${settingsProviderTabsHtml(settings)}
        </div>
        <div class="codex-usage-hud-price-unit">USD / 1M tokens</div>
      </div>
    `;
    const entry = draft.providers[activeProvider];
    if (!activeProvider || !entry) {
      return `${head}<div class="codex-usage-hud-provider-empty">尚未发现 Provider</div>`;
    }
    const providerSettings = entry.settings;
    const required = activeProvider === draft.appProvider;
    const meta = settingsProviderMeta(settings, activeProvider);
    const weeklyAdjustment = Number(providerSettings.weekly_adjustment_usd);
    const weeklyAdjustmentValue = Number.isFinite(weeklyAdjustment) && weeklyAdjustment > 0
      ? String(weeklyAdjustment)
      : "";
    const pricingUrlPlaceholder = "计费单价获取地址 · https://example.com/model-prices.json";
    return `
      ${head}
      <div class="codex-usage-hud-provider-context">
        <div class="codex-usage-hud-provider-scope-options">
          <label class="codex-usage-hud-provider-scope" ${required ? 'title="Codex App Provider 必须纳入统计"' : ""}>
            <input type="checkbox" data-provider-enabled="true" ${entry.enabled || required ? "checked" : ""} ${required ? "disabled" : ""}>
            <span>纳入统计</span>
          </label>
          <label class="codex-usage-hud-provider-scope" ${required ? 'title="Codex App Provider 必须纳入统计"' : ""}>
            <input type="checkbox" data-provider-notification-only="true" ${entry.notificationOnly && !required ? "checked" : ""} ${required ? "disabled" : ""}>
            <span>仅气泡通知不统计</span>
          </label>
          <div class="codex-usage-hud-provider-context-adjustment">
            <input data-setting-key="weekly_adjustment_usd" type="number" min="0" step="0.01" value="${escapeHtml(weeklyAdjustmentValue)}" placeholder="本周补充额度 USD" aria-label="本周补充额度 USD" title="本周补充额度 USD">
          </div>
        </div>
        <div class="codex-usage-hud-provider-meta" data-tone="${escapeHtml(meta.tone)}">${escapeHtml(meta.text)}</div>
      </div>
      <div class="codex-usage-hud-price-table">
        <div class="codex-usage-hud-price-header">
          <div>模型</div><div>输入</div><div>输出</div><div>缓存读取</div><div>缓存写入</div><div class="codex-usage-hud-price-advanced">渠道</div><div class="codex-usage-hud-price-advanced">Base URL</div>
        </div>
        <div data-price-rows="true">${priceRowsHtml(providerSettings)}</div>
        ${detectedPriceModelsHtml(providerSettings)}
        <div class="codex-usage-hud-price-actions">
          <button type="button" class="codex-usage-hud-settings-action" data-action="settings-add-model">添加模型</button>
          <input data-setting-key="pricing_url" value="${escapeHtml(providerSettings.pricing_url)}" placeholder="${escapeHtml(pricingUrlPlaceholder)}" aria-label="计费单价获取地址" title="${escapeHtml(pricingUrlPlaceholder)}">
          <button type="button" class="codex-usage-hud-settings-action" data-action="settings-fetch-prices">拉取</button>
        </div>
      </div>
    `;
  }

  function revealSettingsProviderTab(tab) {
    const tabs = tab?.parentElement;
    if (!tab || !tabs) return;
    const left = tab.offsetLeft;
    const right = left + tab.offsetWidth;
    if (left < tabs.scrollLeft) {
      tabs.scrollLeft = left;
    } else if (right > tabs.scrollLeft + tabs.clientWidth) {
      tabs.scrollLeft = right - tabs.clientWidth;
    }
  }

  function renderSettingsProviderTabs() {
    const tabs = document.querySelector(`#${settingsModalId} [data-provider-tabs="true"]`);
    if (!tabs || !settingsProviderDraft) return;
    tabs.innerHTML = settingsProviderTabsHtml(hudSettingsFromPayload());
    revealSettingsProviderTab(tabs.querySelector('[aria-selected="true"]'));
  }

  function captureSettingsProviderForm() {
    const modal = document.getElementById(settingsModalId);
    const editor = modal?.querySelector('[data-provider-editor="true"]');
    const activeProvider = String(editor?.dataset.activeProvider || "").trim().toLowerCase();
    const entry = settingsProviderDraft?.providers?.[activeProvider];
    if (!editor || !activeProvider || !entry) return "";
    const modelPrices = {};
    editor.querySelectorAll("[data-price-row='true']").forEach((row) => {
      const model = String(row.querySelector("[data-price-field='model']")?.value || "").trim();
      if (!model) return;
      const provider = String(row.querySelector("[data-price-field='provider']")?.value || "").trim().toLowerCase();
      const baseUrl = String(row.querySelector("[data-price-field='base_url']")?.value || "").trim().replace(/\/+$/, "");
      const field = (name) => {
        const value = Number(row.querySelector(`[data-price-field="${name}"]`)?.value);
        return Number.isFinite(value) && value >= 0 ? value : 0;
      };
      const key = provider ? `${provider}/${model}` : (baseUrl ? `${baseUrl}/${model}` : model);
      modelPrices[key] = {
        model,
        input: field("input"),
        output: field("output"),
        cached_input: field("cached_input"),
        cache_write: field("cache_write"),
      };
      if (provider) modelPrices[key].provider = provider;
      if (baseUrl) modelPrices[key].base_url = baseUrl;
    });
    const enabledNode = editor.querySelector('[data-provider-enabled="true"]');
    const notificationOnlyNode = editor.querySelector('[data-provider-notification-only="true"]');
    const pricingNode = editor.querySelector('[data-setting-key="pricing_url"]');
    const adjustmentNode = editor.querySelector('[data-setting-key="weekly_adjustment_usd"]');
    const adjustment = Number(adjustmentNode?.value);
    entry.enabled = activeProvider === settingsProviderDraft.appProvider || !!enabledNode?.checked;
    entry.notificationOnly = !entry.enabled && !!notificationOnlyNode?.checked;
    entry.settings = {
      ...entry.settings,
      model_prices: modelPrices,
      pricing_url: String(pricingNode?.value || "").trim(),
      weekly_adjustment_usd: Number.isFinite(adjustment) && adjustment >= 0 ? adjustment : 0,
    };
    return activeProvider;
  }

  function updateSettingsProviderDraftStatus() {
    const count = settingsDirtyProviders.size;
    if (count) setSettingsStatus(`${count} 个 Provider 有未保存修改`);
  }

  function markSettingsProviderDirty() {
    const activeProvider = captureSettingsProviderForm();
    if (!activeProvider) return;
    settingsDirtyProviders.add(activeProvider);
    renderSettingsProviderTabs();
    updateSettingsProviderDraftStatus();
  }

  function renderSettingsProviderEditor({ focusTab = false } = {}) {
    const editor = document.querySelector(`#${settingsModalId} [data-provider-editor="true"]`);
    if (!editor || !settingsProviderDraft) return;
    editor.dataset.activeProvider = settingsProviderDraft.activeProvider;
    editor.innerHTML = settingsProviderEditorHtml(hudSettingsFromPayload());
    const activeTab = editor.querySelector('[data-provider-tab="true"][aria-selected="true"]');
    revealSettingsProviderTab(activeTab);
    if (focusTab) activeTab?.focus?.();
    updateSettingsProviderDraftStatus();
  }

  function switchSettingsProvider(provider, { focusTab = false } = {}) {
    const nextProvider = String(provider || "").trim().toLowerCase();
    if (!settingsProviderDraft?.order.includes(nextProvider) || nextProvider === settingsProviderDraft.activeProvider) return;
    captureSettingsProviderForm();
    settingsProviderDraft.activeProvider = nextProvider;
    window[settingsProviderName] = nextProvider;
    renderSettingsProviderEditor({ focusTab });
  }

  function fileManagementFromPayload() {
    const value = currentPayload()?.fileManagement;
    return value && typeof value === "object" ? value : {
      rootLabel: "CODEX_HOME", generatedAt: "", revision: "",
      totals: { bytes: 0, files: 0, items: 0 }, categories: [], items: [],
      operation: { state: "idle", progress: 0, error: "" },
    };
  }

  function storageFormatBytes(value) {
    let bytes = Math.max(0, Number(value) || 0);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let index = 0;
    while (bytes >= 1024 && index < units.length - 1) { bytes /= 1024; index += 1; }
    return `${bytes >= 10 || index === 0 ? bytes.toFixed(0) : bytes.toFixed(1)} ${units[index]}`;
  }

  function storagePolicyLabel(policy) {
    return ({ candidate: "可候选", managed: "官方动作", blocked: "禁止直接删", unknown: "未知保护" })[policy] || "保护";
  }

  function storageOperationLabel(operation) {
    return ({ idle: "尚未生成清理计划", running: "正在处理存储操作", accepted: "存储操作已排队", cancelling: "正在取消存储操作", preview: "清理预览已生成", queued_exit: "已加入退出后队列", completed: "存储操作已完成", partial: "存储操作部分完成", cancelled: "存储操作已取消", failed: "存储操作失败" })[String(operation?.state || "idle")] || "存储状态待更新";
  }

  function legacyStoragePanelHtml(data) {
    const totals = data?.totals && typeof data.totals === "object" ? data.totals : {};
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const allItems = Array.isArray(data?.items) ? data.items : [];
    const items = storageFilter === "all" ? allItems : allItems.filter((item) => String(item?.policy || "unknown") === storageFilter);
    const categories = Array.isArray(data?.categories) ? data.categories : [];
    const previewItems = Array.isArray(operation?.items) ? operation.items : [];
    const managedAction = String(operation?.managedAction || "");
    const operationState = String(operation?.state || "idle");
    const visibleItems = items.slice(0, 160);
    const policyCounts = { candidate: 0, managed: 0, blocked: 0, unknown: 0 };
    allItems.forEach((item) => { const policy = String(item?.policy || "unknown"); if (policy in policyCounts) policyCounts[policy] += 1; });
    const categoryHtml = categories
      .filter((category) => storageFilter === "all" || String(category?.policy || "unknown") === storageFilter)
      .map((category) => {
        const policy = String(category?.policy || "unknown");
        return `<div class="codex-usage-hud-storage-category" data-policy="${escapeHtml(policy)}"><div class="codex-usage-hud-storage-category-main"><div class="codex-usage-hud-storage-category-title"><span class="codex-usage-hud-storage-marker" data-policy="${escapeHtml(policy)}"></span>${escapeHtml(category?.category || "未知")}</div><div class="codex-usage-hud-storage-meta">${escapeHtml(category?.reason || "受保护")}</div></div><span class="codex-usage-hud-storage-policy" data-policy="${escapeHtml(policy)}">${escapeHtml(storagePolicyLabel(policy))}</span><span class="codex-usage-hud-storage-size">${storageFormatBytes(category?.size)}</span></div>`;
      }).join("");
    const itemHtml = visibleItems.map((item) => {
      const policy = String(item?.policy || "unknown");
      const id = String(item?.id || "");
      const actions = Array.isArray(item?.allowedActions) ? item.allowedActions : [];
      const selector = policy === "candidate" ? `<input type="checkbox" data-storage-item-id="${escapeHtml(id)}" aria-label="选择 ${escapeHtml(item?.relativePath || "候选项")}">` : "<span aria-hidden=\"true\"></span>";
      const managedButtons = policy === "managed" && actions.length ? `<span class="codex-usage-hud-storage-item-actions">${actions.map((managedAction) => `<button type="button" class="codex-usage-hud-storage-item-action" data-action="storage-managed-preview" data-storage-managed-action="${escapeHtml(managedAction)}" data-storage-item-id="${escapeHtml(id)}">${escapeHtml(managedAction === "logout" ? "官方退出" : managedAction === "remove_plugin" ? "官方移除" : managedAction === "archive_session" ? "官方归档" : "官方删除")}</button>`).join("")}</span>` : `<button type="button" class="codex-usage-hud-storage-item-action" disabled>${escapeHtml(storagePolicyLabel(policy))}</button>`;
      return `<div class="codex-usage-hud-storage-item" data-policy="${escapeHtml(policy)}">${selector}<div class="codex-usage-hud-storage-item-main"><div class="codex-usage-hud-storage-item-path" title="${escapeHtml(item?.relativePath || "")}">${escapeHtml(item?.relativePath || "未知路径")}</div><div class="codex-usage-hud-storage-meta">${escapeHtml(item?.reason || "")}</div></div><span class="codex-usage-hud-storage-policy" data-policy="${escapeHtml(policy)}">${escapeHtml(storagePolicyLabel(policy))}</span><span class="codex-usage-hud-storage-size">${storageFormatBytes(item?.size)}</span>${managedButtons}</div>`;
    }).join("");
    const previewHtml = operationState === "preview" && !storagePreviewHidden ? `<section class="codex-usage-hud-storage-preview" aria-live="polite"><div class="codex-usage-hud-storage-preview-head"><div><strong>清理预览</strong><div class="codex-usage-hud-storage-muted">${previewItems.length} 个项，预计释放 ${storageFormatBytes(operation?.bytes)}</div></div><span class="codex-usage-hud-storage-status" data-state="completed">Dry run</span></div><div class="codex-usage-hud-storage-preview-list">${previewItems.map((item) => `<div class="codex-usage-hud-storage-preview-row"><span class="codex-usage-hud-storage-item-path">${escapeHtml(item?.relativePath || "")}</span><span class="codex-usage-hud-storage-muted">${storageFormatBytes(item?.size)}</span></div>`).join("")}</div><p class="codex-usage-hud-storage-note">Codex 运行期间不会直接修改原始文件；执行前会再次检查 revision、锁定状态和路径指纹。</p><div class="codex-usage-hud-storage-preview-foot"><button type="button" class="codex-usage-hud-settings-link" data-action="storage-clear-preview">取消预览</button><button type="button" class="codex-usage-hud-settings-action" data-action="storage-confirm-preview" data-primary="true">${escapeHtml(managedAction ? "确认官方动作" : "加入退出后队列")}</button></div></section>` : "";
    const operationNote = operation?.error ? `：${operation.error}` : operationState === "queued_exit" ? "；Codex 完全退出后才会执行" : "";
    return `<div class="codex-usage-hud-storage"><div class="codex-usage-hud-storage-pathbar"><div class="codex-usage-hud-storage-path">${escapeHtml(data?.rootLabel || "CODEX_HOME")}</div><span class="codex-usage-hud-storage-status" data-state="${escapeHtml(operationState)}">${escapeHtml(storageOperationLabel(operation) + operationNote)}</span></div><div class="codex-usage-hud-storage-summary"><div class="codex-usage-hud-storage-summary-main"><p class="codex-usage-hud-storage-summary-value">${storageFormatBytes(totals?.bytes)}</p><p class="codex-usage-hud-storage-summary-label">已发现的 Codex 数据</p></div><div><p class="codex-usage-hud-storage-summary-value">${Number(totals?.files || 0).toLocaleString()}</p><p class="codex-usage-hud-storage-summary-label">文件</p></div><div><p class="codex-usage-hud-storage-summary-value">${Number(totals?.items || 0).toLocaleString()}</p><p class="codex-usage-hud-storage-summary-label">管理项</p></div></div><div class="codex-usage-hud-storage-filters" role="tablist" aria-label="文件风险筛选">${[["all", "总览"], ["candidate", "可候选"], ["managed", "官方动作"], ["blocked", "受保护"], ["unknown", "未知"]].map(([key, label]) => `<button type="button" class="codex-usage-hud-storage-filter" data-action="storage-filter" data-storage-filter="${key}" data-active="${storageFilter === key}">${label} ${key === "all" ? "" : `(${policyCounts[key]})`}</button>`).join("")}</div><div class="codex-usage-hud-storage-categories" aria-live="polite">${categoryHtml || '<div class="codex-usage-hud-storage-empty">尚未扫描，点击“重新扫描”查看本地元数据。</div>'}</div><div class="codex-usage-hud-storage-items">${itemHtml || '<div class="codex-usage-hud-storage-empty">当前筛选没有可展示的管理项。</div>'}</div>${visibleItems.length < items.length ? `<div class="codex-usage-hud-storage-muted">仅显示前 ${visibleItems.length} 项；扫描不会自动重复。</div>` : ""}${previewHtml}<p class="codex-usage-hud-storage-note">只显示相对路径和元数据；未知项、凭据、配置、SQLite、会话原始文件、活动插件运行时和 reparse point 不提供直接删除。</p></div>`;
  }

  function typedSettingsRequestId(prefix) {
    return `${String(prefix || "request")}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
  }

  function usageInsightsFromPayload() {
    if (usageInsightsState.data && typeof usageInsightsState.data === "object") {
      return usageInsightsState.data;
    }
    const value = currentPayload()?.usageInsights;
    return value && typeof value === "object" && Object.keys(value).length ? value : null;
  }

  function safeCleanupFromPayload() {
    if (safeCleanupState.data && typeof safeCleanupState.data === "object") {
      return safeCleanupState.data;
    }
    const value = currentPayload()?.safeCleanup;
    return value && typeof value === "object" && Object.keys(value).length ? value : null;
  }

  function sessionCleanupFromPayload() {
    if (sessionCleanupState.data && typeof sessionCleanupState.data === "object") {
      return sessionCleanupState.data;
    }
    const value = currentPayload()?.sessionCleanup;
    return value && typeof value === "object" && Object.keys(value).length ? value : null;
  }

  function usageInsightsFormatTokens(value) {
    const amount = Math.max(0, Number(value) || 0);
    if (amount >= 1000000) return `${(amount / 1000000).toFixed(amount >= 10000000 ? 0 : 1)}M`;
    if (amount >= 1000) return `${(amount / 1000).toFixed(amount >= 100000 ? 0 : 1)}k`;
    return Math.round(amount).toLocaleString();
  }

  function usageInsightsFormatCost(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "费用待估";
    const amount = Math.max(0, Number(value));
    return `$${amount < 1 ? amount.toFixed(3) : amount.toFixed(2)}`;
  }

  function usageInsightsFormatRatio(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "--";
    const raw = Number(value);
    const ratio = raw > 1 ? raw / 100 : raw;
    return `${Math.round(Math.max(0, Math.min(1, ratio)) * 100)}%`;
  }

  function usageInsightsRangeItem(item, range) {
    if (!item || typeof item !== "object") return {};
    const scoped = item[range];
    if (scoped && typeof scoped === "object") return { ...item, ...scoped };
    const prefix = range === "week" ? "week" : "today";
    return {
      ...item,
      tokens: item[`${prefix}Tokens`] ?? item.tokens,
      costUsd: item[`${prefix}CostUsd`] ?? item.costUsd,
      cacheRatio: item[`${prefix}CacheRatio`] ?? item.cacheRatio,
      requestCount: item[`${prefix}RequestCount`] ?? item.requestCount,
    };
  }

  function usageInsightsRangeData(data, range) {
    const scoped = data?.[range] && typeof data[range] === "object" ? data[range] : {};
    const totals = scoped?.totals && typeof scoped.totals === "object"
      ? scoped.totals
      : (data?.[`${range}Totals`] && typeof data[`${range}Totals`] === "object"
        ? data[`${range}Totals`]
        : (data?.totals?.[range] || data?.totals || {}));
    const list = (name) => {
      const values = Array.isArray(scoped?.[name]) ? scoped[name] : (Array.isArray(data?.[name]) ? data[name] : []);
      return values.map((item) => usageInsightsRangeItem(item, range));
    };
    return {
      totals,
      sessions: list("sessions"),
      topSessionsByUsage: list("topSessionsByUsage"),
      topSessionsByCost: list("topSessionsByCost"),
      models: list("models"),
      providers: list("providers"),
      background: scoped?.background || data?.background || {},
      costCoverage: scoped?.costCoverage || totals?.costCoverage || data?.costCoverage || {},
    };
  }

  function usageInsightsRankLabel(item, kind) {
    if (kind === "sessions") return String(item?.title || item?.name || item?.sessionTitle || item?.id || item?.sessionId || "未命名会话");
    if (kind === "models") return String(item?.model || item?.name || "未知模型");
    return String(item?.provider || item?.name || "未知 Provider");
  }

  function usageInsightsSessionModelNames(session) {
    const values = Array.isArray(session?.models) ? session.models : [];
    const names = values.map((item) => String(
      item && typeof item === "object" ? (item.model || item.name || "") : item,
    ).trim()).filter(Boolean);
    return Array.from(new Set(names));
  }

  function usageInsightsSessionModelSummary(session, limit = 2) {
    const names = usageInsightsSessionModelNames(session);
    if (!names.length) return "未知模型";
    const visible = names.slice(0, Math.max(1, Number(limit) || 1));
    return `${visible.join(" + ")}${names.length > visible.length ? ` +${names.length - visible.length}` : ""}`;
  }

  function usageInsightsRankingRowsHtml(items, kind, {
    limit = 5,
    metric = "tokens",
    selectedSessionId = "",
    sessionAction = "usage-insights-session",
  } = {}) {
    return (Array.isArray(items) ? items : []).slice(0, limit).map((item, index) => {
      const sessionId = String(item?.id || item?.sessionId || "");
      const label = usageInsightsRankLabel(item, kind);
      const actionable = kind === "sessions"
        && (item?.actionable === true || item?.canActivate === true)
        && !!sessionId;
      const selectable = kind === "sessions"
        && !!sessionId
        && sessionAction !== "usage-insights-session";
      const opensSession = actionable && sessionAction === "usage-insights-session";
      const tag = selectable || opensSession ? "button" : "div";
      const action = selectable || opensSession ? sessionAction : "";
      const actionAttrs = action
        ? ` type="button" data-action="${escapeHtml(action)}" data-session-id="${escapeHtml(sessionId)}" data-selected="${String(sessionId === selectedSessionId)}" aria-label="${escapeHtml(selectable ? `查看会话 ${label}` : `打开会话 ${label}`)}"`
        : "";
      const provider = String(item?.provider || "").trim();
      const workdir = String(item?.workdirName || "").trim();
      const modelText = usageInsightsSessionModelSummary(item);
      const cache = usageInsightsFormatRatio(item?.cacheRatio);
      const coverage = item?.costCoverage && typeof item.costCoverage === "object"
        ? item.costCoverage
        : {};
      const incompleteCost = coverage?.hasCompleteCost === false;
      const hasEstimatedCost = item?.costUsd !== null && item?.costUsd !== undefined;
      const costCoverageNote = incompleteCost
        ? (hasEstimatedCost ? "费用部分可估" : "费用不可估")
        : "";
      const latestEventAt = String(item?.latestEventAt || "");
      const meta = [
        `工作目录 ${workdir || "--"}`,
        `模型 ${modelText}`,
        provider,
        latestEventAt ? backgroundUsageTime(latestEventAt, { compact: true }) : "",
        costCoverageNote,
      ].filter(Boolean).join(" · ");
      const tokens = `${usageInsightsFormatTokens(item?.tokens ?? item?.totalTokens)} tokens`;
      const cost = usageInsightsFormatCost(item?.costUsd);
      const cacheText = cache === "--" ? "缓存 --" : `缓存 ${cache}`;
      const rankLabel = metric === "cost" ? "金额排名" : "用量排名";
      return `
        <${tag}${actionAttrs} class="codex-usage-hud-background-event codex-usage-hud-session-ranking-row">
          <span class="codex-usage-hud-background-event-head">
            <span class="codex-usage-hud-background-event-title" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
            <span class="codex-usage-hud-background-status" title="${escapeHtml(rankLabel)}">#${index + 1}</span>
          </span>
          <span class="codex-usage-hud-background-event-meta" title="${escapeHtml(meta)}">${escapeHtml(meta)}</span>
          <span class="codex-usage-hud-background-event-totals">
            <strong title="${escapeHtml(tokens)}">${escapeHtml(tokens)}</strong>
            <span title="${escapeHtml(cacheText)}">${escapeHtml(cacheText)}</span>
            <span title="${escapeHtml(cost)}">${escapeHtml(cost)}</span>
          </span>
        </${tag}>
      `;
    }).join("");
  }

  function cleanupIconSvg(name, extraClass = "") {
    const paths = {
      scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="11" cy="11" r="4"/><path d="m16 16 3 3"/>',
      trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v5M14 11v5"/>',
      refresh: '<path d="M20 11a8 8 0 0 0-14.9-4M4 4v6h6M4 13a8 8 0 0 0 14.9 4M20 20v-6h-6"/>',
      shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
      search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
      check: '<path d="m5 12 4 4L19 6"/>',
      alert: '<path d="m21 19-9-16-9 16h18Z"/><path d="M12 9v4M12 17h.01"/>',
      chevron: '<path d="m9 18 6-6-6-6"/>',
      copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
      folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/><path d="M2 10h20"/>',
    };
    const body = paths[String(name || "")] || paths.scan;
    const klass = ["codex-usage-hud-cleanup-icon", extraClass].filter(Boolean).join(" ");
    return `<svg class="${klass}" viewBox="0 0 24 24" aria-hidden="true">${body}</svg>`;
  }

  const safeCleanupDeepGroupId = "__safe_cleanup_deep__";
  const safeCleanupProtectedGroupId = "__safe_cleanup_protected__";

  function safeCleanupRawItems(data = safeCleanupFromPayload()) {
    return Array.isArray(data?.groups) ? data.groups : [];
  }

  function safeCleanupItemIsExecutable(item) {
    const tier = String(item?.tier || "protected");
    return !!String(item?.id || "")
      && !String(item?.blockedReason || "")
      && (tier === "safe" || tier === "consent");
  }

  function safeCleanupPresentationKey(item) {
    const relatedProcesses = Array.isArray(item?.relatedProcesses)
      ? item.relatedProcesses.map((value) => String(value || "")).sort()
      : [];
    return JSON.stringify([
      String(item?.category || ""),
      String(item?.tier || "protected"),
      String(item?.label || ""),
      String(item?.retention || ""),
      String(item?.impact || ""),
      String(item?.blockedReason || ""),
      item?.requiresOffline === true,
      item?.requiresBackup === true,
      item?.requiresCodexClose === true,
      relatedProcesses,
    ]);
  }

  function safeCleanupAggregateResultState(entries) {
    const states = entries.map((entry) => String(entry?.state || "").toLowerCase()).filter(Boolean);
    if (!states.length) return "";
    const unique = new Set(states);
    if (unique.has("failed")) return unique.size === 1 ? "failed" : "partial";
    if (unique.has("skipped")) return unique.size === 1 ? "skipped" : "partial";
    if (unique.has("running")) return "running";
    if (unique.has("selected")) return "selected";
    if (unique.has("restored")) return unique.size === 1 ? "restored" : "partial";
    if ([...unique].every((state) => state === "completed" || state === "deleted")) return "completed";
    return states[0];
  }

  function safeCleanupPresentationGroups(data = safeCleanupFromPayload(), {
    itemIds = null,
    results = [],
  } = {}) {
    const selected = Array.isArray(itemIds)
      ? new Set(itemIds.map((id) => String(id || "")).filter(Boolean))
      : null;
    const resultById = new Map((Array.isArray(results) ? results : [])
      .map((result) => [String(result?.id || ""), result])
      .filter(([id]) => !!id));
    const grouped = new Map();
    safeCleanupRawItems(data).forEach((item, sourceIndex) => {
      const itemId = String(item?.id || "");
      if (!itemId || (selected && !selected.has(itemId))) return;
      const key = safeCleanupPresentationKey(item);
      let group = grouped.get(key);
      if (!group) {
        group = {
          id: key,
          presentationId: key,
          sourceIndex,
          category: String(item?.category || ""),
          tier: String(item?.tier || "protected"),
          label: String(item?.label || ""),
          retention: String(item?.retention || ""),
          impact: String(item?.impact || ""),
          blockedReason: String(item?.blockedReason || ""),
          requiresOffline: item?.requiresOffline === true,
          requiresBackup: item?.requiresBackup === true,
          requiresCodexClose: item?.requiresCodexClose === true,
          relatedProcesses: Array.isArray(item?.relatedProcesses) ? [...item.relatedProcesses] : [],
          entries: [],
          itemIds: [],
          executableIds: [],
          bytes: 0,
          files: 0,
          items: 0,
          targetCount: 0,
          actualBytes: 0,
          deletedRows: 0,
          hasResults: false,
          oldestModifiedAt: "",
          newestModifiedAt: "",
          state: "",
        };
        grouped.set(key, group);
      }
      const result = resultById.get(itemId);
      const entry = result ? { ...item, ...result, id: itemId } : { ...item, id: itemId };
      group.entries.push(entry);
      group.itemIds.push(itemId);
      if (safeCleanupItemIsExecutable(item)) group.executableIds.push(itemId);
      group.bytes += Math.max(0, Number(item?.bytes || 0));
      group.files += Math.max(0, Number(item?.files || 0));
      group.targetCount += 1;
      group.items = group.targetCount;
      if (result) {
        group.hasResults = true;
        group.actualBytes += Math.max(0, Number(result?.actualBytes || 0));
        group.deletedRows += Math.max(0, Number(result?.deletedRows || 0));
      }
    });
    const tierOrder = { consent: 0, safe: 1, protected: 2 };
    return [...grouped.values()].map((group) => {
      group.entries.sort((left, right) => String(left?.path || "").localeCompare(String(right?.path || "")));
      const modified = group.entries
        .map((entry) => ({ raw: String(entry?.modifiedAt || ""), value: Date.parse(String(entry?.modifiedAt || "")) }))
        .filter((entry) => entry.raw && Number.isFinite(entry.value))
        .sort((left, right) => left.value - right.value);
      group.oldestModifiedAt = modified[0]?.raw || "";
      group.newestModifiedAt = modified[modified.length - 1]?.raw || "";
      group.state = safeCleanupAggregateResultState(group.entries);
      return group;
    }).sort((left, right) => (
      (tierOrder[String(left?.tier || "protected")] ?? 3)
      - (tierOrder[String(right?.tier || "protected")] ?? 3)
      || Number(left?.sourceIndex || 0) - Number(right?.sourceIndex || 0)
    ));
  }

  function safeCleanupGroups(data = safeCleanupFromPayload()) {
    return safeCleanupPresentationGroups(data);
  }

  function syncSafeCleanupSelection(data = safeCleanupFromPayload()) {
    const revision = String(data?.revision || "");
    if (!revision || isCleanupScanningRevision(revision)) return;
    const validIds = new Set(safeCleanupRawItems(data)
      .filter(safeCleanupItemIsExecutable)
      .map((item) => String(item.id)));
    if (safeCleanupState.inventoryRevision !== revision) {
      safeCleanupState.inventoryRevision = revision;
      safeCleanupState.selectedIds = new Set((Array.isArray(data?.defaultSelectedIds) ? data.defaultSelectedIds : [])
        .map((id) => String(id || ""))
        .filter((id) => validIds.has(id)));
      safeCleanupState.expandedGroupIds.clear();
    } else {
      safeCleanupState.selectedIds = new Set(
        [...safeCleanupState.selectedIds].filter((id) => validIds.has(id)),
      );
    }
    const itemById = new Map(safeCleanupRawItems(data).map((item) => [String(item?.id || ""), item]));
    safeCleanupState.includeConsent = [...safeCleanupState.selectedIds]
      .some((id) => String(itemById.get(id)?.tier || "") === "consent");
  }

  function safeCleanupSelectedGroupIds(data = safeCleanupFromPayload()) {
    const validIds = new Set(safeCleanupRawItems(data)
      .filter(safeCleanupItemIsExecutable)
      .map((item) => String(item.id)));
    return [...safeCleanupState.selectedIds].filter((id) => validIds.has(id));
  }

  function safeCleanupPreviewMatchesSelection(data = safeCleanupFromPayload()) {
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const previewIds = Array.isArray(operation?.selectedIds)
      ? operation.selectedIds.map((id) => String(id || "")).filter(Boolean)
      : [];
    const selectedIds = safeCleanupSelectedGroupIds(data);
    if (previewIds.length !== selectedIds.length) return false;
    const previewSet = new Set(previewIds);
    return selectedIds.every((id) => previewSet.has(id));
  }

  function safeCleanupRequiresOffline(data = safeCleanupFromPayload()) {
    const selected = new Set(safeCleanupSelectedGroupIds(data));
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const operationBound = !safeCleanupState.previewHidden && safeCleanupPreviewMatchesSelection(data);
    return (operationBound && operation?.requiresOffline === true)
      || safeCleanupRawItems(data).some((item) => selected.has(String(item?.id || "")) && item?.requiresOffline === true);
  }

  function safeCleanupRequiresBackup(data = safeCleanupFromPayload()) {
    const selected = new Set(safeCleanupSelectedGroupIds(data));
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const operationBound = !safeCleanupState.previewHidden && safeCleanupPreviewMatchesSelection(data);
    return (operationBound && operation?.requiresBackup === true)
      || safeCleanupRawItems(data).some((item) => selected.has(String(item?.id || "")) && item?.requiresBackup === true);
  }

  function safeCleanupRequiresCodexClose(data = safeCleanupFromPayload()) {
    const selected = new Set(safeCleanupSelectedGroupIds(data));
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const operationBound = !safeCleanupState.previewHidden && safeCleanupPreviewMatchesSelection(data);
    return (operationBound && operation?.requiresCodexClose === true)
      || safeCleanupRawItems(data).some((item) => selected.has(String(item?.id || "")) && item?.requiresCodexClose === true);
  }

  function safeCleanupTierLabel(tier) {
    return ({ safe: "可直接清理", consent: "确认后清理", protected: "始终保护" })[String(tier || "protected")] || "始终保护";
  }

  function safeCleanupDisplayLabel(group) {
    const raw = String(group?.label || "").trim();
    const category = String(group?.category || "").trim();
    const labels = {
      "Expired user temporary data": "过期用户临时数据",
      "NuGet package cache": "NuGet 包缓存",
      "npm download cache": "npm 下载缓存",
      "pip download cache": "pip 下载缓存",
      "Yarn download cache": "Yarn 下载缓存",
      "pnpm store cache": "pnpm 存储缓存",
      "Bun install cache": "Bun 安装缓存",
      "Go module cache": "Go 模块缓存",
      "Cargo registry cache": "Cargo 注册表缓存",
      "Cargo git checkout cache": "Cargo Git 检出缓存",
      "Gradle dependency cache": "Gradle 依赖缓存",
      "Maven local repository": "Maven 本地仓库",
      "uv download cache": "uv 下载缓存",
      "Poetry cache": "Poetry 缓存",
      "Composer package cache": "Composer 包缓存",
      "Hugging Face model cache": "Hugging Face 模型缓存",
      "PyTorch hub cache": "PyTorch 模型缓存",
      "ModelScope model cache": "ModelScope 模型缓存",
      "Ollama model weights": "Ollama 模型权重",
      "Playwright browser cache": "Playwright 浏览器缓存",
      "Cypress binary cache": "Cypress 二进制缓存",
      "Electron download cache": "Electron 下载缓存",
      "ccache compiler cache": "ccache 编译缓存",
      "sccache compiler cache": "sccache 编译缓存",
      "Android SDK cache": "Android SDK 缓存",
      "Android SDK temporary cache": "Android SDK 临时缓存",
      "Scoop package cache": "Scoop 包缓存",
      "Homebrew download cache": "Homebrew 下载缓存",
      "Visual Studio Code cache": "Visual Studio Code 缓存",
      "Visual Studio cache": "Visual Studio 缓存",
      "JetBrains IDE cache": "JetBrains IDE 缓存",
      "Cursor cache": "Cursor 缓存",
      "Discord cache": "Discord 缓存",
      "Slack cache": "Slack 缓存",
      "Xcode derived data": "Xcode 派生数据",
      "DirectX shader cache": "DirectX 着色器缓存",
      "GPU shader cache": "GPU 着色器缓存",
      "Windows thumbnail cache": "Windows 缩略图缓存",
      "Recycle Bin items": "回收站项目",
      "Trash items": "废纸篓项目",
      "Chrome cache": "Chrome 缓存",
      "Edge cache": "Edge 缓存",
      "Brave cache": "Brave 缓存",
      "Firefox cache": "Firefox 缓存",
      "Old Windows crash dumps": "Windows 旧崩溃转储",
      "Old Windows error reports": "Windows 旧错误报告",
      "Old queued Windows error reports": "Windows 待处理旧错误报告",
      "Old macOS diagnostic reports": "macOS 旧诊断报告",
      "Old macOS crash reports": "macOS 旧崩溃报告",
      "HUD diagnostics": "HUD 诊断日志",
      "HUD overlay history": "HUD 气泡历史",
      "Old cleanup backup": "旧清理备份",
      "Protected HUD data": "受保护的 HUD 数据",
      "HUD runtime data": "HUD 运行数据",
      "Expired Codex temporary data": "过期 Codex 临时数据",
      "Protected Codex temporary data": "受保护的 Codex 临时数据",
      "Old Codex diagnostics": "Codex 旧诊断历史",
      "Old background usage history": "旧后台用量历史",
      "Retained local history": "保留中的本地历史",
      "Protected SQLite history": "受保护的 SQLite 历史",
    };
    const categoryLabels = {
      user_temp: "用户临时数据",
      developer_cache: "开发工具缓存",
      editor_cache: "编辑器缓存",
      system_cache: "系统可再生成缓存",
      browser_cache: "浏览器缓存",
      diagnostic_history: "系统诊断历史",
      codex_temp: "Codex 临时数据",
      hud_diagnostics: "HUD 诊断日志",
      hud_overlay_history: "HUD 气泡历史",
      cleanup_backups: "旧清理备份",
      codex_logs_history: "Codex 旧诊断历史",
      background_usage_history: "旧后台用量历史",
      sqlite_history: "SQLite 历史",
    };
    return labels[raw] || categoryLabels[category] || raw || category || "清理项";
  }

  function safeCleanupDisplayImpact(group) {
    const raw = String(group?.blockedReason || group?.impact || "").trim();
    const impacts = {
      "Applications may recreate temporary files.": "应用可能会按需重新生成临时文件。",
      "Packages may need to be downloaded again.": "后续使用时可能需要重新下载软件包。",
      "Modules may need to be downloaded again.": "后续使用时可能需要重新下载模块。",
      "Crates may need to be downloaded again.": "后续使用时可能需要重新下载 crate。",
      "Git dependencies may need to be fetched again.": "后续使用时可能需要重新获取 Git 依赖。",
      "Dependencies may need to be downloaded again.": "后续使用时可能需要重新下载依赖。",
      "Artifacts may need to be downloaded again.": "后续使用时可能需要重新下载构件。",
      "Models and datasets may need to be downloaded again.": "后续使用时可能需要重新下载模型和数据集。",
      "Models may need to be downloaded again.": "后续使用时可能需要重新下载模型。",
      "Local models may need to be downloaded again.": "后续使用时可能需要重新下载本地模型。",
      "Browser binaries may need to be downloaded again.": "后续使用时可能需要重新下载浏览器二进制文件。",
      "Electron binaries may need to be downloaded again.": "后续使用时可能需要重新下载 Electron 二进制文件。",
      "Compilations may take longer until the cache is rebuilt.": "在缓存重建前，编译可能会变慢。",
      "Android tooling may rebuild cache data.": "Android 工具链可能会重建缓存数据。",
      "Android tooling may recreate temporary downloads.": "Android 工具链可能会重新创建临时下载。",
      "Graphics applications may rebuild shader data on next use.": "图形应用下次使用时会重新生成着色器缓存。",
      "The editor may rebuild cached UI and code data.": "编辑器下次使用时会重建界面和代码缓存。",
      "The IDE may rebuild indexes and local caches.": "IDE 下次使用时会重建索引和本地缓存。",
      "The application may rebuild cached UI data.": "应用下次使用时会重建界面缓存。",
      "Explorer may rebuild thumbnails on next browse.": "资源管理器下次浏览时会重建缩略图。",
      "Deleted files in the Recycle Bin will be permanently removed.": "回收站中的已删除文件将被永久移除。",
      "Deleted files in Trash will be permanently removed.": "废纸篓中的已删除文件将被永久移除。",
      "Visual Studio may rebuild component and image caches.": "Visual Studio 下次使用时会重建组件和图像缓存。",
      "Visual Studio may rebuild cached data.": "Visual Studio 下次使用时会重建缓存。",
      "Xcode may rebuild indexes and build products.": "Xcode 下次使用时会重建索引和构建产物。",
      "Pages and shaders may be cached again on next use.": "浏览器下次使用时会重新生成页面和着色器缓存。",
      "Pages may be cached again on next use.": "浏览器下次使用时会重新生成页面缓存。",
      "Old operating-system diagnostics will no longer be available.": "清理后将无法再用这些旧系统报告排查问题。",
      "Old HUD diagnostics will no longer be available.": "清理后将无法查看这些旧 HUD 诊断日志。",
      "Protected HUD data remains unchanged.": "HUD 配置、状态和受保护数据保持不变。",
      "Codex may recreate temporary staging data when needed.": "Codex 可能会在需要时重新生成临时暂存数据。",
      "Codex temporary data remains unchanged.": "Codex 临时数据保持不变。",
      "A previous local cleanup backup will be removed.": "将删除一份超过保留期的旧清理备份。",
      "History older than the retention period will be permanently removed.": "保留期之前的历史将永久删除。",
      "No history is older than the configured retention period.": "当前没有超过保留期的历史。",
      "The database remains unchanged.": "数据库保持不变。",
      "A related application is currently running.": "相关应用正在运行，当前保持不变。",
      "Cleanup data contains files newer than the retention threshold.": "包含未超过保留期的项目，当前保持不变。",
      "Cleanup data contains a reparse point or unreadable entry.": "包含重解析点或无法读取的项目，当前保持不变。",
      "Cache data contains a reparse point or unreadable entry.": "缓存包含重解析点或无法读取的项目，当前保持不变。",
      "Cache root could not be verified.": "无法完整验证缓存目录，当前保持不变。",
    };
    return impacts[raw] || raw || safeCleanupTierLabel(group?.tier);
  }

  function safeCleanupOperationLabel(operation) {
    const state = String(operation?.state || "idle");
    if (state === "completed" && operation?.action === "scan") return "扫描完成";
    if (state === "failed") {
      const error = String(operation?.error || "");
      if (error.includes("清理已取消") || error.includes("未修改任何数据") || error.includes("未执行")) {
        return "已取消";
      }
    }
    return ({ idle: "等待扫描", scanning: "正在扫描", accepted: "请求已提交", preview: "清理预览", running: "正在清理", queued_exit: "等待退出", completed: "清理完成", partial: "部分完成（部分项已跳过）", cancelled: "已取消", failed: "需要处理", restored: "已从备份恢复" })[state] || "状态更新中";
  }

  function safeCleanupResultStateLabel(state) {
    return ({ selected: "等待清理", running: "清理中", completed: "已完成", deleted: "已删除", skipped: "已跳过", failed: "未完成", partial: "部分完成", restored: "已恢复" })[String(state || "")] || String(state || "状态未知");
  }

  function safeCleanupRetention(group) {
    const settings = hudSettingsFromPayload();
    const category = String(group?.category || group?.id || "").toLowerCase();
    if (category === "background_usage_history") return `保留 ${Math.max(1, Number(settings.cleanup_background_retention_days || 30))} 天`;
    if (category === "codex_logs_history") return `保留 ${Math.max(1, Number(settings.cleanup_log_retention_hours || 24))} 小时`;
    const retention = String(group?.retention || "").trim();
    const days = retention.match(/^(\d+) days?$/i);
    if (days) return `保留 ${days[1]} 天`;
    const hours = retention.match(/^(\d+) hours?$/i);
    if (hours) return `保留 ${hours[1]} 小时`;
    const seconds = retention.match(/^(\d+) seconds?$/i);
    if (seconds) return `保留 ${seconds[1]} 秒`;
    if (retention) return retention;
    return "按安全策略";
  }

  function safeCleanupBackupLocationLabel(operation) {
    const volumeLabels = Array.isArray(operation?.backupVolumeLabels)
      ? operation.backupVolumeLabels.map((value) => String(value || "").trim()).filter(Boolean)
      : [];
    const directoryLabels = Array.isArray(operation?.backupDirectoryLabels)
      ? operation.backupDirectoryLabels.map((value) => String(value || "").trim()).filter(Boolean)
      : [];
    const volume = String(operation?.backupVolumeLabel || volumeLabels.join("、") || "").trim();
    const directory = String(operation?.backupDirectoryLabel || operation?.backupLabel || directoryLabels.join("、") || "").trim();
    return [volume ? `卷 ${volume}` : "", directory && directory !== volume ? `目录 ${directory}` : ""].filter(Boolean).join(" · ");
  }

  function safeCleanupPreviewSpaceSummary(operation) {
    const estimated = Math.max(0, Number(operation?.estimatedBytes ?? operation?.bytes ?? 0));
    const backupBytes = Math.max(0, Number(operation?.backupBytes ?? 0));
    const sameVolumeBytes = Math.max(0, Number(operation?.sameVolumeBackupBytes ?? 0));
    const netEstimated = Math.max(0, Number(operation?.netEstimatedBytes ?? estimated));
    const location = safeCleanupBackupLocationLabel(operation);
    if (!backupBytes) return `预计释放 ${storageFormatBytes(estimated)}`;
    if (!sameVolumeBytes) {
      return `源盘预计释放 ${storageFormatBytes(netEstimated)} · 备份 ${storageFormatBytes(backupBytes)} 存到其他磁盘${location ? `（${location}）` : ""}，不占用源盘`;
    }
    if (sameVolumeBytes >= backupBytes) {
      return `预计清理 ${storageFormatBytes(estimated)} · 同盘备份占用 ${storageFormatBytes(backupBytes)} · 源盘预计净释放 ${storageFormatBytes(netEstimated)}`;
    }
    return `预计清理 ${storageFormatBytes(estimated)} · 备份共 ${storageFormatBytes(backupBytes)}（同卷 ${storageFormatBytes(sameVolumeBytes)}） · 源盘预计净释放 ${storageFormatBytes(netEstimated)}`;
  }

  function safeCleanupConfirmSpaceSummary(operation) {
    const estimated = Math.max(0, Number(operation?.estimatedBytes ?? operation?.bytes ?? 0));
    const backupBytes = Math.max(0, Number(operation?.backupBytes ?? 0));
    const sameVolumeBytes = Math.max(0, Number(operation?.sameVolumeBackupBytes ?? 0));
    const netEstimated = Math.max(0, Number(operation?.netEstimatedBytes ?? estimated));
    const location = safeCleanupBackupLocationLabel(operation);
    if (!backupBytes) return `预计释放 ${storageFormatBytes(estimated)}`;
    if (!sameVolumeBytes) {
      return `预计源盘释放 ${storageFormatBytes(netEstimated)}；备份 ${storageFormatBytes(backupBytes)} 将保存到其他磁盘${location ? `（${location}）` : ""}，不占用源盘`;
    }
    if (sameVolumeBytes >= backupBytes) {
      return `预计源盘净释放 ${storageFormatBytes(netEstimated)}，已扣除同卷备份 ${storageFormatBytes(backupBytes)}`;
    }
    return `预计源盘净释放 ${storageFormatBytes(netEstimated)}；备份共 ${storageFormatBytes(backupBytes)}，其中同卷占用 ${storageFormatBytes(sameVolumeBytes)}`;
  }

  function safeCleanupResultBackupSummary(operation) {
    const backupBytes = Math.max(0, Number(operation?.backupBytes ?? 0));
    if (!backupBytes) return "";
    const location = safeCleanupBackupLocationLabel(operation);
    const files = Array.isArray(operation?.backupFiles)
      ? operation.backupFiles.map((value) => String(value || "").trim()).filter(Boolean)
      : [];
    const visibleFiles = files.slice(0, 3).join("、");
    const fileSuffix = files.length > 3 ? ` 等 ${files.length} 个文件` : visibleFiles;
    return `备份 ${storageFormatBytes(backupBytes)}${location ? ` · ${location}` : ""}${fileSuffix ? ` · ${fileSuffix}` : ""}`;
  }

  function syncSafeCleanupDefaults(data = safeCleanupFromPayload()) {
    const settings = hudSettingsFromPayload();
    const status = currentPayload()?.settingsCommandStatus;
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const selectedDirectory = String(
      data?.backupDirectory
      || operation?.backupDirectory
      || status?.cleanupBackupDirectory
      || ""
    ).trim();
    if (selectedDirectory) {
      safeCleanupState.backupDirectory = selectedDirectory;
      safeCleanupState.backupDirectoryDirty = false;
    } else if (!safeCleanupState.backupDirectoryDirty && !safeCleanupState.backupDirectory) {
      safeCleanupState.backupDirectory = String(settings.cleanup_backup_directory || "").trim();
    }
  }

  function safeCleanupSourceSummary(group, data = safeCleanupFromPayload()) {
    const category = String(group?.category || "");
    const platform = String(data?.platform || "").toLowerCase();
    const sources = {
      user_temp: platform.startsWith("win") ? "%TEMP%" : "用户临时目录",
      codex_temp: "Codex 临时目录",
      hud_diagnostics: "HUD 本地诊断目录",
      hud_overlay_history: "HUD 本地运行目录",
      cleanup_backups: "HUD 清理备份目录",
      codex_logs_history: "Codex 本地诊断库",
      background_usage_history: "HUD 本地用量库",
      sqlite_history: "本地 SQLite 历史库",
    };
    return sources[category] || "本地缓存目录";
  }

  function safeCleanupModifiedRangeLabel(group) {
    const oldest = String(group?.oldestModifiedAt || "");
    const newest = String(group?.newestModifiedAt || "");
    if (!oldest && !newest) return "";
    const oldestLabel = backgroundUsageTime(oldest || newest, { compact: true });
    const newestLabel = backgroundUsageTime(newest || oldest, { compact: true });
    if (oldestLabel === newestLabel) return `修改于 ${newestLabel}`;
    return `最早 ${oldestLabel} · 最近 ${newestLabel}`;
  }

  function safeCleanupPathKindLabel(value) {
    return ({ file: "文件", directory: "目录", unknown: "类型未知" })[String(value || "unknown")] || "类型未知";
  }

  function safeCleanupGroupDetailsHtml(group, { showResults = false } = {}) {
    const rows = (Array.isArray(group?.entries) ? group.entries : []).map((entry) => {
      const itemId = String(entry?.id || "");
      const path = String(entry?.path || "");
      const modified = String(entry?.modifiedAt || "") ? backgroundUsageTime(entry.modifiedAt) : "时间未知";
      const resultState = String(entry?.state || "");
      const stateLabel = resultState
        ? safeCleanupResultStateLabel(resultState)
        : safeCleanupTierLabel(entry?.tier);
      const meta = [
        safeCleanupPathKindLabel(entry?.pathKind),
        storageFormatBytes(entry?.bytes),
        `${Math.max(0, Number(entry?.files || 0)).toLocaleString()} 个文件`,
        modified,
        safeCleanupRetention(entry),
        stateLabel,
      ].filter(Boolean).join(" · ");
      const note = String(entry?.error || "").trim() || safeCleanupDisplayImpact(entry);
      const actions = path && itemId
        ? `<span class="codex-usage-hud-cleanup-target-actions"><button type="button" class="codex-usage-hud-cleanup-target-action" data-action="safe-cleanup-copy-path" data-item-id="${escapeHtml(itemId)}" title="复制完整路径" aria-label="复制完整路径">${cleanupIconSvg("copy")}</button><button type="button" class="codex-usage-hud-cleanup-target-action" data-action="safe-cleanup-reveal" data-item-id="${escapeHtml(itemId)}" title="在资源管理器或 Finder 中打开位置" aria-label="在系统文件管理器中打开位置">${cleanupIconSvg("folder")}</button></span>`
        : "";
      return `<div class="codex-usage-hud-cleanup-target" data-state="${escapeHtml(resultState || String(entry?.tier || "protected"))}"><div class="codex-usage-hud-cleanup-target-head"><code class="codex-usage-hud-cleanup-target-path" title="${escapeHtml(path || "路径不可用")}">${escapeHtml(path || "路径不可用")}</code>${actions}</div><div class="codex-usage-hud-cleanup-target-meta">${escapeHtml(meta)}</div>${note ? `<div class="codex-usage-hud-cleanup-target-note" data-result="${showResults}">${escapeHtml(note)}</div>` : ""}</div>`;
    }).join("");
    return `<div class="codex-usage-hud-cleanup-details" data-cleanup-details="${escapeHtml(group?.presentationId || "")}">${rows || '<div class="codex-usage-hud-cleanup-target-note">没有可显示的目标详情。</div>'}</div>`;
  }

  function safeCleanupGroupRowHtml(group, {
    selectedIds = [],
    kind = "safe",
    interactive = true,
    showResults = false,
  } = {}) {
    const selected = new Set((selectedIds || []).map((id) => String(id || "")).filter(Boolean));
    const executableIds = Array.isArray(group?.executableIds) ? group.executableIds : [];
    const selectedCount = executableIds.filter((id) => selected.has(String(id))).length;
    const checked = executableIds.length > 0 && selectedCount === executableIds.length;
    const partial = selectedCount > 0 && selectedCount < executableIds.length;
    const tier = String(group?.tier || "protected");
    const presentationId = String(group?.presentationId || group?.id || "");
    const expanded = safeCleanupState.expandedGroupIds.has(presentationId);
    const source = safeCleanupSourceSummary(group);
    const modified = safeCleanupModifiedRangeLabel(group);
    const meta = [
      source,
      `${Math.max(0, Number(group?.targetCount || 0)).toLocaleString()} 个目标`,
      `${Math.max(0, Number(group?.files || 0)).toLocaleString()} 个文件`,
      safeCleanupRetention(group),
      modified,
    ].filter(Boolean).join(" · ");
    const resultState = String(group?.state || "selected");
    const impact = showResults
      ? [safeCleanupResultStateLabel(resultState), group?.hasResults ? `实际 ${storageFormatBytes(group?.actualBytes)}` : ""].filter(Boolean).join(" · ")
      : safeCleanupDisplayImpact(group);
    const size = showResults && group?.hasResults ? group.actualBytes : group?.bytes;
    const selector = showResults
      ? `<span class="codex-usage-hud-cleanup-result-mark" data-state="${escapeHtml(resultState)}" aria-label="${escapeHtml(safeCleanupResultStateLabel(resultState))}">${resultState === "completed" ? "✓" : (resultState === "running" ? "…" : (resultState === "selected" ? "○" : "–"))}</span>`
      : `<button type="button" class="codex-usage-hud-cleanup-check" role="checkbox" aria-checked="${partial ? "mixed" : checked}" data-checked="${checked}" data-partial="${partial}" data-disabled="${!interactive || !executableIds.length}" data-action="safe-cleanup-group-toggle" data-cleanup-group-id="${escapeHtml(presentationId)}" ${!interactive || !executableIds.length ? "disabled" : ""} aria-label="${escapeHtml(`${checked ? "取消选择" : "选择"}${safeCleanupDisplayLabel(group)}`)}"></button>`;
    const chevron = `<button type="button" class="codex-usage-hud-cleanup-row-chevron" data-action="safe-cleanup-group-expand" data-cleanup-group-id="${escapeHtml(presentationId)}" aria-expanded="${expanded}" title="${expanded ? "收起路径详情" : "展开路径详情"}" aria-label="${expanded ? "收起路径详情" : "展开路径详情"}">${cleanupIconSvg("chevron")}</button>`;
    return `<div class="codex-usage-hud-cleanup-group" data-group-id="${escapeHtml(presentationId)}" data-expanded="${expanded}"><div class="codex-usage-hud-cleanup-row" data-tier="${escapeHtml(tier)}" data-kind="${escapeHtml(kind)}" data-result-state="${escapeHtml(showResults ? resultState : "")}">${selector}<div class="codex-usage-hud-cleanup-row-main"><div class="codex-usage-hud-cleanup-row-title">${escapeHtml(safeCleanupDisplayLabel(group))}</div><div class="codex-usage-hud-cleanup-row-meta" title="${escapeHtml(meta)}">${escapeHtml(meta || "按安全策略")}</div></div><div class="codex-usage-hud-cleanup-row-impact">${escapeHtml(impact)}</div><div class="codex-usage-hud-cleanup-row-size">${storageFormatBytes(size)}</div>${chevron}</div>${expanded ? safeCleanupGroupDetailsHtml(group, { showResults }) : ""}</div>`;
  }

  function safeCleanupGroupsHtml(data, {
    selectedIds = [],
  } = {}) {
    const groups = safeCleanupGroups(data);
    const selected = new Set((selectedIds || []).map((id) => String(id || "")).filter(Boolean));
    const safeGroups = groups.filter((group) => String(group?.tier || "protected") === "safe");
    const consentGroups = groups.filter((group) => String(group?.tier || "protected") === "consent");
    const protectedGroups = groups.filter((group) => String(group?.tier || "protected") === "protected");
    const interactive = !isSafeCleanupBusy(data);
    const safeRows = safeGroups.map((group) => safeCleanupGroupRowHtml(group, {
      selectedIds,
      kind: "safe",
      interactive,
    })).join("");
    const consentIds = consentGroups.flatMap((group) => group.executableIds || []);
    const consentSelectedCount = consentIds.filter((id) => selected.has(String(id))).length;
    const consentChecked = consentIds.length > 0 && consentSelectedCount === consentIds.length;
    const consentPartial = consentSelectedCount > 0 && consentSelectedCount < consentIds.length;
    const consentBytes = consentGroups.reduce((sum, group) => sum + Math.max(0, Number(group?.bytes || 0)), 0);
    const consentTargets = consentGroups.reduce((sum, group) => sum + Math.max(0, Number(group?.targetCount || 0)), 0);
    const consentFiles = consentGroups.reduce((sum, group) => sum + Math.max(0, Number(group?.files || 0)), 0);
    const deepExpanded = safeCleanupState.expandedGroupIds.has(safeCleanupDeepGroupId);
    const deepRow = consentGroups.length ? `<div class="codex-usage-hud-cleanup-row" data-tier="consent" data-kind="deep"><button type="button" class="codex-usage-hud-cleanup-check" role="checkbox" aria-checked="${consentPartial ? "mixed" : consentChecked}" data-checked="${consentChecked}" data-partial="${consentPartial}" data-disabled="${!interactive || !consentIds.length}" data-action="safe-cleanup-consent-toggle" ${!interactive || !consentIds.length ? "disabled" : ""} aria-label="${consentChecked ? "取消全部深度清理" : "选择全部深度清理"}"></button><div class="codex-usage-hud-cleanup-row-main"><div class="codex-usage-hud-cleanup-row-title">深度清理（可选）</div><div class="codex-usage-hud-cleanup-row-meta">${consentGroups.length} 类 · ${consentTargets} 个目标 · ${consentFiles.toLocaleString()} 个文件</div></div><div class="codex-usage-hud-cleanup-row-impact">需单独确认，部分项需备份或退出</div><div class="codex-usage-hud-cleanup-row-size">${storageFormatBytes(consentBytes)}</div><button type="button" class="codex-usage-hud-cleanup-row-chevron" data-action="cleanup-deep-toggle" aria-expanded="${deepExpanded}" title="${deepExpanded ? "收起深度清理分类" : "展开深度清理分类"}" aria-label="${deepExpanded ? "收起深度清理分类" : "展开深度清理分类"}">${cleanupIconSvg("chevron")}</button></div>` : "";
    const expandedConsent = deepExpanded
      ? consentGroups.map((group) => safeCleanupGroupRowHtml(group, {
        selectedIds,
        kind: "consent",
        interactive,
      })).join("")
      : "";
    const protectedBytes = protectedGroups.reduce((sum, group) => sum + Math.max(0, Number(group?.bytes || 0)), 0);
    const protectedTargets = protectedGroups.reduce((sum, group) => sum + Math.max(0, Number(group?.targetCount || 0)), 0);
    const protectedExpanded = safeCleanupState.expandedGroupIds.has(safeCleanupProtectedGroupId);
    const protectedNote = protectedGroups.length
      ? `<div class="codex-usage-hud-cleanup-protected-note"><button type="button" data-action="safe-cleanup-protected-toggle" aria-expanded="${protectedExpanded}"><span>${cleanupIconSvg("shield")}${escapeHtml(storageFormatBytes(protectedBytes))} 正在使用或受保护 · ${protectedGroups.length} 类 / ${protectedTargets} 个目标</span><span>查看路径 ${cleanupIconSvg("chevron")}</span></button></div>${protectedExpanded ? protectedGroups.map((group) => safeCleanupGroupRowHtml(group, { selectedIds, kind: "protected", interactive: false })).join("") : ""}`
      : "";
    return `${safeRows}${deepRow}${expandedConsent}${protectedNote}`;
  }

  function safeCleanupResultGroupsHtml(data, selectedIds, results, { fallbackState = "selected" } = {}) {
    const resultById = new Map((Array.isArray(results) ? results : [])
      .map((result) => [String(result?.id || ""), result])
      .filter(([id]) => !!id));
    const completeResults = (Array.isArray(selectedIds) ? selectedIds : []).map((id) => {
      const normalizedId = String(id || "");
      return resultById.get(normalizedId) || { id: normalizedId, state: fallbackState };
    });
    return safeCleanupPresentationGroups(data, { itemIds: selectedIds, results: completeResults })
      .map((group) => safeCleanupGroupRowHtml(group, {
        selectedIds,
        kind: `result-${String(group?.tier || "protected")}`,
        interactive: false,
        showResults: true,
      }))
      .join("");
  }

  function safeCleanupPreviewHtml(data) {
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const state = String(operation?.state || "idle");
    if (operation?.action === "scan") return "";
    if (!new Set(["preview", "completed", "partial", "failed", "restored", "accepted", "running", "queued_exit"]).has(state) || safeCleanupState.previewHidden) return "";
    const results = Array.isArray(operation?.results) ? operation.results : [];
    const selectedIds = Array.isArray(operation?.selectedIds) ? operation.selectedIds : safeCleanupSelectedGroupIds(data);
    if (state === "preview" && !safeCleanupPreviewMatchesSelection(data)) return "";
    const rows = safeCleanupResultGroupsHtml(data, selectedIds, results);
    const actual = operation?.actualBytes;
    const value = actual === null || actual === undefined
      ? safeCleanupPreviewSpaceSummary(operation)
      : `实际回收 ${storageFormatBytes(actual)}`;
    const backupSummary = actual === null || actual === undefined ? "" : safeCleanupResultBackupSummary(operation);
    return `<div class="codex-usage-hud-cleanup-preview" aria-live="polite"><div class="codex-usage-hud-cleanup-preview-head"><div><strong>${escapeHtml(safeCleanupOperationLabel(operation))}</strong><div class="codex-usage-hud-cleanup-meta">${escapeHtml(value)}</div>${backupSummary ? `<div class="codex-usage-hud-cleanup-meta">${escapeHtml(backupSummary)}</div>` : ""}</div><span class="codex-usage-hud-storage-status" data-state="${escapeHtml(state)}">${escapeHtml(safeCleanupOperationLabel(operation))}</span></div><div class="codex-usage-hud-cleanup-results">${rows || '<div class="codex-usage-hud-cleanup-meta">当前没有已选目标。</div>'}</div>${operation?.error ? `<div class="codex-usage-hud-cleanup-meta" data-kind="error">${escapeHtml(operation.error)}</div>` : ""}</div>`;
  }


  function isCleanupScanningRevision(revision) {
    return String(revision || "").startsWith("scanning:");
  }

  function isSafeCleanupExecuting(data = safeCleanupFromPayload()) {
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const state = String(operation?.state || "").toLowerCase();
    const action = String(operation?.action || "");
    const isExecuteAction = !action
      || new Set(["execute", "safeCleanupExecute"]).has(action);
    if (safeCleanupState.pendingRequestId && String(safeCleanupState.pendingRequestId).startsWith("safe-cleanup-execute")) {
      return true;
    }
    if (!isExecuteAction) return false;
    return new Set(["accepted", "running", "queued_exit"]).has(state);
  }

  function isSafeCleanupBusy(data = safeCleanupFromPayload()) {
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const state = String(operation?.state || "").toLowerCase();
    return new Set(["scanning", "accepted", "running", "queued_exit"]).has(state)
      || !!safeCleanupState.pendingRequestId;
  }

  function isSafeCleanupScanning(data = safeCleanupFromPayload()) {
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const state = String(operation?.state || "").toLowerCase();
    const action = String(operation?.action || "");
    if (safeCleanupState.pendingRequestId && !String(data?.revision || "")) return true;
    if (!new Set(["scanning", "accepted"]).has(state)) return false;
    if (action && !new Set(["scan", "safeCleanupScan", ""]).has(action)) {
      // execute/preview accepted states are not inventory scans
      return state === "scanning";
    }
    return true;
  }

  function hasStableSafeCleanupInventory(data = safeCleanupState.stableData) {
    const revision = String(data?.revision || "");
    return !!revision && !isCleanupScanningRevision(revision);
  }

  function safeCleanupPhaseLabel(operation = {}) {
    const phase = String(operation?.phase || "").toLowerCase();
    const raw = String(operation?.phaseLabel || "").trim();
    const map = {
      hud: "HUD 诊断",
      codex: "Codex 临时项",
      processes: "相关应用状态",
      caches: "应用与开发缓存",
      backups: "旧清理备份",
      sqlite: "历史数据库",
      preview: "生成默认安全预览",
      sessions: "读取会话索引",
      merge: "归并主会话与子任务",
      capability: "校验删除能力",
      prepare: "准备清理",
      execute: "正在删除",
      queued_exit: "等待退出后清理",

    };
    if (map[phase]) {
      // Keep neutral detail after English label if backend appended ": detail"
      if (raw.includes(":")) {
        const detail = raw.split(":").slice(1).join(":").trim();
        if (detail && phase === "caches") return `${map[phase]} · ${detail}`;
      }
      return map[phase];
    }
    if (raw) {
      const english = {
        "HUD diagnostics": "HUD 诊断",
        "Codex temporary items": "Codex 临时项",
        "Related application state": "相关应用状态",
        "Application and developer caches": "应用与开发缓存",
        "Old cleanup backups": "旧清理备份",
        "Historical databases": "历史数据库",
        "Default safe preview": "生成默认安全预览",
      };
      for (const [en, zh] of Object.entries(english)) {
        if (raw === en || raw.startsWith(`${en}:`)) {
          const detail = raw.startsWith(`${en}:`) ? raw.slice(en.length + 1).trim() : "";
          return detail ? `${zh} · ${detail}` : zh;
        }
      }
      return raw;
    }
    return "正在扫描";
  }

  function formatCleanupElapsed(startedAt) {
    const start = Number(startedAt || 0);
    if (!start) return "0:00";
    const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${String(secs).padStart(2, "0")}`;
  }

  function safeCleanupScanStripHtml(data, {
    title = "扫描进度",
    rescan = false,
    mode = "scan",
    startedAt = 0,
  } = {}) {
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const maxProgress = mode === "execute" ? 100 : 99;
    const progress = Math.max(0, Math.min(maxProgress, Number(operation?.progress || 0)));
    const phaseIndex = Math.max(1, Number(operation?.phaseIndex || 1));
    const phaseCount = Math.max(phaseIndex, Number(operation?.phaseCount || (mode === "execute" ? 1 : 6)));
    const phaseLabel = safeCleanupPhaseLabel(operation);
    const discoveredGroups = Number(operation?.discoveredGroups ?? (Array.isArray(data?.groups) ? data.groups.length : 0));
    const discoveredBytes = Number(operation?.discoveredBytes ?? data?.totals?.reclaimableBytes ?? 0);
    const results = Array.isArray(operation?.results) ? operation.results : [];
    const doneCount = results.filter((item) => {
      const state = String(item?.state || "").toLowerCase();
      return new Set(["deleted", "completed", "skipped", "failed", "restored"]).has(state);
    }).length;
    const actualBytes = Number(operation?.actualBytes || 0);
    const elapsed = formatCleanupElapsed(startedAt || (mode === "execute" ? safeCleanupState.executeStartedAt : safeCleanupState.scanStartedAt));
    const indeterminate = progress <= 0;
    const meta = mode === "execute"
      ? `第 ${phaseIndex}/${phaseCount} 项 · ${progress || 1}% · 已用时 ${escapeHtml(elapsed)}`
      : `第 ${phaseIndex}/${phaseCount} 步 · 约 ${progress || 1}% · 已用时 ${escapeHtml(elapsed)}`;
    const stageRight = mode === "execute"
      ? `已处理 ${doneCount}/${phaseCount} · 已回收 ${storageFormatBytes(actualBytes)}`
      : `已发现 ${discoveredGroups} 组 · ${storageFormatBytes(discoveredBytes)}`;
    return `<div class="codex-usage-hud-cleanup-scan-strip" data-mode="${escapeHtml(mode)}" aria-live="polite"><div class="codex-usage-hud-cleanup-scan-strip-top"><div class="codex-usage-hud-cleanup-scan-strip-title"><span class="codex-usage-hud-cleanup-mini-spinner" aria-hidden="true"></span>${escapeHtml(rescan ? "重新扫描" : title)}</div><div class="codex-usage-hud-cleanup-scan-strip-meta">${meta}</div></div><div class="codex-usage-hud-cleanup-scan-track"><div class="codex-usage-hud-cleanup-scan-fill" data-indeterminate="${indeterminate}" style="width:${Math.max(progress, indeterminate ? 38 : 4)}%"></div></div><div class="codex-usage-hud-cleanup-scan-stage"><span>当前：<strong>${escapeHtml(phaseLabel)}</strong></span><span>${stageRight}</span></div></div>`;
  }

  function safeCleanupBootHtml(data, { pending = true } = {}) {
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const phaseIndex = Math.max(1, Number(operation?.phaseIndex || 1));
    const phaseCount = Math.max(phaseIndex, Number(operation?.phaseCount || 6));
    const phaseLabel = safeCleanupPhaseLabel(operation);
    const elapsed = formatCleanupElapsed(safeCleanupState.scanStartedAt);
    return `<section class="codex-usage-hud-cleanup" aria-label="垃圾清理" aria-busy="true"><div class="codex-usage-hud-cleanup-empty-state"><div class="codex-usage-hud-cleanup-scan-mark" data-live="true">${cleanupIconSvg("scan", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-cleanup-empty-title">正在扫描</h2><p class="codex-usage-hud-cleanup-empty-meta">准备本地清理清单 · <strong>第 ${phaseIndex}/${phaseCount} 步 · ${escapeHtml(phaseLabel)}</strong></p><div class="codex-usage-hud-cleanup-scan-track" style="width:min(280px,70%);margin-top:4px"><div class="codex-usage-hud-cleanup-scan-fill" data-indeterminate="true" style="width:38%"></div></div><p class="codex-usage-hud-cleanup-empty-meta">已用时 ${escapeHtml(elapsed)} · 不会在扫描时删除任何文件</p><button type="button" class="codex-usage-hud-settings-action" data-action="safe-cleanup-cancel" ${pending ? "" : "disabled"}>取消扫描</button></div></section>`;
  }

  function safeCleanupPlaceholderRowsHtml(data) {
    const groups = safeCleanupGroups(data);
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const phase = String(operation?.phase || "").toLowerCase();
    const safeGroups = groups.filter((group) => String(group?.tier || "protected") === "safe");
    const consentGroups = groups.filter((group) => String(group?.tier || "protected") === "consent");
    const protectedGroups = groups.filter((group) => String(group?.tier || "protected") === "protected");
    const selected = new Set(safeCleanupSelectedGroupIds(data));
    const placeholders = [
      { id: "ph-app-cache", title: "应用缓存", meta: "Chrome、Edge、VS Code", kind: "safe", phaseKey: "caches" },
      { id: "ph-dev-cache", title: "开发工具缓存", meta: "pip、npm、NuGet、着色器", kind: "safe", phaseKey: "caches" },
      { id: "ph-runtime", title: "运行残留", meta: "过期临时项与旧清理备份", kind: "safe", phaseKey: "codex" },
      { id: "ph-hud", title: "HUD 诊断日志", meta: "当前与轮转日志", kind: "safe", phaseKey: "hud" },
    ];
    const foundRows = safeGroups.map((group) => {
      const executableIds = Array.isArray(group?.executableIds) ? group.executableIds : [];
      const checked = executableIds.length > 0 && executableIds.every((id) => selected.has(String(id)));
      const impact = safeCleanupDisplayImpact(group);
      const items = Array.isArray(group?.items) ? group.items.length : Number(group?.items || group?.itemCount || 0);
      const meta = [safeCleanupRetention(group), items > 0 ? `${items} 项` : ""].filter(Boolean).join(" · ");
      return `<div class="codex-usage-hud-cleanup-row" data-scan-state="found" data-tier="${escapeHtml(String(group?.tier || "safe"))}"><span class="codex-usage-hud-cleanup-check" data-checked="${checked}" data-disabled="false"></span><div class="codex-usage-hud-cleanup-row-main"><div class="codex-usage-hud-cleanup-row-title">${escapeHtml(safeCleanupDisplayLabel(group))}</div><div class="codex-usage-hud-cleanup-row-meta">${escapeHtml(meta || "按安全策略")}</div></div><div class="codex-usage-hud-cleanup-row-impact">${escapeHtml(impact)}</div><div class="codex-usage-hud-cleanup-row-size">${storageFormatBytes(group?.bytes)}</div><span class="codex-usage-hud-cleanup-row-chevron">${cleanupIconSvg("chevron")}</span></div>`;
    }).join("");
    // If real rows already present, only show deep/protected pending extras.
    const showPlaceholders = safeGroups.length === 0;
    const pendingRows = showPlaceholders ? placeholders.map((item) => {
      const isCurrent = phase === item.phaseKey || (item.phaseKey === "caches" && ["caches", "processes"].includes(phase));
      const scanState = isCurrent ? "current" : "pending";
      return `<div class="codex-usage-hud-cleanup-row" data-scan-state="${scanState}" data-kind="${escapeHtml(item.kind)}"><span class="codex-usage-hud-cleanup-check" data-checked="false" data-disabled="true" data-skeleton="true"></span><div class="codex-usage-hud-cleanup-row-main"><div class="codex-usage-hud-cleanup-row-title">${escapeHtml(item.title)}</div><div class="codex-usage-hud-cleanup-row-meta">${isCurrent ? "正在统计…" : escapeHtml(item.meta)}</div></div><div class="codex-usage-hud-cleanup-row-impact">${isCurrent ? `<span class="codex-usage-hud-cleanup-row-status"><span class="codex-usage-hud-cleanup-mini-spinner"></span>扫描中</span>` : "排队中"}</div><div class="codex-usage-hud-cleanup-row-size" style="color:var(--codex-usage-hud-muted,#9da1a8);font-weight:600">—</div><span class="codex-usage-hud-cleanup-row-chevron">${cleanupIconSvg("chevron")}</span></div>`;
    }).join("") : "";
    const consentBytes = consentGroups.reduce((sum, group) => sum + Math.max(0, Number(group?.bytes || 0)), 0);
    const deepPending = !consentGroups.length;
    const deepRow = deepPending
      ? `<div class="codex-usage-hud-cleanup-row" data-tier="consent" data-kind="deep" data-scan-state="${["sqlite", "backups", "preview"].includes(phase) ? "current" : "pending"}"><span class="codex-usage-hud-cleanup-check" data-checked="false" data-disabled="true" data-skeleton="true"></span><div class="codex-usage-hud-cleanup-row-main"><div class="codex-usage-hud-cleanup-row-title">深度清理（可选）</div><div class="codex-usage-hud-cleanup-row-meta">${["sqlite", "backups", "preview"].includes(phase) ? "旧 Codex 诊断历史 · 计量中" : "扫描完成后可选"}</div></div><div class="codex-usage-hud-cleanup-row-impact">${["sqlite", "backups", "preview"].includes(phase) ? `<span class="codex-usage-hud-cleanup-row-status"><span class="codex-usage-hud-cleanup-mini-spinner"></span>扫描中</span>` : "需备份并关闭 Codex"}</div><div class="codex-usage-hud-cleanup-row-size" style="color:var(--codex-usage-hud-muted,#9da1a8);font-weight:600">—</div><span class="codex-usage-hud-cleanup-row-chevron">${cleanupIconSvg("chevron")}</span></div>`
      : `<div class="codex-usage-hud-cleanup-row" data-tier="consent" data-kind="deep"><span class="codex-usage-hud-cleanup-check" data-checked="false" data-disabled="false"></span><div class="codex-usage-hud-cleanup-row-main"><div class="codex-usage-hud-cleanup-row-title">深度清理（可选）</div><div class="codex-usage-hud-cleanup-row-meta">旧 Codex 诊断历史 · 保留最近 24 小时</div></div><div class="codex-usage-hud-cleanup-row-impact">需备份并关闭 Codex</div><div class="codex-usage-hud-cleanup-row-size">${storageFormatBytes(consentBytes)}</div><span class="codex-usage-hud-cleanup-row-chevron">${cleanupIconSvg("chevron")}</span></div>`;
    const protectedBytes = protectedGroups.reduce((sum, group) => sum + Math.max(0, Number(group?.bytes || 0)), 0);
    const protectedNote = protectedGroups.length
      ? `<div class="codex-usage-hud-cleanup-protected-note"><span>${cleanupIconSvg("shield")}${escapeHtml(storageFormatBytes(protectedBytes))} 正在使用或受保护 · ${protectedGroups.length} 类</span><span>配置、凭据和会话默认受保护</span></div>`
      : `<div class="codex-usage-hud-cleanup-protected-note" style="opacity:.55"><span>${cleanupIconSvg("shield")}受保护项统计中…</span><span>配置与会话不会入选</span></div>`;
    return `${foundRows}${pendingRows}${deepRow}${protectedNote}`;
  }

  function safeCleanupScanningPanelHtml(data, {
    rescan = false,
    stableData = null,
  } = {}) {
    const live = data && typeof data === "object" ? data : {};
    const operation = live?.operation && typeof live.operation === "object" ? live.operation : {};
    const groups = safeCleanupGroups(live);
    const discoveredBytes = Number(operation?.discoveredBytes ?? live?.totals?.reclaimableBytes ?? 0);
    const discoveredGroups = Number(operation?.discoveredGroups ?? groups.length);
    const strip = safeCleanupScanStripHtml(live, { rescan });
    if (rescan && stableData && hasStableSafeCleanupInventory(stableData)) {
      const stableTotals = stableData?.totals && typeof stableData.totals === "object" ? stableData.totals : {};
      const stableReclaimable = stableTotals?.reclaimableBytes;
      return `<section class="codex-usage-hud-cleanup" aria-label="垃圾清理" aria-busy="true">${strip}<div class="codex-usage-hud-cleanup-rescan-shell"><div class="codex-usage-hud-cleanup-rescan-chip"><span class="codex-usage-hud-cleanup-mini-spinner"></span>正在重新扫描本地可清理项…</div><div class="codex-usage-hud-cleanup-rescan-dim"><div class="codex-usage-hud-cleanup-summary-band" data-scanning="true"><div><div class="codex-usage-hud-cleanup-summary-label">上次结果（将被替换）</div><div class="codex-usage-hud-cleanup-summary-value" style="font-size:20px;opacity:.8">${storageFormatBytes(stableReclaimable)}</div></div><div class="codex-usage-hud-cleanup-summary-side">确认清理已锁定<br>等待新清单…</div></div><div class="codex-usage-hud-cleanup-list">${safeCleanupGroupsHtml(stableData, { selectedIds: safeCleanupSelectedGroupIds(stableData) })}</div></div></div></section>`;
    }
    if (!groups.length && Number(operation?.progress || 0) < 12) {
      return safeCleanupBootHtml(live, { pending: true });
    }
    return `<section class="codex-usage-hud-cleanup" aria-label="垃圾清理" aria-busy="true">${strip}<div class="codex-usage-hud-cleanup-summary-band" data-scanning="true"><div><div class="codex-usage-hud-cleanup-summary-label"><span class="codex-usage-hud-cleanup-mini-spinner"></span>累计可清理（扫描中）</div><div class="codex-usage-hud-cleanup-summary-value">${storageFormatBytes(discoveredBytes)}</div></div><div class="codex-usage-hud-cleanup-summary-side">已发现 ${discoveredGroups} 组<br>仍在扫描更多位置…</div></div><div class="codex-usage-hud-cleanup-list">${safeCleanupPlaceholderRowsHtml(live)}</div></section>`;
  }

  function safeCleanupExecutingPanelHtml(data) {
    const live = data && typeof data === "object" ? data : {};
    const operation = live?.operation && typeof live.operation === "object" ? live.operation : {};
    const state = String(operation?.state || "running").toLowerCase();
    const title = state === "queued_exit"
      ? "等待退出后清理"
      : (state === "accepted" ? "准备清理" : "正在清理");
    const strip = safeCleanupScanStripHtml(live, {
      title,
      mode: "execute",
      startedAt: safeCleanupState.executeStartedAt,
    });
    const selectedIds = Array.isArray(operation?.selectedIds)
      ? operation.selectedIds.map((id) => String(id || "")).filter(Boolean)
      : safeCleanupSelectedGroupIds(live);
    const results = Array.isArray(operation?.results) ? operation.results : [];
    const rows = safeCleanupResultGroupsHtml(live, selectedIds, results, {
      fallbackState: state === "accepted" ? "selected" : "running",
    });
    const note = state === "queued_exit"
      ? "HUD 即将退出，离线清理会在退出后继续，完成后自动恢复。"
      : (state === "accepted"
        ? "正在检查活动任务与进程占用，通过后开始删除。"
        : "正在逐项删除；被占用或无法删除的项会自动跳过。");
    return `<section class="codex-usage-hud-cleanup" aria-label="垃圾清理" aria-busy="true">${strip}<div class="codex-usage-hud-cleanup-preview" aria-live="polite"><div class="codex-usage-hud-cleanup-preview-head"><div><strong>${escapeHtml(safeCleanupOperationLabel(operation))}</strong><div class="codex-usage-hud-cleanup-meta">${escapeHtml(note)}</div></div><span class="codex-usage-hud-storage-status" data-state="${escapeHtml(state)}">${escapeHtml(safeCleanupOperationLabel(operation))}</span></div><div class="codex-usage-hud-cleanup-results">${rows || `<div class="codex-usage-hud-cleanup-meta">等待清理项清单…</div>`}</div>${operation?.error ? `<div class="codex-usage-hud-cleanup-meta" data-kind="error">${escapeHtml(operation.error)}</div>` : ""}</div></section>`;
  }

  function safeCleanupPanelHtml() {
    const data = safeCleanupFromPayload();
    const scanning = isSafeCleanupScanning(data);
    const executing = isSafeCleanupExecuting(data);
    const scanned = !!String(data?.revision || "") && !isCleanupScanningRevision(data?.revision);
    const pending = !!safeCleanupState.pendingRequestId;
    const stable = hasStableSafeCleanupInventory(safeCleanupState.stableData) ? safeCleanupState.stableData : null;
    if (scanning) {
      return safeCleanupScanningPanelHtml(data, {
        rescan: !!stable,
        stableData: stable,
      });
    }
    if (executing) {
      return safeCleanupExecutingPanelHtml(data);
    }
    if (!data || !scanned) {
      const failed = String(data?.operation?.state || "") === "failed";
      if (failed) {
        const errorText = String(data?.operation?.error || "扫描未能完成");
        return `<section class="codex-usage-hud-cleanup" aria-label="垃圾清理"><div class="codex-usage-hud-cleanup-empty-state"><div class="codex-usage-hud-cleanup-scan-mark" style="border-color:#754247;background:#2b191b;color:#ff858a">${cleanupIconSvg("alert", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-cleanup-empty-title">扫描未能完成</h2><p class="codex-usage-hud-cleanup-empty-meta">${escapeHtml(errorText)}</p><button type="button" class="codex-usage-hud-settings-action" data-action="safe-cleanup-scan" data-primary="true" data-size="large">${cleanupIconSvg("refresh")}重新扫描</button></div></section>`;
      }
      return `<section class="codex-usage-hud-cleanup" aria-label="垃圾清理"><div class="codex-usage-hud-cleanup-empty-state"><div class="codex-usage-hud-cleanup-scan-mark">${cleanupIconSvg("scan", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-cleanup-empty-title">尚未扫描</h2><p class="codex-usage-hud-cleanup-empty-meta">本机缓存、临时文件与 HUD 诊断数据</p><button type="button" class="codex-usage-hud-settings-action" data-action="safe-cleanup-scan" data-primary="true" data-size="large" ${pending ? "disabled" : ""}>${pending ? "正在扫描..." : `${cleanupIconSvg("scan")}扫描垃圾`}</button></div></section>`;
    }
    syncSafeCleanupSelection(data);
    syncSafeCleanupDefaults(data);
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const operationState = String(operation?.state || "idle");
    const previewLocked = operationState === "preview"
      && !safeCleanupState.previewHidden
      && safeCleanupPreviewMatchesSelection(data);
    const requiresBackup = safeCleanupRequiresBackup(data);
    const requiresCodexClose = safeCleanupRequiresCodexClose(data);
    const selectedIds = safeCleanupSelectedGroupIds(data);
    const selectedGroups = safeCleanupPresentationGroups(data, { itemIds: selectedIds });
    const reclaimable = previewLocked
      ? (operation?.netEstimatedBytes ?? operation?.estimatedBytes)
      : selectedGroups.reduce((sum, group) => sum + Math.max(0, Number(group?.bytes || 0)), 0);
    const backupControl = requiresBackup ? `<div class="codex-usage-hud-cleanup-backup"><input type="text" data-cleanup-backup-directory="true" value="${escapeHtml(safeCleanupState.backupDirectory)}" placeholder="选择备份目录" aria-label="SQLite 备份目录" ${previewLocked ? "disabled" : ""}><button type="button" class="codex-usage-hud-settings-action" data-action="safe-cleanup-choose-backup" ${previewLocked ? "disabled" : ""}>选择目录</button></div>` : "";
    const autoCloseControl = requiresCodexClose ? `<label class="codex-usage-hud-cleanup-control"><input type="checkbox" data-cleanup-auto-close="true" ${safeCleanupState.autoCloseConfirmed ? "checked" : ""}><span><strong>允许自动关闭并恢复 Codex App 与 HUD</strong><span class="codex-usage-hud-cleanup-meta">检测到独立 Codex CLI 或活动任务时仍会停止执行</span></span></label>` : "";
    const deepExpanded = safeCleanupState.expandedGroupIds.has(safeCleanupDeepGroupId);
    const controls = deepExpanded && (backupControl || autoCloseControl)
      ? `<div class="codex-usage-hud-cleanup-controls">${backupControl}${autoCloseControl}</div>`
      : "";
    return `<section class="codex-usage-hud-cleanup" aria-label="垃圾清理"><div class="codex-usage-hud-cleanup-summary-band"><div><div class="codex-usage-hud-cleanup-summary-label">${cleanupIconSvg("check")}当前选择</div><div class="codex-usage-hud-cleanup-summary-value">${storageFormatBytes(reclaimable)}</div></div><div class="codex-usage-hud-cleanup-summary-side">已选 ${selectedGroups.length} 类 / ${selectedIds.length} 个目标<br>${escapeHtml(safeCleanupOperationLabel(operation))}</div></div><div class="codex-usage-hud-cleanup-list">${safeCleanupGroupsHtml(data, { selectedIds })}${controls}</div>${safeCleanupPreviewHtml(data)}</section>`;
  }

  function storagePanelHtml() {
    const isSessions = cleanupActiveSection === "sessions";
    const sessionData = sessionCleanupFromPayload();
    const cleanupData = safeCleanupFromPayload();
    const sessionCount = Number(sessionData?.totals?.sessions || (Array.isArray(sessionData?.sessions) ? sessionData.sessions.length : 0));
    const headMeta = isSessions
      ? (new Set(["scanning", "accepted"]).has(String(sessionData?.operation?.state || "").toLowerCase()) || !!sessionCleanupState.pendingRequestId
        ? `<span class="codex-usage-hud-cleanup-pulse-dot"></span>正在扫描会话…`
        : (sessionCount ? `${sessionCount.toLocaleString()} 个本地会话` : "按需扫描 · 无后台轮询"))
      : (isSafeCleanupScanning(cleanupData)
        ? `<span class="codex-usage-hud-cleanup-pulse-dot"></span>${escapeHtml(safeCleanupPhaseLabel(cleanupData?.operation || {}) || "正在扫描…")}`
        : (isSafeCleanupExecuting(cleanupData)
          ? `<span class="codex-usage-hud-cleanup-pulse-dot"></span>${escapeHtml(safeCleanupOperationLabel(cleanupData?.operation || {}) || "正在清理")}`
          : (cleanupData?.revision && !isCleanupScanningRevision(cleanupData?.revision)
            ? escapeHtml(safeCleanupOperationLabel(cleanupData?.operation || {}))
            : "按需扫描 · 无后台轮询")));
    const body = isSessions ? sessionCleanupPanelHtml() : safeCleanupPanelHtml();
    const junkScanning = isSafeCleanupScanning(cleanupData);
    const junkScanned = !!String(cleanupData?.revision || "") && !isCleanupScanningRevision(cleanupData?.revision) && !junkScanning;
    const junkBusy = isSafeCleanupBusy(cleanupData);
    const sessionScanned = !!String(sessionData?.revision || "");
    const sessionBusy = new Set(["scanning", "accepted", "running"]).has(String(sessionData?.operation?.state || "")) || !!sessionCleanupState.pendingRequestId;
    const selectedCleanupIds = safeCleanupSelectedGroupIds(cleanupData);
    const selectedCleanupGroups = safeCleanupPresentationGroups(cleanupData, { itemIds: selectedCleanupIds });
    const junkReady = String(cleanupData?.operation?.state || "") === "preview"
      && !!String(cleanupData?.operation?.confirmationToken || "")
      && !safeCleanupState.previewHidden
      && safeCleanupPreviewMatchesSelection(cleanupData);
    const reclaimable = junkReady
      ? (cleanupData?.operation?.netEstimatedBytes ?? cleanupData?.operation?.estimatedBytes)
      : selectedCleanupGroups.reduce((sum, group) => sum + Math.max(0, Number(group?.bytes || 0)), 0);
    let footerMeta = "配置、凭据和会话默认受保护";
    let footerActions = "";
    if (isSessions) {
      const selectedCount = sessionCleanupState.selectedIds.size;
      const selectedRows = sessionCleanupRows(sessionData).filter((item) => sessionCleanupState.selectedIds.has(String(item?.id || "")));
      const descendants = selectedRows.reduce((sum, item) => sum + Math.max(0, Number(item?.descendantCount || 0)), 0);
      const bytes = selectedRows.reduce((sum, item) => sum + Math.max(0, Number(item?.bytes || 0)), 0);
      footerMeta = selectedCount
        ? `已选 ${selectedCount} 个会话 · 含 ${descendants} 个关联子任务 · ${storageFormatBytes(bytes)}`
        : (sessionScanned ? "当前/运行中会话不可选；子任务随主会话汇总" : "上次扫描：--");
      footerActions = `<div class="codex-usage-hud-cleanup-footer-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-scan" ${sessionBusy ? "disabled" : ""}>${sessionBusy ? "正在扫描..." : (sessionScanned ? "重新扫描" : "扫描会话")}</button><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-preview" data-danger="true" data-size="large" ${sessionBusy || !selectedCount || sessionData?.capability?.available !== true ? "disabled" : ""}>${cleanupIconSvg("trash")}永久删除</button></div>`;
    } else if (junkScanning) {
      footerMeta = "仅统计可清理项 · 扫描时不删除";
      footerActions = `<div class="codex-usage-hud-cleanup-footer-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="safe-cleanup-cancel">取消扫描</button><button type="button" class="codex-usage-hud-settings-action" data-action="safe-cleanup-confirm" data-primary="true" data-size="large" disabled>${cleanupIconSvg("check")}确认清理</button></div>`;
    } else if (!junkScanned) {
      footerMeta = `上次扫描：-- · ${cleanupIconSvg("shield")} 配置、凭据和会话默认受保护`;
      footerActions = "";
    } else if (isSafeCleanupExecuting(cleanupData)) {
      const operation = cleanupData?.operation && typeof cleanupData.operation === "object" ? cleanupData.operation : {};
      const progress = Math.max(0, Math.min(100, Number(operation?.progress || 0)));
      const phaseIndex = Math.max(1, Number(operation?.phaseIndex || 1));
      const phaseCount = Math.max(phaseIndex, Number(operation?.phaseCount || 1));
      footerMeta = `正在清理 ${phaseIndex}/${phaseCount} · ${progress || 1}% · 已用时 ${formatCleanupElapsed(safeCleanupState.executeStartedAt)}`;
      footerActions = `<div class="codex-usage-hud-cleanup-footer-actions"><button type="button" class="codex-usage-hud-cleanup-icon-button" data-action="safe-cleanup-scan" aria-label="重新扫描" disabled>${cleanupIconSvg("refresh")}</button><button type="button" class="codex-usage-hud-settings-action" data-action="safe-cleanup-confirm" data-primary="true" data-size="large" disabled><span class="codex-usage-hud-cleanup-mini-spinner" aria-hidden="true"></span>正在清理 ${progress || 1}%</button></div>`;
    } else {
      const offline = safeCleanupRequiresOffline(cleanupData);
      const requiresBackup = safeCleanupRequiresBackup(cleanupData);
      const requiresCodexClose = safeCleanupRequiresCodexClose(cleanupData);
      footerMeta = `已选 ${selectedCleanupGroups.length} 类 / ${selectedCleanupIds.length} 个目标 · 执行前重验路径与指纹${requiresBackup ? " · 需 SQLite 备份" : (requiresCodexClose ? " · 需重启 Codex" : (offline ? " · HUD 将短暂重启" : ""))}`;
      const confirmLabel = junkBusy
        ? (String(cleanupData?.operation?.state || "") === "accepted"
          ? "准备清理..."
          : "正在清理...")
        : `${cleanupIconSvg("check")}${junkReady ? `确认清理 ${storageFormatBytes(reclaimable)}` : "确认清理"}`;
      footerActions = `<div class="codex-usage-hud-cleanup-footer-actions"><button type="button" class="codex-usage-hud-cleanup-icon-button" data-action="safe-cleanup-scan" aria-label="重新扫描" ${junkBusy ? "disabled" : ""}>${cleanupIconSvg("refresh")}</button><button type="button" class="codex-usage-hud-settings-action" data-action="safe-cleanup-confirm" data-primary="true" data-size="large" ${junkBusy || !junkReady ? "disabled" : ""}>${confirmLabel}</button></div>`;
    }
    return `<div class="codex-usage-hud-cleanup-workspace"><div class="codex-usage-hud-cleanup-page-head"><div class="codex-usage-hud-cleanup-segments" role="tablist" aria-label="空间清理分类"><button type="button" role="tab" aria-selected="${!isSessions}" data-action="cleanup-section" data-cleanup-section="junk" data-active="${!isSessions}">${cleanupIconSvg("scan")}垃圾清理</button><button type="button" role="tab" aria-selected="${isSessions}" data-action="cleanup-section" data-cleanup-section="sessions" data-active="${isSessions}">${cleanupIconSvg("trash")}会话管理</button></div><span class="codex-usage-hud-cleanup-head-meta">${headMeta}</span></div><div class="codex-usage-hud-cleanup-content">${body}</div><div class="codex-usage-hud-cleanup-footer"><span class="codex-usage-hud-cleanup-footer-meta">${footerMeta}</span>${footerActions}</div></div>`;
  }

  function sessionCleanupRows(data = sessionCleanupFromPayload()) {
    const search = String(sessionCleanupState.search || "").trim().toLowerCase();
    const status = String(sessionCleanupState.status || "all");
    const timeFilter = String(sessionCleanupState.time || "all");
    const now = Date.now();
    return (Array.isArray(data?.sessions) ? data.sessions : []).filter((item) => {
      if (status === "archived" && item?.archived !== true) return false;
      if (status === "active" && !new Set(["current", "running"]).has(String(item?.status || ""))) return false;
      if (status === "selectable" && item?.selectable !== true) return false;
      if (timeFilter !== "all") {
        const updatedAt = Date.parse(String(item?.updatedAt || ""));
        if (!Number.isFinite(updatedAt)) return false;
        const age = Math.max(0, now - updatedAt);
        if (timeFilter === "7d" && age > 7 * 86400000) return false;
        if (timeFilter === "30d" && age > 30 * 86400000) return false;
        if (timeFilter === "older" && age <= 30 * 86400000) return false;
      }
      if (!search) return true;
      return `${item?.title || ""} ${item?.workdirName || ""}`.toLowerCase().includes(search);
    });
  }

  function sessionCleanupStatusLabel(item) {
    const status = String(item?.status || "idle");
    return ({ idle: "普通", archived: "已归档", current: "当前", running: "运行中", unresolved: "映射异常", unavailable: "不可用" })[status] || status;
  }

  function sessionCleanupReasonLabel(value) {
    const reason = String(value || "").trim();
    const exact = {
      "Not scanned yet.": "尚未扫描。",
      "Codex CLI command is unavailable.": "未找到可用的 Codex CLI。",
      "This Codex CLI cannot delete sessions.": "当前 Codex CLI 不支持永久删除会话。",
      "This Codex CLI does not expose non-interactive permanent deletion.": "当前 Codex CLI 不支持非交互永久删除。",
      "The current session cannot be permanently deleted.": "当前会话不可永久删除。",
      "This session tree still has active work.": "该会话或关联子任务仍在运行。",
      "The session spawn relation could not be verified.": "无法完整验证主会话与子任务关系。",
      "The session rollout mapping could not be verified.": "无法完整验证会话本地记录映射。",
    };
    if (exact[reason]) return exact[reason];
    if (reason.startsWith("Codex delete capability could not be verified")) {
      return "无法验证 Codex 永久删除能力。";
    }
    return reason;
  }

  function sessionCleanupPanelHtml() {
    const data = sessionCleanupFromPayload();
    const scanned = !!String(data?.revision || "");
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const state = String(operation?.state || "idle");
    const busy = new Set(["scanning", "accepted", "running"]).has(state) || !!sessionCleanupState.pendingRequestId;
    if (busy && (!scanned || new Set(["scanning", "accepted"]).has(state))) {
      const phaseLabel = safeCleanupPhaseLabel(operation);
      const progress = Math.max(0, Math.min(99, Number(operation?.progress || 0)));
      const phaseIndex = Math.max(1, Number(operation?.phaseIndex || 1));
      const phaseCount = Math.max(phaseIndex, Number(operation?.phaseCount || 3));
      const elapsed = formatCleanupElapsed(sessionCleanupState.scanStartedAt);
      return `<section class="codex-usage-hud-session-cleanup" aria-label="会话管理" aria-busy="true"><div class="codex-usage-hud-cleanup-scan-strip" aria-live="polite"><div class="codex-usage-hud-cleanup-scan-strip-top"><div class="codex-usage-hud-cleanup-scan-strip-title"><span class="codex-usage-hud-cleanup-mini-spinner"></span>扫描本地会话</div><div class="codex-usage-hud-cleanup-scan-strip-meta">第 ${phaseIndex}/${phaseCount} 步 · 约 ${progress || 1}% · 已用时 ${escapeHtml(elapsed)}</div></div><div class="codex-usage-hud-cleanup-scan-track"><div class="codex-usage-hud-cleanup-scan-fill" data-indeterminate="${progress <= 0}" style="width:${Math.max(progress, 8)}%"></div></div><div class="codex-usage-hud-cleanup-scan-stage"><span>当前：<strong>${escapeHtml(phaseLabel || "读取会话索引")}</strong></span><span>筛选与删除在完成后解锁</span></div></div><div class="codex-usage-hud-cleanup-empty-state" style="min-height:180px"><div class="codex-usage-hud-cleanup-scan-mark" data-live="true">${cleanupIconSvg("trash", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-cleanup-empty-title">正在扫描会话</h2><p class="codex-usage-hud-cleanup-empty-meta">按主会话归并本地记录与关联子任务</p><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-cancel">取消扫描</button></div></section>`;
    }
    if (!data || !scanned) {
      return `<section class="codex-usage-hud-session-cleanup" aria-label="会话管理"><div class="codex-usage-hud-cleanup-empty-state"><div class="codex-usage-hud-cleanup-scan-mark">${cleanupIconSvg("trash", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-cleanup-empty-title">尚未扫描会话</h2><p class="codex-usage-hud-cleanup-empty-meta">按主会话整理本地记录，关联子任务会随主会话一起永久删除。</p><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-scan" data-primary="true" data-size="large" ${busy ? "disabled" : ""}>${busy ? "正在扫描..." : `${cleanupIconSvg("search")}扫描会话`}</button></div></section>`;
    }
    const capability = data?.capability && typeof data.capability === "object" ? data.capability : {};
    const rows = sessionCleanupRows(data);
    const visibleSelectable = rows.filter((item) => item?.selectable === true && String(item?.id || ""));
    const allVisibleSelected = visibleSelectable.length > 0
      && visibleSelectable.every((item) => sessionCleanupState.selectedIds.has(String(item.id)));
    const rowHtml = rows.slice(0, 180).map((item) => {
      const id = String(item?.id || "");
      const selectable = item?.selectable === true && !!id;
      const checked = selectable && sessionCleanupState.selectedIds.has(id);
      const descendants = Math.max(0, Number(item?.descendantCount || 0));
      const updatedAt = item?.updatedAt ? backgroundUsageTime(item.updatedAt, { compact: true }) : "--";
      const secondary = [
        String(item?.workdirName || ""),
        updatedAt,
        descendants ? `含 ${descendants} 个关联子任务` : "无关联子任务",
      ].filter(Boolean).join(" · ");
      const related = descendants ? `含 ${descendants} 个关联子任务` : "无关联子任务";
      return `<label class="codex-usage-hud-session-row" data-selectable="${selectable}" data-selected="${checked}"><input type="checkbox" data-session-cleanup-id="${escapeHtml(id)}" ${checked ? "checked" : ""} ${selectable ? "" : "disabled"}><div class="codex-usage-hud-session-title"><strong title="${escapeHtml(item?.title || "未命名会话")}">${escapeHtml(item?.title || "未命名会话")}</strong><span>${escapeHtml(related)}</span><span data-secondary="true">${escapeHtml(secondary)}</span>${item?.blockedReason ? `<span data-kind="warning">${escapeHtml(sessionCleanupReasonLabel(item.blockedReason))}</span>` : ""}</div><span class="codex-usage-hud-session-workdir" title="${escapeHtml(item?.workdirName || "")}">${escapeHtml(item?.workdirName || "--")}</span><span class="codex-usage-hud-session-cell">${escapeHtml(updatedAt)}</span><span class="codex-usage-hud-session-badge" data-state="${escapeHtml(item?.status || "idle")}">${escapeHtml(sessionCleanupStatusLabel(item))}</span><span class="codex-usage-hud-session-size">${storageFormatBytes(item?.bytes)}</span></label>`;
    }).join("");
    const results = Array.isArray(operation?.results) ? operation.results : [];
    const resultHtml = results.length ? `<div class="codex-usage-hud-session-results"><strong>${state === "completed" ? "删除完成" : state === "partial" ? "部分完成" : "删除失败"}</strong>${results.map((item) => `<div><span>${escapeHtml(item?.title || "会话")}</span><span data-kind="${item?.state === "deleted" ? "success" : "error"}">${escapeHtml(item?.state === "deleted" ? "已永久删除" : item?.error || "删除失败")}</span></div>`).join("")}</div>` : "";
    const unavailable = capability?.available === false ? `<div class="codex-usage-hud-session-capability" data-kind="warning">${escapeHtml(sessionCleanupReasonLabel(capability?.reason) || "当前 Codex CLI 不支持永久删除，会话清单保持只读。")}</div>` : "";
    const clipped = rows.length > 180
      ? `<div class="codex-usage-hud-cleanup-meta" style="padding:8px 13px">当前筛选共 ${rows.length} 项，仅显示前 180 项。</div>`
      : "";
    const statusFilter = String(sessionCleanupState.status || "all");
    const timeFilterChip = String(sessionCleanupState.time || "all");
    const statusChips = [
      ["all", "全部"],
      ["archived", "已归档"],
      ["active", "当前/运行中"],
      ["selectable", "可删除"],
    ].map(([value, label]) => `<button type="button" class="codex-usage-hud-session-filter" data-action="session-cleanup-status" data-session-cleanup-status="${value}" data-active="${statusFilter === value}" aria-pressed="${statusFilter === value}">${label}</button>`).join("");
    const timeChips = [
      ["all", "全部时间"],
      ["30d", "30 天未用"],
      ["older", "更早"],
      ["7d", "近 7 天"],
    ].map(([value, label]) => `<button type="button" class="codex-usage-hud-session-filter" data-action="session-cleanup-time" data-session-cleanup-time="${value}" data-active="${timeFilterChip === value}" aria-pressed="${timeFilterChip === value}">${label}</button>`).join("");
    return `<section class="codex-usage-hud-session-cleanup" aria-label="会话管理">${unavailable}<div class="codex-usage-hud-session-tools"><div class="codex-usage-hud-session-search">${cleanupIconSvg("search")}<input type="search" data-session-cleanup-search="true" value="${escapeHtml(sessionCleanupState.search)}" placeholder="搜索标题或工作目录" aria-label="搜索会话"></div><div class="codex-usage-hud-session-filters" role="group" aria-label="会话筛选">${statusChips}${timeChips}</div></div><div class="codex-usage-hud-session-table"><div class="codex-usage-hud-session-head"><span><input type="checkbox" data-session-cleanup-select-all="true" ${allVisibleSelected ? "checked" : ""} ${visibleSelectable.length ? "" : "disabled"} aria-label="全选当前筛选"></span><span>会话</span><span>工作目录</span><span>最后活动</span><span>状态</span><span>占用</span></div>${rowHtml || '<div class="codex-usage-hud-cleanup-empty">当前筛选没有会话。</div>'}</div>${clipped}${resultHtml}</section>`;
  }

  function captureStorageUiState() {
    const modal = document.getElementById(settingsModalId);
    const body = modal?.querySelector?.(".codex-usage-hud-settings-body");
    if (body) storageBodyScrollTop = body.scrollTop;
    const cleanupContent = modal?.querySelector?.(".codex-usage-hud-cleanup-content");
    if (cleanupContent) cleanupContentScrollTop = cleanupContent.scrollTop;
    const sessionTable = modal?.querySelector?.(".codex-usage-hud-session-table");
    if (sessionTable) sessionTableScrollTop = sessionTable.scrollTop;
    const backup = modal?.querySelector?.("[data-cleanup-backup-directory='true']");
    if (backup instanceof HTMLInputElement) safeCleanupState.backupDirectory = String(backup.value || "").trim();
    const consent = modal?.querySelector?.("[data-cleanup-consent='true']");
    if (consent instanceof HTMLInputElement) safeCleanupState.includeConsent = !!consent.checked;
    const autoClose = modal?.querySelector?.("[data-cleanup-auto-close='true']");
    if (autoClose instanceof HTMLInputElement) safeCleanupState.autoCloseConfirmed = !!autoClose.checked;
  }

  function restoreStorageUiState() {
    const modal = document.getElementById(settingsModalId);
    const body = modal?.querySelector?.(".codex-usage-hud-settings-body");
    if (body) body.scrollTop = storageBodyScrollTop;
    const cleanupContent = modal?.querySelector?.(".codex-usage-hud-cleanup-content");
    if (cleanupContent) cleanupContent.scrollTop = cleanupContentScrollTop;
    const sessionTable = modal?.querySelector?.(".codex-usage-hud-session-table");
    if (sessionTable) sessionTable.scrollTop = sessionTableScrollTop;
  }

  function requestUsageInsightsRefresh({ force = false } = {}) {
    if (usageInsightsState.refreshRequestId && !force) return false;
    const requestId = typedSettingsRequestId("usage-insights");
    usageInsightsState.refreshRequestId = requestId;
    usageInsightsState.error = "";
    const submitted = submitSettingsCommand(
      { action: "usageInsightsRefresh", requestId },
      "正在刷新会话排行...",
      { preserveOverlay: true },
    );
    if (!submitted) {
      usageInsightsState.refreshRequestId = "";
      usageInsightsState.error = "无法提交会话排行刷新请求。";
    }
    return submitted;
  }

  function requestSafeCleanupScan() {
    if (safeCleanupState.pendingRequestId) return false;
    const current = safeCleanupFromPayload();
    if (hasStableSafeCleanupInventory(current)) {
      safeCleanupState.stableData = current;
    } else if (!hasStableSafeCleanupInventory(safeCleanupState.stableData)) {
      safeCleanupState.stableData = null;
    }
    const requestId = typedSettingsRequestId("safe-cleanup-scan");
    safeCleanupState.pendingRequestId = requestId;
    safeCleanupState.scanStartedAt = Date.now();
    safeCleanupState.previewHidden = false;
    safeCleanupState.previewBackupDirectory = "";
    ensureSafeCleanupLiveTicker();
    rerenderUsageInsightsIfVisible();
    const submitted = submitSettingsCommand(
      { action: "safeCleanupScan", requestId },
      "正在扫描可安全清理的本地数据...",
      { preserveOverlay: true },
    );
    if (!submitted) safeCleanupState.pendingRequestId = "";
    return submitted;
  }

  function requestSafeCleanupCancel() {
    const requestId = typedSettingsRequestId("safe-cleanup-cancel");
    const submitted = submitSettingsCommand(
      { action: "safeCleanupCancel", requestId },
      "正在取消扫描...",
      { preserveOverlay: true },
    );
    if (submitted) {
      safeCleanupState.pendingRequestId = "";
      safeCleanupState.scanStartedAt = 0;
      safeCleanupState.executeStartedAt = 0;
    }
    return submitted;
  }

  function requestSessionCleanupCancel() {
    const requestId = typedSettingsRequestId("session-cleanup-cancel");
    const submitted = submitSettingsCommand(
      { action: "sessionCleanupCancel", requestId },
      "正在取消会话扫描...",
      { preserveOverlay: true },
    );
    if (submitted) {
      sessionCleanupState.pendingRequestId = "";
      sessionCleanupState.scanStartedAt = 0;
    }
    return submitted;
  }

  function refreshSafeCleanupSelection(data = safeCleanupFromPayload()) {
    const itemById = new Map(safeCleanupRawItems(data).map((item) => [String(item?.id || ""), item]));
    safeCleanupState.includeConsent = [...safeCleanupState.selectedIds]
      .some((id) => String(itemById.get(id)?.tier || "") === "consent");
    safeCleanupState.previewHidden = true;
    safeCleanupState.previewBackupDirectory = "";
    if (!safeCleanupSelectedGroupIds(data).length) {
      clearTimeout(safeCleanupPreviewTimer);
      if (safeCleanupState.pendingRequestId === "pending-preview") safeCleanupState.pendingRequestId = "";
      setSettingsStatus("未选择清理目标。", "");
      rerenderUsageInsightsIfVisible();
      return;
    }
    scheduleSafeCleanupPreview();
  }

  function toggleSafeCleanupItemIds(itemIds, data = safeCleanupFromPayload()) {
    const validIds = (Array.isArray(itemIds) ? itemIds : [])
      .map((id) => String(id || ""))
      .filter(Boolean);
    if (!validIds.length) return false;
    const allSelected = validIds.every((id) => safeCleanupState.selectedIds.has(id));
    validIds.forEach((id) => {
      if (allSelected) safeCleanupState.selectedIds.delete(id);
      else safeCleanupState.selectedIds.add(id);
    });
    refreshSafeCleanupSelection(data);
    return true;
  }

  function requestSafeCleanupPreview() {
    captureStorageUiState();
    const data = safeCleanupFromPayload();
    const groupIds = safeCleanupSelectedGroupIds(data);
    const inventoryRevision = String(data?.revision || "");
    const requiresBackup = safeCleanupRequiresBackup(data);
    if (!inventoryRevision || !groupIds.length) {
      setSettingsStatus("请先扫描可清理项。", "error");
      return false;
    }
    if (requiresBackup && !safeCleanupState.backupDirectory) {
      setSettingsStatus("SQLite 维护必须先选择备份目录。", "error");
      return false;
    }
    const requestId = typedSettingsRequestId("safe-cleanup-preview");
    safeCleanupState.pendingRequestId = requestId;
    safeCleanupState.previewHidden = false;
    const previewBackupDirectory = requiresBackup ? safeCleanupState.backupDirectory : "";
    const submitted = submitSettingsCommand({
      action: "safeCleanupPreview",
      requestId,
      groupIds,
      inventoryRevision,
      consentConfirmed: safeCleanupState.includeConsent,
      backupDirectory: safeCleanupState.backupDirectory,
      autoCloseAndRestore: safeCleanupState.autoCloseConfirmed,
    }, "正在生成清理预览...", { preserveOverlay: true });
    if (submitted) {
      safeCleanupState.previewBackupDirectory = previewBackupDirectory;
    } else {
      safeCleanupState.pendingRequestId = "";
      safeCleanupState.previewBackupDirectory = "";
    }
    return submitted;
  }

  function requestSafeCleanupReveal(itemId) {
    const data = safeCleanupFromPayload();
    const normalizedItemId = String(itemId || "").trim();
    const inventoryRevision = String(data?.revision || "");
    if (!normalizedItemId || !inventoryRevision || isCleanupScanningRevision(inventoryRevision)) {
      setSettingsStatus("该路径已过期，请重新扫描后再打开。", "error");
      return false;
    }
    return submitSettingsCommand({
      action: "safeCleanupReveal",
      requestId: typedSettingsRequestId("safe-cleanup-reveal"),
      inventoryRevision,
      itemId: normalizedItemId,
    }, "正在打开本地位置...", { preserveOverlay: true });
  }

  function openSafeCleanupExecuteConfirm() {
    captureStorageUiState();
    const dialog = settingsDialogRoot();
    const data = safeCleanupFromPayload();
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    if (!dialog || String(operation?.state || "") !== "preview") return;
    if (safeCleanupState.previewHidden || !safeCleanupPreviewMatchesSelection(data)) {
      setSettingsStatus("清理选择已变化，请等待新预览。", "error");
      return;
    }
    const offline = safeCleanupRequiresOffline(data);
    const requiresBackup = safeCleanupRequiresBackup(data);
    const requiresCodexClose = safeCleanupRequiresCodexClose(data);
    const selectedIds = Array.isArray(operation?.selectedIds) ? operation.selectedIds : [];
    const itemById = new Map(safeCleanupRawItems(data).map((item) => [String(item?.id || ""), item]));
    const includesConsent = operation?.includesConsent === true
      || selectedIds.some((id) => String(itemById.get(String(id))?.tier || "") === "consent");
    const selectedGroupCount = safeCleanupPresentationGroups(data, { itemIds: selectedIds }).length;
    const previewBackupDirectory = String(safeCleanupState.previewBackupDirectory || "").trim();
    if (requiresBackup && !previewBackupDirectory) {
      setSettingsStatus("预览绑定的 SQLite 备份目录不可用，请取消后重新生成预览。", "error");
      return;
    }
    if (requiresCodexClose && !safeCleanupState.autoCloseConfirmed) {
      setSettingsStatus("请先确认允许自动关闭并恢复 Codex App 与 HUD。", "error");
      return;
    }
    closeSettingsConfirm();
    const layer = document.createElement("div");
    layer.className = "codex-usage-hud-settings-confirm-layer";
    layer.dataset.settingsConfirm = "true";
    const offlineNote = requiresBackup
      ? `\nSQLite 将先备份到：${escapeHtml(previewBackupDirectory)}\n活动任务或独立 Codex CLI 会阻止执行。`
      : (requiresCodexClose ? "\nCodex App 与 HUD 会正常退出并在清理后自动恢复。活动任务或独立 Codex CLI 会阻止执行。" : (offline ? "\nHUD 会短暂退出并在清理自身日志后自动恢复。活动任务会阻止执行。" : ""));
    const spaceSummary = safeCleanupConfirmSpaceSummary(operation);
    layer.innerHTML = `<div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="确认一键清理"><div class="codex-usage-hud-settings-confirm-main"><div class="codex-usage-hud-settings-confirm-kicker">二次确认</div><div class="codex-usage-hud-settings-confirm-title">清理 ${selectedGroupCount} 类、${selectedIds.length} 个本地目标？</div><div class="codex-usage-hud-settings-confirm-body">${escapeHtml(spaceSummary)}。${includesConsent ? "已包含会失去历史或诊断信息的确认项。" : "仅包含可直接清理项。"}${offlineNote}</div></div><div class="codex-usage-hud-settings-confirm-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="safe-cleanup-confirm-cancel">取消</button><button type="button" class="codex-usage-hud-settings-action" data-action="safe-cleanup-execute" data-primary="true">确认清理</button></div></div>`;
    dialog.appendChild(layer);
    layer.querySelector('[data-action="safe-cleanup-confirm-cancel"]')?.focus?.();
  }

  function executeSafeCleanup() {
    const data = safeCleanupFromPayload();
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    if (String(operation?.state || "") !== "preview" || safeCleanupState.previewHidden || !safeCleanupPreviewMatchesSelection(data)) {
      setSettingsStatus("清理选择尚未完成预览，请稍候。", "error");
      return false;
    }
    const groupIds = Array.isArray(operation?.selectedIds) ? operation.selectedIds : safeCleanupSelectedGroupIds(data);
    const requestId = typedSettingsRequestId("safe-cleanup-execute");
    safeCleanupState.pendingRequestId = requestId;
    safeCleanupState.executeStartedAt = Date.now();
    safeCleanupState.previewHidden = false;
    if (safeCleanupState.data && typeof safeCleanupState.data === "object") {
      const previous = safeCleanupState.data.operation && typeof safeCleanupState.data.operation === "object"
        ? safeCleanupState.data.operation
        : {};
      safeCleanupState.data = {
        ...safeCleanupState.data,
        operation: {
          ...previous,
          requestId,
          action: "safeCleanupExecute",
          state: "accepted",
          progress: 5,
          phase: "prepare",
          phaseLabel: "Preparing cleanup",
          phaseIndex: 1,
          phaseCount: Math.max(1, groupIds.length || Number(previous?.phaseCount || 1)),
          selectedIds: groupIds,
          results: groupIds.map((id) => ({
            id,
            state: "selected",
            estimatedBytes: 0,
            actualBytes: 0,
            deletedRows: 0,
            error: "",
          })),
        },
      };
    }
    closeSettingsConfirm();
    ensureSafeCleanupLiveTicker();
    rerenderUsageInsightsIfVisible();
    const submitted = submitSettingsCommand({
      action: "safeCleanupExecute",
      requestId,
      groupIds,
      inventoryRevision: String(data?.revision || operation?.inventoryRevision || ""),
      confirmationToken: String(operation?.confirmationToken || ""),
      autoCloseAndRestore: safeCleanupState.autoCloseConfirmed,
    }, "清理已确认，正在检查安全门禁...", { preserveOverlay: true });
    if (!submitted) {
      safeCleanupState.pendingRequestId = "";
      safeCleanupState.executeStartedAt = 0;
      rerenderUsageInsightsIfVisible();
    }
    return submitted;
  }

  function scheduleSafeCleanupPreview() {
    clearTimeout(safeCleanupPreviewTimer);
    safeCleanupState.pendingRequestId = "pending-preview";
    rerenderUsageInsightsIfVisible();
    safeCleanupPreviewTimer = setTimeout(() => {
      safeCleanupState.pendingRequestId = "";
      const submitted = requestSafeCleanupPreview();
      if (!submitted) {
        rerenderUsageInsightsIfVisible();
        const data = safeCleanupFromPayload();
        const selectedIds = safeCleanupSelectedGroupIds(data);
        const missingBackup = safeCleanupRequiresBackup(data) && !safeCleanupState.backupDirectory;
        setSettingsStatus(
          !selectedIds.length
            ? "未选择清理目标。"
            : (missingBackup ? "SQLite 维护必须先选择备份目录。" : "无法提交清理预览。"),
          "error",
        );
      }
    }, 280);
  }

  function requestSessionCleanupScan() {
    if (sessionCleanupState.pendingRequestId) return false;
    const requestId = typedSettingsRequestId("session-cleanup-scan");
    sessionCleanupState.pendingRequestId = requestId;
    sessionCleanupState.scanStartedAt = Date.now();
    sessionCleanupState.selectedIds.clear();
    sessionCleanupState.previewTokenShown = "";
    const submitted = submitSettingsCommand(
      { action: "sessionCleanupScan", requestId },
      "正在扫描本地会话清单...",
      { preserveOverlay: true },
    );
    if (!submitted) sessionCleanupState.pendingRequestId = "";
    return submitted;
  }

  function requestSessionCleanupPreview() {
    const data = sessionCleanupFromPayload();
    const itemIds = Array.from(sessionCleanupState.selectedIds);
    const revision = String(data?.revision || "");
    if (!revision || !itemIds.length) {
      setSettingsStatus("请先扫描并选择可删除会话。", "error");
      return false;
    }
    const requestId = typedSettingsRequestId("session-cleanup-preview");
    sessionCleanupState.pendingRequestId = requestId;
    sessionCleanupState.previewTokenShown = "";
    const submitted = submitSettingsCommand({
      action: "sessionCleanupPreview",
      requestId,
      itemIds,
      inventoryRevision: revision,
    }, "正在生成永久删除确认...", { preserveOverlay: true });
    if (!submitted) sessionCleanupState.pendingRequestId = "";
    return submitted;
  }

  function openSessionCleanupExecuteConfirm() {
    const dialog = settingsDialogRoot();
    const data = sessionCleanupFromPayload();
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const token = String(operation?.confirmationToken || "");
    if (!dialog || String(operation?.state || "") !== "preview" || !token) return;
    const selectedIds = Array.isArray(operation?.selectedIds) ? operation.selectedIds : [];
    closeSettingsConfirm();
    const layer = document.createElement("div");
    layer.className = "codex-usage-hud-settings-confirm-layer";
    layer.dataset.settingsConfirm = "true";
    layer.dataset.sessionCleanupConfirm = "true";
    layer.dataset.sessionCleanupConfirmToken = token;
    const descendants = Math.max(0, Number(operation?.descendantCount || 0));
    const estimatedBytes = storageFormatBytes(operation?.estimatedBytes);
    layer.innerHTML = `<div class="codex-usage-hud-settings-confirm-card" data-tone="danger" role="alertdialog" aria-modal="true" aria-label="确认永久删除会话"><div class="codex-usage-hud-settings-confirm-main"><div class="codex-usage-hud-settings-confirm-danger-mark">${cleanupIconSvg("trash", "codex-usage-hud-cleanup-icon-lg")}</div><h2 class="codex-usage-hud-settings-confirm-title">永久删除 ${selectedIds.length} 个会话？</h2><p class="codex-usage-hud-settings-confirm-body">会话内容、索引和关联子任务将从本机移除。此操作不会进入回收站，也无法恢复。Codex App 的归档入口无法恢复这些会话。</p><div class="codex-usage-hud-settings-confirm-summary"><div><span>主会话</span><strong>${selectedIds.length}</strong></div><div><span>关联子任务</span><strong>${descendants}</strong></div><div><span>本地数据</span><strong>${escapeHtml(estimatedBytes)}</strong></div></div><div class="codex-usage-hud-settings-confirm-note">${cleanupIconSvg("alert")}<span>执行前会再次核验会话身份与运行状态；任何活动项都会被跳过并单独报告。</span></div></div><div class="codex-usage-hud-settings-confirm-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-confirm-cancel">取消</button><button type="button" class="codex-usage-hud-settings-action" data-action="session-cleanup-execute" data-danger="true">${cleanupIconSvg("trash")}永久删除</button></div></div>`;
    dialog.appendChild(layer);
    layer.querySelector('[data-action="session-cleanup-confirm-cancel"]')?.focus?.();
  }

  function restoreSessionCleanupConfirm(expectedToken) {
    const token = String(expectedToken || "");
    if (!token) return;
    queueMicrotask(() => {
      const modal = document.getElementById(settingsModalId);
      const data = sessionCleanupFromPayload();
      const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
      const currentToken = String(operation?.confirmationToken || "");
      if (
        !modal
        || modal.hidden
        || settingsActiveTab !== "storage"
        || cleanupActiveSection !== "sessions"
        || String(operation?.state || "") !== "preview"
        || currentToken !== token
      ) return;
      const existing = modal.querySelector('[data-session-cleanup-confirm="true"]');
      if (String(existing?.dataset.sessionCleanupConfirmToken || "") === token) return;
      openSessionCleanupExecuteConfirm();
    });
  }

  function executeSessionCleanup() {
    const data = sessionCleanupFromPayload();
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const itemIds = Array.isArray(operation?.selectedIds) ? operation.selectedIds : [];
    const requestId = typedSettingsRequestId("session-cleanup-execute");
    sessionCleanupState.pendingRequestId = requestId;
    closeSettingsConfirm();
    const submitted = submitSettingsCommand({
      action: "sessionCleanupExecute",
      requestId,
      itemIds,
      inventoryRevision: String(data?.revision || operation?.inventoryRevision || ""),
      confirmationToken: String(operation?.confirmationToken || ""),
    }, "正在逐项调用 Codex 官方永久删除...", { preserveOverlay: true });
    if (!submitted) sessionCleanupState.pendingRequestId = "";
    return submitted;
  }

  function storageSelectedItemIds() {
    return Array.from(document.querySelectorAll(`#${settingsModalId} [data-storage-item-id]`))
      .filter((node) => node instanceof HTMLInputElement && node.checked)
      .map((node) => String(node.dataset.storageItemId || "")).filter(Boolean);
  }

  function storagePreviewSelected(managedAction = "", managedItemId = "") {
    const data = fileManagementFromPayload();
    const itemIds = managedItemId ? [managedItemId] : storageSelectedItemIds();
    const revision = String(data?.revision || "");
    if (!revision || !itemIds.length) {
      setSettingsStatus("请先扫描并选择一个可候选项。", "error");
      return;
    }
    storagePreviewHidden = false;
    submitSettingsCommand({ action: "preview", itemIds, inventoryRevision: revision, managedAction }, managedAction ? "官方动作预览已提交..." : "清理预览请求已提交...", { preserveOverlay: true });
  }

  function openStorageExecuteConfirm() {
    const dialog = settingsDialogRoot();
    const data = fileManagementFromPayload();
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    if (!dialog || String(operation?.state || "") !== "preview") return;
    closeSettingsConfirm();
    const managedAction = String(operation?.managedAction || "");
    const count = Array.isArray(operation?.items) ? operation.items.length : 0;
    const layer = document.createElement("div");
    layer.className = "codex-usage-hud-settings-confirm-layer";
    layer.dataset.settingsConfirm = "true";
    layer.innerHTML = `<div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="确认存储操作"><div class="codex-usage-hud-settings-confirm-kicker">二次确认</div><div class="codex-usage-hud-settings-confirm-title">${escapeHtml(managedAction ? "执行官方 Codex 动作？" : "加入退出后清理队列？")}</div><div class="codex-usage-hud-settings-confirm-body">已预览 ${count} 个项。${managedAction ? "将只调用官方命令，不会直接删除会话或插件文件。" : "Codex 仍在运行时不会修改原始文件；退出后会再次检查。"}</div><div class="codex-usage-hud-settings-confirm-actions"><button type="button" class="codex-usage-hud-settings-action" data-action="storage-confirm-cancel">取消</button><button type="button" class="codex-usage-hud-settings-action" data-action="storage-confirm-execute" data-primary="true">确认</button></div></div>`;
    dialog.appendChild(layer);
    layer.querySelector('[data-action="storage-confirm-cancel"]')?.focus?.();
  }

  function backgroundUsageFormatCost(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return "估算不可用";
    }
    const amount = Number(value);
    const digits = amount >= 10 ? 2 : amount >= 1 ? 3 : 6;
    return `估算 $${amount.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "")}`;
  }

  function normalizeBackgroundUsageRange(value) {
    const normalized = String(value || "today").trim().toLowerCase();
    return new Set(["today", "7d", "30d", "all"]).has(normalized)
      ? normalized
      : "today";
  }

  function renderBackgroundUsageNotification(root, payload) {
    const notification = payload?.backgroundUsageNotification;
    const count = Math.max(0, Number(notification?.count || 0));
    const eventId = String(notification?.eventId || "").trim();
    const range = normalizeBackgroundUsageRange(notification?.range);
    const visible = count > 0 && !!eventId;
    root.querySelectorAll('[data-action="background-usage-open-notification"]').forEach((button) => {
      button.dataset.visible = String(visible);
      button.dataset.eventId = visible ? eventId : "";
      button.dataset.backgroundRange = visible ? range : "today";
      button.setAttribute("aria-hidden", String(!visible));
      button.tabIndex = visible ? 0 : -1;
      const label = visible
        ? `${count.toLocaleString()} 条未查看后台用量，打开用量总览`
        : "后台用量提醒";
      button.title = label;
      button.setAttribute("aria-label", label);
      const badge = button.querySelector('[data-field="backgroundUsageNotificationCount"]');
      if (badge) {
        badge.hidden = !visible;
        badge.textContent = count > 99 ? "99+" : String(count);
      }
    });
  }

  function markBackgroundUsageEventViewed(eventId) {
    const normalized = String(eventId || "").trim();
    if (!normalized) return;
    const data = backgroundUsageState.data;
    if (data && Array.isArray(data.events)) {
      backgroundUsageState.data = {
        ...data,
        events: data.events.map((event) => (
          String(event?.eventId || "") === normalized
            ? { ...event, unread: false }
            : event
        )),
      };
    }
    if (
      backgroundUsageState.detail
      && String(backgroundUsageState.detail?.eventId || "") === normalized
    ) {
      backgroundUsageState.detail = {
        ...backgroundUsageState.detail,
        unread: false,
      };
    }
  }

  function backgroundUsageTime(value, { compact = false } = {}) {
    const parsed = new Date(String(value || ""));
    if (Number.isNaN(parsed.getTime())) return "--";
    return parsed.toLocaleString([], compact
      ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
      : { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function backgroundUsageRedactedPrompt(value) {
    return String(value || "")
      .replace(/([A-Za-z]:\\Users\\)[^\\/\r\n]+/gi, "$1[user]")
      .replace(/(\/Users\/)[^\/\r\n]+/g, "$1[user]")
      .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[email]");
  }

  function backgroundUsageEventHtml(event) {
    const selected = String(event?.eventId || "") === backgroundUsageState.selectedEventId;
    const unread = event?.unread === true;
    const models = Array.isArray(event?.models) ? event.models.filter(Boolean) : [];
    const modelText = models.join(" + ") || "未知模型";
    const eventTime = backgroundUsageTime(event?.lastSeenAt, { compact: true });
    const eventTimeTitle = backgroundUsageTime(event?.lastSeenAt);
    return `
      <button type="button" class="codex-usage-hud-background-event"
        data-action="background-usage-select" data-event-id="${escapeHtml(event?.eventId || "")}" data-selected="${selected}" data-unread="${unread}">
        ${unread ? '<span class="codex-usage-hud-background-unread-dot" aria-label="未查看"></span>' : ""}
        <span class="codex-usage-hud-background-event-head">
          <span class="codex-usage-hud-background-event-title">${escapeHtml(event?.featureLabel || "未知后台任务")}</span>
          <span class="codex-usage-hud-background-status" title="${escapeHtml(eventTimeTitle)}">${escapeHtml(eventTime)}</span>
        </span>
        <span class="codex-usage-hud-background-event-meta">${escapeHtml(modelText)}</span>
        <span class="codex-usage-hud-background-event-totals">
          <strong>${escapeHtml(humanizeTokens(event?.totalTokens || 0))} tokens</strong>
          <span>${Number(event?.requestCount || 0).toLocaleString()} 次请求</span>
          <span>${escapeHtml(backgroundUsageFormatCost(event?.estimatedCostUsd))}</span>
        </span>
      </button>
    `;
  }

  function backgroundUsageDetailHtml(detail) {
    if (backgroundUsageState.detailLoading && !detail) {
      return '<div class="codex-usage-hud-background-empty">正在读取请求明细...</div>';
    }
    if (!detail || typeof detail !== "object") {
      return '<div class="codex-usage-hud-background-empty">选择一项后台任务查看请求明细。</div>';
    }
    const models = Array.isArray(detail.models) ? detail.models.filter(Boolean) : [];
    const requests = Array.isArray(detail.requests) ? detail.requests : [];
    const detailTime = backgroundUsageTime(detail.lastSeenAt, { compact: true });
    const detailTimeTitle = backgroundUsageTime(detail.lastSeenAt);
    const rawPrompt = String(detail.prompt || "");
    const redactedPrompt = backgroundUsageRedactedPrompt(rawPrompt);
    const promptText = backgroundUsageState.promptExpanded
      ? rawPrompt
      : `${redactedPrompt.slice(0, 520)}${redactedPrompt.length > 520 ? "\n…" : ""}`;
    const requestRows = requests.map((request, index) => `
      <div class="codex-usage-hud-background-request">
        <span>${escapeHtml(backgroundUsageTime(request?.occurredAt, { compact: true }))}</span>
        <span class="codex-usage-hud-background-request-endpoint">POST ${escapeHtml(request?.endpoint || "/responses")}</span>
        <span title="${escapeHtml(request?.model || "")}">${escapeHtml(request?.model || "未知模型")}</span>
        <strong>${escapeHtml(humanizeTokens(request?.totalTokens || 0))}</strong>
        <span>${escapeHtml(backgroundUsageFormatCost(request?.estimatedCostUsd))}</span>
        <span class="codex-usage-hud-background-request-index">#${index + 1}</span>
      </div>
    `).join("");
    const processText = String(detail.processUuid || "");
    const threadText = String(detail.threadId || detail.eventId || "");
    return `
      <div class="codex-usage-hud-background-detail-head">
        <div>
          <h3>${escapeHtml(detail.featureLabel || "未知后台任务")}</h3>
          <span class="codex-usage-hud-background-detail-sub">Codex App 后台用量 · 本地记录</span>
        </div>
        <span class="codex-usage-hud-background-status" title="${escapeHtml(detailTimeTitle)}">${escapeHtml(detailTime)}</span>
      </div>
      <div class="codex-usage-hud-background-detail-grid">
        <div><span>模型</span><strong>${escapeHtml(models.join(" + ") || "未知")}</strong></div>
        <div><span>请求</span><strong>${Number(detail.requestCount || 0).toLocaleString()} 次</strong></div>
        <div title="${escapeHtml(threadText)}"><span>线程</span><strong>${escapeHtml(threadText ? `…${threadText.slice(-12)}` : "--")}</strong></div>
        <div title="${escapeHtml(processText)}"><span>进程</span><strong>${escapeHtml(processText.split(":").slice(0, 2).join(":") || "--")}</strong></div>
        <div class="codex-usage-hud-background-detail-wide"><span>时段</span><strong>${escapeHtml(backgroundUsageTime(detail.firstSeenAt))} - ${escapeHtml(backgroundUsageTime(detail.lastSeenAt))}</strong></div>
        <div class="codex-usage-hud-background-detail-wide" title="${escapeHtml(detail.cwd || "")}"><span>工作目录</span><strong>${escapeHtml(detail.cwd || "--")}</strong></div>
      </div>
      <section class="codex-usage-hud-background-requests">
        <div class="codex-usage-hud-background-section-title">请求明细 <span>${requests.length}</span></div>
        <div class="codex-usage-hud-background-request-list">${requestRows || '<div class="codex-usage-hud-background-empty">没有可用请求明细。</div>'}</div>
      </section>
      ${rawPrompt ? `
        <section class="codex-usage-hud-background-prompt">
          <div class="codex-usage-hud-background-section-title">
            <span>请求内容</span>
            <span>
              <button type="button" class="codex-usage-hud-settings-link" data-action="background-usage-toggle-prompt">${backgroundUsageState.promptExpanded ? "收起原文" : "展开原文"}</button>
              <button type="button" class="codex-usage-hud-settings-link" data-action="background-usage-copy-prompt">复制原文</button>
            </span>
          </div>
          <pre data-expanded="${backgroundUsageState.promptExpanded}">${escapeHtml(promptText)}</pre>
        </section>
      ` : ""}
    `;
  }

  function backgroundUsageSessionRankingMode() {
    const key = String(backgroundUsageState.feature || "");
    if (key === "__session_top_usage__") return "usage";
    if (key === "__session_top_cost__") return "cost";
    return "";
  }

  function backgroundUsageSessionRankingRange() {
    if (backgroundUsageState.range === "today") return "today";
    if (backgroundUsageState.range === "30d") return "month";
    return "week";
  }

  function backgroundUsageSessionRankingDetailHtml(session, mode) {
    if (!session || typeof session !== "object") {
      return '<div class="codex-usage-hud-background-empty">选择一个会话查看用量汇总。</div>';
    }
    const sessionId = String(session?.id || session?.sessionId || "");
    const actionable = (session?.actionable === true || session?.canActivate === true) && !!sessionId;
    const coverage = session?.costCoverage && typeof session.costCoverage === "object"
      ? session.costCoverage
      : {};
    const totalEvents = Math.max(0, Number(coverage?.totalEventCount || 0));
    const pricedEvents = Math.max(0, Number(coverage?.pricedEventCount || 0));
    const completeCost = coverage?.hasCompleteCost !== false && (!totalEvents || pricedEvents >= totalEvents);
    const title = usageInsightsRankLabel(session, "sessions");
    const latestEventAt = String(session?.latestEventAt || "");
    const workdir = String(session?.workdirName || "").trim();
    const modelNames = usageInsightsSessionModelNames(session);
    const modelText = modelNames.join("、") || "未知模型";
    const costText = usageInsightsFormatCost(session?.costUsd);
    const costNote = completeCost ? "HUD 本地估算" : (session?.costUsd == null ? "费用不可估" : "费用部分可估");
    return `
      <div class="codex-usage-hud-background-detail-head">
        <div>
          <h3>${escapeHtml(title)}</h3>
          <span class="codex-usage-hud-background-detail-sub">会话用量 · 本地聚合</span>
        </div>
        ${actionable ? '<button type="button" class="codex-usage-hud-settings-action" data-action="usage-insights-session" data-session-id="' + escapeHtml(sessionId) + '" data-target-title="' + escapeHtml(title) + '" data-workdir="' + escapeHtml(workdir) + '">打开会话</button>' : '<span class="codex-usage-hud-background-status">仅统计</span>'}
      </div>
      <div class="codex-usage-hud-background-detail-grid">
        <div><span>Provider</span><strong>${escapeHtml(String(session?.provider || "未知"))}</strong></div>
        <div><span>Tokens</span><strong>${escapeHtml(usageInsightsFormatTokens(session?.tokens ?? session?.totalTokens))}</strong></div>
        <div><span>输入</span><strong>${escapeHtml(usageInsightsFormatTokens(session?.inputTokens))}</strong></div>
        <div><span>缓存命中</span><strong>${escapeHtml(usageInsightsFormatRatio(session?.cacheRatio))}</strong></div>
        <div><span>金额</span><strong>${escapeHtml(costText)}</strong></div>
        <div><span>费用覆盖</span><strong>${escapeHtml(costNote)}</strong></div>
        <div><span>已计价请求</span><strong>${Math.min(pricedEvents, totalEvents).toLocaleString()} / ${totalEvents.toLocaleString()}</strong></div>
        <div><span>最近活动</span><strong>${escapeHtml(latestEventAt ? backgroundUsageTime(latestEventAt, { compact: true }) : "--")}</strong></div>
        <div class="codex-usage-hud-background-detail-full" title="${escapeHtml(modelText)}"><span>使用模型${modelNames.length > 1 ? `（${modelNames.length} 个）` : ""}</span><strong>${escapeHtml(modelText)}</strong></div>
        <div class="codex-usage-hud-background-detail-full" title="${escapeHtml(workdir)}"><span>工作目录</span><strong>${escapeHtml(workdir || "--")}</strong></div>
      </div>
      <section class="codex-usage-hud-background-requests">
        <div class="codex-usage-hud-background-section-title"><span>${escapeHtml(mode === "cost" ? "金额排名" : "用量排名")}</span><span>${escapeHtml(mode === "cost" ? costText : `${usageInsightsFormatTokens(session?.tokens ?? session?.totalTokens)} tokens`)}</span></div>
        <div class="codex-usage-hud-insights-meta">会话详情只展示本地聚合统计；打开会话后可继续查看原对话。</div>
      </section>
    `;
  }

  function backgroundUsageFeatureOptionsHtml(featureOptions) {
    const sessionRankingOptions = [
      ["__session_top_usage__", "Top10会话用量"],
      ["__session_top_cost__", "Top10会话金额"],
    ];
    const selected = String(backgroundUsageState.feature || "");
    const reserved = new Set(sessionRankingOptions.map(([key]) => key));
    const backgroundOptions = featureOptions
      .filter((item) => !reserved.has(String(item?.key || "")))
      .map((item) => `<option value="${escapeHtml(item?.key || "")}" ${selected === String(item?.key || "") ? "selected" : ""}>${escapeHtml(item?.label || item?.key || "")}</option>`)
      .join("");
    const sessionOptions = sessionRankingOptions
      .map(([key, label]) => `<option value="${key}" ${selected === key ? "selected" : ""}>${label}</option>`)
      .join("");
    return `<option value="">全部后台功能</option><optgroup label="后台任务">${backgroundOptions || '<option value="" disabled>暂无后台任务功能</option>'}</optgroup><optgroup label="用户会话排行">${sessionOptions}</optgroup>`;
  }

  function backgroundUsageSessionRankingPanelHtml(featureOptions, modelOptions) {
    const mode = backgroundUsageSessionRankingMode();
    const data = usageInsightsFromPayload();
    const state = String(data?.state || data?.status || "").toLowerCase();
    const range = backgroundUsageSessionRankingRange();
    const scoped = data ? usageInsightsRangeData(data, range) : {};
    const totals = scoped?.totals || {};
    const coverage = scoped?.costCoverage || {};
    const totalEvents = Math.max(0, Number(coverage?.totalEventCount || 0));
    const pricedEvents = Math.max(0, Number(coverage?.pricedEventCount || 0));
    const completeCost = coverage?.hasCompleteCost !== false && (!totalEvents || pricedEvents >= totalEvents);
    const rankingKey = mode === "cost" ? "topSessionsByCost" : "topSessionsByUsage";
    const sessions = Array.isArray(scoped?.[rankingKey]) ? scoped[rankingKey] : [];
    const selectedSessionId = sessions.some((item) => String(item?.id || item?.sessionId || "") === backgroundUsageState.selectedSessionId)
      ? backgroundUsageState.selectedSessionId
      : String(sessions[0]?.id || sessions[0]?.sessionId || "");
    const selectedSession = sessions.find((item) => String(item?.id || item?.sessionId || "") === selectedSessionId) || null;
    const title = mode === "cost" ? "Top10金额" : "Top10用量";
    const rankingRows = usageInsightsRankingRowsHtml(
      sessions,
      "sessions",
      {
        limit: 10,
        metric: mode === "cost" ? "cost" : "tokens",
        selectedSessionId,
        sessionAction: "background-usage-session-select",
      },
    );
    const error = String(data?.error || usageInsightsState.error || "");
    const loading = state === "loading" || (!data && !!usageInsightsState.refreshRequestId);
    const listHtml = loading
      ? '<div class="codex-usage-hud-background-empty" role="status">正在汇总会话排行...</div>'
      : error || state === "error" || state === "failed"
        ? `<div class="codex-usage-hud-background-empty" data-kind="error" role="alert">${escapeHtml(error || "会话排行生成失败，请重试。")}</div>`
        : `<div class="codex-usage-hud-background-event-list codex-usage-hud-session-ranking-list">${rankingRows || `<div class="codex-usage-hud-background-empty">${mode === "cost" ? "暂无可估金额的会话。" : "暂无会话用量数据。"}</div>`}</div>`;
    const sessionCount = Number(totals?.sessionCount || 0);
    const primaryValue = mode === "cost"
      ? usageInsightsFormatCost(totals?.costUsd)
      : usageInsightsFormatTokens(totals?.tokens ?? totals?.totalTokens);
    const primaryMeta = mode === "cost"
      ? (completeCost ? "HUD 本地估算" : "部分会话可估算")
      : "已确认会话 token";
    const rankingNote = mode === "cost"
      ? "仅按有本地估算费用的会话排序"
      : "按已确认的会话 token 排序";
    return `
      <div class="codex-usage-hud-background" data-background-usage-root="true"
        data-background-usage-filter-key="${escapeHtml(backgroundUsageFilterKey())}"
        aria-busy="${loading}">
        <div class="codex-usage-hud-background-metrics">
          <div><span>${escapeHtml(title)}</span><strong>${escapeHtml(primaryValue)}</strong><small>${escapeHtml(primaryMeta)}</small></div>
          <div><span>Tokens</span><strong>${escapeHtml(usageInsightsFormatTokens(totals?.tokens ?? totals?.totalTokens))}</strong><small>本地会话统计</small></div>
          <div><span>会话</span><strong>${sessionCount.toLocaleString()}</strong><small>最多展示 10 项</small></div>
          <div><span>缓存命中</span><strong>${escapeHtml(usageInsightsFormatRatio(totals?.cacheRatio))}</strong><small>${escapeHtml(data?.generatedAt ? `更新于 ${backgroundUsageTime(data.generatedAt, { compact: true })}` : "本地聚合")}</small></div>
        </div>
        <div class="codex-usage-hud-background-toolbar">
          <div class="codex-usage-hud-background-range" role="group" aria-label="会话排行范围">
            ${[["today", "今天"], ["7d", "近 7 天"], ["30d", "近 30 天"]].map(([key, label]) => `<button type="button" data-action="background-usage-range" data-background-range="${key}" data-active="${backgroundUsageState.range === key}">${label}</button>`).join("")}
          </div>
          <select data-background-usage-filter="feature" aria-label="功能筛选">
            ${backgroundUsageFeatureOptionsHtml(featureOptions)}
          </select>
          <select data-background-usage-filter="model" aria-label="模型筛选" disabled title="会话排行不按后台模型筛选">
            <option value="">全部模型</option>
            ${modelOptions.map((model) => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join("")}
          </select>
        </div>
        <div class="codex-usage-hud-background-master-detail" data-session-ranking="true">
          <section class="codex-usage-hud-background-history">
            <div class="codex-usage-hud-background-section-title"><span>${escapeHtml(title)}</span><span>${sessions.length} 项</span></div>
            <div class="codex-usage-hud-insights-meta codex-usage-hud-session-ranking-note">${escapeHtml(rankingNote)}</div>
            ${listHtml}
          </section>
          <section class="codex-usage-hud-background-detail" data-session-ranking-detail="true">
            ${loading || error || state === "error" || state === "failed"
              ? '<div class="codex-usage-hud-background-empty">等待会话排行就绪。</div>'
              : backgroundUsageSessionRankingDetailHtml(selectedSession, mode)}
          </section>
        </div>
      </div>
    `;
  }

  function backgroundUsagePanelHtml() {
    const data = backgroundUsageState.data;
    const summary = data?.summary && typeof data.summary === "object" ? data.summary : {};
    const events = Array.isArray(data?.events) ? data.events : [];
    const filters = data?.filters && typeof data.filters === "object" ? data.filters : {};
    const featureOptions = Array.isArray(filters.features) ? filters.features : [];
    const modelOptions = Array.isArray(filters.models) ? filters.models : [];
    if (backgroundUsageSessionRankingMode()) {
      return backgroundUsageSessionRankingPanelHtml(featureOptions, modelOptions);
    }
    if (!backgroundUsageBridgeUrl()) {
      return '<div class="codex-usage-hud-background" data-background-usage-root="true"><div class="codex-usage-hud-background-empty">用量总览当前不可用。</div></div>';
    }
    const modelSummary = Array.isArray(summary.models) && summary.models.length
      ? summary.models.join(" + ")
      : "--";
    const costNote = summary.costComplete === false ? "部分模型缺少价格" : "HUD 估算";
    return `
      <div class="codex-usage-hud-background" data-background-usage-root="true"
        data-background-usage-filter-key="${escapeHtml(backgroundUsageFilterKey())}"
        aria-busy="${backgroundUsageState.loading}">
        <div class="codex-usage-hud-background-metrics">
          <div><span>筛选费用</span><strong>${escapeHtml(backgroundUsageFormatCost(summary.estimatedCostUsd))}</strong><small>${escapeHtml(costNote)}</small></div>
          <div><span>Tokens</span><strong>${escapeHtml(humanizeTokens(summary.totalTokens || 0))}</strong><small>本机日志值</small></div>
          <div><span>后台任务</span><strong>${Number(summary.eventCount || 0).toLocaleString()}</strong><small>${Number(summary.requestCount || 0).toLocaleString()} 次请求</small></div>
          <div title="${escapeHtml(modelSummary)}"><span>使用模型</span><strong>${escapeHtml(modelSummary)}</strong><small>${Array.isArray(summary.models) ? summary.models.length : 0} 个模型</small></div>
        </div>
        <div class="codex-usage-hud-background-toolbar">
          <div class="codex-usage-hud-background-range" role="group" aria-label="日期范围">
            ${[["today", "今天"], ["7d", "近 7 天"], ["30d", "近 30 天"], ["all", "全部"]].map(([key, label]) => `<button type="button" data-action="background-usage-range" data-background-range="${key}" data-active="${backgroundUsageState.range === key}">${label}</button>`).join("")}
          </div>
          <select data-background-usage-filter="feature" aria-label="功能筛选">
            ${backgroundUsageFeatureOptionsHtml(featureOptions)}
          </select>
          <select data-background-usage-filter="model" aria-label="模型筛选">
            <option value="">全部模型</option>
            ${modelOptions.map((model) => `<option value="${escapeHtml(model)}" ${backgroundUsageState.model === String(model) ? "selected" : ""}>${escapeHtml(model)}</option>`).join("")}
          </select>
        </div>
        ${backgroundUsageState.error ? `<div class="codex-usage-hud-background-error">${escapeHtml(backgroundUsageState.error)}</div>` : ""}
        <div class="codex-usage-hud-background-master-detail">
          <section class="codex-usage-hud-background-history">
            <div class="codex-usage-hud-background-section-title"><span>后台任务历史</span><span>${events.length} 项</span></div>
            <div class="codex-usage-hud-background-event-list">
              ${events.map(backgroundUsageEventHtml).join("") || `<div class="codex-usage-hud-background-empty">${backgroundUsageState.loading ? "正在读取用量总览..." : "当前筛选没有后台任务。"}</div>`}
            </div>
          </section>
          <section class="codex-usage-hud-background-detail"
            data-background-usage-detail-event-id="${escapeHtml(backgroundUsageState.selectedEventId)}"
            data-background-usage-detail-loaded="${!!backgroundUsageState.detail}">
            ${backgroundUsageDetailHtml(backgroundUsageState.detail)}
          </section>
        </div>
      </div>
    `;
  }

  function backgroundUsageFilterKey() {
    return JSON.stringify([
      backgroundUsageState.range,
      backgroundUsageState.feature,
      backgroundUsageState.model,
    ]);
  }

  function captureBackgroundUsageScrollPositions() {
    const modal = document.getElementById(settingsModalId);
    const body = modal?.querySelector?.(".codex-usage-hud-settings-body");
    const panel = body?.querySelector?.('[data-background-usage-root="true"]');
    if (!body || !panel) return;
    const filterKey = String(panel.dataset.backgroundUsageFilterKey || "");
    const history = panel.querySelector(".codex-usage-hud-background-history");
    const detail = panel.querySelector(".codex-usage-hud-background-detail");
    const detailEventId = String(
      detail?.dataset?.backgroundUsageDetailEventId || "",
    );
    if (filterKey) {
      backgroundUsageBodyScrollTops.set(filterKey, Number(body.scrollTop || 0));
      backgroundUsageHistoryScrollTops.set(
        filterKey,
        Number(history?.scrollTop || 0),
      );
    }
    if (
      detailEventId
      && detail?.dataset?.backgroundUsageDetailLoaded === "true"
    ) {
      backgroundUsageDetailScrollTops.set(
        detailEventId,
        Number(detail?.scrollTop || 0),
      );
    }
  }

  function restoreBackgroundUsageScrollPositions() {
    const modal = document.getElementById(settingsModalId);
    const body = modal?.querySelector?.(".codex-usage-hud-settings-body");
    const panel = body?.querySelector?.('[data-background-usage-root="true"]');
    if (!body || !panel) return;
    const filterKey = String(panel.dataset.backgroundUsageFilterKey || "");
    const history = panel.querySelector(".codex-usage-hud-background-history");
    const detail = panel.querySelector(".codex-usage-hud-background-detail");
    const detailEventId = String(
      detail?.dataset?.backgroundUsageDetailEventId || "",
    );
    const apply = () => {
      body.scrollTop = Number(backgroundUsageBodyScrollTops.get(filterKey) || 0);
      if (history) {
        history.scrollTop = Number(
          backgroundUsageHistoryScrollTops.get(filterKey) || 0,
        );
      }
      if (detail) {
        detail.scrollTop = Number(
          backgroundUsageDetailScrollTops.get(detailEventId) || 0,
        );
      }
    };
    apply();
    requestAnimationFrame(apply);
  }

  function clearBackgroundUsageRequestTimeout(kind) {
    if (kind === "query") {
      clearTimeout(backgroundUsageQueryTimeoutId);
      backgroundUsageQueryTimeoutId = 0;
      return;
    }
    clearTimeout(backgroundUsageDetailTimeoutId);
    backgroundUsageDetailTimeoutId = 0;
  }

  function scheduleBackgroundUsageRequestTimeout(kind, requestId, eventId = "") {
    clearBackgroundUsageRequestTimeout(kind);
    const onTimeout = () => {
      if (kind === "query") {
        if (requestId !== backgroundUsageState.queryRequestId) return;
        backgroundUsageQueryTimeoutId = 0;
        backgroundUsageState.loading = false;
        backgroundUsageState.error = "用量总览读取超时，请重试。";
      } else {
        if (
          requestId !== backgroundUsageState.detailRequestId
          || eventId !== backgroundUsageState.selectedEventId
        ) return;
        backgroundUsageDetailTimeoutId = 0;
        backgroundUsageState.detailLoading = false;
        backgroundUsageState.error = "请求明细读取超时，请重试。";
      }
      syncBackgroundUsagePanel();
    };
    const timeoutId = setTimeout(onTimeout, backgroundUsageRequestTimeoutMs);
    if (kind === "query") backgroundUsageQueryTimeoutId = timeoutId;
    else backgroundUsageDetailTimeoutId = timeoutId;
  }

  async function fetchBackgroundUsageWithTimeout(url, options = {}) {
    const controller = new AbortController();
    const timeoutId = setTimeout(
      () => controller.abort(),
      backgroundUsageRequestTimeoutMs,
    );
    try {
      return await fetch(url, {
        cache: "no-store",
        ...options,
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeoutId);
    }
  }

  function syncBackgroundUsagePanel() {
    const modal = document.getElementById(settingsModalId);
    if (!modal || modal.hidden || settingsActiveTab !== "backgroundUsage") return;
    const body = modal.querySelector(".codex-usage-hud-settings-body");
    if (!body) return;
    captureBackgroundUsageScrollPositions();
    body.innerHTML = backgroundUsagePanelHtml();
    restoreBackgroundUsageScrollPositions();
  }

  async function loadBackgroundUsageDetail(eventId, { markViewed = false } = {}) {
    const normalized = String(eventId || "").trim();
    const url = backgroundUsageEndpoint("/detail");
    if (!normalized || backgroundUsageSessionRankingMode()) return;
    const requestSeq = ++backgroundUsageDetailSeq;
    backgroundUsageState.detailLoading = true;
    backgroundUsageState.promptExpanded = false;
    clearBackgroundUsageRequestTimeout("detail");
    backgroundUsageState.detailRequestId = "";
    syncBackgroundUsagePanel();
    const bindingRequestId = submitBackgroundUsageCommand(
      "backgroundUsageDetail",
      { eventId: normalized, markViewed: markViewed === true },
    );
    if (bindingRequestId) {
      backgroundUsageState.detailRequestId = bindingRequestId;
      scheduleBackgroundUsageRequestTimeout("detail", bindingRequestId, normalized);
      return;
    }
    if (!url) {
      backgroundUsageState.detailLoading = false;
      backgroundUsageState.error ||= "用量总览桥接未连接";
      syncBackgroundUsagePanel();
      return;
    }
    url.searchParams.set("eventId", normalized);
    try {
      if (markViewed) {
        const confirmUrl = backgroundUsageEndpoint("/confirm");
        if (!confirmUrl) throw new Error("用量总览确认桥接未连接");
        const confirmResponse = await fetchBackgroundUsageWithTimeout(
          confirmUrl.toString(),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ eventId: normalized }),
          },
        );
        const confirmPayload = await confirmResponse.json();
        if (!confirmResponse.ok || confirmPayload?.status !== "ok") {
          throw new Error(confirmPayload?.message || `HTTP ${confirmResponse.status}`);
        }
      }
      const response = await fetchBackgroundUsageWithTimeout(url.toString());
      const payload = await response.json();
      if (!response.ok || payload?.status !== "ok") {
        throw new Error(payload?.message || `HTTP ${response.status}`);
      }
      if (requestSeq !== backgroundUsageDetailSeq || backgroundUsageState.selectedEventId !== normalized) return;
      backgroundUsageState.detail = payload.backgroundUsageDetail || null;
      if (markViewed && backgroundUsageState.detail?.unread === false) {
        markBackgroundUsageEventViewed(normalized);
      }
      backgroundUsageState.error = "";
    } catch (error) {
      if (requestSeq !== backgroundUsageDetailSeq) return;
      backgroundUsageState.error = error?.name === "AbortError"
        ? "请求明细读取超时，请重试。"
        : `请求明细读取失败：${error?.message || error}`;
    } finally {
      if (requestSeq === backgroundUsageDetailSeq) {
        backgroundUsageState.detailLoading = false;
        syncBackgroundUsagePanel();
      }
    }
  }

  async function loadBackgroundUsage({ eventId = "", force = false } = {}) {
    if (backgroundUsageSessionRankingMode()) {
      clearBackgroundUsageRequestTimeout("query");
      clearBackgroundUsageRequestTimeout("detail");
      backgroundUsageState.loading = false;
      backgroundUsageState.detailLoading = false;
      const insights = usageInsightsFromPayload();
      const state = String(insights?.state || insights?.status || "").toLowerCase();
      if (force || !insights || state === "idle" || state === "failed" || state === "error") {
        requestUsageInsightsRefresh({ force });
      }
      syncBackgroundUsagePanel();
      return;
    }
    const url = backgroundUsageEndpoint();
    const revision = Math.max(0, Number(currentPayload()?.backgroundUsageRevision || 0));
    const requestedEventId = String(eventId || backgroundUsageState.selectedEventId || "").trim();
    if (!force && backgroundUsageState.data && backgroundUsageState.loadedRevision === revision) {
      if (requestedEventId && requestedEventId !== backgroundUsageState.selectedEventId) {
        backgroundUsageState.selectedEventId = requestedEventId;
        backgroundUsageState.detail = null;
        syncBackgroundUsagePanel();
        await loadBackgroundUsageDetail(requestedEventId);
      }
      return;
    }
    const requestSeq = ++backgroundUsageFetchSeq;
    backgroundUsageState.loading = true;
    backgroundUsageState.error = "";
    clearBackgroundUsageRequestTimeout("query");
    backgroundUsageState.queryRequestId = "";
    syncBackgroundUsagePanel();
    const bindingRequestId = submitBackgroundUsageCommand(
      "backgroundUsageQuery",
      {
        filters: {
          range: backgroundUsageState.range,
          feature: backgroundUsageState.feature,
          model: backgroundUsageState.model,
          eventId: requestedEventId,
        },
      },
    );
    if (bindingRequestId) {
      backgroundUsageState.queryRequestId = bindingRequestId;
      scheduleBackgroundUsageRequestTimeout("query", bindingRequestId);
      return;
    }
    if (!url) {
      backgroundUsageState.loading = false;
      backgroundUsageState.error ||= "用量总览桥接未连接";
      syncBackgroundUsagePanel();
      return;
    }
    url.searchParams.set("range", backgroundUsageState.range);
    if (backgroundUsageState.feature) url.searchParams.set("feature", backgroundUsageState.feature);
    if (backgroundUsageState.model) url.searchParams.set("model", backgroundUsageState.model);
    if (requestedEventId) url.searchParams.set("eventId", requestedEventId);
    try {
      const response = await fetchBackgroundUsageWithTimeout(url.toString());
      const payload = await response.json();
      if (!response.ok || payload?.status !== "ok") {
        throw new Error(payload?.message || `HTTP ${response.status}`);
      }
      if (requestSeq !== backgroundUsageFetchSeq) return;
      backgroundUsageState.data = payload.backgroundUsage || null;
      backgroundUsageState.loadedRevision = revision;
      backgroundUsageState.selectedEventId = String(
        payload?.backgroundUsage?.selectedEventId || requestedEventId || ""
      );
      backgroundUsageState.detail = null;
      backgroundUsageState.error = "";
      syncBackgroundUsagePanel();
      if (backgroundUsageState.selectedEventId) {
        await loadBackgroundUsageDetail(backgroundUsageState.selectedEventId);
      }
    } catch (error) {
      if (requestSeq !== backgroundUsageFetchSeq) return;
      backgroundUsageState.error = error?.name === "AbortError"
        ? "用量总览读取超时，请重试。"
        : `用量总览读取失败：${error?.message || error}`;
      backgroundUsageState.data = null;
      backgroundUsageState.detail = null;
      syncBackgroundUsagePanel();
    } finally {
      if (requestSeq === backgroundUsageFetchSeq) {
        backgroundUsageState.loading = false;
        syncBackgroundUsagePanel();
      }
    }
  }

  function renderSettingsModal(tab = "settings", status = "", { resetProviderDraft = false } = {}) {
    const root = document.getElementById(rootId);
    const modal = document.getElementById(settingsModalId);
    if (!root || !modal) return;
    const sessionCleanupConfirmToken = String(
      modal.querySelector('[data-session-cleanup-confirm="true"]')?.dataset.sessionCleanupConfirmToken || "",
    );
    if (!modal.hidden) {
      captureSettingsProviderForm();
      if (settingsActiveTab === "backgroundUsage") {
        captureBackgroundUsageScrollPositions();
      }
      if (settingsActiveTab === "storage") captureStorageUiState();
    }
    const settings = hudSettingsFromPayload();
    const activeTab = ["storage", "backgroundUsage", "support", "about"].includes(tab) ? tab : "settings";
    settingsActiveTab = activeTab;
    if (activeTab === "settings") ensureSettingsProviderDraft(settings, resetProviderDraft);
    const path = settingsPathLabel();
    const bridge = settingsBridgeUrl();
    const defaultStatus = activeTab === "about"
      ? "可检查 GitHub Release 并启动 Windows 安装器。"
      : activeTab === "storage"
        ? "扫描与永久删除只在用户明确操作时执行。"
        : activeTab === "backgroundUsage"
          ? "Tokens 来自本机日志；费用均为 HUD 估算。"
        : (bridge ? "设置将保存到本地配置文件" : "设置桥接未连接，可导出 JSON 手动写入配置文件");
    modal.innerHTML = `
      <div class="codex-usage-hud-settings-dialog" data-active-tab="${escapeHtml(activeTab)}" role="dialog" aria-modal="true" aria-label="codex-usage-hud 设置">
        <div class="codex-usage-hud-settings-head">
          <div class="codex-usage-hud-settings-title">codex-usage-hud v${escapeHtml(appVersion())}</div>
          <button type="button" class="codex-usage-hud-settings-close" data-action="settings-close" aria-label="关闭">×</button>
        </div>
        <div class="codex-usage-hud-settings-tabs" role="tablist">
          <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="settings" data-active="${activeTab === "settings"}">设置</button>
          <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="storage" data-active="${activeTab === "storage"}">空间清理</button>
          <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="backgroundUsage" data-active="${activeTab === "backgroundUsage"}">用量总览</button>
          <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="support" data-active="${activeTab === "support"}">请作者喝咖啡</button>
          <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="about" data-active="${activeTab === "about"}">版本更新</button>
        </div>
        <div class="codex-usage-hud-settings-body">
          ${activeTab === "support" ? supportPanelHtml(settings, path) : activeTab === "about" ? aboutPanelHtml(path) : activeTab === "storage" ? storagePanelHtml() : activeTab === "backgroundUsage" ? backgroundUsagePanelHtml() : settingsPanelHtml(settings, bridge, path)}
        </div>
        <div class="codex-usage-hud-settings-actions">
          <div class="codex-usage-hud-settings-status" data-settings-status="true">${escapeHtml(status || defaultStatus)}</div>
          <div>
            ${activeTab === "settings" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-export">导出 JSON</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-restart" hidden>立即重启 HUD</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-save" data-primary="true">保存</button>' : activeTab === "backgroundUsage" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="background-usage-refresh">刷新</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-close" data-primary="true">关闭</button>' : activeTab === "about" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-check-update">检查更新</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-install-update" data-primary="true">安装更新</button>' : '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-close" data-primary="true">关闭</button>'}
          </div>
        </div>
      </div>
    `;
    modal.hidden = false;
    ensureRestReminderCountdownTicker();
    updateAboutActionButtons(currentUpdateState());
    if (activeTab === "storage") {
      restoreStorageUiState();
      restoreSessionCleanupConfirm(sessionCleanupConfirmToken);
    }
    if (activeTab === "backgroundUsage") {
      restoreBackgroundUsageScrollPositions();
      const revision = Math.max(0, Number(currentPayload()?.backgroundUsageRevision || 0));
      if (!backgroundUsageState.data || backgroundUsageState.loadedRevision !== revision) {
        void loadBackgroundUsage({ force: true });
      } else if (backgroundUsageState.selectedEventId && !backgroundUsageState.detail) {
        // Keep default markViewed=false here: explicit list click and the open
        // notification path pass markViewed:true themselves.
        void loadBackgroundUsageDetail(backgroundUsageState.selectedEventId);
      }
    }
  }

  function settingsPanelHtml(settings, bridge, path) {
    ensureSettingsProviderDraft(settings);
    const overlaySelectableMax = workOverlaySelectableMax();
    const overlayValue = Math.min(
      overlaySelectableMax,
      Math.max(0, Math.round(Number(settings.work_overlay_max_items) || 0)),
    );
    const overlayOptions = Array.from({ length: overlaySelectableMax + 1 }, (_, index) => `
      <option value="${index}" ${overlayValue === index ? "selected" : ""}>${index}${index === 0 ? " - 不启用" : ""}</option>
    `).join("");
    const weekdayOptions = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
      .map((label, index) => `<option value="${index}" ${Number(settings.weekly_reset_weekday) === index ? "selected" : ""}>${label}</option>`)
      .join("");
    return `
      <div class="codex-usage-hud-settings-grid">
        <div class="codex-usage-hud-settings-field">
          <label>日额度 USD</label>
          <input data-setting-key="daily_budget_usd" type="number" min="0" step="0.01" value="${escapeHtml(settings.daily_budget_usd)}">
        </div>
        <div class="codex-usage-hud-settings-field">
          <label>周额度 USD</label>
          <input data-setting-key="weekly_budget_usd" type="number" min="0" step="0.01" value="${escapeHtml(settings.weekly_budget_usd)}">
        </div>
        <div class="codex-usage-hud-settings-field">
          <label>日额度重置时间</label>
          <input data-setting-key="daily_reset_time" type="time" value="${escapeHtml(settings.daily_reset_time)}">
        </div>
        <div class="codex-usage-hud-settings-field">
          <label>周额度重置</label>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">
            <select data-setting-key="weekly_reset_weekday">${weekdayOptions}</select>
            <input data-setting-key="weekly_reset_time" type="time" value="${escapeHtml(settings.weekly_reset_time)}">
          </div>
        </div>
        <div class="codex-usage-hud-settings-compact-row">
          <div class="codex-usage-hud-settings-field">
            <label>超额提醒阈值</label>
            <input data-setting-key="budget_thresholds" value="${escapeHtml(thresholdText(settings))}" placeholder="50,80,100">
          </div>
          <div class="codex-usage-hud-settings-field">
            <label>会话进度气泡数量（0 为关闭）</label>
            <select data-setting-key="work_overlay_max_items">${overlayOptions}</select>
          </div>
          <div class="codex-usage-hud-settings-field">
            <label>气泡运行环境</label>
            <div data-desktop-overlay-dependency="true">${desktopOverlayDependencyHtml()}</div>
          </div>
        </div>
        <div class="codex-usage-hud-provider-editor" data-provider-editor="true" data-active-provider="${escapeHtml(settingsProviderDraft?.activeProvider || "")}">
          ${settingsProviderEditorHtml(settings)}
        </div>
        <div class="codex-usage-hud-settings-footnote">
          <button type="button" class="codex-usage-hud-settings-action" data-action="settings-exit" data-variant="ghost">退出 HUD</button>
          <div class="codex-usage-hud-settings-status">配置文件：${escapeHtml(path || "未提供")} ${bridge ? "" : "（桥接未连接）"}</div>
        </div>
      </div>
    `;
  }

  function supportPanelHtml(settings, path) {
    const url = String(settings.support_url || "https://github.com/mingbingfeng/codex-usage-hud");
    const images = Array.isArray(currentPayload()?.supportImages) ? currentPayload().supportImages : [];
    const enabled = !!settings.rest_reminder_enabled;
    const interval = Math.min(180, Math.max(15, Math.round(Number(settings.rest_reminder_interval_minutes) || 45)));
    const postpone = Math.min(30, Math.max(5, Math.round(Number(settings.rest_reminder_postpone_minutes) || 10)));
    const idleReset = Math.min(60, Math.max(0, Math.round(Number(settings.rest_reminder_idle_reset_minutes) || 5)));
    const qrItems = images.map((item) => `
      <div class="codex-usage-hud-support-qr">
        <div class="codex-usage-hud-support-qr-title">
          <span>${escapeHtml(item?.label || "赞赏码")}</span>
          <span>${escapeHtml(item?.hint || "扫码支持")}</span>
        </div>
        <img src="${escapeHtml(item?.src || "")}" alt="${escapeHtml(item?.label || "赞赏码")}">
      </div>
    `).join("");
    return `
      <div class="codex-usage-hud-support">
        <div class="codex-usage-hud-rest-reminder-card">
          <div class="codex-usage-hud-rest-reminder-title">☕ 专注休息提醒</div>
          <label class="codex-usage-hud-rest-reminder-toggle">
            <input type="checkbox" data-setting-key="rest_reminder_enabled" ${enabled ? "checked" : ""}>
            <span>开启休息提醒</span>
          </label>
          <div class="codex-usage-hud-rest-reminder-grid">
            <label>
                <span>专注时长（分钟）</span>
              <input data-setting-key="rest_reminder_interval_minutes" type="number" min="15" max="180" step="1" value="${escapeHtml(interval)}">
            </label>
            <label>
                <span>稍后提醒（分钟）</span>
              <input data-setting-key="rest_reminder_postpone_minutes" type="number" min="5" max="30" step="1" value="${escapeHtml(postpone)}">
            </label>
            <label>
                <span>离开后重新计时（分钟，0=关闭）</span>
              <input data-setting-key="rest_reminder_idle_reset_minutes" type="number" min="0" max="60" step="1" value="${escapeHtml(idleReset)}">
            </label>
          </div>
          <div class="codex-usage-hud-rest-reminder-status" aria-live="polite">
            <div class="codex-usage-hud-rest-reminder-status-item">
              <span class="codex-usage-hud-rest-reminder-status-label">本轮开始</span>
              <strong class="codex-usage-hud-rest-reminder-status-value" data-rest-reminder-start="true">--:--:--</strong>
            </div>
            <div class="codex-usage-hud-rest-reminder-status-item">
              <span class="codex-usage-hud-rest-reminder-status-label">距离下一次提醒</span>
              <strong class="codex-usage-hud-rest-reminder-status-value" data-rest-reminder-remaining="true">--:--:--</strong>
            </div>
          </div>
          <div class="codex-usage-hud-support-note">开启后会在专注时长结束时轻柔提醒你休息，不会锁定屏幕；你可以稍后再提醒一次。</div>
          <button type="button" class="codex-usage-hud-settings-action" data-action="rest-reminder-save">保存提醒设置</button>
        </div>
        <div class="codex-usage-hud-support-note">如果这个 HUD 帮你节省了排查 token 和费用的时间，可以扫码支持维护。</div>
        <div class="codex-usage-hud-support-qr-grid">
          ${qrItems || '<div class="codex-usage-hud-support-note">赞赏码资源未加载，请等待 HUD 刷新。</div>'}
        </div>
        <div class="codex-usage-hud-support-note">项目链接：<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(url)}</a></div>
        <div class="codex-usage-hud-support-note">当前配置文件：${escapeHtml(path || "未提供")}</div>
      </div>
    `;
  }

  function aboutPanelHtml(path) {
    return `
      <div class="codex-usage-hud-support">
        <div class="codex-usage-hud-support-note">当前版本：<strong>v${escapeHtml(appVersion())}</strong></div>
        <div class="codex-usage-hud-support-note">更新源：GitHub Releases / mingbingfeng/codex-usage-hud</div>
        <div class="codex-usage-hud-support-note">Windows 安装包：codex-usage-hud-v*-windows-x64-setup.exe</div>
        <div class="codex-usage-hud-support-note">自动更新会下载最新版安装包并启动安装器；安装器会先关闭正在运行的 HUD，再替换本地文件。</div>
        <div class="codex-usage-hud-support-note">当前配置文件：${escapeHtml(path || "未提供")}</div>
      </div>
    `;
  }

  function setSettingsStatus(text, kind = "") {
    const node = document.querySelector(`#${settingsModalId} [data-settings-status="true"]`);
    if (!node) return;
    node.textContent = String(text || "");
    node.dataset.kind = kind;
  }

  function setSettingsRestartVisible(visible) {
    const node = document.querySelector(`#${settingsModalId} [data-action="settings-restart"]`);
    if (node) node.hidden = !visible;
  }

  function setSettingsActionState(actionName, { label = "", disabled = false } = {}) {
    const node = document.querySelector(`#${settingsModalId} [data-action="${actionName}"]`);
    if (!(node instanceof HTMLButtonElement)) return;
    if (label) node.textContent = label;
    node.disabled = !!disabled;
  }

  function formatRestReminderClock(milliseconds) {
    if (!Number.isFinite(milliseconds) || milliseconds <= 0) return "--:--:--";
    return new Date(milliseconds).toLocaleTimeString("zh-CN", {
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    });
  }

  function formatRestReminderRemaining(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "--:--:--";
    const total = Math.max(0, Math.ceil(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const remainder = total % 60;
    return [hours, minutes, remainder].map((value) => String(value).padStart(2, "0")).join(":");
  }

  function syncRestReminderCountdown() {
    const modal = document.getElementById(settingsModalId);
    if (!modal || modal.hidden || settingsActiveTab !== "support") return;
    const timing = currentPayload()?.restReminder;
    const enabled = !!timing?.enabled;
    const start = modal.querySelector('[data-rest-reminder-start="true"]');
    const remaining = modal.querySelector('[data-rest-reminder-remaining="true"]');
    if (start) start.textContent = enabled
      ? formatRestReminderClock(Number(timing?.timerStartedAtMs))
      : "开启后计时";
    if (remaining) remaining.textContent = enabled
      ? formatRestReminderRemaining((Number(timing?.nextReminderAtMs) - Date.now()) / 1000)
      : "未开启";
  }

  function ensureRestReminderCountdownTicker() {
    const modal = document.getElementById(settingsModalId);
    if (!modal || modal.hidden || settingsActiveTab !== "support") {
      if (restReminderCountdownTimer) {
        clearInterval(restReminderCountdownTimer);
        restReminderCountdownTimer = 0;
      }
      return;
    }
    syncRestReminderCountdown();
    if (!restReminderCountdownTimer) {
      restReminderCountdownTimer = setInterval(syncRestReminderCountdown, 1000);
    }
  }

  function updateAboutActionButtons(state) {
    const phase = String(state?.phase || "");
    const progressText = String(state?.progressText || "").trim();
    let checkLabel = "检查更新";
    let installLabel = "安装更新";
    let disableCheck = false;
    let disableInstall = false;
    if (phase === "checking") {
      checkLabel = "检查中...";
      installLabel = "请稍候";
      disableCheck = true;
      disableInstall = true;
    } else if (phase === "downloading") {
      installLabel = progressText ? `下载中 ${progressText}` : "下载中...";
      disableCheck = true;
      disableInstall = true;
    } else if (phase === "ready") {
      installLabel = "打开安装器";
    }
    setSettingsActionState("settings-check-update", {
      label: checkLabel,
      disabled: disableCheck,
    });
    setSettingsActionState("settings-install-update", {
      label: installLabel,
      disabled: disableInstall,
    });
  }

  function showSettingsRestartPrompt(message, kind = "error") {
    setSettingsStatus(`${message} 是否立即重启 HUD？`, kind);
    setSettingsRestartVisible(true);
  }

  function setSettingsLoadingText({ kicker = "", title = "", body = "" } = {}) {
    const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"][data-loading-mode]`);
    if (!layer) return;
    const kickerNode = layer.querySelector(".codex-usage-hud-settings-confirm-kicker");
    const titleNode = layer.querySelector(".codex-usage-hud-settings-confirm-title");
    const bodyNode = layer.querySelector(".codex-usage-hud-settings-confirm-body");
    if (kickerNode) kickerNode.textContent = String(kicker || "");
    if (titleNode) titleNode.textContent = String(title || "");
    if (bodyNode) bodyNode.textContent = String(body || "");
  }

  function syncSettingsUpdateLoading(payload) {
    const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"][data-loading-mode]`);
    if (!layer) return;
    const mode = String(layer.dataset.loadingMode || "");
    const state = updateStateFromPayload(payload);
    const phase = String(state?.phase || "");
    const progressText = String(state?.progressText || "").trim();
    if (mode === "check-update") {
      if (phase === "checking") {
        setSettingsLoadingText({
          kicker: "正在检查",
          title: "正在检查更新",
          body: "HUD daemon 正在查询 GitHub Release。通常只需 1 到 3 秒。",
        });
        return;
      }
      closeSettingsConfirm();
      return;
    }
    if (mode === "install-update") {
      if (phase === "checking") {
        setSettingsLoadingText({
          kicker: "正在准备",
          title: "正在检查并准备安装更新",
          body: "HUD daemon 正在查询 GitHub Release，并准备下载安装包。",
        });
        return;
      }
      if (phase === "downloading") {
        setSettingsLoadingText({
          kicker: "正在下载",
          title: "正在下载安装更新",
          body: progressText
            ? `当前进度：${progressText}\n\n下载完成后会自动启动安装器。`
            : "正在下载 Windows 安装包。\n\n下载完成后会自动启动安装器。",
        });
        return;
      }
      closeSettingsConfirm();
    }
  }

  function applySettingsCommandStatus(payload) {
    const modal = document.getElementById(settingsModalId);
    if (!modal || modal.hidden) return;
    const state = updateStateFromPayload(payload);
    updateAboutActionButtons(state);
    syncDesktopOverlayDependency();
    syncSettingsUpdateLoading(payload);
    const status = payload?.settingsCommandStatus;
    if (status && typeof status === "object" && String(status.message || "")) {
      setSettingsStatus(status.message || "", status.kind || "");
      setSettingsRestartVisible(!!status.restartVisible);
      return;
    }
    setSettingsStatus(state.message || state.title || "", state.error ? "error" : "");
    setSettingsRestartVisible(false);
  }

  function submitSettingsCommand(command, pendingMessage, { preserveOverlay = false } = {}) {
    const payload = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      createdAt: Date.now(),
      ...command,
    };
    const bridge = settingsBridgeUrl();
    if (!bridge) {
      setSettingsStatus("无法提交设置命令：settings bridge 未连接", "error");
      return false;
    }
    try {
      const binding = window[settingsCommandBindingName];
      if (typeof binding === "function") {
        try {
          binding(JSON.stringify(payload));
        } catch (error) {
          setSettingsStatus(`设置命令提交失败：${error?.message || error}`, "error");
          return false;
        }
      } else {
      fetch(`${bridge}/command`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive: true,
      }).catch((error) => {
        setSettingsStatus(`设置命令提交失败：${error?.message || error}`, "error");
      });
      }
    } catch (error) {
      setSettingsStatus(`设置命令提交失败：${error?.message || error}`, "error");
      return false;
    }
    setSettingsStatus(pendingMessage || "设置命令已提交，等待 HUD daemon 写入本地配置...");
    setSettingsRestartVisible(false);
    if (!preserveOverlay) closeSettingsConfirm();
    return true;
  }

  function settingsDialogRoot() {
    return document.querySelector(`#${settingsModalId} .codex-usage-hud-settings-dialog`);
  }

  function closeSettingsConfirm() {
    const layer = document.querySelector(`#${settingsModalId} [data-settings-confirm="true"]`);
    if (layer) layer.remove();
  }

  function openSettingsLoading({ kicker = "正在处理", title = "", body = "", mode = "" } = {}) {
    const dialog = settingsDialogRoot();
    if (!dialog) return;
    closeSettingsConfirm();
    const layer = document.createElement("div");
    layer.className = "codex-usage-hud-settings-confirm-layer";
    layer.dataset.settingsConfirm = "true";
    if (mode) layer.dataset.loadingMode = mode;
    layer.innerHTML = `
      <div class="codex-usage-hud-settings-confirm-card" role="status" aria-live="polite" aria-label="${escapeHtml(title || "正在处理设置变更")}">
        <div class="codex-usage-hud-settings-confirm-kicker">${escapeHtml(kicker)}</div>
        <div class="codex-usage-hud-settings-confirm-title">${escapeHtml(title)}</div>
        <div class="codex-usage-hud-settings-confirm-body">${escapeHtml(body)}</div>
        <div class="codex-usage-hud-settings-loading-track" aria-hidden="true">
          <div class="codex-usage-hud-settings-loading-bar"></div>
          <div class="codex-usage-hud-settings-loading-glow"></div>
        </div>
      </div>
    `;
    dialog.appendChild(layer);
  }

  function collectSettingsForm() {
    const modal = document.getElementById(settingsModalId);
    const settings = hudSettingsFromPayload();
    const settingNode = (key) => modal?.querySelector(`[data-setting-key="${key}"]`);
    const read = (key) => settingNode(key)?.value;
    const numberValue = (key, fallback) => {
      const value = Number(read(key));
      return Number.isFinite(value) && value >= 0 ? value : fallback;
    };
    const integerValue = (key, fallback, min, max) => {
      const value = Number(read(key));
      if (!Number.isFinite(value)) return fallback;
      return Math.min(max, Math.max(min, Math.round(value)));
    };
    captureSettingsProviderForm();
    const draft = ensureSettingsProviderDraft(settings);
    const providerSettings = { ...(settings.provider_settings || {}) };
    draft.order.forEach((provider) => {
      providerSettings[provider] = {
        ...(providerSettings[provider] || {}),
        ...(draft.providers[provider]?.settings || {}),
      };
    });
    const selectedProviders = draft.order.filter((provider) => (
      provider === draft.appProvider || !!draft.providers[provider]?.enabled
    ));
    const notificationOnlyProviders = draft.order.filter((provider) => (
      provider !== draft.appProvider
      && !draft.providers[provider]?.enabled
      && !!draft.providers[provider]?.notificationOnly
    ));
    const allProvidersSelected = draft.order.every((provider) => selectedProviders.includes(provider));
    const displayMode = "renderer";
    const next = {
      ...settings,
      daily_budget_usd: numberValue("daily_budget_usd", settings.daily_budget_usd),
      weekly_budget_usd: numberValue("weekly_budget_usd", settings.weekly_budget_usd),
      daily_reset_time: String(read("daily_reset_time") || settings.daily_reset_time),
      weekly_reset_weekday: Number(read("weekly_reset_weekday") ?? settings.weekly_reset_weekday),
      weekly_reset_time: String(read("weekly_reset_time") || settings.weekly_reset_time),
      display_mode: displayMode,
      work_overlay_max_items: integerValue(
        "work_overlay_max_items",
        Number(settings.work_overlay_max_items) || 0,
        0,
        workOverlaySelectableMax(),
      ),
      pricing_url: String(settings.pricing_url || "").trim(),
      provider_settings: providerSettings,
      provider_scope_mode: allProvidersSelected ? "all" : "custom",
      selected_providers: selectedProviders,
      notification_only_providers: notificationOnlyProviders,
      budget_thresholds: String(read("budget_thresholds") || "")
        .split(",")
        .map((item) => Number(item.trim()))
        .filter((item) => Number.isFinite(item) && item > 0),
      weekly_adjustment_usd: settings.weekly_adjustment_usd,
      support_url: String(settings.support_url || "https://github.com/mingbingfeng/codex-usage-hud").trim(),
      model_prices: settings.model_prices,
    };
    // Rest-reminder controls live on the support tab; keep previous values when absent.
    if (settingNode("rest_reminder_enabled")) {
      next.rest_reminder_enabled = !!settingNode("rest_reminder_enabled").checked;
    }
    if (settingNode("rest_reminder_interval_minutes")) {
      next.rest_reminder_interval_minutes = integerValue(
        "rest_reminder_interval_minutes",
        Number(settings.rest_reminder_interval_minutes) || 45,
        15,
        180,
      );
    }
    if (settingNode("rest_reminder_postpone_minutes")) {
      next.rest_reminder_postpone_minutes = integerValue(
        "rest_reminder_postpone_minutes",
        Number(settings.rest_reminder_postpone_minutes) || 10,
        5,
        30,
      );
    }
    if (settingNode("rest_reminder_idle_reset_minutes")) {
      next.rest_reminder_idle_reset_minutes = integerValue(
        "rest_reminder_idle_reset_minutes",
        Number(settings.rest_reminder_idle_reset_minutes) || 5,
        0,
        60,
      );
    }
    return next;
  }

  function saveSettingsFromModal({ section = "" } = {}) {
    const settings = collectSettingsForm();
    const submitted = submitSettingsCommand(
      { action: "save", settings, ...(section ? { section } : {}) },
      section === "restReminder" ? "正在保存提醒设置..." : "正在保存设置..."
    );
    if (submitted) {
      settingsDirtyProviders.clear();
      renderSettingsProviderTabs();
    }
  }

  function fetchPricesFromModal() {
    const settings = collectSettingsForm();
    const provider = String(settingsProviderDraft?.activeProvider || "").trim().toLowerCase();
    submitSettingsCommand(
      { action: "fetchPrices", settings, provider },
      "价格拉取请求已提交，等待 HUD daemon 拉取并写入..."
    );
  }

  function restartHudFromModal() {
    submitSettingsCommand(
      { action: "restart", reason: "settings" },
      "重启请求已提交，等待 HUD daemon 处理..."
    );
  }

  function installDesktopOverlayFromModal() {
    submitSettingsCommand(
      { action: "installDesktopOverlay" },
      "正在准备安装气泡组件..."
    );
  }

  function enableDesktopOverlayFromModal() {
    submitSettingsCommand(
      { action: "enableDesktopOverlay" },
      "正在重新检测气泡组件..."
    );
  }

  function exitHudFromModal() {
    openSettingsLoading({
      kicker: "正在退出",
      title: "正在停止 HUD",
      body: "HUD 正在退出当前界面，并停止后台守护进程（如果正在运行）。",
    });
    const submitted = submitSettingsCommand(
      { action: "exit", reason: "settings", expiresAt: Date.now() + 10000 },
      "退出请求已提交，正在停止 HUD...",
      { preserveOverlay: true }
    );
    if (submitted) {
      setTimeout(() => {
        try {
          if (typeof window.__codexUsageHudRemove === "function") {
            window.__codexUsageHudRemove();
            return;
          }
        } catch (_) {}
        document.getElementById(rootId)?.remove();
        document.getElementById(styleId)?.remove();
      }, 120);
    }
  }

  function checkUpdateFromModal() {
    openSettingsLoading({
      kicker: "正在检查",
      title: "正在检查更新",
      body: "HUD daemon 正在查询 GitHub Release。通常只需 1 到 3 秒。",
      mode: "check-update",
    });
    submitSettingsCommand(
      { action: "checkUpdate" },
      "检查更新请求已提交，等待 HUD daemon 查询 GitHub Release...",
      { preserveOverlay: true }
    );
  }

  function installUpdateFromModal() {
    openSettingsLoading({
      kicker: "正在准备",
      title: "正在检查并准备安装更新",
      body: "HUD daemon 会先检查 GitHub Release，再后台下载 Windows 安装包。",
      mode: "install-update",
    });
    submitSettingsCommand(
      { action: "installUpdate" },
      "安装更新请求已提交，等待 HUD daemon 下载并启动安装器...",
      { preserveOverlay: true }
    );
  }

  function openSettingsExitConfirm() {
    const dialog = settingsDialogRoot();
    if (!dialog) return;
    closeSettingsConfirm();
    const layer = document.createElement("div");
    layer.className = "codex-usage-hud-settings-confirm-layer";
    layer.dataset.settingsConfirm = "true";
    layer.innerHTML = `
      <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="确认退出 HUD">
        <div class="codex-usage-hud-settings-confirm-kicker">退出 HUD</div>
        <div class="codex-usage-hud-settings-confirm-title">完全退出并停止守护进程？</div>
        <div class="codex-usage-hud-settings-confirm-body">这会完全退出 HUD，并停止后台守护进程（如果当前正在运行）。\n\n是否继续？</div>
        <div class="codex-usage-hud-settings-confirm-actions">
          <button type="button" class="codex-usage-hud-settings-action" data-action="settings-exit-cancel" data-variant="ghost">取消</button>
          <button type="button" class="codex-usage-hud-settings-action" data-action="settings-exit-confirm" data-primary="true">退出 HUD</button>
        </div>
      </div>
    `;
    dialog.appendChild(layer);
  }

  function runUpdateAction() {
    const state = currentUpdateState();
    let pending = "更新操作请求已提交，等待 HUD daemon 处理...";
    if (String(state?.phase || "") === "downloading") {
      pending = "暂停下载请求已提交...";
    } else if (String(state?.phase || "") === "paused") {
      pending = "继续下载请求已提交...";
    } else if (String(state?.phase || "") === "ready") {
      pending = "正在打开已下载的安装程序...";
    }
    submitSettingsCommand(
      { action: "updateAction" },
      pending
    );
  }

  function dismissWarningsToday() {
    const root = document.getElementById(rootId);
    if (root) {
      setText(root, "topWarnings", "");
      root.querySelectorAll('[data-field-panel="topWarnings"]').forEach((node) => {
        node.hidden = true;
      });
    }
    submitSettingsCommand(
      { action: "dismissWarningsToday" },
      "今天不再显示预算预警。"
    );
  }

  function exportSettingsFromModal() {
    const settings = collectSettingsForm();
    const data = JSON.stringify({ user: settings }, null, 2);
    const blob = new Blob([data], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "codex-usage-hud-settings.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setSettingsStatus("已导出 JSON");
  }

  function addModelPriceRow(initialModel = "") {
    const rows = document.querySelector(`#${settingsModalId} [data-price-rows="true"]`);
    if (!rows) return;
    const row = document.createElement("div");
    row.className = "codex-usage-hud-price-row";
    row.dataset.priceRow = "true";
    row.innerHTML = `
      <input data-price-field="model" value="${escapeHtml(initialModel)}" aria-label="模型">
      <input data-price-field="input" type="number" min="0" step="0.000001" value="0" aria-label="输入单价">
      <input data-price-field="output" type="number" min="0" step="0.000001" value="0" aria-label="输出单价">
      <input data-price-field="cached_input" type="number" min="0" step="0.000001" value="0" aria-label="缓存读取单价">
      <input data-price-field="cache_write" type="number" min="0" step="0.000001" value="0" aria-label="缓存写入单价">
      <input class="codex-usage-hud-price-advanced" data-price-field="provider" value="" aria-label="渠道">
      <input class="codex-usage-hud-price-advanced" data-price-field="base_url" value="" aria-label="Base URL">
    `;
    rows.appendChild(row);
    markSettingsProviderDirty();
    const target = initialModel ? row.querySelector('[data-price-field="input"]') : row.querySelector("input");
    target?.focus?.();
  }

  function openSettingsDiscardConfirm() {
    const dialog = settingsDialogRoot();
    if (!dialog) return;
    closeSettingsConfirm();
    const layer = document.createElement("div");
    layer.className = "codex-usage-hud-settings-confirm-layer";
    layer.dataset.settingsConfirm = "true";
    layer.innerHTML = `
      <div class="codex-usage-hud-settings-confirm-card" role="alertdialog" aria-modal="true" aria-label="放弃未保存的 Provider 修改">
        <div class="codex-usage-hud-settings-confirm-kicker">未保存修改</div>
        <div class="codex-usage-hud-settings-confirm-title">关闭设置并放弃修改？</div>
        <div class="codex-usage-hud-settings-confirm-body">${settingsDirtyProviders.size} 个 Provider 仍有未保存修改。</div>
        <div class="codex-usage-hud-settings-confirm-actions">
          <button type="button" class="codex-usage-hud-settings-action" data-action="settings-discard-cancel" data-variant="ghost">继续编辑</button>
          <button type="button" class="codex-usage-hud-settings-action" data-action="settings-discard-confirm" data-primary="true">放弃修改</button>
        </div>
      </div>
    `;
    dialog.appendChild(layer);
  }

  function closeSettingsModal({ force = false } = {}) {
    const modal = document.getElementById(settingsModalId);
    if (!modal) return;
    captureSettingsProviderForm();
    if (!force && settingsDirtyProviders.size) {
      openSettingsDiscardConfirm();
      return;
    }
    closeSettingsConfirm();
    if (settingsActiveTab === "backgroundUsage") {
      captureBackgroundUsageScrollPositions();
      clearBackgroundUsageRequestTimeout("query");
      clearBackgroundUsageRequestTimeout("detail");
    }
    modal.hidden = true;
    ensureRestReminderCountdownTicker();
    settingsProviderDraft = null;
    settingsDirtyProviders.clear();
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
      const data = JSON.parse(localStorage.getItem(storageKey) || "{}");
      return data && typeof data === "object" ? data : {};
    } catch (_) {
      return {};
    }
  }

  function saveStates(states) {
    try {
      localStorage.setItem(storageKey, JSON.stringify(states));
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
    // 悬浮徽章 → 展开 A/B/C/D/F 明细；移开 → 收起（事件委托，徽章重建也生效）。
    root.addEventListener("mouseover", (event) => {
      if (event.target?.closest?.(".codex-usage-hud-token-badge")) {
        showComposerBreakdown(root);
      }
    });
    root.addEventListener("mouseout", (event) => {
      const from = event.target?.closest?.(".codex-usage-hud-token-badge");
      if (from && !from.contains(event.relatedTarget)) {
        hideComposerBreakdown(root);
      }
    });
    root.addEventListener("wheel", (event) => {
      const select = event.target?.closest?.(`#${settingsModalId} select[data-setting-key]`);
      if (!select || !root.contains(select)) return;
      event.preventDefault();
      event.stopPropagation();
      select.closest(".codex-usage-hud-settings-dialog")?.scrollBy({
        left: event.deltaX,
        top: event.deltaY,
      });
    }, { capture: true, passive: false });
    root.addEventListener("input", (event) => {
      const backupInput = event.target?.closest?.('[data-cleanup-backup-directory="true"]');
      if (backupInput && root.contains(backupInput)) {
        safeCleanupState.backupDirectory = String(backupInput.value || "").trim();
        safeCleanupState.backupDirectoryDirty = true;
        return;
      }
      const sessionSearch = event.target?.closest?.('[data-session-cleanup-search="true"]');
      if (sessionSearch && root.contains(sessionSearch)) {
        const selection = Number(sessionSearch.selectionStart || 0);
        sessionCleanupState.search = String(sessionSearch.value || "");
        sessionCleanupState.selectedIds.clear();
        renderSettingsModal("storage");
        const next = root.querySelector('[data-session-cleanup-search="true"]');
        next?.focus?.();
        next?.setSelectionRange?.(selection, selection);
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
    root.addEventListener("change", (event) => {
      const consent = event.target?.closest?.('[data-cleanup-consent="true"]');
      if (consent && root.contains(consent)) {
        safeCleanupState.includeConsent = !!consent.checked;
        safeCleanupState.previewHidden = true;
        scheduleSafeCleanupPreview();
        return;
      }
      const backup = event.target?.closest?.('[data-cleanup-backup-directory="true"]');
      if (backup && root.contains(backup)) {
        safeCleanupState.backupDirectory = String(backup.value || "").trim();
        safeCleanupState.backupDirectoryDirty = true;
        scheduleSafeCleanupPreview();
        return;
      }
      const autoClose = event.target?.closest?.('[data-cleanup-auto-close="true"]');
      if (autoClose && root.contains(autoClose)) {
        safeCleanupState.autoCloseConfirmed = !!autoClose.checked;
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
      void loadBackgroundUsage({ force: true });
    });
    root.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        const modal = document.getElementById(settingsModalId);
        if (modal && !modal.hidden) {
          const confirm = modal.querySelector('[data-settings-confirm="true"]');
          event.preventDefault();
          event.stopPropagation();
          if (confirm) closeSettingsConfirm();
          else closeSettingsModal();
          return;
        }
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
    root.addEventListener("click", (event) => {
      if (event.target?.id === settingsModalId) {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsModal();
        return;
      }
      const copyNode = event.target?.closest?.("[data-copyable='true']");
      if (copyNode && root.contains(copyNode)) {
        event.preventDefault();
        event.stopPropagation();
        void copyHudText(copyNode.dataset.copyText || "").then((ok) => {
          flashCopyState(copyNode, ok);
        });
        return;
      }
      const action = event.target?.closest?.("[data-action]");
      if (!action || !root.contains(action)) return;
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
        renderSettingsModal(action.dataset.tab || "settings");
        return;
      }
      if (action.dataset.action === "cleanup-section") {
        event.preventDefault();
        event.stopPropagation();
        cleanupActiveSection = String(action.dataset.cleanupSection || "junk");
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "session-cleanup-status") {
        event.preventDefault();
        event.stopPropagation();
        sessionCleanupState.status = String(action.dataset.sessionCleanupStatus || "all");
        sessionCleanupState.selectedIds.clear();
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "session-cleanup-time") {
        event.preventDefault();
        event.stopPropagation();
        sessionCleanupState.time = String(action.dataset.sessionCleanupTime || "all");
        sessionCleanupState.selectedIds.clear();
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "safe-cleanup-group-toggle") {
        event.preventDefault();
        event.stopPropagation();
        const groupId = String(action.dataset.cleanupGroupId || "");
        const group = safeCleanupGroups().find((item) => String(item?.presentationId || "") === groupId);
        if (group) toggleSafeCleanupItemIds(group.executableIds);
        return;
      }
      if (action.dataset.action === "safe-cleanup-consent-toggle") {
        event.preventDefault();
        event.stopPropagation();
        const consentIds = safeCleanupGroups()
          .filter((group) => String(group?.tier || "") === "consent")
          .flatMap((group) => group.executableIds || []);
        toggleSafeCleanupItemIds(consentIds);
        return;
      }
      if (action.dataset.action === "safe-cleanup-group-expand") {
        event.preventDefault();
        event.stopPropagation();
        captureStorageUiState();
        const groupId = String(action.dataset.cleanupGroupId || "");
        if (safeCleanupState.expandedGroupIds.has(groupId)) safeCleanupState.expandedGroupIds.delete(groupId);
        else safeCleanupState.expandedGroupIds.add(groupId);
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "cleanup-deep-toggle") {
        event.preventDefault();
        event.stopPropagation();
        captureStorageUiState();
        if (safeCleanupState.expandedGroupIds.has(safeCleanupDeepGroupId)) safeCleanupState.expandedGroupIds.delete(safeCleanupDeepGroupId);
        else safeCleanupState.expandedGroupIds.add(safeCleanupDeepGroupId);
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "safe-cleanup-protected-toggle") {
        event.preventDefault();
        event.stopPropagation();
        captureStorageUiState();
        if (safeCleanupState.expandedGroupIds.has(safeCleanupProtectedGroupId)) safeCleanupState.expandedGroupIds.delete(safeCleanupProtectedGroupId);
        else safeCleanupState.expandedGroupIds.add(safeCleanupProtectedGroupId);
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "safe-cleanup-copy-path") {
        event.preventDefault();
        event.stopPropagation();
        const itemId = String(action.dataset.itemId || "");
        const item = safeCleanupRawItems().find((entry) => String(entry?.id || "") === itemId);
        const path = String(item?.path || "");
        void copyHudText(path).then((ok) => {
          setSettingsStatus(ok ? "已复制完整路径。" : "路径复制失败。", ok ? "" : "error");
          flashCopyState(action, ok);
        });
        return;
      }
      if (action.dataset.action === "safe-cleanup-reveal") {
        event.preventDefault();
        event.stopPropagation();
        requestSafeCleanupReveal(action.dataset.itemId);
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
        void loadBackgroundUsage({ force: true });
        return;
      }
      if (action.dataset.action === "background-usage-session-select") {
        event.preventDefault();
        event.stopPropagation();
        backgroundUsageState.selectedSessionId = String(action.dataset.sessionId || "").trim();
        syncBackgroundUsagePanel();
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
        const sessionId = String(action.dataset.sessionId || "").trim();
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
      if (action.dataset.action === "safe-cleanup-scan") {
        event.preventDefault();
        event.stopPropagation();
        requestSafeCleanupScan();
        return;
      }
      if (action.dataset.action === "safe-cleanup-cancel") {
        event.preventDefault();
        event.stopPropagation();
        requestSafeCleanupCancel();
        return;
      }
      if (action.dataset.action === "session-cleanup-cancel") {
        event.preventDefault();
        event.stopPropagation();
        requestSessionCleanupCancel();
        return;
      }
      if (action.dataset.action === "safe-cleanup-preview") {
        event.preventDefault();
        event.stopPropagation();
        requestSafeCleanupPreview();
        return;
      }
      if (action.dataset.action === "safe-cleanup-confirm") {
        event.preventDefault();
        event.stopPropagation();
        const cleanup = safeCleanupFromPayload();
        const cleanupOperation = cleanup?.operation && typeof cleanup.operation === "object" ? cleanup.operation : {};
        if (cleanupOperation?.includesConsent === true || cleanupOperation?.requiresBackup === true || cleanupOperation?.requiresCodexClose === true) {
          openSafeCleanupExecuteConfirm();
        } else {
          executeSafeCleanup();
        }
        return;
      }
      if (action.dataset.action === "safe-cleanup-confirm-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        return;
      }
      if (action.dataset.action === "safe-cleanup-execute") {
        event.preventDefault();
        event.stopPropagation();
        executeSafeCleanup();
        return;
      }
      if (action.dataset.action === "safe-cleanup-cancel") {
        event.preventDefault();
        event.stopPropagation();
        safeCleanupState.previewHidden = true;
        safeCleanupState.previewBackupDirectory = "";
        submitSettingsCommand(
          { action: "safeCleanupCancel", requestId: typedSettingsRequestId("safe-cleanup-cancel") },
          "已请求取消清理操作。",
          { preserveOverlay: true },
        );
        return;
      }
      if (action.dataset.action === "safe-cleanup-choose-backup") {
        event.preventDefault();
        event.stopPropagation();
        const requestId = typedSettingsRequestId("safe-cleanup-backup");
        submitSettingsCommand(
          {
            action: "safeCleanupChooseBackupDirectory",
            requestId,
            currentDirectory: safeCleanupState.backupDirectory,
          },
          "正在选择 SQLite 备份目录...",
          { preserveOverlay: true },
        );
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
      if (action.dataset.action === "storage-filter") {
        event.preventDefault();
        event.stopPropagation();
        storageFilter = String(action.dataset.storageFilter || "all");
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "storage-scan") {
        event.preventDefault();
        event.stopPropagation();
        storagePreviewHidden = true;
        submitSettingsCommand({ action: "scan" }, "正在准备新的存储扫描...", { preserveOverlay: true });
        return;
      }
      if (action.dataset.action === "storage-preview") {
        event.preventDefault();
        event.stopPropagation();
        storagePreviewSelected();
        return;
      }
      if (action.dataset.action === "storage-managed-preview") {
        event.preventDefault();
        event.stopPropagation();
        storagePreviewSelected(action.dataset.storageManagedAction || "", action.dataset.storageItemId || "");
        return;
      }
      if (action.dataset.action === "storage-clear-preview") {
        event.preventDefault();
        event.stopPropagation();
        storagePreviewHidden = true;
        renderSettingsModal("storage");
        return;
      }
      if (action.dataset.action === "storage-confirm-preview") {
        event.preventDefault();
        event.stopPropagation();
        openStorageExecuteConfirm();
        return;
      }
      if (action.dataset.action === "storage-confirm-cancel") {
        event.preventDefault();
        event.stopPropagation();
        closeSettingsConfirm();
        return;
      }
      if (action.dataset.action === "storage-confirm-execute") {
        event.preventDefault();
        event.stopPropagation();
        const data = fileManagementFromPayload();
        const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
        const managedAction = String(operation?.managedAction || "");
        const itemIds = Array.isArray(operation?.itemIds) ? operation.itemIds : [];
        const commandAction = managedAction || "execute";
        closeSettingsConfirm();
        submitSettingsCommand({ action: commandAction, itemIds, inventoryRevision: String(data?.revision || operation?.inventoryRevision || ""), confirmationToken: String(operation?.confirmationToken || "") }, commandAction === "execute" ? "清理已加入退出后队列..." : "官方动作已提交...", { preserveOverlay: true });
        return;
      }
      if (action.dataset.action === "settings-provider-tab") {
        event.preventDefault();
        event.stopPropagation();
        switchSettingsProvider(action.dataset.provider || "");
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
        submitSettingsCommand({ action: "restReminderAck" }, "正在开始新一轮专注计时...");
        const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
        if (toast) toast.dataset.visible = "false";
        return;
      }
      if (action.dataset.action === "rest-reminder-postpone") {
        event.preventDefault();
        event.stopPropagation();
        submitSettingsCommand({ action: "restReminderPostpone" }, "正在安排稍后提醒...");
        const toast = document.querySelector(`#${rootId} [data-rest-reminder-toast="true"]`);
        if (toast) toast.dataset.visible = "false";
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
    root.addEventListener("pointerdown", (event) => {
      if (event.button !== undefined && event.button !== 0) return;
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
        const width = desiredWidth(name, getPanelState(name), gesture.expanded, gesture.width, gesture.anchor.maxWidth);
        const height = desiredHeight(name, getPanelState(name), gesture.expanded, gesture.height);
        const left = clamp(gesture.left + dx, 8, Math.max(8, innerWidth - width - 8));
        const top = clamp(gesture.top + dy, 8, Math.max(8, innerHeight - height - 8));
        applyRect(panel, left, top, width, height);
        setPanelState(name, manualPatchFor(name, left, top, width, gesture.anchor, { manual: true }));
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
      document.removeEventListener("pointermove", move, true);
      document.removeEventListener("pointerup", done, true);
      document.removeEventListener("pointercancel", done, true);
      // A pure tap (no movement) falls through to the click handler, which
      // toggles the panel. Only a real drag persists a new position.
      syncPosition();
      if (gesture.moved) {
        scheduleLayoutReport(
          gesture.action === "resize" ? "resize" : "move",
          gesture.name,
        );
      }
    };
    document.addEventListener("pointermove", move, true);
    document.addEventListener("pointerup", done, true);
    document.addEventListener("pointercancel", done, true);
  }

  function beginRuntimeErrorsGesture(event) {
    const panel = document.querySelector(`#${rootId} [data-field="runtimeErrorsPanel"]`);
    if (!panel || panel.hidden) return;
    const rect = panel.getBoundingClientRect();
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
      document.removeEventListener("pointermove", move, true);
      document.removeEventListener("pointerup", done, true);
      document.removeEventListener("pointercancel", done, true);
      applyRuntimeErrorsPanelState(panel);
    };
    document.addEventListener("pointermove", move, true);
    document.addEventListener("pointerup", done, true);
    document.addEventListener("pointercancel", done, true);
  }

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

  function composerElement() {
    if (anchorUsable(cachedComposerNode)) return cachedComposerNode;
    cachedComposerNode = null;
    const candidates = new Set();
    const composerClasses = [
      "relative",
      "flex",
      "flex-col",
      "bg-token-input-background/90",
    ];
    Array.from(document.querySelectorAll("div")).forEach((node) => {
      if (hasAllClasses(node, composerClasses) && visible(node)) candidates.add(node);
    });
    Array.from(document.querySelectorAll(".composer-footer")).forEach((footer) => {
      if (!visible(footer)) return;
      let node = footer;
      for (let depth = 0; node instanceof HTMLElement && depth < 7; depth += 1, node = node.parentElement) {
        if (visible(node)) candidates.add(node);
      }
    });
    Array.from(document.querySelectorAll("textarea, [contenteditable='true']")).forEach((input) => {
      if (!visible(input)) return;
      let node = input;
      for (let depth = 0; node instanceof HTMLElement && depth < 7; depth += 1, node = node.parentElement) {
        if (visible(node)) candidates.add(node);
      }
    });
    const best = Array.from(candidates)
      .map((node, index) => ({ node, index, score: scoreComposer(node) }))
      .sort((left, right) => (right.score - left.score) || (left.index - right.index))[0]?.node;
    cachedComposerNode = anchorUsable(best) ? best : null;
    return cachedComposerNode;
  }

  function composerRect() {
    const best = composerElement();
    return visible(best) ? best.getBoundingClientRect() : null;
  }

  function composerInputElement() {
    const composer = composerElement();
    if (!composer) return null;
    const input = composer.querySelector("textarea, [contenteditable='true'], [role='textbox']");
    return visible(input) ? input : null;
  }

  function composerInputText(input) {
    if (!input) return "";
    if (input.tagName === "TEXTAREA" || typeof input.value === "string") {
      return String(input.value || "");
    }
    return String(input.innerText || input.textContent || "");
  }

  function composerTokenCount(text) {
    const normalized = String(text || "").replace(/\r\n/g, "\n");
    // Count by Unicode code points so CJK characters and emoji each read as one.
    return Array.from(normalized).length;
  }

  // ── 输入框附件采集 ──────────────────────────────────────────────
  // 通过 CDP 实测确认的选择器（见 docs/token_badge_integration.md）：
  //   图片：div.composer-attachment-surface[aria-label] 内含 <img>，取 naturalWidth/Height
  //   文件：button[aria-label]（class 含 group-hover/file-attachment），取文件名
  //   @引用/技能：span.inline-mention-brand-aware 或 chip/pill 文本，按 @ / $ 前缀分类
  function collectComposerAttachments() {
    if (!composerBadgeEnabled) {
      return { images: [], files: [], mentions: [], skills: [] };
    }
    const composer = composerElement();
    const empty = { images: [], files: [], mentions: [], skills: [] };
    if (!composer) return empty;

    const images = [];
    const collectImageAttachment = (surface) => {
      const img = surface.querySelector("img");
      if (!img) return;
      const name = String(surface.getAttribute("aria-label") || img.getAttribute("alt") || "").trim();
      images.push({
        name,
        width: Math.max(0, Math.round(Number(img.naturalWidth) || 0)),
        height: Math.max(0, Math.round(Number(img.naturalHeight) || 0)),
      });
    };
    composer.querySelectorAll("div.composer-attachment-surface").forEach(collectImageAttachment);
    composer.querySelectorAll("img[src^='blob:'], img[src^='data:image']").forEach((img) => {
      if (img.closest("div.composer-attachment-surface")) return;
      const surface = img.closest("[aria-label], button, [role='button'], div") || img.parentElement || img;
      collectImageAttachment(surface);
    });

    const files = [];
    const normalizeFileAttachmentName = (label) => {
      let value = normalize(label || "");
      value = value.replace(/^(remove|delete|open|preview|download)\s+(file|attachment)\s+/i, "");
      value = value.replace(/^(remove|delete|open|preview|download)\s+/i, "");
      value = value.replace(/^(file|attachment):?\s+/i, "");
      return value.trim();
    };
    // Codex 文件引用 chip 的 React fiber 暴露 resourcePath（绝对路径），
    // 桌面/仓库外文件靠文件名根本搜不到，必须直接拿这个路径读盘才准。
    // mention 场景（inline `@file` chip）是 ProseMirror 节点，路径挂在 pmViewDesc.node.attrs 上。
    const readFiberFilePath = (node) => {
      if (!node) return "";
      // 先看 ProseMirror atMention 节点（span.inline-mention-brand-aware）：
      // node.pmViewDesc.node.attrs 里通常有 { label, path, fsPath }，path 是相对项目根的路径。
      const pmDesc = node.pmViewDesc;
      if (pmDesc && pmDesc.node && pmDesc.node.attrs) {
        const attrs = pmDesc.node.attrs;
        for (const key of ["fsPath", "path", "absolutePath", "resourcePath"]) {
          const value = attrs[key];
          if (typeof value === "string" && value.trim()) return value.trim();
        }
      }
      const fiberKey = Object.keys(node).find((k) => k.startsWith("__reactFiber$"));
      let fiber = fiberKey ? node[fiberKey] : null;
      for (let depth = 0; fiber && depth < 15; depth += 1, fiber = fiber.return) {
        const props = fiber.memoizedProps;
        if (!props || typeof props !== "object") continue;
        for (const key of ["resourcePath", "localPath", "absolutePath", "filePath"]) {
          const value = props[key];
          if (typeof value === "string" && value.trim()) return value.trim();
        }
      }
      return "";
    };
    composer.querySelectorAll("button[aria-label]").forEach((button) => {
      if (!/file-attachment/.test(String(button.className || ""))) return;
      const name = normalizeFileAttachmentName(button.getAttribute("aria-label"));
      if (!name) return;
      const path = readFiberFilePath(button);
      files.push(path ? { name, path } : name);
    });

    const mentions = [];
    const mentionSeen = new Set();
    const skills = [];
    const looksLikeFileReferenceText = (value) => (
      /^[\w.() -]+\.[A-Za-z0-9]{1,12}$/.test(value)
      || /^\[[^\]]+\]\([^)]+\)$/.test(value)
      || /[/\\][^/\\]+\.[A-Za-z0-9]{1,12}$/.test(value)
    );
    // 复制 mention chip 出来是 `[name](abs-path)`，直接解析出绝对路径最省事；
    // 页面里 mention 还只是 chip 元素，无 markdown 文本，靠 fiber 兜底。
    const parseMarkdownFileRef = (value) => {
      const match = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(String(value || "").trim());
      if (!match) return null;
      const name = match[1].trim();
      const path = match[2].trim();
      return name ? { name, path } : null;
    };
    const pushMentionEntry = (name, path) => {
      const cleanName = String(name || "").trim();
      if (!cleanName) return;
      const cleanPath = String(path || "").trim();
      const key = `${cleanName}|${cleanPath}`;
      if (mentionSeen.has(key)) return;
      mentionSeen.add(key);
      mentions.push(cleanPath ? { name: cleanName, path: cleanPath } : cleanName);
    };
    const addMentionOrSkill = (text, defaultKind = "", fiberPath = "") => {
      const raw = normalize(text || "");
      if (!raw) return;
      const parsed = parseMarkdownFileRef(raw);
      if (parsed) {
        pushMentionEntry(parsed.name, parsed.path);
        return;
      }
      if (raw.startsWith("$")) {
        if (!skills.includes(raw)) skills.push(raw);
      } else if (raw.startsWith("@")) {
        pushMentionEntry(raw, fiberPath);
      } else if (defaultKind === "mention" && looksLikeFileReferenceText(raw)) {
        pushMentionEntry(raw, fiberPath);
      }
    };
    const collectMentionOrSkillText = (node) => {
      if (!node || node.closest?.(`#${rootId}`)) return;
      // 带图标的工具开关（浏览器/电脑等）是 composer 控件装饰，不进 prompt，跳过。
      const text = normalize(node.textContent || "");
      const defaultKind = looksLikeFileReferenceText(text) ? "mention" : "";
      if (node.querySelector?.("img, svg") && !/^[@$]/.test(text) && defaultKind !== "mention") return;
      // mention chip 的 React fiber 与 file-attachment 一样带 resourcePath，
      // 只有拿到绝对路径才能让 Python 端跨仓库读盘算真实 token（否则退化成按名估算）。
      const fiberPath = readFiberFilePath(node);
      addMentionOrSkill(node.getAttribute?.("aria-label"), defaultKind, fiberPath);
      addMentionOrSkill(node.getAttribute?.("title"), defaultKind, fiberPath);
      addMentionOrSkill(text, defaultKind, fiberPath);
    };
    composer.querySelectorAll([
      "span.inline-mention-brand-aware",
      "[data-testid*='mention']",
      "[data-testid*='skill']",
      "[class*='mention']",
      "[class*='skill']",
      "[aria-label^='@']",
      "[aria-label^='$']",
    ].join(",")).forEach(collectMentionOrSkillText);

    return { images, files, mentions, skills };
  }

  function composerAttachmentsSignature(attachments) {
    return JSON.stringify([
      attachments.images.map((item) => `${item.name}|${item.width}x${item.height}`),
      attachments.files.map((item) =>
        (item && typeof item === "object") ? `${item.name}|${item.path || ""}` : String(item)
      ),
      attachments.mentions.map((item) =>
        (item && typeof item === "object") ? `${item.name}|${item.path || ""}` : String(item)
      ),
      attachments.skills,
    ]);
  }

  function reportComposerAttachments(force = false) {
    if (!composerBadgeEnabled) return;
    const attachments = collectComposerAttachments();
    const signature = composerAttachmentsSignature(attachments);
    if (!force && window[composerAttachmentsSignatureName] === signature) return;
    window[composerAttachmentsSignatureName] = signature;
    const ref = readActiveSessionRef();
    const payload = {
      sessionId: ref.sessionId || "",
      images: attachments.images,
      files: attachments.files,
      mentions: attachments.mentions,
      skills: attachments.skills,
      observedAt: Date.now(),
    };
    // 页面 CSP 的 connect-src 不含 http://127.0.0.1，fetch 到本地桥会被拦，
    // 因此优先走 CDP binding（与 active-session 一致），fetch 仅作兜底。
    const binding = window[composerAttachmentsBindingName];
    if (typeof binding === "function") {
      try {
        binding(JSON.stringify(payload));
        return;
      } catch (_) {}
    }
    const bridge = settingsBridgeUrl();
    if (!bridge) return;
    fetch(`${bridge}/composer-attachments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {});
  }

  function scheduleComposerAttachmentsReport(force = false) {
    if (!composerBadgeEnabled) return;
    clearTimeout(window[composerAttachmentsTimerName] || 0);
    window[composerAttachmentsTimerName] = setTimeout(() => {
      reportComposerAttachments(force);
    }, composerAttachmentsDebounceMs);
  }

  function collectLayoutSnapshot() {
    const states = loadStates();
    const panels = {};
    for (const name of Object.keys(PANEL)) {
      const state = states[name] || {};
      const node = document.querySelector(`#${rootId} [data-panel="${name}"]`);
      const rect = node ? node.getBoundingClientRect() : null;
      panels[name] = {
        expanded: !!state.expanded,
        manual: !!state.manual,
        x: Number.isFinite(state.x) ? Math.round(state.x) : (rect ? Math.round(rect.left) : null),
        y: Number.isFinite(state.y) ? Math.round(state.y) : (rect ? Math.round(rect.top) : null),
        width: Number.isFinite(state.width) ? Math.round(state.width) : (rect ? Math.round(rect.width) : null),
        collapsedHeight: Number.isFinite(state.collapsedHeight) ? Math.round(state.collapsedHeight) : null,
        expandedHeight: Number.isFinite(state.expandedHeight) ? Math.round(state.expandedHeight) : null,
      };
    }
    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      panels,
    };
  }

  function reportLayout(reason, panelName) {
    const snapshot = collectLayoutSnapshot();
    const signature = JSON.stringify([reason, panelName || "", snapshot.panels]);
    if (window[layoutReportSignatureName] === signature) return;
    window[layoutReportSignatureName] = signature;
    const payload = {
      reason: String(reason || ""),
      panel: String(panelName || ""),
      layout: snapshot,
      observedAt: Date.now(),
    };
    const binding = window[layoutBindingName];
    if (typeof binding === "function") {
      try {
        binding(JSON.stringify(payload));
        return;
      } catch (_) {}
    }
    const bridge = settingsBridgeUrl();
    if (!bridge) return;
    fetch(`${bridge}/layout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {});
  }

  function scheduleLayoutReport(reason, panelName) {
    clearTimeout(window[layoutReportTimerName] || 0);
    window[layoutReportTimerName] = setTimeout(() => {
      reportLayout(reason, panelName);
    }, 80);
  }

  function humanizeTokens(value) {
    const n = Math.max(0, Math.round(Number(value) || 0));
    if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return String(n);
  }

  function currentPayload() {
    const payload = window[stateName]?.payload || {};
    if (Array.isArray(payload.supportImages) && payload.supportImages.length) {
      return payload;
    }
    const persistedSupportImages = loadPersistedSupportImages();
    if (!persistedSupportImages.length) return payload;
    return { ...payload, supportImages: persistedSupportImages };
  }

  function formatMoney3(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "";
    return `$${(Number(value) || 0).toFixed(3)}`;
  }

  function updateComposerBadgeText(root = document.getElementById(rootId)) {
    window[composerBadgeRafName] = 0;
    if (!composerBadgeEnabled || !root) return;
    const payload = currentPayload();
    // 运行中：黄灯优先，滚动显示 AI 正在读取的文件。
    if (payload.activityWarning && payload.activityReadingFile) {
      setText(root, "requestComposerTokens", String(payload.activityReadingFile));
      return;
    }
    // 未发送：静态底价（会话/规则/工具/协议，来自 Python）+ 实时输入文本（浏览器侧）。
    const base = Math.max(0, Number(payload.preSendBaseTokens) || 0);
    const input = window[composerInputNodeName];
    const live = composerTokenCount(composerInputText(input));
    const totalTokens = base + live;
    // 合计金额 = Python 预算(不含实时输入) + 实时输入 × input 单价。
    const hasPrices = !!payload.preSendHasPrices;
    const liveCost = live * (Number(payload.preSendInputPrice) || 0);
    const totalCost = hasPrices ? (Number(payload.preSendTotalCost) || 0) + liveCost : null;
    const money = hasPrices ? formatMoney3(totalCost) : "";
    const badgeText = money
      ? `预估 ${money} · ${humanizeTokens(totalTokens)} Ts`
      : `预估 ~${humanizeTokens(totalTokens)} Ts`;
    setText(root, "requestComposerTokens", badgeText);
    renderComposerBreakdown(root, payload, live, totalTokens, totalCost);
  }

  function scheduleComposerBadgeUpdate() {
    if (!composerBadgeEnabled) return;
    if (window[composerBadgeRafName]) return;
    window[composerBadgeRafName] = requestAnimationFrame(() => updateComposerBadgeText());
  }

  function renderComposerBreakdown(root, payload, liveInputTokens, totalTokens, totalCost) {
    if (!composerBadgeEnabled) return;
    const node = root.querySelector('[data-field="requestComposerBreakdown"]');
    if (!node) return;
    const rows = Array.isArray(payload.preSendBreakdown) ? payload.preSendBreakdown : [];
    if (!rows.length) {
      node.innerHTML = "";
      hideComposerBreakdown(root);
      return;
    }
    const hasPrices = !!payload.preSendHasPrices;
    const inputPrice = Number(payload.preSendInputPrice) || 0;
    const rowHtml = rows.map((row, index) => {
      // 第一行「输入框内容」用浏览器侧实时值覆盖 Python 的占位（token 与金额都实时）。
      const isLiveInput = index === 0;
      const tokens = isLiveInput ? liveInputTokens : (Number(row?.tokens) || 0);
      const cost = isLiveInput
        ? (hasPrices ? tokens * inputPrice : null)
        : (row?.cost ?? null);
      const label = escapeHtml(String(row?.label || ""));
      const note = row?.note ? `<span class="codex-usage-hud-token-breakdown-note">${escapeHtml(String(row.note))}</span>` : "";
      const money = hasPrices ? escapeHtml(formatMoney3(cost)) : "—";
      return `<span class="codex-usage-hud-token-breakdown-row">`
        + `<span class="codex-usage-hud-token-breakdown-name">${label}${note}</span>`
        + `<span class="codex-usage-hud-token-breakdown-tok">${escapeHtml(humanizeTokens(tokens))} Ts</span>`
        + `<span class="codex-usage-hud-token-breakdown-cost">${money}</span>`
        + `</span>`;
    }).join("");
    const titleMoney = hasPrices ? formatMoney3(totalCost) : "";
    const titleHtml = `<div class="codex-usage-hud-token-breakdown-title">`
      + `<span>发送底价预估</span>`
      + `<span class="codex-usage-hud-token-breakdown-title-cost">${escapeHtml(titleMoney)}</span>`
      + `</div>`;
    const footHtml = `<span class="codex-usage-hud-token-breakdown-row" data-total="true">`
      + `<span class="codex-usage-hud-token-breakdown-name">合计</span>`
      + `<span class="codex-usage-hud-token-breakdown-tok">${escapeHtml(humanizeTokens(totalTokens))} Ts</span>`
      + `<span class="codex-usage-hud-token-breakdown-cost">${hasPrices ? escapeHtml(formatMoney3(totalCost)) : "—"}</span>`
      + `</span>`;
    node.innerHTML = titleHtml + rowHtml + footHtml;
    // 若正处于展开态，内容刷新后重新定位（打字时金额/合计会变）。
    if (node.dataset.open === "true") positionComposerBreakdown(root, node);
  }

  function composerBadgeElement(root = document.getElementById(rootId)) {
    if (!composerBadgeEnabled) return null;
    return root?.querySelector(".codex-usage-hud-token-badge") || null;
  }

  function positionComposerBreakdown(root, node) {
    const badge = composerBadgeElement(root);
    if (!badge) return;
    const rect = badge.getBoundingClientRect();
    // 先显示以取得尺寸，再定位到徽章正上方、右对齐。
    const width = node.offsetWidth || 200;
    const height = node.offsetHeight || 80;
    const margin = 6;
    let left = rect.right - width;
    let top = rect.top - height - margin;
    if (top < 4) top = rect.bottom + margin;         // 顶部空间不足则翻到下方
    if (left < 4) left = 4;                            // 防止溢出左边界
    const maxLeft = window.innerWidth - width - 4;
    if (left > maxLeft) left = Math.max(4, maxLeft);
    node.style.left = `${Math.round(left)}px`;
    node.style.top = `${Math.round(top)}px`;
  }

  function showComposerBreakdown(root = document.getElementById(rootId)) {
    if (!composerBadgeEnabled) return;
    if (!root) return;
    if (badgeWarningActive()) return;                  // 黄灯态不显示底价明细
    const node = root.querySelector('[data-field="requestComposerBreakdown"]');
    if (!node || !node.innerHTML) return;
    node.hidden = false;
    node.dataset.open = "true";
    positionComposerBreakdown(root, node);
  }

  function hideComposerBreakdown(root = document.getElementById(rootId)) {
    if (!composerBadgeEnabled) return;
    const node = root?.querySelector('[data-field="requestComposerBreakdown"]');
    if (!node) return;
    node.dataset.open = "false";
    node.hidden = true;
  }

  function badgeWarningActive() {
    const payload = currentPayload();
    return !!(payload.activityWarning && payload.activityReadingFile);
  }

  function refreshComposerBadgeState(root = document.getElementById(rootId)) {
    if (!composerBadgeEnabled) return;
    if (!root) return;
    const warning = badgeWarningActive();
    const focused = !!window[composerFocusStateName];
    root.querySelectorAll('[data-composer-badge]').forEach((node) => {
      if (warning) {
        node.dataset.badgeState = "warning";
        node.dataset.composerBadge = "active";
      } else {
        delete node.dataset.badgeState;
        node.dataset.composerBadge = focused ? "active" : "idle";
      }
    });
    // 黄灯运行时或输入框聚焦时都需要刷新文案。
    if (warning || focused) scheduleComposerBadgeUpdate();
    // 徽章隐藏（idle）或黄灯态时，强制收起底价明细浮层。
    if (warning || !focused) hideComposerBreakdown(root);
  }

  function setComposerBadgeActive(active) {
    if (!composerBadgeEnabled) return;
    const root = document.getElementById(rootId);
    if (!root) return;
    const changed = window[composerFocusStateName] !== !!active;
    window[composerFocusStateName] = !!active;
    refreshComposerBadgeState(root);
    // Showing/hiding the badge changes the marquee line's available width, so
    // re-evaluate scrolling without disturbing the marquee logic itself.
    if (changed) requestAnimationFrame(() => refreshAllMarquees(root));
  }

  function detachComposerInputWatchers() {
    const input = window[composerInputNodeName];
    const handlers = window[composerInputHandlersName];
    if (input && handlers) {
      input.removeEventListener("focus", handlers.focus, true);
      input.removeEventListener("blur", handlers.blur, true);
      input.removeEventListener("input", handlers.input, true);
      input.removeEventListener("keydown", handlers.keydown, true);
    }
    if (handlers?.composer && handlers?.click) {
      handlers.composer.removeEventListener("click", handlers.click, true);
    }
    window[composerInputNodeName] = null;
    window[composerInputHandlersName] = null;
    window[composerAttachmentsObserverName]?.disconnect?.();
    window[composerAttachmentsObserverName] = null;
  }

  function ensureComposerInputWatchers() {
    if (!composerBadgeEnabled) {
      detachComposerInputWatchers();
      return;
    }
    const input = composerInputElement();
    const composer = composerElement();
    const existingHandlers = window[composerInputHandlersName];
    if (input === window[composerInputNodeName] && composer && existingHandlers?.composer === composer) {
      if (input && window[composerFocusStateName]) scheduleComposerBadgeUpdate();
      return;
    }
    detachComposerInputWatchers();
    if (!input) {
      setComposerBadgeActive(false);
      return;
    }
    const handlers = {
      focus: () => setComposerBadgeActive(true),
      blur: () => setComposerBadgeActive(false),
      input: () => {
        if (window[composerFocusStateName]) scheduleComposerBadgeUpdate();
        // 粘贴/删除文本也可能带走 @ 引用，顺带核对附件。
        scheduleComposerAttachmentsReport();
      },
      keydown: (event) => {
        if (
          event.defaultPrevented
          || event.isComposing
          || event.key !== "Enter"
          || event.shiftKey
          || event.altKey
          || event.ctrlKey
          || event.metaKey
        ) {
          return;
        }
        scheduleActiveSessionSendFollowup("composer-enter");
      },
      click: (event) => {
        const button = event.target?.closest?.("button, [role='button']");
        if (!button || !composer?.contains?.(button)) return;
        if (!activeSessionComposerSubmitButton(button)) return;
        scheduleActiveSessionSendFollowup("composer-send-click");
      },
      composer,
    };
    input.addEventListener("focus", handlers.focus, true);
    input.addEventListener("blur", handlers.blur, true);
    input.addEventListener("input", handlers.input, true);
    input.addEventListener("keydown", handlers.keydown, true);
    window[composerInputNodeName] = input;
    window[composerInputHandlersName] = handlers;
    // The composer may already hold focus when we (re)attach after a re-inject.
    const focused = document.activeElement === input
      || (input.contains?.(document.activeElement) ?? false);
    setComposerBadgeActive(focused);
    // 附件（图片/文件/@引用）是异步插入的 DOM 节点，靠 MutationObserver 捕获增删。
    if (composer) {
      composer.addEventListener("click", handlers.click, true);
      const observer = new MutationObserver(() => scheduleComposerAttachmentsReport());
      observer.observe(composer, { subtree: true, childList: true });
      window[composerAttachmentsObserverName] = observer;
    }
    // 首次挂载立即上报一次（可能已带附件，例如重注入后）。
    scheduleComposerAttachmentsReport(true);
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

  function syncPosition(names = Object.keys(PANEL)) {
    const root = document.getElementById(rootId);
    if (!root) return;
    positionStartupBubble(root);
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
    for (const timer of (window[settleTimerName] || [])) clearTimeout(timer);
    window[settleTimerName] = [
      setTimeout(() => syncPosition(names), 50),
      setTimeout(() => syncPosition(names), 140),
      setTimeout(() => syncPosition(names), 260),
    ];
  }

  function scheduleForPanels(names = Object.keys(PANEL), { invalidateTop = false } = {}) {
    const panelNames = Array.isArray(names) ? names.filter((name) => PANEL[name]) : Object.keys(PANEL);
    if (invalidateTop || panelNames.includes("top")) topSlotCache = null;
    if (!pendingSyncPanels) pendingSyncPanels = new Set();
    for (const name of panelNames) pendingSyncPanels.add(name);
    cancelAnimationFrame(window[rafName] || 0);
    window[rafName] = requestAnimationFrame(() => {
      const nextPanels = Array.from(pendingSyncPanels || Object.keys(PANEL));
      pendingSyncPanels = null;
      syncPosition(nextPanels);
    });
  }

  function scheduleRequestAfterComposerSettles() {
    clearTimeout(window[composerSettleTimerName] || 0);
    window[composerSettleTimerName] = setTimeout(() => {
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
    window[mutationObserverName]?.disconnect?.();
    window[resizeObserverName]?.disconnect?.();
    window[mutationObserverName] = new MutationObserver(handleLayoutMutations);
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
      window[resizeObserverName] = new ResizeObserver(() => scheduleForPanels(Object.keys(PANEL), { invalidateTop: true }));
      if (headerNode) window[resizeObserverName].observe(headerNode);
      if (composerNode && composerNode !== headerNode) window[resizeObserverName].observe(composerNode);
    } else {
      window[resizeObserverName] = { disconnect() {} };
    }
    ensureComposerInputWatchers();
  }

  function stopBootstrapObserver() {
    window[bootstrapObserverName]?.disconnect?.();
    clearTimeout(window[bootstrapTimerName] || 0);
    delete window[bootstrapObserverName];
    delete window[bootstrapTimerName];
  }

  function startBootstrapObserver() {
    if (cachedHeaderNode && cachedComposerNode) {
      stopBootstrapObserver();
      return;
    }
    if (window[bootstrapObserverName] || !document.body) return;
    window[bootstrapObserverName] = new MutationObserver(() => {
      invalidateHeaderAnchor();
      invalidateComposerAnchor();
      const headerNode = conversationHeaderElement();
      const composerNode = composerElement();
      if (!headerNode && !composerNode) return;
      scheduleForPanels(Object.keys(PANEL), { invalidateTop: true });
      if (headerNode && composerNode) stopBootstrapObserver();
    });
    window[bootstrapObserverName].observe(document.body, { childList: true, subtree: true });
    window[bootstrapTimerName] = setTimeout(stopBootstrapObserver, 5000);
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

  function lineInner(node) {
    let inner = node.querySelector(":scope > .codex-usage-hud-line-inner");
    if (!inner) {
      const text = node.textContent || "";
      node.textContent = "";
      inner = document.createElement("span");
      inner.className = "codex-usage-hud-line-inner";
      inner.textContent = text;
      node.appendChild(inner);
    }
    return inner;
  }

  function progressStripViewport(node) {
    const parent = node?.parentElement;
    if (!parent?.classList?.contains("codex-usage-hud-progress-strip-viewport")) return null;
    return parent;
  }

  function measureProgressRailWidth(rail) {
    const probe = rail?.querySelector?.(":scope > .codex-usage-hud-progress-size-probe");
    if (!probe) return 0;
    const measured = Math.max(
      probe.scrollWidth || 0,
      probe.getBoundingClientRect?.().width || 0,
    );
    return Math.max(0, Math.ceil(measured + 2));
  }

  function clearCollapsedProgressStrip(node) {
    if (!node) return;
    delete node.dataset.overflow;
    node.style.removeProperty("--codex-usage-hud-progress-strip-distance");
    node.style.removeProperty("--codex-usage-hud-progress-strip-duration");
    node.style.removeProperty("grid-template-columns");
    node.style.removeProperty("width");
    node.style.removeProperty("min-width");
    node.querySelectorAll(":scope > .codex-usage-hud-progress-rail").forEach((rail) => {
      rail.style.removeProperty("width");
    });
  }

  function collapsedProgressStripSignature(widths, overflow) {
    return `${widths.join(",")}|${overflow}`;
  }

  function refreshCollapsedProgressStrip(node) {
    if (!node?.classList?.contains("codex-usage-hud-progress-strip") || !node.isConnected) return;
    const viewport = progressStripViewport(node);
    if (!viewport) return;
    const rails = Array.from(node.querySelectorAll(":scope > .codex-usage-hud-progress-rail"));
    if (rails.length <= 1) {
      clearCollapsedProgressStrip(node);
      delete node.dataset.layoutSignature;
      return;
    }

    const widths = rails.map(measureProgressRailWidth);
    if (widths.some((width) => width <= 0)) {
      clearCollapsedProgressStrip(node);
      delete node.dataset.layoutSignature;
      return;
    }

    const available = Math.max(1, viewport.clientWidth || viewport.getBoundingClientRect().width || 0);
    const gapStyle = getComputedStyle(node);
    const gap = Number.parseFloat(gapStyle.columnGap || gapStyle.gap || "0") || 0;
    const collapsedTailPeekWidth = 40;
    const remainingAfterSession = Math.max(0, available - widths[0] - (gap * Math.max(0, rails.length - 1)));
    const tailShare = remainingAfterSession / Math.max(1, rails.length - 1);
    const required = widths.reduce((sum, width) => sum + width, 0) + (gap * Math.max(0, rails.length - 1));
    if (required <= available + 1 || tailShare > collapsedTailPeekWidth) {
      clearCollapsedProgressStrip(node);
      delete node.dataset.layoutSignature;
      rails[0].style.width = `${widths[0]}px`;
      return;
    }

    const overflow = Math.ceil(required - available);
    const signature = collapsedProgressStripSignature(widths, overflow);
    if (node.dataset.layoutSignature === signature) return;

    clearCollapsedProgressStrip(node);
    node.dataset.layoutSignature = signature;
    rails.forEach((rail, index) => {
      rail.style.width = `${widths[index]}px`;
    });
    node.dataset.overflow = "true";
    node.style.gridTemplateColumns = widths.map((width) => `${width}px`).join(" ");
    node.style.width = `${required}px`;
    node.style.minWidth = `${required}px`;
    node.style.setProperty("--codex-usage-hud-progress-strip-distance", `${overflow}px`);
    node.style.setProperty(
      "--codex-usage-hud-progress-strip-duration",
      `${Math.max(5000, 3200 + (overflow * 55))}ms`,
    );
  }

  function refreshMarquee(node) {
    if (!node?.classList?.contains("codex-usage-hud-line") || !node.isConnected) return;
    const inner = node.querySelector(":scope > .codex-usage-hud-line-inner");
    if (!inner) return;
    const available = Math.max(1, node.clientWidth || node.getBoundingClientRect().width || 0);
    const overflow = Math.ceil((inner.scrollWidth || inner.getBoundingClientRect().width || 0) - available);
    if (overflow > 1) {
      node.dataset.marquee = "true";
      node.style.setProperty("--codex-usage-hud-marquee-distance", `${overflow}px`);
      node.style.setProperty("--codex-usage-hud-marquee-duration", `${Math.max(4000, 3000 + (overflow * 60))}ms`);
      return;
    }
    delete node.dataset.marquee;
    node.style.removeProperty("--codex-usage-hud-marquee-distance");
    node.style.removeProperty("--codex-usage-hud-marquee-duration");
  }

  function refreshAllMarquees(root = document.getElementById(rootId)) {
    if (!root) return;
    root.querySelectorAll(".codex-usage-hud-line").forEach(refreshMarquee);
    root.querySelectorAll(".codex-usage-hud-progress-strip").forEach(refreshCollapsedProgressStrip);
  }

  function applyLineText(node, value, { refresh = true } = {}) {
    const text = String(value || "");
    const inner = lineInner(node);
    inner.textContent = text;
    node.dataset.currentText = text;
    if (refresh) requestAnimationFrame(() => refreshMarquee(node));
  }

  function cancelNumericAnimation(node) {
    const animation = numericAnimations.get(node);
    if (!animation) return;
    cancelAnimationFrame(animation.raf);
    numericAnimations.delete(node);
  }

  function extractNumericParts(text) {
    const source = String(text || "");
    const parts = [];
    const tokens = [];
    let cursor = 0;
    numericTokenRe.lastIndex = 0;
    for (const match of source.matchAll(numericTokenRe)) {
      parts.push(source.slice(cursor, match.index));
      tokens.push(match[0]);
      cursor = Number(match.index || 0) + match[0].length;
    }
    parts.push(source.slice(cursor));
    return { parts, tokens };
  }

  function parseNumericToken(token) {
    const match = String(token || "").match(/^(\$?)(\d+(?:,\d{3})*(?:\.\d+)?)([kM%]?)$/);
    if (!match) return null;
    const [, prefix, amount, suffix] = match;
    const decimals = amount.includes(".") ? amount.split(".", 2)[1].length : 0;
    const usesGrouping = amount.includes(",");
    const value = Number(amount.replace(/,/g, ""));
    if (!Number.isFinite(value)) return null;
    return { prefix, value, suffix, decimals, usesGrouping };
  }

  function formatNumericToken(value, template) {
    const decimals = Math.max(0, template.decimals || 0);
    let body = "";
    if (decimals <= 0) {
      const rounded = Math.round(value);
      body = template.usesGrouping ? rounded.toLocaleString("en-US") : String(rounded);
    } else if (template.usesGrouping) {
      body = value.toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      });
    } else {
      body = value.toFixed(decimals);
    }
    return `${template.prefix}${body}${template.suffix}`;
  }

  function canAnimateNumericText(startText, endText) {
    const start = extractNumericParts(startText);
    const end = extractNumericParts(endText);
    if (!start.tokens.length || start.tokens.length !== end.tokens.length) return false;
    if (start.parts.length !== end.parts.length || start.parts.some((part, index) => part !== end.parts[index])) return false;
    return start.tokens.every((token, index) => {
      const startToken = parseNumericToken(token);
      const endToken = parseNumericToken(end.tokens[index]);
      return !!startToken && !!endToken && startToken.prefix === endToken.prefix && startToken.suffix === endToken.suffix;
    });
  }

  function interpolateNumericText(startText, endText, progress) {
    const start = extractNumericParts(startText);
    const end = extractNumericParts(endText);
    const clamped = clamp(progress, 0, 1);
    const pieces = [];
    for (let index = 0; index < end.parts.length - 1; index += 1) {
      pieces.push(end.parts[index]);
      const startToken = parseNumericToken(start.tokens[index]);
      const endToken = parseNumericToken(end.tokens[index]);
      if (!startToken || !endToken) {
        pieces.push(end.tokens[index]);
        continue;
      }
      const value = startToken.value + ((endToken.value - startToken.value) * clamped);
      pieces.push(formatNumericToken(value, endToken));
    }
    pieces.push(end.parts[end.parts.length - 1]);
    return pieces.join("");
  }

  function setAnimatedLineText(node, value) {
    const next = String(value || "");
    const current = node.dataset.currentText ?? node.textContent ?? "";
    cancelNumericAnimation(node);
    if (!current || current === next || !canAnimateNumericText(current, next)) {
      applyLineText(node, next);
      return;
    }
    const startedAt = performance.now();
    const step = (now) => {
      const progress = clamp((now - startedAt) / 360, 0, 1);
      applyLineText(node, interpolateNumericText(current, next, progress), { refresh: progress >= 1 });
      if (progress >= 1) {
        applyLineText(node, next);
        numericAnimations.delete(node);
        return;
      }
      numericAnimations.set(node, { raf: requestAnimationFrame(step) });
    };
    numericAnimations.set(node, { raf: requestAnimationFrame(step) });
  }

  function setText(root, field, value) {
    root.querySelectorAll(`[data-field="${field}"]`).forEach((node) => {
      const text = String(value || "");
      if (node.classList.contains("codex-usage-hud-line")) {
        node.title = text;
        if (field === "requestLine" || field === "requestLineExpanded") {
          setAnimatedLineText(node, text);
        } else {
          cancelNumericAnimation(node);
          applyLineText(node, text);
        }
        return;
      }
      node.textContent = text;
      if (text) node.title = text;
      else node.removeAttribute("title");
    });
  }

  function setFieldTitle(root, field, value) {
    const title = String(value || "");
    root.querySelectorAll(`[data-field="${field}"]`).forEach((node) => {
      if (title) node.title = title;
      else if (!node.dataset.copyable) node.removeAttribute("title");
    });
  }

  function fallbackCopyHudText(text) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "readonly");
    area.style.position = "fixed";
    area.style.left = "-1000px";
    area.style.top = "0";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.focus();
    area.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_) {
      ok = false;
    }
    area.remove();
    return ok;
  }

  async function copyHudText(text) {
    const value = String(text || "");
    if (!value) return false;
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_) {}
    }
    return fallbackCopyHudText(value);
  }

  function flashCopyState(node, ok) {
    const previousTitle = node.dataset.copyTitle || node.title || "";
    node.dataset.copied = ok ? "true" : "false";
    node.title = ok ? "已复制" : "复制失败";
    setTimeout(() => {
      if (!node.isConnected) return;
      delete node.dataset.copied;
      node.title = previousTitle;
    }, 900);
  }

  function configureCopy(root, field, text, title, copyField) {
    root.querySelectorAll(`[data-field="${field}"]`).forEach((node) => {
      const value = String(text || "");
      if (value) {
        node.dataset.copyable = "true";
        node.dataset.copyText = value;
        node.dataset.copyTitle = title;
        node.dataset.copyField = copyField;
        node.title = title;
        return;
      }
      delete node.dataset.copyable;
      delete node.dataset.copyText;
      delete node.dataset.copyTitle;
      delete node.dataset.copyField;
      delete node.dataset.copied;
      node.removeAttribute("title");
    });
  }

  function elapsedSecondsText(startedAtMs, nowMs = Date.now()) {
    const seconds = Math.max(0, Math.floor((nowMs - startedAtMs) / 1000));
    return `${seconds}s`.padStart(8, " ");
  }

  function refreshRunningRows() {
    const root = document.getElementById(rootId);
    if (!root) {
      clearInterval(window[runningTimerName] || 0);
      window[runningTimerName] = 0;
      return;
    }
    const nodes = Array.from(root.querySelectorAll(".codex-usage-hud-row-time[data-running='true']"));
    if (!nodes.length) {
      clearInterval(window[runningTimerName] || 0);
      window[runningTimerName] = 0;
      return;
    }
    const now = Date.now();
    for (const node of nodes) {
      const startedAtMs = Date.parse(node.dataset.startedAt || "");
      if (!Number.isFinite(startedAtMs)) continue;
      node.textContent = elapsedSecondsText(startedAtMs, now);
      node.dataset.tick = String(Math.floor(now / 1000) % 2);
    }
  }

  function syncRunningRowsTimer(root) {
    refreshRunningRows();
    const hasRunningRow = !!root.querySelector(".codex-usage-hud-row-time[data-running='true']");
    if (hasRunningRow && !window[runningTimerName]) {
      window[runningTimerName] = setInterval(refreshRunningRows, 1000);
    }
    if (!hasRunningRow && window[runningTimerName]) {
      clearInterval(window[runningTimerName]);
      window[runningTimerName] = 0;
    }
  }

  function appendStructuredRequestRow(list, item, index) {
    const row = document.createElement("div");
    row.className = "codex-usage-hud-row";
    row.dataset.latest = String(index === 0);
    const prefix = String(item?.prefix ?? "");
    const time = String(item?.time ?? "");
    const suffix = String(item?.suffix ?? "");
    if (!prefix && !suffix) {
      row.textContent = String(item?.text || "");
      list.appendChild(row);
      return;
    }
    const prefixNode = document.createElement("span");
    prefixNode.textContent = prefix;
    const timeNode = document.createElement("span");
    timeNode.className = "codex-usage-hud-row-time";
    timeNode.textContent = time;
    const running = index === 0 && item?.running && item?.startedAt;
    if (running) {
      timeNode.dataset.running = "true";
      timeNode.dataset.startedAt = String(item.startedAt);
    }
    const suffixNode = document.createElement("span");
    suffixNode.textContent = suffix;
    row.append(prefixNode, timeNode, suffixNode);
    list.appendChild(row);
  }

  function renderRequestRows(root, rows, rowDetails, newSession = false) {
    const list = root.querySelector('[data-field="requestRows"]');
    if (!list) return;
    list.textContent = "";
    const details = Array.isArray(rowDetails) && rowDetails.length ? rowDetails : [];
    if (details.length) {
      details.forEach((item, index) => appendStructuredRequestRow(list, item, index));
      syncRunningRowsTimer(root);
      return;
    }
    if (newSession && (!Array.isArray(rows) || !rows.length)) {
      syncRunningRowsTimer(root);
      return;
    }
    const items = Array.isArray(rows) && rows.length ? rows : ["本次请求(等待) $0.0000 ↑- ◎- ↓- ◇- ↻- ∑-"];
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "codex-usage-hud-row";
      row.textContent = String(item || "");
      list.appendChild(row);
    }
    syncRunningRowsTimer(root);
  }

  function normalizeProgressRatio(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number)) return 0;
    return clamp(number, 0, 1);
  }

  function progressRail(metric) {
    const rail = document.createElement("span");
    rail.className = "codex-usage-hud-progress-rail";
    rail.dataset.tone = String(metric?.tone || "day");
    const label = String(metric?.label || "");
    const rightText = String(metric?.rightText || "");
    const overflowBadge = String(metric?.overflowBadge || "");
    const ratio = normalizeProgressRatio(metric?.ratio);
    const overflowRatio = normalizeProgressRatio(metric?.overflowRatio);
    const hasOverflow = overflowRatio > 0;
    if (hasOverflow) rail.dataset.overflow = "true";
    else delete rail.dataset.overflow;
    if (overflowBadge) rail.dataset.badge = "true";
    else delete rail.dataset.badge;
    const fullText = rightText ? `${label} / ${rightText}` : label;
    const tooltip = overflowBadge ? `${fullText || label} | ${overflowBadge}` : fullText;
    rail.title = tooltip;
    rail.setAttribute("aria-label", tooltip);

    function progressTextLayer(className, textClass = "codex-usage-hud-progress-text") {
      const layer = document.createElement("span");
      layer.className = className;
      layer.title = tooltip;
      if (rightText && textClass === "codex-usage-hud-progress-text") {
        const leftNode = document.createElement("span");
        leftNode.className = textClass;
        leftNode.textContent = label;
        leftNode.title = tooltip;
        const rightNode = document.createElement("span");
        rightNode.className = "codex-usage-hud-progress-right-text";
        rightNode.textContent = rightText;
        rightNode.title = tooltip;
        layer.append(leftNode, rightNode);
        return layer;
      }
      const textNode = document.createElement("span");
      textNode.className = textClass;
      textNode.textContent = fullText;
      textNode.title = tooltip;
      layer.appendChild(textNode);
      return layer;
    }

    rail.appendChild(progressTextLayer("codex-usage-hud-progress-size-probe", "codex-usage-hud-progress-probe-text"));

    const fill = document.createElement("span");
    fill.className = "codex-usage-hud-progress-fill";
    fill.style.width = `${Math.round(ratio * 1000) / 10}%`;
    rail.appendChild(fill);
    rail.appendChild(progressTextLayer("codex-usage-hud-progress-track-text"));
    if (hasOverflow) {
      const overflow = document.createElement("span");
      overflow.className = "codex-usage-hud-progress-overflow";
      overflow.style.width = `${Math.round(overflowRatio * 1000) / 10}%`;
      rail.appendChild(overflow);

      const anchor = document.createElement("span");
      anchor.className = "codex-usage-hud-progress-overflow-anchor";
      rail.appendChild(anchor);
    }
    if (overflowBadge) {
      const badge = document.createElement("span");
      badge.className = "codex-usage-hud-progress-badge";
      badge.textContent = overflowBadge;
      rail.appendChild(badge);
    }
    return rail;
  }

  function renderProgressList(container, metrics) {
    if (!container) return false;
    const items = Array.isArray(metrics) ? metrics.filter((item) => item && item.label) : [];
    container.replaceChildren();
    if (items.length > 0) container.dataset.count = String(items.length);
    else delete container.dataset.count;
    for (const item of items) container.appendChild(progressRail(item));
    return items.length > 0;
  }

  function renderTopProgress(root, payload) {
    const progress = payload?.topProgress || {};
    const main = root.querySelector(`.${topClass} .codex-usage-hud-main`);
    const collapsed = root.querySelector('[data-field="topCollapsedProgress"]');
    const hasCollapsed = renderProgressList(collapsed, progress.collapsed || []);
    if (main) main.dataset.progress = hasCollapsed ? "true" : "false";
    renderProgressList(root.querySelector('[data-field="topCacheProgress"]'), progress.cache ? [progress.cache] : []);
    renderProgressList(root.querySelector('[data-field="topBudgetProgress"]'), progress.budget || []);
  }

  function renderHeavyRounds(root, details) {
    const list = root.querySelector('[data-field="topHeavyRounds"]');
    if (!list) return;
    list.replaceChildren();
    const items = Array.isArray(details?.heavyRounds) ? details.heavyRounds.slice(0, 3) : [];
    if (!items.length) {
      list.dataset.empty = "true";
      const placeholders = [
        ["暂无会话高消耗轮次", "会话出现 token 确认后展示 Top 3"],
        ["等待统计", "不会因新需求开始而清空"],
        ["保持占位", "新轮次超过历史 Top 3 后刷新"],
      ];
      for (const [titleText, detailText] of placeholders) {
        const empty = document.createElement("div");
        empty.className = "codex-usage-hud-heavy-round";
        empty.dataset.placeholder = "true";
        empty.innerHTML = `
          <span class="codex-usage-hud-heavy-round-title">${titleText}</span>
          <span class="codex-usage-hud-heavy-round-detail">${detailText}</span>
        `;
        list.appendChild(empty);
      }
      return;
    }
    delete list.dataset.empty;
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "codex-usage-hud-heavy-round";
      const tooltip = String(item.tooltip || [item.title, item.detail].filter(Boolean).join("  "));
      if (tooltip) row.title = tooltip;
      const copyText = String(item.copyText || "");
      if (copyText) {
        row.dataset.copyable = "true";
        row.dataset.copyText = copyText;
        row.dataset.copyTitle = tooltip ? `点击复制轮次内容\n${tooltip}` : "点击复制轮次内容";
        row.dataset.copyField = "heavy";
        row.title = row.dataset.copyTitle;
      }
      const title = document.createElement("span");
      title.className = "codex-usage-hud-heavy-round-title";
      title.textContent = String(item.title || "");
      const detail = document.createElement("span");
      detail.className = "codex-usage-hud-heavy-round-detail";
      detail.textContent = String(item.detail || "");
      if (tooltip) detail.title = tooltip;
      row.append(title, detail);
      list.appendChild(row);
    }
  }

  function renderActivityTimeline(root, details) {
    const list = root.querySelector('[data-field="topActivityTrail"]');
    if (!list) return;
    const previousScrollTop = list.scrollTop || 0;
    const button = root.querySelector('[data-field="topActivityLoadMore"]');
    const items = Array.isArray(details?.activityTrail) ? details.activityTrail : [];
    const allItems = items.filter((item) => item && (item.title || item.detail || item.time));
    const signature = allItems.map((item) => [item.time, item.title, item.detail, item.tooltip].join("|")).join(";");
    const context = [
      details?.taskOrdinal || "",
      details?.currentTask || "",
      details?.title || "",
    ].join("|");
    const contextChanged = list.dataset.context !== context;
    if (contextChanged) {
      list.dataset.context = context;
      list.dataset.visibleCount = "4";
      list.scrollTop = 0;
    } else if (!list.dataset.visibleCount) {
      list.dataset.visibleCount = "4";
    }
    list.dataset.signature = signature;
    const visibleCount = Math.max(4, Number(list.dataset.visibleCount || 4));
    const visibleItems = allItems.slice(0, visibleCount);
    list.dataset.fill = visibleItems.length > 0 && visibleItems.length <= 4 ? "spread" : "dense";
    list.replaceChildren();
    if (button) {
      button.hidden = false;
      button.disabled = visibleItems.length >= allItems.length;
      button.textContent = "查看更多";
      button.title = visibleItems.length >= allItems.length ? "已显示全部活动轨迹" : "加载更早的活动轨迹";
    }
    if (!visibleItems.length) {
      const empty = document.createElement("div");
      empty.className = "codex-usage-hud-activity-node";
      empty.title = "等待会话产生新活动";
      empty.innerHTML = `
        <span class="codex-usage-hud-activity-node-time">--:--</span>
        <span class="codex-usage-hud-activity-node-dot"></span>
        <span>
          <span class="codex-usage-hud-activity-node-title">暂无时间节点</span>
          <span class="codex-usage-hud-activity-node-detail">等待会话产生新活动</span>
        </span>
      `;
      list.appendChild(empty);
      return;
    }
    for (const item of visibleItems) {
      const row = document.createElement("div");
      row.className = "codex-usage-hud-activity-node";
      row.dataset.active = String(!!item.active);
      const tooltip = String(item.tooltip || [item.time, item.title, item.detail].filter(Boolean).join("  "));
      if (tooltip) row.title = tooltip;
      const time = document.createElement("span");
      time.className = "codex-usage-hud-activity-node-time";
      time.textContent = String(item.time || "--:--");
      const dot = document.createElement("span");
      dot.className = "codex-usage-hud-activity-node-dot";
      const body = document.createElement("span");
      const title = document.createElement("span");
      title.className = "codex-usage-hud-activity-node-title";
      title.textContent = String(item.title || "活动");
      title.title = tooltip || title.textContent;
      const detail = document.createElement("span");
      detail.className = "codex-usage-hud-activity-node-detail";
      detail.textContent = String(item.detail || "");
      detail.title = tooltip || detail.textContent;
      if (detail.textContent || tooltip) {
        detail.dataset.copyable = "true";
        detail.dataset.copyText = tooltip || detail.textContent;
        detail.dataset.copyTitle = "点击复制轨迹详情";
        detail.dataset.copyField = "trail";
        detail.title = tooltip ? `点击复制轨迹详情\n${tooltip}` : "点击复制轨迹详情";
      }
      body.append(title, detail);
      row.append(time, dot, body);
      list.appendChild(row);
    }
    if (!contextChanged) {
      list.scrollTop = previousScrollTop;
    }
  }

  function renderTopDetails(root, payload) {
    const details = payload?.topDetails || {};
    const copies = payload?.topCopies || {};
    const mapping = {
      topTitle: details.title || "Codex 会话 / 预算",
      topSession: details.session || "",
      topSessionCost: details.sessionCost || "",
      topSessionTokens: details.sessionTokens || "",
      topSessionRounds: details.sessionRounds || "",
      topTaskOrdinalSession: details.taskOrdinalSession || "",
      topTaskOrdinalActivity: details.taskOrdinalActivity || "",
      topCacheText: details.cacheText || "",
      topSessionMix: details.sessionMix || "",
      topSessionAverage: details.sessionAverage || "",
      topSessionComposition: details.sessionComposition || "",
      topHeavyRoundsSummary: details.heavyRoundsSummary || "",
      topSessionInputTokens: details.sessionInputTokens || "",
      topSessionCachedTokens: details.sessionCachedTokens || "",
      topSessionOutputTokens: details.sessionOutputTokens || "",
      topSessionReasoningTokens: details.sessionReasoningTokens || "",
      topWarnings: details.warnings || "",
      topExecutingLabel: details.executingLabel || "正在执行",
      topExecuting: details.executing || "",
      topCurrentTaskLabel: details.currentTaskLabel || "当前需求",
      topCurrentTask: details.currentTask || "",
      topActivityState: details.activityState || "",
      topActivityElapsedLabel: details.activityElapsedLabel || "已运行",
      topActivityElapsed: details.activityElapsed || "",
      topActivityGapLabel: details.activityGapLabel || "当前等待",
      topActivityGap: details.activityGap || "",
      topActivityLastLabel: details.activityLastLabel || "需求轮次",
      topActivityLast: details.activityLast || "",
      topSlow: details.slow || "",
      topGap: details.gap || "",
    };
    for (const [field, value] of Object.entries(mapping)) setText(root, field, value);
    setFieldTitle(root, "topActivityLast", details.activityLastTooltip || details.activityLast || "");
    renderHeavyRounds(root, details);
    renderActivityTimeline(root, details);
    const hasWarnings = !!String(details.warnings || "").trim();
    root.querySelectorAll('[data-field-panel="topWarnings"]').forEach((node) => {
      node.hidden = !hasWarnings;
    });
    renderTopProgress(root, payload || {});
    configureCopy(root, "topSlow", copies.slow || "", "点击复制最慢工具命令", "slow");
    configureCopy(root, "topGap", copies.gap || "", "点击复制最长等待详情", "gap");
    configureCopy(root, "topCurrentTask", details.currentTask || "", `点击复制当前需求\n${details.currentTask || ""}`, "task");
    configureCopy(root, "topExecuting", details.executing || "", `点击复制${details.executingLabel || "当前活动"}\n${details.executing || ""}`, "executing");
  }

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

  function markHudStale() {
    const root = document.getElementById(rootId);
    const state = window[stateName] || {};
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
    clearTimeout(window[staleTimerName] || 0);
    if (!payloadNeedsStaleGuard(payload)) return;
    window[staleTimerName] = setTimeout(markHudStale, staleUpdateMs + 250);
  }

  function normalizePayloadDomains(payload) {
    const provided = payload?.payloadDomains && typeof payload.payloadDomains === "object"
      ? payload.payloadDomains
      : {};
    const allDomains = ["startup", "currentSession", "sessionSwitch", "budget", "settings", "overlay", "backgroundUsage", "diagnostics", "fileManagement", "usageInsights", "safeCleanup", "sessionCleanup"];
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

  function applyCurrentSessionPayload(root, payload) {
    setText(root, "topLine", payload?.topLine || "codex-usage-hud 等待数据");
    setText(root, "requestLine", payload?.requestLine || "本次请求 等待");
    setText(root, "requestLineExpanded", payload?.requestLine || "最近模型请求轮次");
    root.querySelectorAll('[data-field="topLine"], [data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.remove(warningClass);
    });
    root.querySelectorAll('[data-field="topLine"]').forEach((node) => {
      node.classList.toggle(warningClass, !!payload?.warning);
    });
    root.querySelectorAll('[data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.toggle(errorClass, payload?.requestStatus === "error");
    });
    renderTopDetails(root, payload || {});
    renderRequestRows(root, payload?.requestRows || [], payload?.requestRowDetails || [], !!(payload?.newSession || payload?.pendingSession));
    renderBackgroundUsageNotification(root, payload || {});
    applyActiveSessionSequence(payload);
  }

  function applyActiveSessionSequence(payload) {
    if (payload?.cachedPreview) return;
    const appliedSeq = Number(payload?.selectionSeq || 0);
    if (appliedSeq > Number(window[activeSessionAppliedSeqName] || 0)) {
      window[activeSessionAppliedSeqName] = appliedSeq;
    }
    if (appliedSeq > Number(window[activeSessionSelectionSeqName] || 0)) {
      // A HUD reinjection can receive an observation from the previous script
      // realm before installing the new one. Keep the next click monotonic
      // relative to the Python tracker that already accepted that sequence.
      window[activeSessionSelectionSeqName] = appliedSeq;
    }
  }

  function applySessionSwitchPayload(root, payload) {
    setText(root, "topLine", payload?.topLine || "codex-usage-hud 等待数据");
    setText(root, "requestLine", payload?.requestLine || "本次请求 等待");
    setText(root, "requestLineExpanded", payload?.requestLine || "最近模型请求轮次");
    root.querySelectorAll('[data-field="topLine"], [data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.remove(warningClass);
    });
    root.querySelectorAll('[data-field="topLine"]').forEach((node) => {
      node.classList.toggle(warningClass, !!payload?.warning);
    });
    root.querySelectorAll('[data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.toggle(errorClass, payload?.requestStatus === "error");
    });
    renderBackgroundUsageNotification(root, payload || {});
    applyActiveSessionSequence(payload);
  }

  function applySettingsPayload(root, payload) {
    applyTheme(root, payload || {});
    renderUpdateButtons(root, payload || {});
    applySettingsCommandStatus(payload || {});
    renderRestReminderToast(root, payload || {});
    refreshComposerBadgeState(root);
  }

  function renderRestReminderToast(root, payload) {
    const host = root || document.getElementById(rootId);
    if (!host) return;
    let toast = host.querySelector('[data-rest-reminder-toast="true"]');
    if (!toast) {
      host.insertAdjacentHTML("beforeend", restReminderToastMarkup());
      toast = host.querySelector('[data-rest-reminder-toast="true"]');
    }
    if (!toast) return;
    const reminder = payload?.restReminder && typeof payload.restReminder === "object"
      ? payload.restReminder
      : {};
    const visible = !!reminder.visible;
    toast.dataset.visible = visible ? "true" : "false";
    const messageNode = toast.querySelector('[data-rest-reminder-message="true"]');
    if (messageNode) {
      messageNode.textContent = String(reminder.message || "站起来走走，让眼睛放松片刻。");
    }
    const postponeBtn = toast.querySelector('[data-action="rest-reminder-postpone"]');
    if (postponeBtn) {
      const canPostpone = !!reminder.canPostpone;
      postponeBtn.hidden = !canPostpone;
      const minutes = Math.max(1, Math.round(Number(reminder.postponeMinutes) || 10));
      postponeBtn.textContent = `延后 ${minutes} 分钟`;
    }
  }

  function applyOverlayPayload(_root, _payload) {
    // Overlay payload is currently consumed by Python/desktop IPC. Keeping this
    // domain explicit lets renderer updates skip unrelated DOM work.
  }

  function backgroundUsageSelectedDetail(responsePayload, eventId) {
    const detail = responsePayload?.selectedDetail;
    if (!detail || typeof detail !== "object") return null;
    const detailEventId = String(detail.eventId || "").trim();
    return detailEventId && detailEventId === eventId ? detail : null;
  }

  function applyBackgroundUsagePayload(root, payload) {
    renderBackgroundUsageNotification(root, payload || {});
    const response = payload?.settingsCommandStatus?.backgroundUsageResponse;
    const openEventId = String(
      payload?.settingsCommandStatus?.backgroundUsageOpenEventId || ""
    ).trim();
    if (response && typeof response === "object") {
      const kind = String(response.kind || "");
      const requestId = String(response.requestId || "");
      const responseError = String(response.error || "");
      if (kind === "query") {
        if (requestId !== backgroundUsageState.queryRequestId) return;
        clearBackgroundUsageRequestTimeout("query");
        backgroundUsageState.loading = false;
        backgroundUsageState.data = response.payload || null;
        backgroundUsageState.loadedRevision = Math.max(
          0,
          Number(response?.payload?.revision ?? payload?.backgroundUsageRevision ?? 0),
        );
        backgroundUsageState.selectedEventId = String(
          response?.payload?.selectedEventId || backgroundUsageState.selectedEventId || "",
        );
        backgroundUsageState.detail = backgroundUsageSelectedDetail(
          response.payload,
          backgroundUsageState.selectedEventId,
        );
        backgroundUsageState.error = responseError;
        syncBackgroundUsagePanel();
        if (!responseError && backgroundUsageState.selectedEventId) {
          void loadBackgroundUsageDetail(backgroundUsageState.selectedEventId);
        }
        return;
      }
      if (kind === "detail") {
        if (requestId !== backgroundUsageState.detailRequestId) return;
        if (String(response.eventId || "") !== backgroundUsageState.selectedEventId) return;
        clearBackgroundUsageRequestTimeout("detail");
        backgroundUsageState.detailLoading = false;
        backgroundUsageState.detail = response.payload || null;
        if (backgroundUsageState.detail?.unread === false) {
          markBackgroundUsageEventViewed(response.eventId);
        }
        backgroundUsageState.error = responseError;
        syncBackgroundUsagePanel();
        return;
      }
      if (kind === "open") {
        clearBackgroundUsageRequestTimeout("query");
        clearBackgroundUsageRequestTimeout("detail");
        backgroundUsageFetchSeq += 1;
        backgroundUsageDetailSeq += 1;
        backgroundUsageState.queryRequestId = "";
        backgroundUsageState.detailRequestId = "";
        backgroundUsageState.range = normalizeBackgroundUsageRange(
          response?.payload?.range,
        );
        backgroundUsageState.feature = "";
        backgroundUsageState.model = "";
        backgroundUsageState.selectedSessionId = "";
        backgroundUsageState.loading = false;
        backgroundUsageState.detailLoading = false;
        backgroundUsageState.data = response.payload || null;
        backgroundUsageState.loadedRevision = Math.max(
          0,
          Number(response?.payload?.revision ?? payload?.backgroundUsageRevision ?? 0),
        );
        backgroundUsageState.selectedEventId = String(
          response?.payload?.selectedEventId
          || response.eventId
          || openEventId
          || "",
        );
        backgroundUsageState.detail = backgroundUsageSelectedDetail(
          response.payload,
          backgroundUsageState.selectedEventId,
        );
        backgroundUsageState.promptExpanded = false;
        backgroundUsageState.error = responseError;
        const hasPreview = !!backgroundUsageState.detail;
        renderSettingsModal("backgroundUsage");
        // Auto-located open path: mark the jumped-to event as viewed.
        if (hasPreview && backgroundUsageState.selectedEventId) {
          void loadBackgroundUsageDetail(
            backgroundUsageState.selectedEventId,
            { markViewed: true },
          );
        } else if (backgroundUsageState.selectedEventId) {
          markBackgroundUsageEventViewed(backgroundUsageState.selectedEventId);
          syncBackgroundUsagePanel();
        }
        return;
      }
    }
    if (openEventId) {
      clearBackgroundUsageRequestTimeout("query");
      clearBackgroundUsageRequestTimeout("detail");
      const notification = payload?.backgroundUsageNotification;
      backgroundUsageState.range = normalizeBackgroundUsageRange(
        String(notification?.eventId || "") === openEventId
          ? notification?.range
          : "today",
      );
      backgroundUsageState.feature = "";
      backgroundUsageState.model = "";
      backgroundUsageState.selectedSessionId = "";
      backgroundUsageState.selectedEventId = openEventId;
      backgroundUsageState.data = null;
      backgroundUsageState.detail = null;
      backgroundUsageState.loadedRevision = -1;
      backgroundUsageState.promptExpanded = false;
      // Fallback open path (no correlated open response yet): still mark viewed.
      markBackgroundUsageEventViewed(openEventId);
      renderSettingsModal("backgroundUsage");
      return;
    }
    const modal = document.getElementById(settingsModalId);
    if (!modal || modal.hidden || settingsActiveTab !== "backgroundUsage") return;
    const revision = Math.max(0, Number(payload?.backgroundUsageRevision || 0));
    if (!backgroundUsageState.data || backgroundUsageState.loadedRevision !== revision) {
      void loadBackgroundUsage({ force: true });
    }
  }

  function applyFileManagementPayload(_root, payload) {
    const modal = document.getElementById(settingsModalId);
    if (!modal || modal.hidden || settingsActiveTab !== "storage") return;
    if (String(payload?.fileManagement?.operation?.state || "") === "preview") storagePreviewHidden = false;
    else storagePreviewHidden = true;
    const scrollTop = modal.querySelector(".codex-usage-hud-settings-body")?.scrollTop || 0;
    renderSettingsModal("storage");
    const body = modal.querySelector(".codex-usage-hud-settings-body");
    if (body) body.scrollTop = scrollTop;
  }

  function ensureSafeCleanupLiveTicker() {
    const data = safeCleanupFromPayload();
    const live = isSafeCleanupScanning(data) || isSafeCleanupExecuting(data);
    if (!live) {
      if (safeCleanupLiveTimer) {
        clearInterval(safeCleanupLiveTimer);
        safeCleanupLiveTimer = 0;
      }
      return;
    }
    if (safeCleanupLiveTimer) return;
    safeCleanupLiveTimer = setInterval(() => {
      const current = safeCleanupFromPayload();
      if (!isSafeCleanupScanning(current) && !isSafeCleanupExecuting(current)) {
        clearInterval(safeCleanupLiveTimer);
        safeCleanupLiveTimer = 0;
        return;
      }
      if (settingsActiveTab === "storage") {
        rerenderUsageInsightsIfVisible();
      }
    }, 1000);
  }

  function rerenderUsageInsightsIfVisible() {
    const modal = document.getElementById(settingsModalId);
    if (!modal || modal.hidden) return;
    if (settingsActiveTab === "storage") {
      const scrollTop = modal.querySelector(".codex-usage-hud-settings-body")?.scrollTop || 0;
      renderSettingsModal("storage");
      const body = modal.querySelector(".codex-usage-hud-settings-body");
      if (body) body.scrollTop = scrollTop;
      return;
    }
    if (
      settingsActiveTab === "backgroundUsage"
      && backgroundUsageSessionRankingMode()
    ) {
      syncBackgroundUsagePanel();
    }
  }

  function applyUsageInsightsPayload(_root, payload) {
    const data = payload?.usageInsights;
    if (!data || typeof data !== "object") return;
    usageInsightsState.data = data;
    const state = String(data.state || "").toLowerCase();
    const responseRequestId = String(data.requestId || "");
    if (
      state !== "loading"
      && (!usageInsightsState.refreshRequestId || responseRequestId === usageInsightsState.refreshRequestId)
    ) {
      usageInsightsState.refreshRequestId = "";
    }
    usageInsightsState.error = String(data.error || "");
    rerenderUsageInsightsIfVisible();
  }

  function applySafeCleanupPayload(_root, payload) {
    const data = payload?.safeCleanup;
    if (!data || typeof data !== "object") return;
    const commandStatus = payload?.settingsCommandStatus && typeof payload.settingsCommandStatus === "object"
      ? payload.settingsCommandStatus
      : {};
    const pickerRequestId = String(commandStatus?.safeCleanupRequestId || "");
    const pickerDirectory = String(commandStatus?.cleanupBackupDirectory || "").trim();
    const pickerChanged = !!pickerRequestId
      && !!pickerDirectory
      && pickerRequestId !== safeCleanupState.lastBackupPickerRequestId;
    if (pickerRequestId) safeCleanupState.lastBackupPickerRequestId = pickerRequestId;
    safeCleanupState.data = data;
    syncSafeCleanupSelection(data);
    syncSafeCleanupDefaults(data);
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const state = String(operation.state || "").toLowerCase();
    const action = String(operation.action || "");
    const responseRequestId = String(operation.requestId || "");
    const revision = String(data?.revision || "");
    if (revision && !isCleanupScanningRevision(revision) && new Set(["preview", "completed", "partial", "restored"]).has(state)) {
      safeCleanupState.stableData = data;
      safeCleanupState.scanStartedAt = 0;
    } else if (state === "failed" || state === "cancelled") {
      safeCleanupState.scanStartedAt = 0;
    }
    if (state === "preview") {
      if (operation?.requiresBackup === true && safeCleanupState.previewBackupDirectory) {
        safeCleanupState.backupDirectory = safeCleanupState.previewBackupDirectory;
      }
    } else if (
      new Set(["scan", "cancel", "execute", "safeCleanupScan", "safeCleanupCancel", "safeCleanupExecute"]).has(action)
      || (action === "safeCleanupPreview" && state === "failed")
    ) {
      safeCleanupState.previewBackupDirectory = "";
    }
    if (
      !new Set(["scanning", "accepted", "running", "queued_exit"]).has(state)
      && (!safeCleanupState.pendingRequestId || responseRequestId === safeCleanupState.pendingRequestId)
    ) {
      safeCleanupState.pendingRequestId = "";
      if (new Set(["completed", "partial", "failed", "cancelled", "restored", "preview"]).has(state)) {
        safeCleanupState.executeStartedAt = 0;
      }
    } else if (new Set(["accepted", "running", "queued_exit"]).has(state)
      && new Set(["execute", "safeCleanupExecute"]).has(action)
      && !safeCleanupState.executeStartedAt) {
      safeCleanupState.executeStartedAt = Date.now();
    }
    if (
      pickerChanged
      && safeCleanupState.includeConsent
      && safeCleanupRequiresBackup(data)
      && (state !== "preview" || safeCleanupState.previewHidden)
    ) {
      safeCleanupState.previewHidden = true;
      scheduleSafeCleanupPreview();
      return;
    }
    ensureSafeCleanupLiveTicker();
    rerenderUsageInsightsIfVisible();
  }

  function applySessionCleanupPayload(_root, payload) {
    const data = payload?.sessionCleanup;
    if (!data || typeof data !== "object") return;
    sessionCleanupState.data = data;
    const sessions = Array.isArray(data?.sessions) ? data.sessions : [];
    const validIds = new Set(sessions.filter((item) => item?.selectable === true).map((item) => String(item?.id || "")));
    sessionCleanupState.selectedIds = new Set(
      Array.from(sessionCleanupState.selectedIds).filter((id) => validIds.has(id)),
    );
    const operation = data?.operation && typeof data.operation === "object" ? data.operation : {};
    const state = String(operation?.state || "").toLowerCase();
    const responseRequestId = String(operation?.requestId || "");
    if (
      !new Set(["scanning", "accepted", "running"]).has(state)
      && (!sessionCleanupState.pendingRequestId || responseRequestId === sessionCleanupState.pendingRequestId)
    ) {
      sessionCleanupState.pendingRequestId = "";
    }
    rerenderUsageInsightsIfVisible();
    const token = String(operation?.confirmationToken || "");
    if (state === "preview" && token && token !== sessionCleanupState.previewTokenShown) {
      sessionCleanupState.previewTokenShown = token;
      restoreSessionCleanupConfirm(token);
    }
  }

  function applyPayloadDomains(root, payload, domains) {
    if ("currentSession" in domains) {
      applyCurrentSessionPayload(root, { ...(payload || {}), ...(domains.currentSession || {}) });
    }
    if ("sessionSwitch" in domains) {
      applySessionSwitchPayload(root, { ...(payload || {}), ...(domains.sessionSwitch || {}) });
    }
    if ("budget" in domains) {
      renderTopProgress(root, { ...(payload || {}), ...(domains.budget || {}) });
    }
    if ("settings" in domains) {
      applySettingsPayload(root, { ...(payload || {}), ...(domains.settings || {}) });
    }
    if ("overlay" in domains) {
      applyOverlayPayload(root, { ...(payload || {}), ...(domains.overlay || {}) });
    }
    if ("backgroundUsage" in domains) {
      applyBackgroundUsagePayload(root, { ...(payload || {}), ...(domains.backgroundUsage || {}) });
    }
    if ("diagnostics" in domains) {
      renderRuntimeErrors(root, { ...(payload || {}), ...(domains.diagnostics || {}) });
    }
    if ("fileManagement" in domains) {
      applyFileManagementPayload(root, { ...(payload || {}), ...(domains.fileManagement || {}) });
    }
    if ("usageInsights" in domains) {
      applyUsageInsightsPayload(root, { ...(payload || {}), ...(domains.usageInsights || {}) });
    }
    if ("safeCleanup" in domains) {
      applySafeCleanupPayload(root, { ...(payload || {}), ...(domains.safeCleanup || {}) });
    }
    if ("sessionCleanup" in domains) {
      applySessionCleanupPayload(root, { ...(payload || {}), ...(domains.sessionCleanup || {}) });
    }
  }

  window.__codexUsageHudUpdate = (payload) => {
    const previousState = window[stateName] || {};
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
    window[stateName] = {
      payload: nextPayload,
      domains: retainedDomains,
      hydrated: hudHydrated,
      updatedAt: Date.now(),
    };
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
    root?.remove();
    document.getElementById(styleId)?.remove();
    window.removeEventListener("resize", window[resizeHandlerName]);
    window.removeEventListener("scroll", window[scrollHandlerName], true);
    window[mutationObserverName]?.disconnect?.();
    window[resizeObserverName]?.disconnect?.();
    stopRendererThemeObserver();
    try {
      removeActiveSessionWatchers();
    } catch (_) {}
    detachComposerInputWatchers();
    stopBootstrapObserver();
    observedHeaderNode = null;
    observedComposerNode = null;
    cancelAnimationFrame(window[rafName] || 0);
    cancelAnimationFrame(window[composerBadgeRafName] || 0);
    clearInterval(window[runningTimerName] || 0);
    clearInterval(restReminderCountdownTimer);
    restReminderCountdownTimer = 0;
    clearTimeout(window[staleTimerName] || 0);
    clearTimeout(window[composerSettleTimerName] || 0);
    clearBackgroundUsageRequestTimeout("query");
    clearBackgroundUsageRequestTimeout("detail");
    for (const timer of (window[settleTimerName] || [])) clearTimeout(timer);
    for (const timer of (window[modelPickerPatchTimersName] || [])) clearTimeout(timer);
    cancelAnimationFrame(window[modelPickerPatchRafName] || 0);
    if (window[modelPickerPatchHandlerName]) {
      document.removeEventListener("pointerdown", window[modelPickerPatchHandlerName], true);
      document.removeEventListener("pointerover", window[modelPickerPatchHandlerName], true);
      document.removeEventListener("focusin", window[modelPickerPatchHandlerName], true);
      document.removeEventListener("keydown", window[modelPickerPatchHandlerName], true);
    }
    delete window[mutationObserverName];
    delete window[resizeObserverName];
    delete window[bootstrapObserverName];
    delete window[bootstrapTimerName];
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
  window[modelPickerPatchHandlerName] = scheduleCodexModelPickerPatch;
  window.addEventListener("resize", window[resizeHandlerName]);
  window.addEventListener("scroll", window[scrollHandlerName], true);
  document.addEventListener("pointerdown", window[modelPickerPatchHandlerName], true);
  document.addEventListener("pointerover", window[modelPickerPatchHandlerName], true);
  document.addEventListener("focusin", window[modelPickerPatchHandlerName], true);
  document.addEventListener("keydown", window[modelPickerPatchHandlerName], true);
  startRendererThemeObserver();
  const boot = () => {
    const state = window[stateName];
    if (state?.payload) {
      window.__codexUsageHudUpdate(state.payload);
    } else {
      // The new-document script can run before Python has a real session
      // payload. Do not create top/bottom panels here: a visible empty HUD is
      // both misleading and needlessly causes layout work while Codex loads.
      startBootstrapObserver();
    }
    scheduleCodexModelPickerPatch();
  };
  if (document.body) {
    boot();
  } else {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  }
})()
""".replace(
    "__COMPOSER_TIKTOKEN_BADGE_ENABLED__",
    "true" if COMPOSER_TIKTOKEN_BADGE_ENABLED else "false",
)


def _configured_model_catalog_path() -> Path | None:
    config_path = Path.home() / ".codex" / "config.toml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"(?m)^\s*model_catalog_json\s*=\s*(['\"])(.*?)\1", text)
    if not match:
        return None
    raw = match.group(2).strip()
    if not raw:
        return None
    return Path(raw.replace("\\\\", "\\"))


def _model_catalog_candidate_paths() -> list[Path]:
    paths: list[Path] = []
    env_path = os.environ.get(MODEL_CATALOG_JSON_ENV, "").strip()
    if env_path:
        paths.append(Path(env_path))
    configured = _configured_model_catalog_path()
    if configured is not None:
        paths.append(configured)
    catalog_dir = Path.home() / ".codex" / "model-catalogs"
    try:
        catalog_paths = sorted(
            catalog_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        catalog_paths = []
    paths.extend(catalog_paths)

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.expanduser()).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path.expanduser())
    return deduped


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
    return items


def _normalize_catalog_reasoning_levels(model: dict[str, object]) -> list[dict[str, str]]:
    raw_levels = model.get("supported_reasoning_levels")
    if raw_levels is None:
        raw_levels = model.get("supportedReasoningEfforts")
    if not isinstance(raw_levels, list):
        return []
    levels: list[dict[str, str]] = []
    for item in raw_levels:
        if not isinstance(item, dict):
            continue
        effort = str(item.get("effort") or item.get("reasoningEffort") or "").strip()
        if not effort:
            continue
        description = str(item.get("description") or effort).strip()
        levels.append({"reasoningEffort": effort, "description": description})
    return levels


def _normalize_catalog_model(model: object) -> dict[str, object] | None:
    if not isinstance(model, dict):
        return None
    visibility = str(model.get("visibility") or "list").strip().lower()
    if visibility not in {"", "list", "visible"}:
        return None
    slug = str(model.get("slug") or model.get("model") or model.get("id") or "").strip()
    if not slug:
        return None
    reasoning_efforts = _normalize_catalog_reasoning_levels(model)
    default_reasoning = str(
        model.get("default_reasoning_level")
        or model.get("defaultReasoningEffort")
        or (reasoning_efforts[0]["reasoningEffort"] if reasoning_efforts else "medium")
    ).strip()
    return {
        "model": slug,
        "displayName": str(model.get("display_name") or model.get("displayName") or slug).strip(),
        "description": str(model.get("description") or "").strip(),
        "defaultReasoningEffort": default_reasoning,
        "supportedReasoningEfforts": reasoning_efforts,
        "inputModalities": _as_string_list(model.get("input_modalities") or model.get("inputModalities")) or ["text"],
        "priority": int(model.get("priority") or 1000),
    }


def _renderer_model_catalog_payload() -> list[dict[str, object]]:
    models_by_slug: dict[str, dict[str, object]] = {}
    for path in _model_catalog_candidate_paths():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        raw_models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(raw_models, list):
            continue
        for raw_model in raw_models:
            model = _normalize_catalog_model(raw_model)
            if model is None:
                continue
            slug = str(model["model"])
            models_by_slug.setdefault(slug, model)
    models = sorted(
        models_by_slug.values(),
        key=lambda item: (int(item.get("priority") or 1000), str(item.get("model") or "")),
    )
    for model in models:
        model.pop("priority", None)
    return models


def _renderer_hud_script_with_model_catalog(
    catalog: list[dict[str, object]] | None = None,
) -> str:
    payload = _renderer_model_catalog_payload() if catalog is None else catalog
    return _RENDERER_HUD_SCRIPT_TEMPLATE.replace(
        "__CODEX_MODEL_PICKER_CATALOG__",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


RENDERER_HUD_SCRIPT = _renderer_hud_script_with_model_catalog([])


@dataclass(frozen=True)
class RendererHudPayload:
    top_line: str
    request_line: str
    session: str
    model: str
    source: str
    request_status: str
    last_event: str
    refreshed_at: str
    warning: bool = False
    new_session: bool = False
    pending_session: bool = False
    selection_seq: int = 0
    session_id: str = ""
    renderer_session_id: str = ""
    selection_observed_at_ms: int = 0
    follow_state: str = ""
    follow_reason: str = ""
    follow_elapsed_ms: int = 0
    follow_timing: dict[str, int] = field(default_factory=dict)
    top_details: dict[str, object] = field(default_factory=dict)
    top_progress: dict[str, object] = field(default_factory=dict)
    top_copies: dict[str, str] = field(default_factory=dict)
    request_rows: list[str] = field(default_factory=list)
    request_row_details: list[dict[str, object]] = field(default_factory=list)
    observed_models: list[str] = field(default_factory=list)
    settings: dict[str, object] = field(default_factory=dict)
    active_display_mode: str = "renderer"
    settings_path: str = ""
    settings_bridge_url: str = ""
    background_usage_bridge_url: str = ""
    background_usage_revision: int = 0
    background_usage_notification: dict[str, object] = field(default_factory=dict)
    rest_reminder: dict[str, object] = field(default_factory=dict)
    settings_command_status: dict[str, object] = field(default_factory=dict)
    file_management: dict[str, object] = field(default_factory=dict)
    usage_insights: dict[str, object] = field(default_factory=dict)
    safe_cleanup: dict[str, object] = field(default_factory=dict)
    session_cleanup: dict[str, object] = field(default_factory=dict)
    work_overlay_selectable_max: int = 6
    desktop_overlay_dependency: dict[str, object] = field(default_factory=dict)
    support_images: list[dict[str, str]] = field(default_factory=list)
    theme: dict[str, object] = field(default_factory=dict)
    update_state: dict[str, object] = field(default_factory=dict)
    app_version: str = __version__
    pre_send_estimate: str = ""
    pre_send_base_tokens: int = 0
    pre_send_breakdown: list[dict[str, object]] = field(default_factory=list)
    pre_send_input_price: float = 0.0
    pre_send_total_cost: float | None = None
    pre_send_has_prices: bool = False
    activity_warning: bool = False
    activity_reading_file: str = ""
    debug: bool = False
    runtime_errors: list[dict[str, object]] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "topLine": self.top_line,
            "requestLine": self.request_line,
            "session": self.session,
            "model": self.model,
            "source": self.source,
            "requestStatus": self.request_status,
            "lastEvent": self.last_event,
            "refreshedAt": self.refreshed_at,
            "warning": self.warning,
            "newSession": bool(self.new_session),
            "pendingSession": bool(self.pending_session),
            "selectionSeq": int(self.selection_seq),
            "sessionId": self.session_id,
            "rendererSessionId": self.renderer_session_id,
            "cachedPreview": False,
            "selectionObservedAt": int(self.selection_observed_at_ms),
            "followState": self.follow_state,
            "followReason": self.follow_reason,
            "followElapsedMs": int(self.follow_elapsed_ms),
            "followTiming": dict(self.follow_timing),
            "topDetails": dict(self.top_details),
            "topProgress": dict(self.top_progress),
            "topCopies": dict(self.top_copies),
            "requestRows": list(self.request_rows),
            "requestRowDetails": [dict(item) for item in self.request_row_details],
            "observedModels": list(self.observed_models),
            "settings": dict(self.settings),
            "activeDisplayMode": self.active_display_mode,
            "settingsPath": self.settings_path,
            "settingsBridgeUrl": self.settings_bridge_url,
            "backgroundUsageBridgeUrl": self.background_usage_bridge_url,
            "backgroundUsageRevision": int(self.background_usage_revision),
            "backgroundUsageNotification": dict(self.background_usage_notification),
            "restReminder": dict(self.rest_reminder),
            "settingsCommandStatus": dict(self.settings_command_status),
            "fileManagement": dict(self.file_management),
            "usageInsights": dict(self.usage_insights),
            "safeCleanup": dict(self.safe_cleanup),
            "sessionCleanup": dict(self.session_cleanup),
            "workOverlaySelectableMax": int(self.work_overlay_selectable_max),
            "desktopOverlayDependency": dict(self.desktop_overlay_dependency),
            "supportImages": [dict(item) for item in self.support_images],
            "theme": dict(self.theme),
            "updateState": dict(self.update_state),
            "appVersion": self.app_version,
            "preSendEstimate": self.pre_send_estimate,
            "preSendBaseTokens": int(self.pre_send_base_tokens),
            "preSendBreakdown": [dict(item) for item in self.pre_send_breakdown],
            "preSendInputPrice": float(self.pre_send_input_price),
            "preSendTotalCost": self.pre_send_total_cost,
            "preSendHasPrices": bool(self.pre_send_has_prices),
            "activityWarning": bool(self.activity_warning),
            "activityReadingFile": self.activity_reading_file,
            "debug": bool(self.debug),
            "runtimeErrors": [dict(item) for item in self.runtime_errors],
        }
        payload["payloadDomains"] = _payload_domains(payload)
        return payload

    def to_domain_json(self, *domain_names: str) -> dict[str, object]:
        payload = self.to_json()
        domains = payload.get("payloadDomains")
        if not isinstance(domains, dict):
            return payload
        selected: dict[str, dict[str, object]] = {}
        for name in domain_names:
            key = str(name or "").strip()
            value = domains.get(key)
            if isinstance(value, dict):
                selected[key] = dict(value)
        if not selected:
            return {}
        partial: dict[str, object] = {}
        for domain_payload in selected.values():
            partial.update(domain_payload)
        if partial.get("supportImages") == []:
            partial.pop("supportImages", None)
            settings_domain = selected.get("settings")
            if isinstance(settings_domain, dict):
                settings_domain.pop("supportImages", None)
        if partial.get("theme") == {}:
            partial.pop("theme", None)
            settings_domain = selected.get("settings")
            if isinstance(settings_domain, dict):
                settings_domain.pop("theme", None)
        partial["payloadDomains"] = selected
        return partial


def _payload_domains(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    session_switch_keys = (
        "topLine",
        "requestLine",
        "session",
        "model",
        "source",
        "requestStatus",
        "lastEvent",
        "refreshedAt",
        "warning",
        "newSession",
        "pendingSession",
        "selectionSeq",
        "sessionId",
        "rendererSessionId",
        "cachedPreview",
        "selectionObservedAt",
        "followState",
        "followReason",
        "followElapsedMs",
        "followTiming",
        "backgroundUsageNotification",
    )
    current_session_keys = (
        "topLine",
        "requestLine",
        "session",
        "model",
        "source",
        "requestStatus",
        "lastEvent",
        "refreshedAt",
        "warning",
        "newSession",
        "pendingSession",
        "selectionSeq",
        "sessionId",
        "rendererSessionId",
        "cachedPreview",
        "selectionObservedAt",
        "followState",
        "followReason",
        "followElapsedMs",
        "topDetails",
        "topCopies",
        "requestRows",
        "requestRowDetails",
        "observedModels",
        "preSendEstimate",
        "preSendBaseTokens",
        "preSendBreakdown",
        "preSendInputPrice",
        "preSendTotalCost",
        "preSendHasPrices",
        "activityWarning",
        "activityReadingFile",
        "backgroundUsageNotification",
    )
    budget_keys = ("topProgress",)
    settings_keys = (
        "settings",
        "activeDisplayMode",
        "settingsPath",
        "settingsBridgeUrl",
        "settingsCommandStatus",
        "restReminder",
        "supportImages",
        "theme",
        "updateState",
        "appVersion",
    )
    overlay_keys = ("workOverlaySelectableMax", "desktopOverlayDependency")
    background_usage_keys = (
        "backgroundUsageBridgeUrl",
        "backgroundUsageRevision",
        "backgroundUsageNotification",
        "settingsCommandStatus",
    )
    diagnostics_keys = ("debug", "runtimeErrors")
    file_management = payload.get("fileManagement")
    usage_insights = payload.get("usageInsights")
    safe_cleanup = payload.get("safeCleanup")
    session_cleanup = payload.get("sessionCleanup")

    def pick(keys: tuple[str, ...]) -> dict[str, object]:
        return {key: payload[key] for key in keys if key in payload}

    domains = {
        "currentSession": pick(current_session_keys),
        "sessionSwitch": pick(session_switch_keys),
        "budget": pick(budget_keys),
        "settings": pick(settings_keys),
        "overlay": pick(overlay_keys),
        "backgroundUsage": pick(background_usage_keys),
        "diagnostics": pick(diagnostics_keys),
    }
    if isinstance(file_management, dict) and file_management:
        domains["fileManagement"] = {"fileManagement": dict(file_management)}
    if isinstance(usage_insights, dict) and usage_insights:
        domains["usageInsights"] = {"usageInsights": dict(usage_insights)}
    if isinstance(safe_cleanup, dict) and safe_cleanup:
        domains["safeCleanup"] = {"safeCleanup": dict(safe_cleanup)}
    if isinstance(session_cleanup, dict) and session_cleanup:
        domains["sessionCleanup"] = {"sessionCleanup": dict(session_cleanup)}
    return domains


class _RendererTargetDiscovery:
    """Keep the selected CDP page target as subscribed runtime state."""

    def __init__(
        self,
        *,
        port: int,
        timeout_seconds: float,
    ) -> None:
        self.port = int(port)
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self._lock = threading.Lock()
        self._target: dict[str, Any] | None = None
        self._disconnected_reason = ""

    def target(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            disconnected_reason = self._disconnected_reason
            cached = dict(self._target or {}) if self._target is not None else None
        if disconnected_reason:
            raise RuntimeError(
                f"CDP target discovery disconnected: {disconnected_reason}"
            )
        if cached is not None and not force:
            return cached
        targets = list_targets(self.port, self.timeout_seconds)
        target = pick_page_target(targets)
        selected = dict(target)
        with self._lock:
            self._target = dict(selected)
        return selected

    def mark_disconnected(self, reason: object = "") -> None:
        text = str(reason or "").strip() or "CDP websocket closed"
        with self._lock:
            self._disconnected_reason = text

    def clear(self) -> None:
        with self._lock:
            self._target = None


class _RendererBinding:
    """Receive events from the renderer over a CDP ``Runtime.addBinding`` channel.

    The renderer page runs under a strict CSP whose ``connect-src`` does not
    allow ``http://127.0.0.1``, so in-page ``fetch``/XHR to the local settings
    bridge is blocked. A CDP binding is the reliable push channel: the page
    calls ``window[binding_name](json)`` and we receive it as
    ``Runtime.bindingCalled``. Used for both active-session and composer
    attachment events.
    """

    def __init__(
        self,
        binding_name: str,
        callback: Any,
        *,
        timeout_seconds: float,
        disconnect_callback: Any = None,
        retry_same_target: bool = False,
    ) -> None:
        self.binding_name = str(binding_name or "").strip()
        self.callback = callback
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.disconnect_callback = disconnect_callback
        self.retry_same_target = bool(retry_same_target)
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._websocket_url = ""
        self._target_id = ""
        self._disconnected_target_id = ""
        self._next_command_id = 100
        self._pending_responses: dict[
            int, tuple[threading.Event, dict[str, object]]
        ] = {}

    def ensure(self, websocket_url: str, target_id: str) -> None:
        """Start or restart the binding listener for the current page target.

        Subscription setup is intentionally asynchronous.  A renderer payload
        update must not serially wait for every optional CDP binding to finish
        its websocket handshake.  The active-session bootstrap is the sole
        caller that explicitly waits for its critical binding.
        """
        if not self.binding_name or not callable(self.callback) or not websocket_url:
            return
        with self._lock:
            thread = self._thread
            disconnected_target_id = self._disconnected_target_id
            if (
                thread is not None
                and thread.is_alive()
                and websocket_url == self._websocket_url
                and target_id == self._target_id
            ):
                return
            if (
                disconnected_target_id
                and disconnected_target_id == target_id
                and websocket_url == self._websocket_url
                and not self.retry_same_target
            ):
                # Do not turn an auxiliary binding disconnect into a retry
                # loop.  A real target transition supplies a new target id and
                # creates a fresh binding explicitly.
                return
        self.close(join_timeout=0.3)
        with self._lock:
            self._stop_event = threading.Event()
            self._ready_event = threading.Event()
            self._websocket_url = websocket_url
            self._target_id = target_id
            self._disconnected_target_id = ""
            self._thread = threading.Thread(
                target=self._run,
                args=(websocket_url,),
                name="codex-hud-active-session-cdp",
                daemon=True,
            )
            self._thread.start()

    def wait_ready(self, timeout_seconds: float | None = None) -> bool:
        """Wait for this binding only when its first event is correctness-critical."""
        with self._lock:
            ready_event = self._ready_event
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        return bool(ready_event.wait(max(0.0, float(timeout))))

    def send_command(
        self,
        websocket_url: str,
        method: str,
        params: dict[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]:
        """Send a CDP command over the already-subscribed binding socket."""
        response_ready = threading.Event()
        response: dict[str, object] = {}
        with self._lock:
            sock = self._sock
            if (
                sock is None
                or websocket_url != self._websocket_url
                or not self._ready_event.is_set()
            ):
                raise RuntimeError("renderer binding command channel is not ready")
            command_id = self._next_command_id
            self._next_command_id += 1
            self._pending_responses[command_id] = (response_ready, response)
        try:
            with self._send_lock:
                self._send_command(sock, command_id, method, params)
        except Exception:
            with self._lock:
                self._pending_responses.pop(command_id, None)
            raise
        if not response_ready.wait(max(0.05, float(timeout_seconds))):
            with self._lock:
                self._pending_responses.pop(command_id, None)
            raise TimeoutError("timed out waiting for persistent CDP response")
        with self._lock:
            self._pending_responses.pop(command_id, None)
        error = response.get("error")
        if error:
            raise RuntimeError(str(error))
        payload = response.get("payload")
        if not isinstance(payload, dict):
            raise RuntimeError("persistent CDP response was invalid")
        return payload

    def close(self, *, join_timeout: float = 1.0) -> None:
        self._stop_event.set()
        with self._lock:
            sock = self._sock
            thread = self._thread
            self._sock = None
            self._thread = None
            self._websocket_url = ""
            self._target_id = ""
            self._disconnected_target_id = ""
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=max(0.0, float(join_timeout)))

    def _run(self, websocket_url: str) -> None:
        sock: socket.socket | None = None
        disconnect_reason = ""
        try:
            sock = self._connect(websocket_url)
            with self._lock:
                if self._stop_event.is_set():
                    return
                self._sock = sock
            self._send_command(sock, 1, "Runtime.enable", {})
            self._send_command(
                sock,
                2,
                "Runtime.addBinding",
                {"name": self.binding_name},
            )
            pending = {1, 2}
            while not self._stop_event.is_set():
                try:
                    message = _receive_text_message(sock)
                except socket.timeout:
                    continue
                payload = json.loads(message)
                command_id = payload.get("id")
                if command_id in pending:
                    pending.remove(int(command_id))
                    if not pending:
                        self._ready_event.set()
                    continue
                try:
                    response_id = int(command_id)
                except (TypeError, ValueError):
                    response_id = 0
                with self._lock:
                    pending_response = self._pending_responses.get(response_id)
                if pending_response is not None:
                    response_ready, response = pending_response
                    response["payload"] = payload
                    response_ready.set()
                    continue
                if payload.get("method") != "Runtime.bindingCalled":
                    continue
                params = payload.get("params") or {}
                if str(params.get("name") or "") != self.binding_name:
                    continue
                self._handle_binding_payload(str(params.get("payload") or ""))
        except Exception as exc:
            disconnect_reason = f"{self.binding_name} binding closed: {type(exc).__name__}"
            self._ready_event.set()
            return
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            with self._lock:
                if self._sock is sock:
                    self._sock = None
                stopped = self._stop_event.is_set()
                if disconnect_reason and not stopped:
                    self._disconnected_target_id = self._target_id
                pending_responses = list(self._pending_responses.values())
            for response_ready, response in pending_responses:
                response["error"] = disconnect_reason or "renderer binding closed"
                response_ready.set()
            if (
                disconnect_reason
                and not stopped
                and callable(self.disconnect_callback)
            ):
                try:
                    self.disconnect_callback(disconnect_reason)
                except Exception:
                    pass

    def _connect(self, websocket_url: str) -> socket.socket:
        parsed = urlparse(websocket_url)
        if parsed.scheme != "ws":
            raise RuntimeError("Only local ws:// CDP endpoints are supported")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        sock = socket.create_connection((host, port), timeout=self.timeout_seconds)
        sock.settimeout(0.25)
        _websocket_handshake(sock, host, port, path)
        return sock

    @staticmethod
    def _send_command(
        sock: socket.socket,
        command_id: int,
        method: str,
        params: dict[str, object],
    ) -> None:
        _send_text_frame(
            sock,
            json.dumps(
                {"id": command_id, "method": method, "params": params},
                separators=(",", ":"),
            ),
        )

    def _handle_binding_payload(self, raw_payload: str) -> None:
        try:
            value = json.loads(raw_payload)
        except json.JSONDecodeError:
            return
        if not isinstance(value, dict):
            return
        try:
            self.callback(value)
        except Exception:
            return


class RendererHudClient:
    """Install and update the in-renderer HUD through a local CDP target."""

    def __init__(
        self,
        *,
        port: int | None = None,
        timeout_seconds: float = DEFAULT_RENDERER_TIMEOUT_SECONDS,
        target_cache_seconds: float = DEFAULT_RENDERER_TARGET_CACHE_SECONDS,
        enabled: bool | None = None,
    ) -> None:
        self.port = int(port or cdp_port_from_env())
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.target_cache_seconds = max(0.0, float(target_cache_seconds))
        self.enabled = renderer_enabled_from_env() if enabled is None else bool(enabled)
        self.last_status = "idle" if self.enabled else "disabled"
        self.last_error = ""
        self.last_update_metrics: dict[str, object] = {}
        self.last_bootstrap_metrics: dict[str, object] = {}
        self.last_attach_metrics: dict[str, object] = {}
        self._target_id = ""
        self._script_identifier = ""
        self._websocket_url = ""
        self._cached_target_id = ""
        self._cached_websocket_url = ""
        self._target_cache_at = 0.0
        self._support_images_sent = False
        self._target_discovery = _RendererTargetDiscovery(
            port=self.port,
            timeout_seconds=self.timeout_seconds,
        )
        self._active_session_binding: _RendererBinding | None = None
        self._active_session_callback: Any = None
        self._settings_command_binding: _RendererBinding | None = None
        self._attachments_binding: _RendererBinding | None = None
        self._layout_binding: _RendererBinding | None = None
        self._theme_binding: _RendererBinding | None = None
        self._theme_callback: Any = None
        self._theme_bootstrap_target_id = ""
        # Theme changes are pushed by the renderer binding.  Keep the last
        # snapshot here so a normal HUD refresh never has to synchronously
        # walk the Codex DOM again.  That probe can block the renderer for
        # hundreds of milliseconds while Codex is busy.
        self._theme_snapshot: CodexThemeSnapshot | None = None
        self._theme_probe = CodexThemeProbe(
            port=self.port,
            timeout_seconds=max(0.08, min(self.timeout_seconds, 0.25)),
            cache_seconds=max(0.35, self.target_cache_seconds),
            failure_cooldown_seconds=4.0,
        )

    def set_active_session_callback(self, callback: Any) -> None:
        """Receive renderer active-session events over CDP instead of HTTP fetch."""
        if self._active_session_binding is not None:
            self._active_session_binding.close()
            self._active_session_binding = None
        self._active_session_callback = callback if callable(callback) else None
        if callable(callback):
            self._active_session_binding = _RendererBinding(
                ACTIVE_SESSION_BINDING_NAME,
                callback,
                timeout_seconds=self.timeout_seconds,
                disconnect_callback=self._handle_active_session_binding_disconnect,
            )
            self._active_session_binding.retry_same_target = True

    def _handle_active_session_binding_disconnect(self, reason: str) -> None:
        callback = self._active_session_callback
        if not callable(callback):
            return
        try:
            callback(
                {
                    "channelUnavailable": True,
                    "reason": str(reason or "renderer binding disconnected"),
                    "observedAt": int(time.time() * 1000),
                }
            )
        except Exception:
            return

    def set_settings_command_callback(self, callback: Any) -> None:
        """Receive renderer settings commands over CDP instead of HTTP fetch."""
        if self._settings_command_binding is not None:
            self._settings_command_binding.close()
            self._settings_command_binding = None
        if callable(callback):
            self._settings_command_binding = _RendererBinding(
                SETTINGS_COMMAND_BINDING_NAME,
                callback,
                timeout_seconds=self.timeout_seconds,
            )

    def set_attachments_callback(self, callback: Any) -> None:
        """Receive renderer composer-attachment events over CDP instead of HTTP fetch.

        The page CSP blocks in-page fetch to the local bridge, so this binding
        is the reliable channel for delivering attachment token estimates.
        """
        if self._attachments_binding is not None:
            self._attachments_binding.close()
            self._attachments_binding = None
        if callable(callback):
            self._attachments_binding = _RendererBinding(
                COMPOSER_ATTACHMENTS_BINDING_NAME,
                callback,
                timeout_seconds=self.timeout_seconds,
            )

    def set_layout_callback(self, callback: Any) -> None:
        """Receive renderer HUD layout events (drag/resize/toggle) over CDP.

        The renderer JS reports panel geometry changes through a dedicated
        binding so the Python loop can emit ``renderer_layout_changed`` events
        without polling localStorage or waiting for the next refresh tick.
        """
        if self._layout_binding is not None:
            self._layout_binding.close()
            self._layout_binding = None
        if callable(callback):
            self._layout_binding = _RendererBinding(
                LAYOUT_BINDING_NAME,
                callback,
                timeout_seconds=self.timeout_seconds,
            )

    def set_theme_callback(self, callback: Any) -> None:
        """Receive live Codex renderer theme changes over CDP."""
        if self._theme_binding is not None:
            self._theme_binding.close()
            self._theme_binding = None
        self._theme_callback = callback if callable(callback) else None
        self._theme_bootstrap_target_id = ""
        if callable(callback):
            self._theme_binding = _RendererBinding(
                THEME_BINDING_NAME,
                self._handle_theme_binding_payload,
                timeout_seconds=self.timeout_seconds,
            )

    def _handle_theme_binding_payload(self, payload: dict[str, object]) -> None:
        callback = self._theme_callback
        if not callable(callback):
            return
        snapshot = CodexThemeSnapshot.from_probe_result(payload, source="cdp")
        if snapshot is None:
            return
        self._theme_snapshot = snapshot
        try:
            callback(_renderer_theme_payload(snapshot))
        except Exception:
            return

    def set_audit_callback(self, callback: Any) -> None:
        """Deprecated: request/response audit capture has been removed.

        Kept as a no-op so callers set up before wiring is torn down don't
        crash. Any callback passed here is discarded.
        """
        del callback

    def bootstrap_active_session(
        self,
        *,
        startup_payload: dict[str, object] | None = None,
    ) -> bool:
        """Install the renderer controller and ask it to report the selected session."""
        if not self.enabled:
            self.last_status = "disabled"
            return False
        started = time.perf_counter()
        stage = "target_discovery"
        try:
            target = self._page_target()
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            if target_id != self._target_id or not self._script_identifier:
                stage = "script_install"
                self._install(websocket_url, target_id)
            if self._active_session_binding is not None:
                stage = "active_session_binding"
                self._active_session_binding.ensure(websocket_url, target_id)
                wait_ready = getattr(self._active_session_binding, "wait_ready", None)
                if callable(wait_ready) and not wait_ready(self.timeout_seconds):
                    raise RuntimeError("renderer active-session binding was not ready")
            active_expression = (
                "typeof window.__codexUsageHudReportActiveSession === 'function' && "
                "window.__codexUsageHudReportActiveSession('bootstrap')"
            )
            stage = "active_session_report"
            expression = active_expression
            if startup_payload:
                startup_json = json.dumps(startup_payload, ensure_ascii=False)
                expression = (
                    "(() => {"
                    "const startup = typeof window.__codexUsageHudUpdate === 'function' && "
                    f"window.__codexUsageHudUpdate({startup_json});"
                    f"const active = {active_expression};"
                    "return { startup: !!startup, active: active || {} };"
                    "})()"
                )
            result = send_cdp_command(
                websocket_url,
                "Runtime.evaluate",
                _runtime_expression_params(expression),
                self.timeout_seconds,
            )
            value = result.get("result", {}).get("result", {}).get("value", False)
            active_value = value.get("active", False) if startup_payload and isinstance(value, dict) else value
            acknowledged = (
                True if isinstance(active_value, dict) else bool(active_value)
            )
            if not acknowledged:
                raise RuntimeError(
                    "renderer active session bootstrap did not acknowledge request"
                )
            self._deliver_bootstrap_active_session(active_value)
        except Exception as exc:
            self.last_bootstrap_metrics = {
                "totalMs": (time.perf_counter() - started) * 1000.0,
                "failureStage": stage,
                "startupBubble": bool(startup_payload),
            }
            self._clear_target_cache(clear_script=True)
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        self.last_bootstrap_metrics = {
            "totalMs": (time.perf_counter() - started) * 1000.0,
            "failureStage": "",
            "startupBubble": bool(startup_payload),
        }
        self.last_status = "ok"
        self.last_error = ""
        return True

    def _deliver_bootstrap_active_session(self, value: object) -> None:
        """Synchronously publish the page-active session returned by bootstrap."""
        callback = self._active_session_callback
        if not callable(callback) or not isinstance(value, dict):
            return
        payload = dict(value)
        if not (
            str(payload.get("sessionId") or payload.get("session_id") or "").strip()
            or str(payload.get("title") or "").strip()
            or bool(payload.get("newSession") or payload.get("new_session"))
            or bool(payload.get("pendingSession") or payload.get("pending_session"))
        ):
            return
        try:
            callback(payload)
        except Exception:
            return

    def update(
        self,
        snapshot: ParsedSession,
        *,
        settings: UserConfig | None = None,
        active_display_mode: str = "renderer",
        settings_path: Path | str | None = None,
        settings_bridge_url: str = "",
        background_usage_bridge_url: str = "",
        background_usage_revision: int = 0,
        background_usage_notification: dict[str, object] | None = None,
        rest_reminder: dict[str, object] | None = None,
        settings_command_status: dict[str, object] | None = None,
        update_state: dict[str, object] | None = None,
        debug: bool = False,
        runtime_errors: list[RuntimeErrorEvent | dict[str, object]] | None = None,
        work_overlay_selectable_max: int = 6,
        desktop_overlay_dependency: dict[str, object] | None = None,
        provider_registry: dict[str, object] | None = None,
        app_provider: str = "",
        file_management: dict[str, object] | None = None,
        usage_insights: dict[str, object] | None = None,
        safe_cleanup: dict[str, object] | None = None,
        session_cleanup: dict[str, object] | None = None,
    ) -> bool:
        started = time.perf_counter()
        support_images = [] if self._support_images_sent else support_qr_payload()
        theme_started = time.perf_counter()
        theme_snapshot = self._theme_snapshot
        if theme_snapshot is None:
            theme_snapshot = self._theme_probe.snapshot()
            self._theme_snapshot = theme_snapshot
        theme_probe_ms = (time.perf_counter() - theme_started) * 1000.0
        payload = payload_from_snapshot(
            snapshot,
            settings=settings,
            active_display_mode=active_display_mode,
            settings_path=settings_path,
            settings_bridge_url=settings_bridge_url,
            background_usage_bridge_url=background_usage_bridge_url,
            background_usage_revision=background_usage_revision,
            background_usage_notification=background_usage_notification,
            rest_reminder=rest_reminder,
            settings_command_status=settings_command_status,
            support_images=support_images,
            theme=_renderer_theme_payload(theme_snapshot),
            update_state=update_state,
            debug=debug,
            runtime_errors=runtime_errors,
            work_overlay_selectable_max=work_overlay_selectable_max,
            desktop_overlay_dependency=desktop_overlay_dependency,
            provider_registry=provider_registry,
            app_provider=app_provider,
            file_management=file_management,
            usage_insights=usage_insights,
            safe_cleanup=safe_cleanup,
            session_cleanup=session_cleanup,
        ).to_json()
        update_ok = self.update_payload(payload)
        metrics = dict(self.last_update_metrics)
        metrics.update(
            {
                "themeProbeMs": theme_probe_ms,
                "payloadBuildMs": (time.perf_counter() - theme_started) * 1000.0,
                "totalMs": (time.perf_counter() - started) * 1000.0,
            }
        )
        self.last_update_metrics = metrics
        if update_ok:
            if support_images:
                self._support_images_sent = True
            return True
        return False

    def show_startup(self, payload: dict[str, object]) -> bool:
        """Paint a startup-only payload before normal HUD domain updates begin."""
        return self.update_payload(payload)

    def update_payload(self, payload: dict[str, object]) -> bool:
        if not self.enabled:
            self.last_status = "disabled"
            return False
        started = time.perf_counter()
        stage = "target_discovery"
        try:
            target_started = time.perf_counter()
            target = self._page_target()
            target_discovery_ms = (time.perf_counter() - target_started) * 1000.0
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            if target_id != self._target_id or not self._script_identifier:
                stage = "script_install"
                self._install(websocket_url, target_id)
            if self._theme_binding is not None:
                stage = "theme_binding"
                self._theme_binding.ensure(websocket_url, target_id)
            stage = "payload_apply"
            if not self._send_update(websocket_url, payload):
                raise RuntimeError("renderer update function did not acknowledge payload")
            if self._active_session_binding is not None:
                stage = "active_session_binding"
                self._active_session_binding.ensure(websocket_url, target_id)
            if self._settings_command_binding is not None:
                stage = "settings_binding"
                self._settings_command_binding.ensure(websocket_url, target_id)
            if self._attachments_binding is not None:
                stage = "attachments_binding"
                self._attachments_binding.ensure(websocket_url, target_id)
            if self._layout_binding is not None:
                stage = "layout_binding"
                self._layout_binding.ensure(websocket_url, target_id)
            if self._theme_binding is not None and self._theme_bootstrap_target_id != target_id:
                try:
                    stage = "theme_bootstrap"
                    send_cdp_command(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(
                            "typeof window.__codexUsageHudReportTheme === 'function' "
                            "&& window.__codexUsageHudReportTheme('binding-ready')"
                        ),
                        self.timeout_seconds,
                    )
                    self._theme_bootstrap_target_id = target_id
                except Exception:
                    pass
        except Exception as exc:
            metrics = dict(self.last_update_metrics)
            metrics.update(
                {
                    "totalMs": (time.perf_counter() - started) * 1000.0,
                    "failureStage": stage,
                }
            )
            self.last_update_metrics = metrics
            self._clear_target_cache(clear_script=True)
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        metrics = dict(self.last_update_metrics)
        metrics.update(
            {
                "targetDiscoveryMs": target_discovery_ms,
                "totalMs": (time.perf_counter() - started) * 1000.0,
                "failureStage": "",
            }
        )
        self.last_update_metrics = metrics
        self.last_status = "ok"
        self.last_error = ""
        return True

    def close(self) -> None:
        if self._active_session_binding is not None:
            self._active_session_binding.close()
            self._active_session_binding = None
        self._active_session_callback = None
        if self._settings_command_binding is not None:
            self._settings_command_binding.close()
            self._settings_command_binding = None
        if self._attachments_binding is not None:
            self._attachments_binding.close()
            self._attachments_binding = None
        if self._layout_binding is not None:
            self._layout_binding.close()
            self._layout_binding = None
        if self._theme_binding is not None:
            self._theme_binding.close()
            self._theme_binding = None
        self._theme_callback = None
        self._theme_bootstrap_target_id = ""
        if not self.enabled:
            return
        try:
            for websocket_url in self._close_websocket_candidates():
                try:
                    send_cdp_command(
                        websocket_url,
                        "Runtime.evaluate",
                        _runtime_expression_params(REMOVE_RENDERER_HUD_SCRIPT),
                        self.timeout_seconds,
                    )
                except Exception:
                    pass
                if not self._script_identifier:
                    continue
                try:
                    remove_new_document_script(
                        websocket_url,
                        self._script_identifier,
                        self.timeout_seconds,
                    )
                except Exception:
                    pass
        except Exception:
            return
        finally:
            self._clear_target_cache(clear_script=True)

    def _close_websocket_candidates(self) -> list[str]:
        urls: list[str] = []
        for websocket_url in (self._websocket_url, self._cached_websocket_url):
            if websocket_url and websocket_url not in urls:
                urls.append(websocket_url)
        try:
            target = self._page_target(force=True)
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            if websocket_url and websocket_url not in urls:
                urls.append(websocket_url)
        except Exception:
            pass
        return urls

    def _page_target(self, *, force: bool = False) -> dict[str, Any]:
        target = self._target_discovery.target(force=force)
        self._cached_target_id = str(
            target.get("id") or target.get("webSocketDebuggerUrl") or ""
        )
        self._cached_websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        self._target_cache_at = time.monotonic()
        return target

    def _clear_target_cache(self, *, clear_script: bool) -> None:
        self._cached_target_id = ""
        self._cached_websocket_url = ""
        self._target_cache_at = 0.0
        self._target_discovery.clear()
        if clear_script:
            self._target_id = ""
            self._websocket_url = ""
            self._script_identifier = ""
            self._theme_bootstrap_target_id = ""
            self._theme_snapshot = None

    def _install(self, websocket_url: str, target_id: str, *, force: bool = False) -> None:
        if target_id != self._target_id:
            self._theme_snapshot = None
        if force and self._script_identifier:
            try:
                remove_new_document_script(
                    websocket_url,
                    self._script_identifier,
                    self.timeout_seconds,
                )
            except Exception:
                pass
        self._script_identifier = install_new_document_script(
            websocket_url,
            _renderer_hud_script_with_model_catalog(),
            self.timeout_seconds,
        )
        self._target_id = target_id
        self._websocket_url = websocket_url
        self._support_images_sent = False

    def _send_update(self, websocket_url: str, payload: dict[str, object]) -> bool:
        payload_json = json.dumps(payload, ensure_ascii=False)
        expression = (
            "(() => {"
            "const started = performance.now();"
            "const ok = typeof window.__codexUsageHudUpdate === 'function' && "
            f"window.__codexUsageHudUpdate({payload_json});"
            "return { ok: !!ok, applyMs: performance.now() - started };"
            "})()"
        )
        started = time.perf_counter()
        transport = "ephemeral"
        persistent_ms: float | None = None
        persistent_fallback_reason = ""
        fallback_ms: float | None = None
        send_persistent = getattr(self._active_session_binding, "send_command", None)
        if callable(send_persistent):
            persistent_started = time.perf_counter()
            try:
                result = send_persistent(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params(expression),
                    self.timeout_seconds,
                )
                persistent_ms = (time.perf_counter() - persistent_started) * 1000.0
                transport = "active-session-binding"
            except Exception as exc:
                persistent_ms = (time.perf_counter() - persistent_started) * 1000.0
                persistent_fallback_reason = f"{type(exc).__name__}: {exc}"
                fallback_started = time.perf_counter()
                result = send_cdp_command(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params(expression),
                    self.timeout_seconds,
                )
                fallback_ms = (time.perf_counter() - fallback_started) * 1000.0
        else:
            result = send_cdp_command(
                websocket_url,
                "Runtime.evaluate",
                _runtime_expression_params(expression),
                self.timeout_seconds,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        value = result.get("result", {}).get("result", {}).get("value", False)
        renderer_apply_ms: float | None = None
        ok: bool
        if isinstance(value, dict):
            ok = bool(value.get("ok", False))
            try:
                renderer_apply_ms = float(value.get("applyMs"))
            except (TypeError, ValueError):
                renderer_apply_ms = None
        else:
            ok = bool(value)
        domains_value = payload.get("payloadDomains")
        payload_domains = (
            sorted(str(key) for key in domains_value)
            if isinstance(domains_value, dict)
            else []
        )
        self.last_update_metrics = {
            "cdpMs": elapsed_ms,
            "rendererApplyMs": renderer_apply_ms,
            "payloadBytes": len(payload_json.encode("utf-8")),
            "payloadDomains": payload_domains,
            "transport": transport,
            "persistentMs": persistent_ms,
            "persistentFallbackReason": persistent_fallback_reason,
            "fallbackMs": fallback_ms,
            "attribution": (
                "hud_dom"
                if renderer_apply_ms is not None
                and renderer_apply_ms >= SLOW_RENDERER_UPDATE_LOG_MS
                else (
                    "codex_renderer_or_cdp"
                    if elapsed_ms >= SLOW_RENDERER_UPDATE_LOG_MS
                    else "normal"
                )
            ),
        }
        slow_session_switch = (
            "sessionSwitch" in payload_domains and elapsed_ms >= 150.0
        )
        if slow_session_switch or elapsed_ms >= SLOW_RENDERER_UPDATE_LOG_MS or (
            renderer_apply_ms is not None
            and renderer_apply_ms >= SLOW_RENDERER_UPDATE_LOG_MS
        ):
            _LOGGER.info(
                "renderer_update_timing attribution=%s transport=%s cdp_ms=%.1f persistent_ms=%s fallback_ms=%s fallback_reason=%s renderer_apply_ms=%s payload_bytes=%s domains=%s ok=%s",
                self.last_update_metrics["attribution"],
                transport,
                elapsed_ms,
                (
                    f"{persistent_ms:.1f}" if persistent_ms is not None else "-"
                ),
                f"{fallback_ms:.1f}" if fallback_ms is not None else "-",
                persistent_fallback_reason or "-",
                (
                    f"{renderer_apply_ms:.1f}"
                    if renderer_apply_ms is not None
                    else "-"
                ),
                self.last_update_metrics["payloadBytes"],
                ",".join(payload_domains),
                ok,
            )
        return ok


def remove_renderer_hud_from_pages(
    *,
    port: int | None = None,
    timeout_seconds: float = DEFAULT_RENDERER_TIMEOUT_SECONDS,
) -> int:
    """Best-effort cleanup for renderer HUD DOM left in any Codex page target."""
    removed = 0
    try:
        targets = list_targets(int(port or cdp_port_from_env()), timeout_seconds)
    except Exception:
        return 0
    for target in targets:
        if target.get("type") != "page":
            continue
        websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        if not websocket_url:
            continue
        try:
            result = send_cdp_command(
                websocket_url,
                "Runtime.evaluate",
                _runtime_expression_params(REMOVE_RENDERER_HUD_SCRIPT),
                timeout_seconds,
            )
        except Exception:
            continue
        if bool(
            result.get("result", {})
            .get("result", {})
            .get("value", False)
        ):
            removed += 1
    return removed


def renderer_enabled_from_env(default: bool = True) -> bool:
    value = os.environ.get(RENDERER_HUD_ENV)
    if value is None:
        return default
    normalized = value.strip().lower()
    if not normalized:
        return default
    return normalized not in {"0", "false", "no", "off"}


def payload_from_snapshot(
    snapshot: ParsedSession,
    *,
    settings: UserConfig | None = None,
    active_display_mode: str = "renderer",
    settings_path: Path | str | None = None,
    settings_bridge_url: str = "",
    background_usage_bridge_url: str = "",
    background_usage_revision: int = 0,
    background_usage_notification: dict[str, object] | None = None,
    rest_reminder: dict[str, object] | None = None,
    settings_command_status: dict[str, object] | None = None,
    support_images: list[dict[str, str]] | None = None,
    theme: dict[str, object] | None = None,
    update_state: dict[str, object] | None = None,
    debug: bool = False,
    runtime_errors: list[RuntimeErrorEvent | dict[str, object]] | None = None,
    work_overlay_selectable_max: int = 6,
    desktop_overlay_dependency: dict[str, object] | None = None,
    provider_registry: dict[str, object] | None = None,
    app_provider: str = "",
    file_management: dict[str, object] | None = None,
    usage_insights: dict[str, object] | None = None,
    safe_cleanup: dict[str, object] | None = None,
    session_cleanup: dict[str, object] | None = None,
) -> RendererHudPayload:
    new_session = _is_new_session_snapshot(snapshot)
    pending_session = _is_pending_session_snapshot(snapshot)
    session_cost = _session_cost(snapshot)
    warnings_dismissed = (
        warning_dismissed_today(settings_path) if settings_path is not None else False
    )
    top_details = _top_details(snapshot, session_cost)
    if warnings_dismissed:
        top_details["warnings"] = _format_notice(
            snapshot,
            include_budget_warnings=False,
        )
    top_line = (
        f"{_top_session_usage_summary(snapshot, session_cost)} | "
        f"今日 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)} | "
        f"本周 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)} | "
        f"状态 {_budget_status(snapshot)}"
    )
    if snapshot.error and snapshot.status in {"missing", "error"}:
        top_line = f"{_status_label(snapshot.status)} | {_compact(snapshot.error, 120)}"
    top_progress = _top_progress(snapshot)
    if snapshot.error and snapshot.status in {"missing", "error"}:
        top_progress = {
            "collapsed": [_top_progress_metric(top_line, 1.0, "error")],
            "budget": [],
        }
    request_line = _request_total_line(snapshot)
    if pending_session:
        request_line = _follow_feedback(snapshot)
    elif snapshot.follow_reason == "renderer-channel-unavailable":
        request_line = _follow_feedback(snapshot)
    if snapshot.request.error:
        request_line = f"本次 Token 出错 | {_compact(snapshot.request.error, 120)}"
    pre_send_estimate = ""
    pre_send_base_tokens = 0
    pre_send_breakdown: list[dict[str, object]] = []
    pre_send_input_price = 0.0
    pre_send_total_cost: float | None = None
    pre_send_has_prices = False
    activity_warning = False
    activity_reading_file = ""
    if COMPOSER_TIKTOKEN_BADGE_ENABLED:
        pre_send_estimate = snapshot.estimate_base.short_label()
        pre_send_base_tokens = int(snapshot.estimate_base.total_tokens or 0)
        pre_send_breakdown = snapshot.estimate_base.breakdown_rows()
        pre_send_input_price = float(snapshot.estimate_base.input_price_per_token or 0.0)
        pre_send_total_cost = snapshot.estimate_base.total_cost()
        pre_send_has_prices = bool(snapshot.estimate_base.has_prices)
        activity_warning = bool(snapshot.reading_activity.active)
        activity_reading_file = snapshot.reading_activity.warning_label()
    settings_payload = (settings or UserConfig.defaults()).to_dict()
    settings_payload["provider_registry"] = dict(provider_registry or {})
    settings_payload["app_provider"] = str(app_provider or "")
    return RendererHudPayload(
        top_line=top_line,
        request_line=request_line,
        session=_session_label(snapshot),
        model=snapshot.request.model or "n/a",
        source=snapshot.selection_source or "activity",
        request_status=snapshot.request.status or "waiting",
        last_event=_format_time(snapshot.last_event_time),
        refreshed_at=_format_time(snapshot.refreshed_at),
        new_session=new_session,
        pending_session=pending_session,
        selection_seq=int(snapshot.selection_seq or 0),
        session_id=str(snapshot.session_id or ""),
        renderer_session_id=str(snapshot.renderer_session_id or ""),
        selection_observed_at_ms=int(snapshot.selection_observed_at_ms or 0),
        follow_state=str(snapshot.follow_state or ""),
        follow_reason=str(snapshot.follow_reason or ""),
        follow_elapsed_ms=_follow_elapsed_ms(snapshot),
        follow_timing=dict(snapshot.follow_timing or {}),
        warning=bool(
            snapshot.error
            or snapshot.request.error
            or snapshot.budget_error
            or (snapshot.budget_warnings and not warnings_dismissed)
        ),
        top_details=top_details,
        top_progress=top_progress,
        top_copies=_top_copy_texts(snapshot),
        request_rows=_request_rows(snapshot),
        request_row_details=_request_row_details(snapshot),
        observed_models=_observed_models(snapshot),
        settings=settings_payload,
        active_display_mode=str(active_display_mode or "renderer"),
        settings_path=str(settings_path or ""),
        settings_bridge_url=settings_bridge_url,
        background_usage_bridge_url=background_usage_bridge_url,
        background_usage_revision=max(0, int(background_usage_revision or 0)),
        background_usage_notification=dict(background_usage_notification or {}),
        rest_reminder=dict(rest_reminder or {}),
        settings_command_status=settings_command_status or {},
        file_management=file_management or {},
        usage_insights=usage_insights or {},
        safe_cleanup=safe_cleanup or {},
        session_cleanup=session_cleanup or {},
        work_overlay_selectable_max=max(1, int(work_overlay_selectable_max or 1)),
        desktop_overlay_dependency=desktop_overlay_dependency or {},
        support_images=support_images or [],
        theme=theme or {},
        update_state=update_state or {},
        app_version=__version__,
        pre_send_estimate=pre_send_estimate,
        pre_send_base_tokens=pre_send_base_tokens,
        pre_send_breakdown=pre_send_breakdown,
        pre_send_input_price=pre_send_input_price,
        pre_send_total_cost=pre_send_total_cost,
        pre_send_has_prices=pre_send_has_prices,
        activity_warning=activity_warning,
        activity_reading_file=activity_reading_file,
        debug=bool(debug),
        runtime_errors=_runtime_errors_payload(runtime_errors or []),
    )


def session_switch_payload_from_snapshot(
    snapshot: ParsedSession,
    *,
    settings_path: Path | str | None = None,
    background_usage_notification: dict[str, object] | None = None,
) -> dict[str, object]:
    session_cost = _session_cost(snapshot)
    warnings_dismissed = (
        warning_dismissed_today(settings_path) if settings_path is not None else False
    )
    top_line = (
        f"{_top_session_usage_summary(snapshot, session_cost)} | "
        f"今日 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)} | "
        f"本周 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)} | "
        f"状态 {_budget_status(snapshot)}"
    )
    if snapshot.error and snapshot.status in {"missing", "error"}:
        top_line = f"{_status_label(snapshot.status)} | {_compact(snapshot.error, 120)}"
    request_line = _request_total_line(snapshot)
    if _is_pending_session_snapshot(snapshot):
        request_line = _follow_feedback(snapshot)
    elif snapshot.follow_reason == "renderer-channel-unavailable":
        request_line = _follow_feedback(snapshot)
    if snapshot.request.error:
        request_line = f"本次 Token 出错 | {_compact(snapshot.request.error, 120)}"
    domain = {
        "topLine": top_line,
        "requestLine": request_line,
        "session": _session_label(snapshot),
        "model": snapshot.request.model or "n/a",
        "source": snapshot.selection_source or "activity",
        "requestStatus": snapshot.request.status or "waiting",
        "lastEvent": _format_time(snapshot.last_event_time),
        "refreshedAt": _format_time(snapshot.refreshed_at),
        "warning": bool(
            snapshot.error
            or snapshot.request.error
            or snapshot.budget_error
            or (snapshot.budget_warnings and not warnings_dismissed)
        ),
        "newSession": bool(_is_new_session_snapshot(snapshot)),
        "pendingSession": bool(_is_pending_session_snapshot(snapshot)),
        "selectionSeq": int(snapshot.selection_seq or 0),
        "sessionId": str(snapshot.session_id or ""),
        "rendererSessionId": str(snapshot.renderer_session_id or ""),
        "cachedPreview": False,
        "selectionObservedAt": int(snapshot.selection_observed_at_ms or 0),
        "followState": str(snapshot.follow_state or ""),
        "followReason": str(snapshot.follow_reason or ""),
        "followElapsedMs": _follow_elapsed_ms(snapshot),
        "followTiming": dict(snapshot.follow_timing or {}),
        "backgroundUsageNotification": dict(background_usage_notification or {}),
    }
    payload = dict(domain)
    payload["payloadDomains"] = {"sessionSwitch": dict(domain)}
    return payload


def _runtime_errors_payload(
    errors: list[RuntimeErrorEvent | dict[str, object]],
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for error in errors:
        if isinstance(error, RuntimeErrorEvent):
            payload.append(error.to_payload())
        elif isinstance(error, dict):
            payload.append(dict(error))
    return payload


def _observed_models(snapshot: ParsedSession) -> list[str]:
    seen: set[str] = set()
    models: list[str] = []
    candidates = [snapshot.request.model]
    candidates.extend(item.model for item in _task_rows(snapshot))
    candidates.extend(
        item.model for item in getattr(snapshot, "session_request_history", []) or []
    )
    for model in candidates:
        text = str(model or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        models.append(text)
    return models


def wait_for_renderer(
    client: RendererHudClient,
    snapshot_factory: Any,
    *,
    timeout_seconds: float,
    progress_callback: Any = None,
) -> bool:
    """Attempt one renderer attach without startup polling or retrying.

    ``timeout_seconds`` is retained in the call shape for compatibility and
    diagnostics; the client owns the one bounded CDP command timeout.  A
    missing target is a strict, explicit unavailable state rather than a
    multi-second retry/relaunch sequence.
    """
    del timeout_seconds
    if callable(progress_callback):
        try:
            progress_callback("reading_session")
        except Exception:
            pass
    started = time.perf_counter()
    snapshot_started = time.perf_counter()
    snapshot = snapshot_factory()
    snapshot_ms = (time.perf_counter() - snapshot_started) * 1000.0
    if callable(progress_callback):
        try:
            progress_callback("showing_hud")
        except Exception:
            pass
    update_started = time.perf_counter()
    attached = bool(client.update(snapshot))
    update_ms = (time.perf_counter() - update_started) * 1000.0
    metrics = {
        "totalMs": (time.perf_counter() - started) * 1000.0,
        "snapshotBuildMs": snapshot_ms,
        "hudUpdateMs": update_ms,
        "update": dict(getattr(client, "last_update_metrics", {}) or {}),
    }
    client.last_attach_metrics = metrics
    if metrics["totalMs"] >= SLOW_RENDERER_UPDATE_LOG_MS:
        update_metrics = metrics["update"]
        attribution = (
            "python_snapshot"
            if snapshot_ms >= update_ms
            else str(update_metrics.get("attribution") or "hud_or_cdp")
        )
        _LOGGER.info(
            "renderer_attach_timing attribution=%s total_ms=%.1f snapshot_ms=%.1f hud_update_ms=%.1f cdp_ms=%s renderer_apply_ms=%s",
            attribution,
            metrics["totalMs"],
            snapshot_ms,
            update_ms,
            update_metrics.get("cdpMs", "-"),
            update_metrics.get("rendererApplyMs", "-"),
        )
    return attached


def _runtime_expression_params(expression: str) -> dict[str, object]:
    return {
        "expression": expression,
        "returnByValue": True,
        "allowUnsafeEvalBlockedByCSP": True,
    }


def _session_label(snapshot: ParsedSession) -> str:
    if _is_new_session_snapshot(snapshot):
        return "新会话"
    if _is_pending_session_snapshot(snapshot):
        return "会话加载中"
    title = _compact(snapshot.session_title, 36)
    if title:
        return title
    session_id = str(snapshot.session_id or "n/a")
    return session_id[-12:] if len(session_id) > 12 else session_id


def _follow_elapsed_ms(snapshot: ParsedSession) -> int:
    observed_at_ms = int(snapshot.selection_observed_at_ms or 0)
    if observed_at_ms <= 0:
        return 0
    return max(0, int(time.time() * 1000) - observed_at_ms)


def _follow_feedback(snapshot: ParsedSession) -> str:
    reason = str(snapshot.follow_reason or "").strip()
    labels = {
        "awaiting-canonical-id": "会话切换中：等待 Codex 提供正式会话 ID",
        "awaiting-persistence": "会话切换中：等待 Codex 写入会话映射",
        "awaiting-exact-mapping": "会话切换中：正式 ID 已收到，等待本地映射",
        "ambiguous-persisted-identity": "会话切换暂停：存在同名历史会话，未自动匹配",
        "renderer-channel-unavailable": "会话切换暂停：renderer 事件通道不可用",
    }
    return labels.get(reason, "会话切换中：正在确认当前会话")


def _is_new_session_snapshot(snapshot: ParsedSession) -> bool:
    return is_new_session_source(str(snapshot.selection_source or ""))


def _is_pending_session_snapshot(snapshot: ParsedSession) -> bool:
    return is_pending_session_source(str(snapshot.selection_source or ""))


def _status_label(value: str) -> str:
    labels = {
        "starting": "启动中",
        "loading": "加载中",
        "waiting": "等待日志",
        "missing": "未找到",
        "error": "出错",
        "parsed": "实时",
        "live": "实时",
        "idle": "空闲",
        "stale": "历史",
    }
    return labels.get(value, value)


def _request_status_label(value: str) -> str:
    labels = {
        "waiting": "等待",
        "running": "运行中",
        "confirmed": "已确认",
        "disabled": "已关闭",
        "error": "出错",
    }
    return labels.get(value, value)


def _activity_label(value: str) -> str:
    labels = {
        "idle": "空闲",
        "user": "用户输入",
        "agent": "助手消息",
        "tool call": "调用工具",
        "tool output": "工具返回",
        "assistant": "助手输出",
        "confirmed": "Token确认",
    }
    return labels.get(value, value)


def _short_num(value: int | None) -> str:
    amount = int(value or 0)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1_000_000:
        return f"{sign}{amount / 1_000_000:.1f}M"
    if amount >= 10_000:
        return f"{sign}{amount / 1_000:.0f}k"
    return f"{sign}{amount:,}"


def _format_money(value: float | None) -> str:
    amount = max(0.0, float(value or 0.0))
    if amount < 0.01:
        return f"${amount:.4f}"
    if amount < 1:
        return f"${amount:.3f}"
    return f"${amount:.2f}"


def _format_realtime_money(value: float | None, estimated: bool) -> str:
    return f"{'~' if estimated else ''}{_format_money(value)}"


def _format_fixed_money(value: float | None, estimated: bool) -> str:
    amount = max(0.0, float(value or 0.0))
    marker = "~" if estimated else ""
    if amount < 1:
        return f"{marker}${amount:.3f}"
    if amount < 100:
        return f"{marker}${amount:.2f}"
    return f"{marker}${amount:.1f}"


def _fixed_token_total(value: int | None) -> str:
    return _short_num(value)


def _format_usage_money(tokens: int | None, cost: float | None) -> str:
    return f"{_short_num(tokens)}/{_format_money(cost)}"


def _format_time(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone().strftime("%m-%d %H:%M:%S")


def _format_start(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.astimezone().strftime("%m-%d %H:%M")


def _gap_label(value: str) -> str:
    labels = {
        "user_wait": "等用户",
        "tool_wait": "等工具",
        "model_or_idle": "模型思考",
        "model_startup": "模型启动",
        "other_gap": "执行等待",
    }
    return labels.get(value, value)


def _copyable_tool_command(snapshot: ParsedSession) -> str | None:
    call = snapshot.slow.slowest_tool_call
    if call is None:
        return None
    raw_args = (call.args or "").strip()
    if not raw_args:
        return None
    try:
        payload = json.loads(raw_args)
    except json.JSONDecodeError:
        return raw_args
    if isinstance(payload, dict):
        command = payload.get("command")
        if command:
            return str(command)
    return raw_args


def _copyable_gap_detail(snapshot: ParsedSession) -> str | None:
    detail = snapshot.slow.longest_gap_detail
    if detail is None:
        return None
    return "\n".join(
        [
            f"类型: {_gap_label(detail.category)}",
            f"时长: {detail.duration_seconds:.1f}s",
            f"开始事件: {detail.from_event}",
            f"结束事件: {detail.to_event}",
            f"行号: {detail.start_line} -> {detail.end_line}",
        ]
    )


def _top_copy_texts(snapshot: ParsedSession) -> dict[str, str]:
    copies: dict[str, str] = {}
    tool_command = _copyable_tool_command(snapshot)
    if tool_command:
        copies["slow"] = tool_command
    gap_detail = _copyable_gap_detail(snapshot)
    if gap_detail:
        copies["gap"] = gap_detail
    return copies


def _compact(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _top_expanded_header_title(snapshot: ParsedSession) -> str:
    if _is_new_session_snapshot(snapshot):
        return "新会话"
    if _is_pending_session_snapshot(snapshot):
        return "会话加载中"
    title = _compact(snapshot.session_title, 72)
    return title or TOP_EXPANDED_HEADER_FALLBACK


def _display_tokens(
    snapshot: ParsedSession,
) -> tuple[int | None, bool, int | None, bool, int | None, bool, int | None, bool]:
    request = snapshot.request
    input_tokens = request.input_tokens
    input_estimated = False
    if input_tokens is None and request.estimated:
        input_tokens = (
            snapshot.confirmed.last_input
            + snapshot.estimate.input_tokens
            + snapshot.estimate.tool_tokens
        )
        input_estimated = input_tokens > 0

    output_tokens = request.output_tokens
    output_estimated = request.estimated and output_tokens is not None
    reasoning_tokens = request.reasoning_tokens
    total_tokens = request.total_tokens
    total_estimated = request.estimated or input_estimated
    if input_tokens is not None and (request.estimated or not total_tokens):
        total_tokens = input_tokens + int(output_tokens or 0)
        total_estimated = True

    return (
        input_tokens,
        input_estimated,
        output_tokens,
        output_estimated,
        reasoning_tokens,
        False,
        total_tokens,
        total_estimated,
    )


def _display_cached_tokens(
    snapshot: ParsedSession,
    input_tokens: int | None,
    input_estimated: bool,
) -> tuple[int | None, bool]:
    cached_tokens = snapshot.request.cached_tokens
    cached_estimated = snapshot.request.estimated and cached_tokens is not None
    if cached_tokens is None and input_tokens is not None:
        cached_tokens = min(snapshot.confirmed.last_cached, int(input_tokens))
        cached_estimated = input_estimated or snapshot.request.estimated
    return cached_tokens, cached_estimated


def _format_rate_marker(value: float | None, estimated: bool) -> str:
    return f"◎{_format_rate_value(value, estimated)}"


def _format_rate_value(value: float | None, estimated: bool) -> str:
    if value is None:
        return "-"
    clamped = max(0.0, min(float(value), 1.0))
    return f"{'~' if estimated else ''}{clamped:.0%}"


def _session_cache_hit_rate(snapshot: ParsedSession) -> tuple[float | None, bool]:
    input_tokens = int(snapshot.confirmed.cumulative_input or 0)
    cached_tokens = int(snapshot.confirmed.cumulative_cached or 0)
    estimated = False
    if snapshot.request.status == "running" or input_tokens <= 0:
        (
            request_input_tokens,
            input_estimated,
            _output_tokens,
            _output_estimated,
            _reasoning_tokens,
            _reasoning_estimated,
            _total_tokens,
            _total_estimated,
        ) = _display_tokens(snapshot)
        request_cached_tokens, cached_estimated = _display_cached_tokens(
            snapshot,
            request_input_tokens,
            input_estimated,
        )
        if request_input_tokens is not None and int(request_input_tokens) > 0:
            request_input = int(request_input_tokens)
            request_cached = int(request_cached_tokens or 0)
            if snapshot.request.status == "running":
                input_tokens += request_input
                cached_tokens += request_cached
            else:
                input_tokens = request_input
                cached_tokens = request_cached
            estimated = input_estimated or cached_estimated or snapshot.request.estimated
    if input_tokens <= 0:
        return None, estimated
    cached_tokens = max(0, min(cached_tokens, input_tokens))
    return cached_tokens / max(1, input_tokens), estimated


def _session_cache_hit_rate_label(snapshot: ParsedSession) -> str:
    ratio, estimated = _session_cache_hit_rate(snapshot)
    return _format_rate_marker(ratio, estimated)


def _top_session_cache_hit_rate_label(snapshot: ParsedSession) -> str:
    label = _session_cache_hit_rate_label(snapshot)
    if label.startswith("◎"):
        return label[1:]
    return label


def _top_session_usage_summary(snapshot: ParsedSession, session_cost: float | None = None) -> str:
    if _is_new_session_snapshot(snapshot):
        return "新会话 等待首个会话事件"
    if _is_pending_session_snapshot(snapshot):
        return "本会话 加载精确会话映射"
    total_tokens = int(snapshot.confirmed.cumulative_total or 0)
    total_cost = _session_cost(snapshot) if session_cost is None else session_cost
    if snapshot.request.status == "running":
        (
            _input_tokens,
            _input_estimated,
            _output_tokens,
            _output_estimated,
            _reasoning_tokens,
            _reasoning_estimated,
            request_total_tokens,
            _total_estimated,
        ) = _display_tokens(snapshot)
        total_tokens += int(request_total_tokens or 0)
    return f"本会话 {_format_usage_money(total_tokens, total_cost)}/{_top_session_cache_hit_rate_label(snapshot)}"


def _top_cache_progress_label(snapshot: ParsedSession) -> str:
    label = _session_cache_hit_rate_label(snapshot)
    if label.startswith("◎"):
        label = label[1:]
    return f"缓存命中 {label}"


def _budget_progress_total_ratio(cost: float | None, limit: float | None) -> float:
    amount = max(0.0, float(cost or 0.0))
    budget = max(0.0, float(limit or 0.0))
    if budget <= 0.0:
        return 0.0
    return max(0.0, amount / budget)


def _budget_progress_ratio(cost: float | None, limit: float | None) -> float:
    return max(0.0, min(1.0, _budget_progress_total_ratio(cost, limit)))


def _budget_progress_total_text(cost: float | None, limit: float | None) -> str:
    total_ratio = _budget_progress_total_ratio(cost, limit)
    if total_ratio <= 0.0:
        return ""
    return f"{total_ratio:.0%}"


def _budget_progress_overflow_ratio(cost: float | None, limit: float | None) -> float:
    total_ratio = _budget_progress_total_ratio(cost, limit)
    return max(0.0, min(1.0, total_ratio - 1.0))


def _budget_progress_overflow_badge(cost: float | None, limit: float | None) -> str:
    total_ratio = _budget_progress_total_ratio(cost, limit)
    if total_ratio <= 1.0:
        return ""
    amount = max(0.0, float(cost or 0.0))
    budget = max(0.0, float(limit or 0.0))
    overflow_ratio = max(0.0, total_ratio - 1.0)
    overflow_cost = max(0.0, amount - budget)
    return f"+{overflow_ratio:.0%} / +{_format_money(overflow_cost)}"


def _budget_limit_text(limit: float | None) -> str:
    return f"总 {_format_money(limit)}"


def _top_progress_metric(
    label: str,
    ratio: float | None,
    tone: str,
    *,
    right_text: str = "",
    overflow_ratio: float | None = None,
    overflow_badge: str = "",
) -> dict[str, object]:
    metric: dict[str, object] = {
        "label": label,
        "ratio": max(0.0, min(1.0, float(ratio or 0.0))),
        "tone": tone,
    }
    if right_text:
        metric["rightText"] = right_text
    if overflow_ratio is not None and float(overflow_ratio) > 0.0:
        metric["overflowRatio"] = max(0.0, min(1.0, float(overflow_ratio)))
    if overflow_badge:
        metric["overflowBadge"] = overflow_badge
    return metric


def _top_progress(snapshot: ParsedSession) -> dict[str, object]:
    cache_ratio, _cache_estimated = _session_cache_hit_rate(snapshot)
    day_overflow = _budget_progress_overflow_ratio(
        snapshot.today_cost_usd,
        snapshot.daily_limit_usd,
    )
    week_overflow = _budget_progress_overflow_ratio(
        snapshot.week_cost_usd,
        snapshot.weekly_limit_usd,
    )
    session = _top_progress_metric(
        _top_session_usage_summary(snapshot),
        0.0,
        "session",
    )
    cache = _top_progress_metric(
        _top_cache_progress_label(snapshot),
        cache_ratio if cache_ratio is not None else 0.0,
        "cache",
    )
    day = _top_progress_metric(
        f"今日 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)}",
        _budget_progress_ratio(snapshot.today_cost_usd, snapshot.daily_limit_usd),
        "day",
        right_text=(
            _budget_progress_total_text(snapshot.today_cost_usd, snapshot.daily_limit_usd)
            if day_overflow > 0.0
            else _budget_limit_text(snapshot.daily_limit_usd)
        ),
        overflow_ratio=day_overflow,
    )
    week = _top_progress_metric(
        f"本周 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)}",
        _budget_progress_ratio(snapshot.week_cost_usd, snapshot.weekly_limit_usd),
        "week",
        right_text=(
            _budget_progress_total_text(snapshot.week_cost_usd, snapshot.weekly_limit_usd)
            if week_overflow > 0.0
            else _budget_limit_text(snapshot.weekly_limit_usd)
        ),
        overflow_ratio=week_overflow,
    )
    budget_day = _top_progress_metric(
        f"今日 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)}",
        _budget_progress_ratio(snapshot.today_cost_usd, snapshot.daily_limit_usd),
        "day",
        right_text="" if day_overflow > 0.0 else _budget_limit_text(snapshot.daily_limit_usd),
        overflow_ratio=day_overflow,
        overflow_badge=_budget_progress_overflow_badge(
            snapshot.today_cost_usd,
            snapshot.daily_limit_usd,
        ),
    )
    budget_week = _top_progress_metric(
        f"本周 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)}",
        _budget_progress_ratio(snapshot.week_cost_usd, snapshot.weekly_limit_usd),
        "week",
        right_text="" if week_overflow > 0.0 else _budget_limit_text(snapshot.weekly_limit_usd),
        overflow_ratio=week_overflow,
        overflow_badge=_budget_progress_overflow_badge(
            snapshot.week_cost_usd,
            snapshot.weekly_limit_usd,
        ),
    )
    return {
        "collapsed": [session, day, week],
        "cache": cache,
        "budget": [budget_day, budget_week],
    }


def _round_cache_hit_rate_value(item: RequestRound) -> str:
    input_tokens = item.input_tokens
    if input_tokens is None or int(input_tokens) <= 0:
        return _format_rate_value(None, item.estimated)
    cached_tokens = max(0, min(int(item.cached_tokens or 0), int(input_tokens)))
    return _format_rate_value(cached_tokens / max(1, int(input_tokens)), item.estimated)


def _request_cost(snapshot: ParsedSession) -> tuple[float | None, bool]:
    request = snapshot.request
    if request.cost_usd is not None and not request.estimated:
        return request.cost_usd, False
    input_tokens = request.input_tokens
    cached_tokens = request.cached_tokens
    cache_write_tokens = request.cache_write_tokens
    output_tokens = request.output_tokens or 0
    if input_tokens is None or request.estimated:
        input_tokens = max(
            int(input_tokens or 0),
            snapshot.confirmed.last_input
            + snapshot.estimate.input_tokens
            + snapshot.estimate.tool_tokens,
        )
        cached_tokens = min(snapshot.confirmed.last_cached, int(input_tokens or 0))
        cache_write_tokens = min(
            snapshot.confirmed.last_cache_write,
            max(0, int(input_tokens or 0) - int(cached_tokens or 0)),
        )
    cost = _COST_ESTIMATOR.calculate(
        request.model,
        input_tokens,
        cached_tokens,
        output_tokens,
        request.reasoning_tokens or 0,
        cache_write_tokens=cache_write_tokens or 0,
    )
    return cost, True


def _round_from_snapshot(snapshot: ParsedSession) -> RequestRound:
    (
        input_tokens,
        _input_estimated,
        output_tokens,
        _output_estimated,
        reasoning_tokens,
        _reasoning_estimated,
        total_tokens,
        total_estimated,
    ) = _display_tokens(snapshot)
    cost, cost_estimated = _request_cost(snapshot)
    return RequestRound(
        index=1,
        status=snapshot.request.status,
        model=snapshot.request.model,
        input_tokens=input_tokens,
        cached_tokens=(
            snapshot.request.cached_tokens
            if snapshot.request.cached_tokens is not None
            else min(snapshot.confirmed.last_cached, int(input_tokens or 0))
        ),
        cache_write_tokens=(
            snapshot.request.cache_write_tokens
            if snapshot.request.cache_write_tokens is not None
            else min(
                snapshot.confirmed.last_cache_write,
                max(
                    0,
                    int(input_tokens or 0)
                    - int(snapshot.request.cached_tokens or snapshot.confirmed.last_cached or 0),
                ),
            )
        ),
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        estimated=snapshot.request.estimated or total_estimated or cost_estimated,
        cost_usd=cost,
        started_at=snapshot.request.started_at,
        completed_at=snapshot.request.completed_at,
    )


def _task_rows(snapshot: ParsedSession) -> list[RequestRound]:
    if _is_new_session_snapshot(snapshot) or _is_pending_session_snapshot(snapshot):
        return []
    return snapshot.request_history or [_round_from_snapshot(snapshot)]


def _task_total(snapshot: ParsedSession) -> tuple[int, int, int, int, int, float | None, bool]:
    rows = _task_rows(snapshot)
    input_tokens = sum(int(item.input_tokens or 0) for item in rows)
    cached_tokens = sum(int(item.cached_tokens or 0) for item in rows)
    output_tokens = sum(int(item.output_tokens or 0) for item in rows)
    reasoning_tokens = sum(int(item.reasoning_tokens or 0) for item in rows)
    total_tokens = sum(int(item.total_tokens or 0) for item in rows)
    if not total_tokens:
        total_tokens = input_tokens + output_tokens
    cost = 0.0
    has_cost = False
    estimated = False
    for item in rows:
        item_cost = item.cost_usd
        item_estimated = item.estimated or item.status == "running"
        if item_cost is None:
            item_cost = _COST_ESTIMATOR.calculate(
                item.model or snapshot.request.model,
                item.input_tokens or 0,
                item.cached_tokens or 0,
                item.output_tokens or 0,
                item.reasoning_tokens or 0,
                cache_write_tokens=item.cache_write_tokens or 0,
            )
            item_estimated = True
        if item_cost is not None:
            cost += item_cost
            has_cost = True
        estimated = estimated or item_estimated
    return (
        input_tokens,
        cached_tokens,
        output_tokens,
        reasoning_tokens,
        total_tokens,
        cost if has_cost else None,
        estimated,
    )


def _session_cost(snapshot: ParsedSession) -> float | None:
    if snapshot.confirmed.cumulative_cost_usd is not None:
        return snapshot.confirmed.cumulative_cost_usd
    return _COST_ESTIMATOR.calculate(
        snapshot.request.model,
        snapshot.confirmed.cumulative_input,
        snapshot.confirmed.cumulative_cached,
        snapshot.confirmed.cumulative_output,
        snapshot.confirmed.cumulative_reasoning,
        cache_write_tokens=snapshot.confirmed.cumulative_cache_write,
    )


def _budget_status(snapshot: ParsedSession) -> str:
    if snapshot.budget_error:
        return "预算不可用"
    if snapshot.budget_warnings:
        tags: list[str] = []
        for warning in snapshot.budget_warnings:
            if warning.startswith("日") and "超过 " in warning:
                tags.append("日>" + warning.split("超过 ", 1)[1].split("%", 1)[0] + "%")
            elif warning.startswith("周") and "超过 " in warning:
                tags.append("周>" + warning.split("超过 ", 1)[1].split("%", 1)[0] + "%")
            else:
                tags.append("额度")
        return "提醒 " + "/".join(tags)
    return _status_label(snapshot.status)


def _request_counter(snapshot: ParsedSession) -> str:
    (
        input_tokens,
        input_estimated,
        output_tokens,
        output_estimated,
        reasoning_tokens,
        reasoning_estimated,
        total_tokens,
        total_estimated,
    ) = _display_tokens(snapshot)
    cost, cost_estimated = _request_cost(snapshot)
    cached_tokens, cached_estimated = _display_cached_tokens(
        snapshot,
        input_tokens,
        input_estimated,
    )
    return " ".join(
        [
            f"↑{'~' if input_estimated else ''}{_short_num(input_tokens)}",
            f"↻{'~' if cached_estimated else ''}{_short_num(cached_tokens)}",
            f"↓{'~' if output_estimated else ''}{_short_num(output_tokens)}",
            f"◇{'~' if reasoning_estimated else ''}{_short_num(reasoning_tokens)}",
            f"∑{'~' if total_estimated else ''}{_short_num(total_tokens)}",
            _format_realtime_money(cost, cost_estimated),
        ]
    )


def _request_total_line(snapshot: ParsedSession) -> str:
    (
        input_tokens,
        cached_tokens,
        output_tokens,
        reasoning_tokens,
        total_tokens,
        cost,
        estimated,
    ) = _task_total(snapshot)
    return " ".join(
        [
            _format_fixed_money(cost, estimated),
            f"↑{'~' if estimated else ''}{_short_num(input_tokens)}",
            _session_cache_hit_rate_label(snapshot),
            f"↓{'~' if estimated else ''}{_short_num(output_tokens)}",
            f"◇{'~' if estimated else ''}{_short_num(reasoning_tokens)}",
            f"↻{'~' if estimated else ''}{_short_num(cached_tokens)}",
            f"∑{_fixed_token_total(total_tokens)}",
        ]
    )


def _round_is_running(item: RequestRound) -> bool:
    return item.status == "running" and item.completed_at is None and item.started_at is not None


def _round_elapsed_text(
    started_at: datetime | None,
    *,
    now: datetime | None = None,
) -> str:
    if started_at is None:
        return "--:--:--"
    if started_at.tzinfo is None:
        current = (now or datetime.now()).replace(tzinfo=None)
    else:
        current = (now or datetime.now().astimezone()).astimezone(started_at.tzinfo)
    elapsed_seconds = max(0, int((current - started_at).total_seconds()))
    return f"{elapsed_seconds}s".rjust(8)


def _round_time_text(
    item: RequestRound,
    *,
    now: datetime | None = None,
) -> str:
    if _round_is_running(item):
        return _round_elapsed_text(item.started_at, now=now)
    time_source = item.completed_at or item.started_at
    return "--:--:--" if time_source is None else time_source.astimezone().strftime("%H:%M:%S")


def _round_time_iso(value: datetime | None) -> str:
    return "" if value is None else value.astimezone().isoformat()


def _round_entry(
    item: RequestRound,
    fallback_model: str,
    *,
    widths: "_RoundColumnWidths | None" = None,
    now: datetime | None = None,
) -> str:
    parts = _round_entry_parts(
        item,
        fallback_model,
        widths=widths,
        now=now,
    )
    return f"{parts['prefix']}{parts['time']}{parts['suffix']}"


def _round_entry_parts(
    item: RequestRound,
    fallback_model: str,
    *,
    widths: "_RoundColumnWidths | None" = None,
    now: datetime | None = None,
) -> dict[str, str]:
    cost = item.cost_usd
    estimated = item.estimated or cost is None
    if cost is None:
        cost = _COST_ESTIMATOR.calculate(
            item.model or fallback_model,
            item.input_tokens or 0,
            item.cached_tokens or 0,
            item.output_tokens or 0,
            item.reasoning_tokens or 0,
            cache_write_tokens=item.cache_write_tokens or 0,
        )
    time_text = _round_time_text(item, now=now)
    index_text = str(item.index)
    money_text = _format_fixed_money(cost, estimated)
    total_text = _fixed_token_total(item.total_tokens)
    input_text = _short_num(item.input_tokens)
    rate_text = _round_cache_hit_rate_value(item)
    output_text = _short_num(item.output_tokens)
    reasoning_text = _short_num(item.reasoning_tokens)
    cached_text = _short_num(item.cached_tokens)
    if widths is not None:
        index_text = index_text.rjust(widths.index)
        money_text = money_text.rjust(widths.money)
        total_text = total_text.rjust(widths.total)
        input_text = input_text.rjust(widths.input)
        rate_text = rate_text.rjust(widths.rate)
        output_text = output_text.rjust(widths.output)
        reasoning_text = reasoning_text.rjust(widths.reasoning)
        cached_text = cached_text.rjust(widths.cached)
    return {
        "prefix": f"#{index_text} {money_text} ",
        "time": time_text,
        "suffix": (
            f" ↑{input_text} ◎{rate_text} "
            f"↓{output_text} ◇{reasoning_text} "
            f"↻{cached_text} ∑{total_text}"
        ),
    }


class _RoundColumnWidths(NamedTuple):
    index: int = 1
    money: int = 1
    total: int = 1
    input: int = 1
    rate: int = 1
    output: int = 1
    reasoning: int = 1
    cached: int = 1


def _round_entry_widths(
    rows: list[RequestRound],
    fallback_model: str,
) -> _RoundColumnWidths:
    index_width = max((len(str(item.index)) for item in rows), default=1)
    money_width = 1
    total_width = 1
    input_width = 1
    rate_width = 1
    output_width = 1
    reasoning_width = 1
    cached_width = 1
    for item in rows:
        cost = item.cost_usd
        estimated = item.estimated or cost is None
        if cost is None:
            cost = _COST_ESTIMATOR.calculate(
                item.model or fallback_model,
                item.input_tokens or 0,
                item.cached_tokens or 0,
                item.output_tokens or 0,
                item.reasoning_tokens or 0,
                cache_write_tokens=item.cache_write_tokens or 0,
            )
        money_width = max(money_width, len(_format_fixed_money(cost, estimated)))
        total_width = max(total_width, len(_fixed_token_total(item.total_tokens)))
        input_width = max(input_width, len(_short_num(item.input_tokens)))
        rate_width = max(rate_width, len(_round_cache_hit_rate_value(item)))
        output_width = max(output_width, len(_short_num(item.output_tokens)))
        reasoning_width = max(reasoning_width, len(_short_num(item.reasoning_tokens)))
        cached_width = max(cached_width, len(_short_num(item.cached_tokens)))
    return _RoundColumnWidths(
        index=index_width,
        money=money_width,
        total=total_width,
        input=input_width,
        rate=rate_width,
        output=output_width,
        reasoning=reasoning_width,
        cached=cached_width,
    )


def _request_rows(snapshot: ParsedSession) -> list[str]:
    display_rows, widths = _display_request_rows(snapshot)
    return [
        _round_entry(
            item,
            snapshot.request.model,
            widths=widths,
        )
        for item in display_rows
    ]


def _display_request_rows(
    snapshot: ParsedSession,
) -> tuple[list[RequestRound], _RoundColumnWidths]:
    if _is_new_session_snapshot(snapshot) or _is_pending_session_snapshot(snapshot):
        return [], _RoundColumnWidths()
    rows = _task_rows(snapshot)[-30:]
    if not rows:
        rows = [_round_from_snapshot(snapshot)]
    display_rows = list(reversed(rows))
    widths = _round_entry_widths(
        display_rows,
        snapshot.request.model,
    )
    return display_rows, widths


def _request_row_details(snapshot: ParsedSession) -> list[dict[str, object]]:
    display_rows, widths = _display_request_rows(snapshot)
    details: list[dict[str, object]] = []
    for item in display_rows:
        parts = _round_entry_parts(
            item,
            snapshot.request.model,
            widths=widths,
        )
        details.append(
            {
                "text": f"{parts['prefix']}{parts['time']}{parts['suffix']}",
                "prefix": parts["prefix"],
                "time": parts["time"],
                "suffix": parts["suffix"],
                "running": _round_is_running(item),
                "startedAt": _round_time_iso(item.started_at),
                "completedAt": _round_time_iso(item.completed_at),
            }
        )
    return details


def _budget_warning_summary(
    snapshot: ParsedSession,
    *,
    include_budget_warnings: bool = True,
) -> str:
    if snapshot.budget_error:
        return snapshot.budget_error
    if not include_budget_warnings or not snapshot.budget_warnings:
        return ""
    messages: list[str] = []
    for warning in snapshot.budget_warnings:
        if warning.startswith("日额度已用") and "超过 " in warning and snapshot.daily_limit_usd > 0:
            threshold = warning.split("超过 ", 1)[1].split("%", 1)[0].strip()
            messages.append(
                f"日已用 {snapshot.today_cost_usd / snapshot.daily_limit_usd:.0%}，超过 {threshold}% 阈值"
            )
            continue
        if warning.startswith("周额度已用") and "超过 " in warning and snapshot.weekly_limit_usd > 0:
            threshold = warning.split("超过 ", 1)[1].split("%", 1)[0].strip()
            messages.append(
                f"周已用 {snapshot.week_cost_usd / snapshot.weekly_limit_usd:.0%}，超过 {threshold}% 阈值"
            )
            continue
        messages.append(warning)
    return "预警  " + "；".join(messages)


def _format_warnings(
    snapshot: ParsedSession,
    *,
    include_budget_warnings: bool = True,
) -> str:
    return _budget_warning_summary(
        snapshot,
        include_budget_warnings=include_budget_warnings,
    )


def _format_notice(
    snapshot: ParsedSession,
    *,
    include_budget_warnings: bool = True,
) -> str:
    parts: list[str] = []
    notice = _format_warnings(
        snapshot,
        include_budget_warnings=include_budget_warnings,
    )
    if notice:
        parts.append(notice)
    if snapshot.error:
        parts.append(f"错误 {_compact(snapshot.error, 80)}")
    if snapshot.request.error:
        parts.append(f"请求 {_compact(snapshot.request.error, 80)}")
    return "  |  ".join(parts)


def _format_slow_panel(snapshot: ParsedSession) -> str:
    return "\n".join(
        [
            f"最慢工具  {snapshot.slow.slowest_tool}",
            f"最慢等待  {snapshot.slow.slowest_user_wait}",
        ]
    )


def _current_gap_text(snapshot: ParsedSession) -> str:
    prefix = "进行中" if snapshot.slow.current_gap_active else "当前"
    return f"{prefix}  {snapshot.slow.current_gap}"


def _format_gap_panel(snapshot: ParsedSession) -> str:
    return (
        f"最长响应等待  {snapshot.slow.longest_gap}\n"
        f"{_current_gap_text(snapshot)}"
    )


def _token_value_text(value: int | None, estimated: bool = False) -> str:
    return f"{'~' if estimated else ''}{_short_num(value)}"


def _cache_percent_text(snapshot: ParsedSession) -> str:
    label = _top_session_cache_hit_rate_label(snapshot)
    return label if label != "-" else "--"


def _component_cost(
    snapshot: ParsedSession,
    *,
    input_tokens: int = 0,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    output_tokens: int = 0,
) -> float | None:
    return _COST_ESTIMATOR.calculate(
        snapshot.request.model,
        input_tokens,
        cached_tokens,
        output_tokens,
        0,
        cache_write_tokens=cache_write_tokens,
    )


def _top_session_composition(snapshot: ParsedSession) -> str:
    confirmed = snapshot.confirmed
    input_tokens = int(confirmed.cumulative_input or 0)
    cached_tokens = max(0, min(int(confirmed.cumulative_cached or 0), input_tokens))
    cache_write_tokens = max(
        0,
        min(
            int(confirmed.cumulative_cache_write or 0),
            input_tokens - cached_tokens,
        ),
    )
    uncached_tokens = max(0, input_tokens - cached_tokens - cache_write_tokens)
    output_tokens = int(confirmed.cumulative_output or 0)
    components = [
        (
            "↑↻",
            cached_tokens,
            _component_cost(snapshot, input_tokens=cached_tokens, cached_tokens=cached_tokens),
        ),
        (
            "↑+",
            cache_write_tokens,
            _component_cost(
                snapshot,
                input_tokens=cache_write_tokens,
                cache_write_tokens=cache_write_tokens,
            ),
        ),
        (
            "↑",
            uncached_tokens,
            _component_cost(snapshot, input_tokens=uncached_tokens),
        ),
        (
            "↓",
            output_tokens,
            _component_cost(snapshot, output_tokens=output_tokens),
        ),
    ]
    components = [item for item in components if item[1] > 0]
    if not components:
        return "暂无可分析的 token 构成"
    cost_components = [(label, cost) for label, _tokens, cost in components if cost is not None]
    if len(cost_components) == len(components):
        return " + ".join(
            f"{label} {_format_money(cost)}"
            for label, cost in cost_components
        )
    return " + ".join(f"{label} {_short_num(tokens)}" for label, tokens, _cost in components)


def _round_duration_text(item: RequestRound) -> str:
    if item.started_at is None:
        return "--"
    finish = item.completed_at
    if finish is None:
        return _round_elapsed_text(item.started_at).strip()
    return _duration_text(seconds_between(item.started_at, finish))


def _round_cost_value(item: RequestRound, fallback_model: str) -> tuple[float | None, bool]:
    cost = item.cost_usd
    estimated = item.estimated or item.status == "running"
    if cost is None:
        cost = _COST_ESTIMATOR.calculate(
            item.model or fallback_model,
            item.input_tokens or 0,
            item.cached_tokens or 0,
            item.output_tokens or 0,
            item.reasoning_tokens or 0,
            cache_write_tokens=item.cache_write_tokens or 0,
        )
        estimated = True
    return cost, estimated


def _session_round_rows(snapshot: ParsedSession) -> list[RequestRound]:
    if _is_new_session_snapshot(snapshot) or _is_pending_session_snapshot(snapshot):
        return []
    rows = list(getattr(snapshot, "session_request_history", []) or [])
    if rows:
        return rows
    return _task_rows(snapshot)


def _top_heavy_rounds(snapshot: ParsedSession) -> list[dict[str, object]]:
    rows = [
        item
        for item in _session_round_rows(snapshot)
        if item.status != "waiting"
        or item.total_tokens
        or item.input_tokens
        or item.output_tokens
        or item.reasoning_tokens
        or item.cost_usd
    ]
    ranked: list[tuple[float, int, RequestRound, float | None, bool]] = []
    for item in rows:
        cost, estimated = _round_cost_value(item, snapshot.request.model)
        total = int(item.total_tokens or 0)
        if total <= 0:
            total = int(item.input_tokens or 0) + int(item.output_tokens or 0)
        ranked.append((float(cost if cost is not None else -1.0), total, item, cost, estimated))
    ranked.sort(key=lambda value: (value[0], value[1]), reverse=True)

    details: list[dict[str, str]] = []
    for _score_cost, total, item, cost, estimated in ranked[:3]:
        duration = _round_duration_text(item)
        breakdown = (
            f"↑{_short_num(item.input_tokens)} "
            f"↻{_short_num(item.cached_tokens)} "
            f"↓{_short_num(item.output_tokens)} "
            f"◇{_short_num(item.reasoning_tokens)}"
        )
        title = f"#{item.index} {_format_fixed_money(cost, estimated)} · ∑{_short_num(total)}"
        detail = _compact(item.activity_summary or f"消耗构成：{breakdown}", 112)
        copy_text = item.copy_text or (
            f"轮次 #{item.index}\n"
            f"金额 {_format_fixed_money(cost, estimated)}\n"
            f"Tokens {total:,}\n"
            f"{breakdown}"
        )
        details.append(
            {
                "title": title,
                "detail": detail,
                "copyText": copy_text,
                "tooltip": (
                    f"轮次 #{item.index} · {duration}\n"
                    f"金额 {_format_fixed_money(cost, estimated)} · Tokens {total:,}\n"
                    f"{detail}"
                ),
            }
        )
    return details


def _top_task_ordinal(snapshot: ParsedSession) -> str:
    count = int(getattr(snapshot, "task_count", 0) or 0)
    index = int(getattr(snapshot, "task_index", 0) or 0)
    if count <= 0:
        return ""
    if _top_task_finished(snapshot):
        return f"共{count}次需求"
    if index > 0:
        return f"第{index}次需求"
    return ""


def _top_task_ordinal_parts(snapshot: ParsedSession) -> dict[str, str]:
    value = _top_task_ordinal(snapshot)
    if not value:
        return {
            "taskOrdinal": "",
            "taskOrdinalSession": "",
            "taskOrdinalActivity": "",
        }
    if _top_task_finished(snapshot):
        return {
            "taskOrdinal": value,
            "taskOrdinalSession": value,
            "taskOrdinalActivity": "",
        }
    return {
        "taskOrdinal": value,
        "taskOrdinalSession": "",
        "taskOrdinalActivity": value,
    }


def _top_session_parts(snapshot: ParsedSession) -> dict[str, str]:
    confirmed = snapshot.confirmed
    if snapshot.token_events > 0:
        average = confirmed.cumulative_total // max(1, snapshot.token_events)
        session_average = f"均值 {_short_num(average)} /轮"
    else:
        session_average = "均值 n/a"
    parts = {
        "sessionMix": _top_cache_progress_label(snapshot),
        "sessionAverage": session_average,
        "sessionComposition": _top_session_composition(snapshot),
        "heavyRoundsSummary": "Top 3",
        "heavyRounds": _top_heavy_rounds(snapshot),
        "sessionInputTokens": _token_value_text(confirmed.cumulative_input),
        "sessionCachedTokens": _token_value_text(confirmed.cumulative_cached),
        "sessionOutputTokens": _token_value_text(confirmed.cumulative_output),
        "sessionReasoningTokens": _token_value_text(confirmed.cumulative_reasoning),
    }
    parts.update(_top_task_ordinal_parts(snapshot))
    return parts


def _top_current_work_item(snapshot: ParsedSession) -> Any | None:
    for item in snapshot.active_work_items:
        if getattr(item, "current", False):
            return item
    return snapshot.active_work_items[0] if snapshot.active_work_items else None


def _top_task_finished(snapshot: ParsedSession) -> bool:
    return (
        (snapshot.task_completed_at is not None or snapshot.task_aborted_at is not None)
        and snapshot.request.status != "running"
        and not snapshot.slow.current_gap_active
    )


def _top_task_aborted(snapshot: ParsedSession) -> bool:
    return (
        snapshot.task_aborted_at is not None
        and (snapshot.task_completed_at is None or snapshot.task_aborted_at >= snapshot.task_completed_at)
    )


def _top_activity_state(snapshot: ParsedSession) -> str:
    if _top_task_finished(snapshot):
        return "已中止" if _top_task_aborted(snapshot) else "已完成"
    item = _top_current_work_item(snapshot)
    if item is not None:
        label = getattr(item, "status_label", "") or getattr(item, "status_text", "") or getattr(item, "status", "")
        if label:
            return _compact(label, 18)
    if snapshot.request.error or snapshot.error:
        return "异常"
    if snapshot.slow.current_gap_active:
        return "等待中"
    if snapshot.request.status == "running":
        return "请求中"
    activity = _activity_label(snapshot.activity.kind)
    if activity not in {"空闲", "Token确认"}:
        return activity
    return _request_status_label(snapshot.request.status or snapshot.status)


def _top_activity_main(snapshot: ParsedSession, *, limit: int = 118) -> str:
    activity = _activity_label(snapshot.activity.kind)
    detail = _compact(snapshot.activity.detail, limit)
    if not detail:
        detail = _request_status_label(snapshot.request.status or snapshot.status)
    return f"{activity}：{detail}"


def _top_executing_text(snapshot: ParsedSession) -> str:
    if _top_task_finished(snapshot):
        summary = _compact(snapshot.last_output.detail, 160)
        if summary:
            return summary
        if _top_task_aborted(snapshot):
            return "任务已中止"
        return _top_activity_main(snapshot)
    item = _top_current_work_item(snapshot)
    if item is not None:
        label = getattr(item, "status_label", "") or _top_activity_state(snapshot)
        detail = (
            getattr(item, "status_text", "")
            or getattr(item, "detail", "")
            or getattr(item, "last_text", "")
            or getattr(item, "progress", "")
        )
        if detail:
            return f"{label}：{_compact(detail, 108)}"
        return _compact(label, 108)
    return _top_activity_main(snapshot)


def _top_current_task(snapshot: ParsedSession) -> str:
    if _is_new_session_snapshot(snapshot):
        return "新会话"
    if _is_pending_session_snapshot(snapshot):
        return "等待 Codex 写入精确会话映射"
    prompt = _compact(getattr(snapshot, "task_prompt", ""), 180)
    if prompt:
        return prompt
    item = _top_current_work_item(snapshot)
    if item is not None:
        title = (
            getattr(item, "title", "")
            or getattr(item, "target_title", "")
            or getattr(item, "workdir_name", "")
        )
        if title:
            return _compact(title, 128)
    if snapshot.session_title:
        return _compact(snapshot.session_title, 128)
    return f"会话 {snapshot.session_id[-12:]}"


def _top_activity_labels(snapshot: ParsedSession) -> dict[str, str]:
    if _top_task_finished(snapshot):
        return {
            "executingLabel": "任务中止" if _top_task_aborted(snapshot) else "完成任务",
            "currentTaskLabel": "当前需求",
            "activityElapsedLabel": "已处理",
            "activityGapLabel": "处理轮次",
            "activityLastLabel": "处理花费",
        }
    return {
        "executingLabel": "正在执行",
        "currentTaskLabel": "当前需求",
        "activityElapsedLabel": "已运行",
        "activityGapLabel": "当前等待",
        "activityLastLabel": "需求轮次",
    }


def _task_finished_at(snapshot: ParsedSession) -> datetime | None:
    if snapshot.task_aborted_at is not None:
        return snapshot.task_aborted_at
    return snapshot.task_completed_at


def _top_activity_elapsed(snapshot: ParsedSession) -> str:
    item = _top_current_work_item(snapshot)
    started_at = None
    if item is not None:
        started_at = (
            getattr(item, "started_at", None)
            or getattr(item, "task_started_at", None)
            or getattr(item, "session_started_at", None)
        )
    started_at = started_at or snapshot.request.started_at or snapshot.task_started_at or snapshot.session_started_at
    if _top_task_finished(snapshot):
        duration = _running_duration(started_at, _task_finished_at(snapshot), snapshot.refreshed_at)
        return _duration_text(duration)
    return _round_elapsed_text(started_at).strip()


def _task_round_count(snapshot: ParsedSession) -> int:
    rows = _task_rows(snapshot)
    count = 0
    for item in rows:
        if item.status == "waiting" and not (
            item.total_tokens
            or item.input_tokens
            or item.output_tokens
            or item.reasoning_tokens
            or item.cost_usd
        ):
            continue
        count += 1
    return count


def _task_cache_hit_rate_label(snapshot: ParsedSession, rows: list[RequestRound]) -> str:
    input_tokens = sum(int(item.input_tokens or 0) for item in rows)
    if input_tokens <= 0:
        return "--"
    cached_tokens = sum(int(item.cached_tokens or 0) for item in rows)
    cached_tokens = max(0, min(cached_tokens, input_tokens))
    estimated = any(item.estimated or item.status == "running" for item in rows)
    label = _format_rate_marker(cached_tokens / max(1, input_tokens), estimated)
    return label[1:] if label.startswith("◎") else label


def _top_task_spend_text(snapshot: ParsedSession) -> str:
    rows = _task_rows(snapshot)
    (
        _input_tokens,
        _cached_tokens,
        _output_tokens,
        _reasoning_tokens,
        total_tokens,
        cost,
        estimated,
    ) = _task_total(snapshot)
    return (
        f"{_short_num(total_tokens)}Tokens/"
        f"{_format_fixed_money(cost, estimated)}/"
        f"{_task_cache_hit_rate_label(snapshot, rows)}"
    )


def _top_task_spend_money_text(snapshot: ParsedSession) -> str:
    (
        _input_tokens,
        _cached_tokens,
        _output_tokens,
        _reasoning_tokens,
        _total_tokens,
        cost,
        estimated,
    ) = _task_total(snapshot)
    return _format_fixed_money(cost, estimated)


def _top_activity_gap_value(snapshot: ParsedSession) -> str:
    if _top_task_finished(snapshot):
        return f"{_task_round_count(snapshot)}轮"
    return snapshot.slow.current_gap


def _top_activity_last(snapshot: ParsedSession) -> str:
    if _top_task_finished(snapshot):
        return _top_task_spend_money_text(snapshot)
    return f"{_task_round_count(snapshot)}轮"


def _top_activity_last_tooltip(snapshot: ParsedSession) -> str:
    if _top_task_finished(snapshot):
        return _top_task_spend_text(snapshot)
    return f"本次需求已产生 {_task_round_count(snapshot)} 轮"


def _duration_text(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    amount = max(0.0, float(seconds))
    if amount < 60:
        return f"{amount:.1f}s"
    minutes = int(amount // 60)
    seconds_left = int(amount % 60)
    if minutes < 60:
        return f"{minutes}m{seconds_left}s"
    hours = minutes // 60
    minutes_left = minutes % 60
    return f"{hours}h{minutes_left}m"


def _running_duration(start: datetime | None, end: datetime | None, now: datetime) -> float | None:
    if start is None:
        return None
    finish = end or now.astimezone(start.tzinfo) if start.tzinfo is not None else end or now.replace(tzinfo=None)
    return max(0.0, (finish - start).total_seconds())


def _first_duration_fragment(value: str) -> str:
    match = re.search(r"\d+(?:\.\d+)?s|\d+m\d+s|\d+h\d+m", value or "")
    return match.group(0) if match else "--"


def _top_slow_chip(snapshot: ParsedSession) -> str:
    call = snapshot.slow.slowest_tool_call
    if call is not None:
        duration = _duration_text(_running_duration(call.start, call.end, snapshot.refreshed_at))
        return _compact(f"最慢工具:{duration}", 28)
    if snapshot.slow.slowest_tool and not snapshot.slow.slowest_tool.startswith("无"):
        return _compact(f"最慢工具:{_first_duration_fragment(snapshot.slow.slowest_tool)}", 28)
    return "最慢工具:--"


def _top_gap_chip(snapshot: ParsedSession) -> str:
    detail = snapshot.slow.longest_gap_detail
    if detail is not None:
        return _compact(f"最长等待:{_duration_text(detail.duration_seconds)}", 28)
    if snapshot.slow.longest_gap and not snapshot.slow.longest_gap.startswith("无"):
        return _compact(f"最长等待:{_first_duration_fragment(snapshot.slow.longest_gap)}", 28)
    return "最长等待:--"


def _timeline_time(value: datetime | None) -> str:
    if value is None:
        return "--:--"
    return value.astimezone().strftime("%H:%M:%S")


def _tool_call_arguments_summary(call: ToolCallTiming) -> str:
    raw_args = (call.args or "").strip()
    if not raw_args:
        return ""
    try:
        payload = json.loads(raw_args)
    except json.JSONDecodeError:
        return _compact(raw_args, 96)
    if isinstance(payload, dict):
        for key in ("command", "query", "q", "url", "path"):
            value = payload.get(key)
            if value:
                return _compact(value, 96)
    return _compact(raw_args, 96)


def _tool_call_timeline_detail(call: ToolCallTiming, duration: str) -> str:
    args = _tool_call_arguments_summary(call)
    if args:
        return f"{duration} {call.name} · {args}"
    return f"{duration} {call.name}"


def _is_token_confirm_event(title: str, detail: str) -> bool:
    return title == "Token确认" or "received token_count" in detail


def _merge_activity_events(
    events: list[tuple[datetime, int, dict[str, object]]],
) -> list[dict[str, object]]:
    grouped: dict[str, list[tuple[datetime, int, dict[str, object]]]] = {}
    for event in events:
        moment = event[0]
        key = moment.astimezone().replace(microsecond=0).isoformat()
        grouped.setdefault(key, []).append(event)

    merged: list[tuple[datetime, int, dict[str, object], bool]] = []
    for group in grouped.values():
        group.sort(key=lambda item: item[1])
        moment = max(item[0] for item in group)
        order = max(item[1] for item in group)
        group_titles = [str(item[2].get("title") or "") for item in group]
        suppress_request_complete = "请求完成" in group_titles and any(
            title in group_titles for title in ("任务完成", "任务中止")
        )
        suppress_round_title = any(title in group_titles for title in ("任务完成", "任务中止"))
        meaningful_titles: list[str] = []
        token_titles: list[str] = []
        details: list[str] = []
        tooltip_lines: list[str] = []
        active = False
        has_meaningful = False
        for _moment, _order, item in group:
            title = str(item.get("title") or "")
            detail = str(item.get("detail") or "")
            token_confirm = _is_token_confirm_event(title, detail)
            if suppress_request_complete and title == "请求完成":
                active = active or bool(item.get("active"))
                continue
            if suppress_round_title and title.startswith("轮次 #"):
                active = active or bool(item.get("active"))
                continue
            title_bucket = token_titles if token_confirm else meaningful_titles
            if title and title not in title_bucket:
                title_bucket.append(title)
            has_meaningful = has_meaningful or not token_confirm
            if detail and not token_confirm and detail not in details:
                details.append(detail)
            tooltip = str(item.get("tooltip") or "").strip()
            if tooltip and not token_confirm and tooltip not in tooltip_lines:
                tooltip_lines.append(tooltip)
            active = active or bool(item.get("active"))

        titles = meaningful_titles + token_titles
        title_text = "，".join(titles) if titles else "活动"
        detail_text = "；".join(details)
        tooltip = "\n".join(tooltip_lines) if tooltip_lines else title_text
        merged.append(
            (
                moment,
                order,
                {
                    "time": _timeline_time(moment),
                    "title": _compact(title_text, 40),
                    "detail": _compact(detail_text, 96),
                    "tooltip": _compact(f"{_timeline_time(moment)}  {title_text}\n{tooltip}", 320),
                    "active": active,
                },
                has_meaningful,
            )
        )

    meaningful = [item for item in merged if item[3]]
    if meaningful:
        merged = meaningful
    merged.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in merged]


def _activity_round_detail(item: RequestRound, fallback_model: str) -> str:
    cost, estimated = _round_cost_value(item, fallback_model)
    total = int(item.total_tokens or 0)
    if total <= 0:
        total = int(item.input_tokens or 0) + int(item.output_tokens or 0)
    parts = [
        _format_fixed_money(cost, estimated),
        f"∑{_short_num(total)}",
    ]
    summary = _compact(item.activity_summary, 64)
    if summary:
        parts.append(summary)
    else:
        parts.append(
            " ".join(
                [
                    f"↑{_short_num(item.input_tokens)}",
                    f"↻{_short_num(item.cached_tokens)}",
                    f"↓{_short_num(item.output_tokens)}",
                    f"◇{_short_num(item.reasoning_tokens)}",
                ]
            )
        )
    return " · ".join(parts)


def _top_activity_trail(snapshot: ParsedSession) -> list[dict[str, object]]:
    now = snapshot.refreshed_at or datetime.now().astimezone()
    events: list[tuple[datetime, int, dict[str, object]]] = []
    seen: set[tuple[str, str, str]] = set()
    order = 0
    row_times = [
        moment
        for item in _task_rows(snapshot)
        for moment in (item.started_at, item.completed_at)
        if moment is not None
    ]
    task_start = snapshot.task_started_at or (min(row_times) if row_times else None)

    def add(
        moment: datetime | None,
        title: str,
        detail: str,
        *,
        active: bool = False,
        round_index: int = 0,
    ) -> None:
        nonlocal order
        if moment is None:
            return
        if task_start is not None:
            current = moment.astimezone(task_start.tzinfo) if task_start.tzinfo else moment.replace(tzinfo=None)
            start = task_start if task_start.tzinfo else task_start.replace(tzinfo=None)
            if current < start:
                return
        key = (moment.astimezone().isoformat(), title, detail)
        if key in seen:
            return
        seen.add(key)
        order += 1
        events.append(
            (
                moment,
                order,
                {
                    "time": _timeline_time(moment),
                    "title": _compact(title, 26),
                    "detail": _compact(detail, 72),
                    "tooltip": _compact(f"{_timeline_time(moment)}  {title}  {detail}", 260),
                    "active": active,
                },
            )
        )

    add(snapshot.task_started_at, "任务开始", _top_current_task(snapshot))
    for item in _task_rows(snapshot):
        moment = item.completed_at or item.started_at
        if moment is None:
            continue
        title = f"轮次 #{item.index}"
        active = item.status == "running" and item.completed_at is None
        add(
            moment,
            title,
            _activity_round_detail(item, snapshot.request.model),
            active=active,
            round_index=int(item.index or 0),
        )
    add(
        snapshot.request.started_at,
        "请求开始",
        snapshot.request.model or _request_status_label(snapshot.request.status),
        active=snapshot.request.status == "running" and snapshot.request.completed_at is None,
    )
    call = snapshot.slow.slowest_tool_call
    if call is not None:
        duration = _duration_text(_running_duration(call.start, call.end, now))
        add(
            call.start,
            "工具调用",
            _tool_call_timeline_detail(call, duration),
            active=call.end is None,
        )
        completion_detail = _tool_call_timeline_detail(call, duration)
        if call.output:
            completion_detail = f"{completion_detail} · 返回 {_compact(call.output, 80)}"
        add(call.end, "工具完成", completion_detail)
    gap = snapshot.slow.longest_gap_detail
    if gap is not None:
        label = _gap_label(gap.category)
        add(gap.start, "等待开始", f"{label}：{gap.from_event}")
        add(gap.end, "等待结束", f"{_duration_text(gap.duration_seconds)} {label}：{gap.to_event}")
    add(snapshot.activity.timestamp, _activity_label(snapshot.activity.kind), snapshot.activity.detail, active=True)
    add(snapshot.request.completed_at, "请求完成", snapshot.request.model or "模型请求")
    add(snapshot.task_completed_at, "任务完成", _top_current_task(snapshot))
    add(snapshot.task_aborted_at, "任务中止", _top_current_task(snapshot))
    recent_detail = _top_activity_main(snapshot, limit=72)
    if "received token_count" not in recent_detail:
        add(snapshot.last_event_time, "最近事件", recent_detail)
    if not events:
        add(snapshot.refreshed_at, "刷新", "等待会话产生新活动")

    return _merge_activity_events(events)


def _top_details(snapshot: ParsedSession, session_cost: float | None) -> dict[str, object]:
    confirmed = snapshot.confirmed
    session_parts = _top_session_parts(snapshot)
    activity_labels = _top_activity_labels(snapshot)
    session_label = (
        "新会话"
        if _is_new_session_snapshot(snapshot)
        else (
            "会话加载中"
            if _is_pending_session_snapshot(snapshot)
            else f"会话 {snapshot.session_id[-12:]}"
        )
    )
    details = {
        "title": _top_expanded_header_title(snapshot),
        "session": (
            f"{session_label} | "
            f"行 {snapshot.line_count} | 确认 {snapshot.token_events}"
        ),
        "sessionCost": _format_money(session_cost),
        "sessionTokens": _short_num(confirmed.cumulative_total),
        "sessionRounds": f"{snapshot.token_events} 轮确认",
        "cacheText": _top_cache_progress_label(snapshot),
        "warnings": _format_notice(snapshot),
        "executing": _top_executing_text(snapshot),
        "currentTask": _top_current_task(snapshot),
        "activityState": _top_activity_state(snapshot),
        "activityElapsed": _top_activity_elapsed(snapshot),
        "activityGap": _top_activity_gap_value(snapshot),
        "activityLast": _top_activity_last(snapshot),
        "activityLastTooltip": _top_activity_last_tooltip(snapshot),
        "activityTrail": _top_activity_trail(snapshot),
        "slow": _top_slow_chip(snapshot),
        "gap": _top_gap_chip(snapshot),
    }
    details.update(session_parts)
    details.update(activity_labels)
    return details


__all__ = [
    "DEFAULT_RENDERER_TIMEOUT_SECONDS",
    "RENDERER_HUD_ENV",
    "RENDERER_HUD_SCRIPT",
    "RendererHudClient",
    "RendererHudPayload",
    "payload_from_snapshot",
    "remove_renderer_hud_from_pages",
    "renderer_enabled_from_env",
    "set_cost_estimator",
    "wait_for_renderer",
]
