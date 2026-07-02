"""Renderer-injected Codex HUD driven through local Chrome DevTools Protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
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
from ..core.parser import CostEstimator, ParsedSession, RequestRound, ToolCallTiming, seconds_between
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
from ..platforms.codex_theme import CodexThemeProbe, CodexThemeSnapshot
from ..support_assets import support_qr_payload

RENDERER_HUD_ENV = "CODEX_USAGE_HUD_RENDERER"
RENDERER_HUD_VERSION = "18"
DEFAULT_RENDERER_TIMEOUT_SECONDS = 0.45
DEFAULT_RENDERER_TARGET_CACHE_SECONDS = 2.0
DEFAULT_RENDERER_SETTINGS_POLL_SECONDS = 1.0
ACTIVE_SESSION_BINDING_NAME = "codexUsageHudActiveSession"
TOKEN_LEGEND_TEXT = "↑ 输入  ↻ 缓存  ↓ 输出\n◇ 推理  ∑ 合计  $ 金额\n◎ 缓存率  ~ 估算"
TOP_EXPANDED_HEADER_FALLBACK = "Codex 会话 / 预算"
SETTINGS_COMMAND_STORAGE_KEY = "codexUsageHudSettingsCommand:v1"
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

RENDERER_HUD_SCRIPT = r"""
(() => {
  const version = "19";
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
  const runningTimerName = "__codexUsageHudRunningTimer";
  const staleTimerName = "__codexUsageHudStaleTimer";
  const storageKey = "codexUsageHudPanelState:v5";
  const settingsCommandKey = "codexUsageHudSettingsCommand:v1";
  const settingsModalId = "codex-usage-hud-settings-modal";
  const activeSessionObserverName = "__codexUsageHudActiveSessionObserver";
  const activeSessionBootstrapObserverName = "__codexUsageHudActiveSessionBootstrapObserver";
  const activeSessionTimerName = "__codexUsageHudActiveSessionTimer";
  const activeSessionClickHandlerName = "__codexUsageHudActiveSessionClick";
  const activeSessionHistoryPatchName = "__codexUsageHudActiveSessionHistoryPatch";
  const activeSessionLastSignatureName = "__codexUsageHudActiveSessionLastSignature";
  const activeSessionBindingName = "codexUsageHudActiveSession";
  const staleUpdateMs = 10000;
  let topSlotCache = null;
  let pendingSyncPanels = null;
  let cachedHeaderNode = null;
  let cachedComposerNode = null;
  let observedHeaderNode = null;
  let observedComposerNode = null;
  const numericTokenRe = /\$?\d+(?:,\d{3})*(?:\.\d+)?(?:[kM%])?/g;
  const numericAnimations = new WeakMap();

  try {
    if (typeof window.__codexUsageHudRemove === "function") {
      window.__codexUsageHudRemove({ preserveState: true });
    }
  } catch (_) {}

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
      #${rootId} .codex-usage-hud-token-badge {
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
        overflow: hidden;
        pointer-events: none;
      }
      #${rootId} .codex-usage-hud-token-badge[data-composer-badge="active"] {
        display: inline-flex;
      }
      #${rootId} .codex-usage-hud-token-badge-text {
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      #${rootId} .codex-usage-hud-handle,
      #${rootId} .codex-usage-hud-update-button,
      #${rootId} .codex-usage-hud-settings-button {
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
        background: rgba(3, 7, 12, .48);
        pointer-events: auto;
      }
      #${rootId} .codex-usage-hud-settings-dialog {
        position: relative;
        width: min(760px, calc(100vw - 32px));
        max-height: min(720px, calc(100vh - 32px));
        display: grid;
        grid-template-rows: auto auto minmax(0, 1fr) auto;
        border: 1px solid rgba(140, 153, 174, .28);
        border-radius: 8px;
        background: #10161d;
        color: #e8eef7;
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
        background: #151d27;
      }
      #${rootId} .codex-usage-hud-settings-title {
        font-size: 13px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-settings-close,
      #${rootId} .codex-usage-hud-settings-action {
        border: 0;
        border-radius: 5px;
        background: #2e3846;
        color: #dde7f2;
        min-height: 28px;
        padding: 4px 9px;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-settings-action[data-primary="true"] {
        background: #f3d27a;
        color: #10161d;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-settings-link {
        min-height: 0;
        border: 0;
        border-radius: 0;
        background: transparent;
        color: #f3d27a;
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
        border-top: 1px solid #202833;
        border-bottom: 1px solid #202833;
        background: #10161d;
      }
      #${rootId} .codex-usage-hud-settings-tab {
        border: 0;
        border-radius: 5px;
        background: transparent;
        color: #a9bcd2;
        padding: 5px 9px;
        cursor: pointer;
      }
      #${rootId} .codex-usage-hud-settings-tab[data-active="true"] {
        background: #202833;
        color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-settings-body {
        min-height: 0;
        overflow: auto;
        padding: 12px;
        scrollbar-width: thin;
        scrollbar-color: #273241 #10161d;
      }
      #${rootId} .codex-usage-hud-settings-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
      }
      #${rootId} .codex-usage-hud-settings-field,
      #${rootId} .codex-usage-hud-price-table {
        min-width: 0;
        display: grid;
        gap: 4px;
      }
      #${rootId} .codex-usage-hud-settings-field label,
      #${rootId} .codex-usage-hud-price-title {
        color: #8492a6;
        font-size: 10px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-settings-field input,
      #${rootId} .codex-usage-hud-settings-field select,
      #${rootId} .codex-usage-hud-price-row input {
        min-width: 0;
        box-sizing: border-box;
        width: 100%;
        border: 1px solid #273241;
        border-radius: 5px;
        background: #141b24;
        color: #e8eef7;
        min-height: 30px;
        padding: 5px 7px;
        outline: none;
      }
      #${rootId} .codex-usage-hud-settings-field input:focus,
      #${rootId} .codex-usage-hud-settings-field select:focus,
      #${rootId} .codex-usage-hud-price-row input:focus {
        border-color: #f3d27a;
      }
      #${rootId} .codex-usage-hud-overlay-dependency {
        min-height: 30px;
        box-sizing: border-box;
        display: grid;
        gap: 5px;
        padding: 7px 8px;
        border: 1px solid #273241;
        border-radius: 5px;
        background: #141b24;
      }
      #${rootId} .codex-usage-hud-overlay-dependency-head {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 6px;
      }
      #${rootId} .codex-usage-hud-overlay-dependency-state {
        color: #e8eef7;
        font-size: 11px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-overlay-dependency-version {
        color: #8fe3a1;
        font: 700 11px Consolas, "Cascadia Mono", ui-monospace, monospace;
      }
      #${rootId} .codex-usage-hud-overlay-dependency-note {
        color: #8492a6;
        font-size: 11px;
        line-height: 1.35;
      }
      #${rootId} .codex-usage-hud-overlay-dependency-actions {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
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
      #${rootId} .codex-usage-hud-price-table {
        grid-column: 1 / -1;
        margin-top: 4px;
      }
      #${rootId} .codex-usage-hud-price-row,
      #${rootId} .codex-usage-hud-price-header {
        display: grid;
        grid-template-columns: minmax(130px, 1.4fr) repeat(4, minmax(72px, 1fr));
        gap: 6px;
        align-items: center;
      }
      #${rootId} .codex-usage-hud-price-table[data-advanced="true"] .codex-usage-hud-price-row,
      #${rootId} .codex-usage-hud-price-table[data-advanced="true"] .codex-usage-hud-price-header {
        grid-template-columns: minmax(130px, 1.2fr) repeat(4, minmax(68px, 1fr)) minmax(92px, .9fr) minmax(150px, 1.3fr);
      }
      #${rootId} .codex-usage-hud-price-advanced {
        display: none;
      }
      #${rootId} .codex-usage-hud-price-table[data-advanced="true"] .codex-usage-hud-price-advanced {
        display: block;
      }
      #${rootId} .codex-usage-hud-price-header {
        color: #8492a6;
        font-size: 10px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-price-detected {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: center;
        color: #8492a6;
        font-size: 11px;
      }
      #${rootId} .codex-usage-hud-settings-status {
        min-width: 0;
        color: #a9bcd2;
        font-size: 11px;
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
        color: #ffb86b;
      }
      #${rootId} .codex-usage-hud-settings-confirm-layer {
        position: absolute;
        inset: 0;
        z-index: 3;
        display: grid;
        place-items: center;
        padding: 24px;
        background: rgba(4, 9, 14, .62);
        backdrop-filter: blur(4px);
      }
      #${rootId} .codex-usage-hud-settings-confirm-card {
        width: min(520px, calc(100% - 12px));
        display: grid;
        gap: 12px;
        padding: 18px 18px 16px;
        border: 1px solid rgba(243, 210, 122, .34);
        border-radius: 12px;
        background: linear-gradient(180deg, rgba(30, 39, 49, .98), rgba(16, 22, 29, .98));
        box-shadow: 0 22px 46px rgba(0, 0, 0, .45);
      }
      #${rootId} .codex-usage-hud-settings-confirm-kicker {
        color: #f3d27a;
        font-size: 11px;
        font-weight: 800;
        letter-spacing: .08em;
        text-transform: uppercase;
      }
      #${rootId} .codex-usage-hud-settings-confirm-title {
        color: #f6f9fc;
        font-size: 18px;
        font-weight: 800;
        line-height: 1.25;
      }
      #${rootId} .codex-usage-hud-settings-confirm-body {
        color: #c7d4e4;
        font-size: 13px;
        line-height: 1.7;
        white-space: pre-wrap;
      }
      #${rootId} .codex-usage-hud-settings-confirm-actions {
        display: flex;
        flex-wrap: wrap;
        justify-content: flex-end;
        gap: 8px;
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
        background: #1A2430;
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
        background: #F3D27A;
      }
      #${rootId} .codex-usage-hud-settings-loading-glow {
        left: 8%;
        width: 16%;
        background: #FFE7A0;
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
        background: #202833;
        color: #f3d27a;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-settings-action[data-variant="ghost"] {
        background: transparent;
        border: 1px solid #2e3846;
        color: #a9bcd2;
      }
      #${rootId} .codex-usage-hud-support {
        display: grid;
        gap: 12px;
        color: #dde7f2;
        line-height: 1.55;
      }
      #${rootId} .codex-usage-hud-support a {
        color: #9ccbff;
      }
      #${rootId} .codex-usage-hud-support-note {
        color: #a9bcd2;
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
        border: 1px solid #273241;
        border-radius: 8px;
        background: #141b24;
      }
      #${rootId} .codex-usage-hud-support-qr-title {
        width: 100%;
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 8px;
        color: #e8eef7;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-support-qr-title span:last-child {
        color: #8492a6;
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
        #${rootId} .codex-usage-hud-support-qr-grid {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-price-row,
        #${rootId} .codex-usage-hud-price-header {
          grid-template-columns: minmax(110px, 1fr) repeat(2, minmax(68px, 1fr));
        }
      }
    `;
    document.documentElement.appendChild(style);
  }

  function applyTheme(root, payload) {
    if (!root) return;
    const tokens = payload?.theme?.tokens || {};
    const variant = String(payload?.theme?.variant || "dark").toLowerCase() === "light" ? "light" : "dark";
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

  function panelMarkup(name, glyph, ariaLabel) {
    const glyphMarkup = glyph ? `<span class="codex-usage-hud-glyph">${glyph}</span>` : "";
    const settingsButtonMarkup = name === "top"
      ? `<button class="codex-usage-hud-settings-button" data-action="settings-open" title="设置" aria-label="设置">⚙</button>`
      : "";
    const tokenBadgeMarkup = name === "request"
      ? `<span class="codex-usage-hud-token-badge" data-composer-badge="idle" aria-hidden="true"><span class="codex-usage-hud-token-badge-text" data-field="requestComposerTokens">TikToken:0 Ts</span></span>`
      : "";
    const updateButtonMarkup = name === "top"
      ? `<button class="codex-usage-hud-update-button" data-action="update-action" title="" aria-label="" hidden>↓</button>`
      : "";
    const leftControlsMarkup = name === "top"
      ? `<div class="codex-usage-hud-left-controls">${updateButtonMarkup}</div>`
      : "";
    return `
      <div class="codex-usage-hud-panel ${PANEL[name].className}" data-panel="${name}" data-expanded="false" role="status" aria-live="polite">
        ${resizeEdgesMarkup()}
        <div class="codex-usage-hud-collapsed" data-has-settings="${name === "top" ? "true" : "false"}" data-has-badge="${name === "request" ? "true" : "false"}">
          ${leftControlsMarkup}
          <button class="codex-usage-hud-main" data-action="toggle" data-has-glyph="${glyph ? "true" : "false"}" aria-label="${ariaLabel}">
            ${glyphMarkup}
            ${name === "top" ? `<span class="codex-usage-hud-progress-strip-viewport"><span class="codex-usage-hud-progress-strip" data-field="topCollapsedProgress"></span></span>` : ""}
            <span class="codex-usage-hud-line" data-field="${name}Line"></span>
          </button>
          ${settingsButtonMarkup}
          ${tokenBadgeMarkup}
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
        </div>
        ${requestExpandedResizeMarkup()}
      </div>
    `;
  }

  function settingsChromeMarkup() {
    return `
      <div id="${settingsModalId}" class="codex-usage-hud-settings-modal" hidden></div>
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

  function currentPayload() {
    return window[stateName]?.payload || {};
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
      support_url: "https://github.com/mingbingfeng/codex-usage-hud",
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

  function hasAdvancedPriceRows(settings) {
    const prices = settings?.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {};
    return Object.values(prices).some((price) => !!String(price?.provider || price?.base_url || price?.baseUrl || "").trim());
  }

  function settingsBridgeUrl() {
    return String(currentPayload()?.settingsBridgeUrl || "").replace(/\/+$/, "");
  }

  function normalizeThreadId(value) {
    const text = normalize(value);
    const match = text.match(/^(?:[a-z0-9_.-]+:)(.+)$/i);
    return match ? normalize(match[1]) : text;
  }

  const activeSessionIdentitySelector = [
    "[data-app-action-sidebar-thread-id]",
    "[data-session-id]",
    "a[href*='thread']",
    "a[href*='conversation']",
    "a[href*='session']",
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

  function cleanActiveSessionTitle(value) {
    return normalize(String(value || "").replace(
      /\s*\d+\s*(秒|分|分钟|小时|天|周|个月|月|年|sec|secs|second|seconds|min|mins|minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)\s*$/i,
      ""
    ));
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
    const titleNode = sourceRow?.querySelector?.("[data-thread-title], .truncate.select-none, .truncate.text-base");
    const rawTitle = titleNode?.textContent || (titleNode ? "" : (sourceRow?.textContent || row?.textContent || ""));
    const title = cleanActiveSessionTitle(titleNode ? rawTitle : rawTitle.replace(/\s*(Export|Delete|Move|Remove from project|导出|删除|移动|移出项目)+$/g, "")).slice(0, 160);
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
      .filter((row) => {
        const ref = activeSessionRefFromRow(row);
        return !!(ref.sessionId || ref.title);
      });
  }

  function readActiveSessionRef() {
    const rows = activeSessionRows();
    const row = rows.find(activeSessionRowSelected) || rows.find(activeSessionRowMatchesLocation) || null;
    const ref = row ? activeSessionRefFromRow(row) : { sessionId: activeSessionLocationId(), title: "" };
    return {
      sessionId: ref.sessionId || "",
      title: ref.title || "",
      url: location.href,
    };
  }

  function activeSessionContainer() {
    const row = document.querySelector(activeSessionRowSelector);
    return row?.closest?.("aside, nav, [role='navigation'], [data-testid*='sidebar' i], [class*='sidebar' i]")
      || row?.parentElement
      || document.querySelector("aside, nav, [role='navigation'], [data-testid*='sidebar' i], [class*='sidebar' i]")
      || null;
  }

  function postActiveSession(reason = "event", overrideRef = null) {
    const bridge = settingsBridgeUrl();
    const ref = overrideRef || readActiveSessionRef();
    if (!ref.sessionId && !ref.title) return;
    const signature = JSON.stringify([ref.sessionId, ref.title, ref.url || location.href]);
    if (window[activeSessionLastSignatureName] === signature) return;
    window[activeSessionLastSignatureName] = signature;
    const payload = {
      sessionId: ref.sessionId,
      title: ref.title,
      url: ref.url || location.href,
      reason,
      observedAt: Date.now(),
    };
    const binding = window[activeSessionBindingName];
    if (typeof binding === "function") {
      try {
        binding(JSON.stringify(payload));
        return;
      } catch (_) {}
    }
    if (!bridge) return;
    fetch(`${bridge}/active-session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {});
  }

  function scheduleActiveSessionReport(reason = "event") {
    clearTimeout(window[activeSessionTimerName] || 0);
    window[activeSessionTimerName] = setTimeout(() => {
      postActiveSession(reason);
      refreshActiveSessionObserver();
    }, 40);
  }

  function refreshActiveSessionObserver() {
    const container = activeSessionContainer();
    window[activeSessionObserverName]?.disconnect?.();
    if (!container) return false;
    window[activeSessionObserverName] = new MutationObserver(() => {
      scheduleActiveSessionReport("sidebar");
    });
    window[activeSessionObserverName].observe(container, {
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
        scheduleActiveSessionReport("history");
        return result;
      },
      replaceState: function(...args) {
        const result = originalReplaceState.apply(this, args);
        scheduleActiveSessionReport("history");
        return result;
      },
      popstate: () => scheduleActiveSessionReport("popstate"),
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
    document.removeEventListener("click", window[activeSessionClickHandlerName], true);
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
    delete window[activeSessionClickHandlerName];
    delete window[activeSessionHistoryPatchName];
    delete window[activeSessionLastSignatureName];
  }

  function ensureActiveSessionWatchers() {
    if (!window[activeSessionClickHandlerName]) {
      window[activeSessionClickHandlerName] = (event) => {
        const container = event.target?.closest?.("aside, nav, [role='navigation'], [data-testid*='sidebar' i], [class*='sidebar' i]");
        const identityRow = event.target?.closest?.(activeSessionIdentitySelector);
        const row = identityRow || event.target?.closest?.(activeSessionRowSelector);
        const explicitRow = !!identityRow || row?.matches?.("[role='link']");
        if (row && !container && !explicitRow) return;
        if (row && (!container || container.contains(row))) {
          const ref = activeSessionRefFromRow(row);
          postActiveSession("click", {
            sessionId: ref.sessionId || "",
            title: ref.title || "",
            url: activeSessionRowUrl(row),
          });
          scheduleActiveSessionReport("click-followup");
        }
      };
      document.addEventListener("click", window[activeSessionClickHandlerName], true);
    }
    installActiveSessionHistoryPatch();
    startActiveSessionBootstrapObserver();
    scheduleActiveSessionReport("payload");
  }

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
    const installCommand = String(dependency.installCommand || "python -m pip install \"PySide6>=6.8\"");
    if (installed) {
      return `
        <div class="codex-usage-hud-overlay-dependency" data-installed="true">
          <div class="codex-usage-hud-overlay-dependency-head">
            <span class="codex-usage-hud-overlay-dependency-state">已安装</span>
            <span class="codex-usage-hud-overlay-dependency-version">${escapeHtml(version ? `PySide6 ${version}` : "PySide6 可用")}</span>
          </div>
          <div class="codex-usage-hud-overlay-dependency-note">修改左侧数量后保存，桌面气泡会自动生效。</div>
        </div>
      `;
    }
    const actions = [];
    if (canInstall && !installing) {
      actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-install-desktop-overlay">立即安装</button>');
    }
    if (!requiresRestart) {
      actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-enable-desktop-overlay">已安装，立即启用</button>');
    } else {
      actions.push('<button type="button" class="codex-usage-hud-settings-link" data-action="settings-restart">立即重启</button>');
    }
    return `
      <div class="codex-usage-hud-overlay-dependency" data-installed="false">
        <div class="codex-usage-hud-overlay-dependency-head">
          <span class="codex-usage-hud-overlay-dependency-state">${installing ? "正在安装" : (requiresRestart ? "需要重启" : "需要安装环境")}</span>
        </div>
        <div class="codex-usage-hud-overlay-dependency-note">${
          installing
            ? "PySide6 正在后台安装；完成后点击“已安装，立即启用”。"
            : (requiresRestart
              ? "安装完成后需要重启 HUD，才能加载桌面气泡环境。"
              : `桌面气泡依赖 PySide6。命令：${escapeHtml(installCommand)}`)
        }</div>
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
    if (!entries.length) entries.push(["gpt-5.5", { input: 5, cached_input: 0.5, output: 30, reasoning: 30 }]);
    const advanced = hasAdvancedPriceRows(settings);
    return entries.map(([key, price]) => {
      const model = String(price?.model || key || "");
      const provider = String(price?.provider || "");
      const baseUrl = String(price?.base_url || price?.baseUrl || "");
      const rowAdvanced = advanced || provider || baseUrl;
      return `
      <div class="codex-usage-hud-price-row" data-price-row="true" data-price-key="${escapeHtml(key)}" data-advanced="${rowAdvanced ? "true" : "false"}">
        <input data-price-field="model" value="${escapeHtml(model)}" aria-label="模型">
        <input data-price-field="input" type="number" min="0" step="0.000001" value="${escapeHtml(price?.input ?? 0)}" aria-label="输入单价">
        <input data-price-field="cached_input" type="number" min="0" step="0.000001" value="${escapeHtml(price?.cached_input ?? 0)}" aria-label="缓存输入单价">
        <input data-price-field="output" type="number" min="0" step="0.000001" value="${escapeHtml(price?.output ?? 0)}" aria-label="输出单价">
        <input data-price-field="reasoning" type="number" min="0" step="0.000001" value="${escapeHtml(price?.reasoning ?? 0)}" aria-label="推理单价">
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

  function renderSettingsModal(tab = "settings", status = "") {
    const root = document.getElementById(rootId);
    const modal = document.getElementById(settingsModalId);
    if (!root || !modal) return;
    const settings = hudSettingsFromPayload();
    const activeTab = ["support", "about"].includes(tab) ? tab : "settings";
    const path = settingsPathLabel();
    const bridge = settingsBridgeUrl();
    const defaultStatus = activeTab === "about"
      ? "可检查 GitHub Release 并启动 Windows 安装器。"
      : (bridge ? "设置将保存到本地配置文件" : "设置桥接未连接，可导出 JSON 手动写入配置文件");
    modal.innerHTML = `
      <div class="codex-usage-hud-settings-dialog" role="dialog" aria-modal="true" aria-label="codex-usage-hud 设置">
        <div class="codex-usage-hud-settings-head">
          <div class="codex-usage-hud-settings-title">codex-usage-hud v${escapeHtml(appVersion())}</div>
          <button type="button" class="codex-usage-hud-settings-close" data-action="settings-close" aria-label="关闭">×</button>
        </div>
        <div class="codex-usage-hud-settings-tabs" role="tablist">
          <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="settings" data-active="${activeTab === "settings"}">设置</button>
          <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="support" data-active="${activeTab === "support"}">请作者喝咖啡</button>
          <button type="button" class="codex-usage-hud-settings-tab" data-action="settings-tab" data-tab="about" data-active="${activeTab === "about"}">版本更新</button>
        </div>
        <div class="codex-usage-hud-settings-body">
          ${activeTab === "support" ? supportPanelHtml(settings, path) : activeTab === "about" ? aboutPanelHtml(path) : settingsPanelHtml(settings, bridge, path)}
        </div>
        <div class="codex-usage-hud-settings-actions">
          <div class="codex-usage-hud-settings-status" data-settings-status="true">${escapeHtml(status || defaultStatus)}</div>
          <div>
            ${activeTab === "settings" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-export">导出 JSON</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-restart" hidden>立即重启 HUD</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-save" data-primary="true">保存</button>' : activeTab === "about" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-check-update">检查更新</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-install-update" data-primary="true">安装更新</button>' : '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-close" data-primary="true">关闭</button>'}
          </div>
        </div>
      </div>
    `;
    modal.hidden = false;
    updateAboutActionButtons(currentUpdateState());
  }

  function settingsPanelHtml(settings, bridge, path) {
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
        <div class="codex-usage-hud-settings-field">
          <label>PySide6 桌面气泡数量（0 为关闭）</label>
          <select data-setting-key="work_overlay_max_items">${overlayOptions}</select>
        </div>
        <div class="codex-usage-hud-settings-field">
          <label>气泡依赖 PySide6</label>
          <div data-desktop-overlay-dependency="true">${desktopOverlayDependencyHtml()}</div>
        </div>
        <div class="codex-usage-hud-settings-field">
          <label>超额提醒阈值</label>
          <input data-setting-key="budget_thresholds" value="${escapeHtml(thresholdText(settings))}">
        </div>
        <div class="codex-usage-hud-settings-field">
          <label>本周补充已使用额度 USD</label>
          <input data-setting-key="weekly_adjustment_usd" type="number" min="0" step="0.01" value="${escapeHtml(settings.weekly_adjustment_usd)}">
        </div>
        <div class="codex-usage-hud-settings-field" style="grid-column:1/-1">
          <label>计费单价获取地址</label>
          <div class="codex-usage-hud-settings-inline">
            <input data-setting-key="pricing_url" value="${escapeHtml(settings.pricing_url)}" placeholder="https://example.com/model-prices.json">
            <button type="button" class="codex-usage-hud-settings-action" data-action="settings-fetch-prices">拉取</button>
          </div>
        </div>
        <div class="codex-usage-hud-price-table" data-advanced="${hasAdvancedPriceRows(settings) ? "true" : "false"}">
          <div class="codex-usage-hud-price-title">模型单价（USD / 1M tokens）</div>
          <div class="codex-usage-hud-price-header">
            <div>模型</div><div>输入</div><div>缓存</div><div>输出</div><div>推理</div><div class="codex-usage-hud-price-advanced">渠道</div><div class="codex-usage-hud-price-advanced">Base URL</div>
          </div>
          <div data-price-rows="true">${priceRowsHtml(settings)}</div>
          ${detectedPriceModelsHtml(settings)}
          <button type="button" class="codex-usage-hud-settings-action" data-action="settings-add-model" style="justify-self:start;margin-top:6px">添加模型</button>
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
    updateAboutActionButtons(updateStateFromPayload(payload));
    syncDesktopOverlayDependency();
    syncSettingsUpdateLoading(payload);
    const status = payload?.settingsCommandStatus;
    if (!status || typeof status !== "object") return;
    setSettingsStatus(status.message || "", status.kind || "");
    setSettingsRestartVisible(!!status.restartVisible);
  }

  function submitSettingsCommand(command, pendingMessage, { preserveOverlay = false } = {}) {
    const payload = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      createdAt: Date.now(),
      ...command,
    };
    try {
      localStorage.setItem(settingsCommandKey, JSON.stringify(payload));
    } catch (error) {
      setSettingsStatus(`无法提交设置命令：${error?.message || error}`, "error");
      return false;
    }
    const bridge = settingsBridgeUrl();
    if (bridge) {
      try {
        fetch(`${bridge}/command`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          keepalive: true,
        }).catch(() => {});
      } catch (_) {}
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
    const modelPrices = {};
    modal?.querySelectorAll("[data-price-row='true']").forEach((row) => {
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
        cached_input: field("cached_input"),
        output: field("output"),
        reasoning: field("reasoning"),
      };
      if (provider) modelPrices[key].provider = provider;
      if (baseUrl) modelPrices[key].base_url = baseUrl;
    });
    const displayMode = "renderer";
    return {
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
      pricing_url: String(read("pricing_url") || "").trim(),
      budget_thresholds: String(read("budget_thresholds") || "")
        .split(",")
        .map((item) => Number(item.trim()))
        .filter((item) => Number.isFinite(item) && item > 0),
      weekly_adjustment_usd: numberValue("weekly_adjustment_usd", settings.weekly_adjustment_usd),
      support_url: String(settings.support_url || "https://github.com/mingbingfeng/codex-usage-hud").trim(),
      model_prices: modelPrices,
    };
  }

  function saveSettingsFromModal() {
    const settings = collectSettingsForm();
    submitSettingsCommand(
      { action: "save", settings },
      "保存请求已提交，等待 HUD daemon 写入本地配置..."
    );
  }

  function fetchPricesFromModal() {
    const settings = collectSettingsForm();
    submitSettingsCommand(
      { action: "fetchPrices", settings },
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
      "正在准备安装 PySide6..."
    );
  }

  function enableDesktopOverlayFromModal() {
    submitSettingsCommand(
      { action: "enableDesktopOverlay" },
      "正在重新检测 PySide6..."
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
    row.dataset.advanced = "false";
    row.innerHTML = `
      <input data-price-field="model" value="${escapeHtml(initialModel)}" aria-label="模型">
      <input data-price-field="input" type="number" min="0" step="0.000001" value="0" aria-label="输入单价">
      <input data-price-field="cached_input" type="number" min="0" step="0.000001" value="0" aria-label="缓存输入单价">
      <input data-price-field="output" type="number" min="0" step="0.000001" value="0" aria-label="输出单价">
      <input data-price-field="reasoning" type="number" min="0" step="0.000001" value="0" aria-label="推理单价">
      <input class="codex-usage-hud-price-advanced" data-price-field="provider" value="" aria-label="渠道">
      <input class="codex-usage-hud-price-advanced" data-price-field="base_url" value="" aria-label="Base URL">
    `;
    rows.appendChild(row);
    const target = initialModel ? row.querySelector('[data-price-field="input"]') : row.querySelector("input");
    target?.focus?.();
  }

  function closeSettingsModal() {
    const modal = document.getElementById(settingsModalId);
    if (modal) modal.hidden = true;
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
    root.innerHTML = panelMarkup("top", "", "展开顶部 HUD") + panelMarkup("request", "", "展开请求 HUD") + settingsChromeMarkup();
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
      if (action.dataset.action === "settings-open") {
        event.preventDefault();
        event.stopPropagation();
        renderSettingsModal("settings");
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
    });
    root.addEventListener("pointerdown", (event) => {
      if (event.button !== undefined && event.button !== 0) return;
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

  function updateComposerBadgeText(root = document.getElementById(rootId)) {
    if (!root) return;
    const input = window[composerInputNodeName];
    const count = composerTokenCount(composerInputText(input));
    setText(root, "requestComposerTokens", `TikToken:${count} Ts`);
  }

  function setComposerBadgeActive(active) {
    const root = document.getElementById(rootId);
    if (!root) return;
    const changed = window[composerFocusStateName] !== !!active;
    window[composerFocusStateName] = !!active;
    root.querySelectorAll('[data-composer-badge]').forEach((node) => {
      node.dataset.composerBadge = active ? "active" : "idle";
    });
    if (active) updateComposerBadgeText(root);
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
    }
    window[composerInputNodeName] = null;
    window[composerInputHandlersName] = null;
  }

  function ensureComposerInputWatchers() {
    const input = composerInputElement();
    if (input === window[composerInputNodeName]) {
      if (input && window[composerFocusStateName]) updateComposerBadgeText();
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
        if (window[composerFocusStateName]) updateComposerBadgeText();
      },
    };
    input.addEventListener("focus", handlers.focus, true);
    input.addEventListener("blur", handlers.blur, true);
    input.addEventListener("input", handlers.input, true);
    window[composerInputNodeName] = input;
    window[composerInputHandlersName] = handlers;
    // The composer may already hold focus when we (re)attach after a re-inject.
    const focused = document.activeElement === input
      || (input.contains?.(document.activeElement) ?? false);
    setComposerBadgeActive(focused);
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
    const root = ensureRoot();
    if (!root) return;
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

  function renderRequestRows(root, rows, rowDetails) {
    const list = root.querySelector('[data-field="requestRows"]');
    if (!list) return;
    list.textContent = "";
    const details = Array.isArray(rowDetails) && rowDetails.length ? rowDetails : [];
    if (details.length) {
      details.forEach((item, index) => appendStructuredRequestRow(list, item, index));
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

  window.__codexUsageHudUpdate = (payload) => {
    const previousPayload = window[stateName]?.payload || {};
    const nextPayload = { ...previousPayload, ...(payload || {}) };
    if (
      (!payload?.supportImages || !payload.supportImages.length) &&
      previousPayload.supportImages?.length
    ) {
      nextPayload.supportImages = previousPayload.supportImages;
    }
    window[stateName] = { payload: nextPayload, updatedAt: Date.now() };
    try {
      ensureActiveSessionWatchers();
    } catch (_) {}
    const root = ensureRoot();
    if (!root) return false;
    applyTheme(root, nextPayload);
    setText(root, "topLine", nextPayload?.topLine || "codex-usage-hud 等待数据");
    setText(root, "requestLine", nextPayload?.requestLine || "本次请求 等待");
    setText(root, "requestLineExpanded", nextPayload?.requestLine || "最近模型请求轮次");
    root.querySelectorAll('[data-field="topLine"], [data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.remove(warningClass);
    });
    root.querySelectorAll('[data-field="topLine"]').forEach((node) => {
      node.classList.toggle(warningClass, !!nextPayload?.warning);
    });
    root.querySelectorAll('[data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.toggle(errorClass, nextPayload?.requestStatus === "error");
    });
    renderTopDetails(root, nextPayload || {});
    renderRequestRows(root, nextPayload?.requestRows || [], nextPayload?.requestRowDetails || []);
    renderUpdateButtons(root, nextPayload || {});
    applySettingsCommandStatus(nextPayload || {});
    syncPosition();
    syncPositionSettled();
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
    try {
      removeActiveSessionWatchers();
    } catch (_) {}
    detachComposerInputWatchers();
    stopBootstrapObserver();
    observedHeaderNode = null;
    observedComposerNode = null;
    cancelAnimationFrame(window[rafName] || 0);
    clearInterval(window[runningTimerName] || 0);
    clearTimeout(window[staleTimerName] || 0);
    clearTimeout(window[composerSettleTimerName] || 0);
    for (const timer of (window[settleTimerName] || [])) clearTimeout(timer);
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
    delete window[composerSettleTimerName];
    delete window[settleTimerName];
    delete window[composerInputNodeName];
    delete window[composerInputHandlersName];
    delete window[composerFocusStateName];
    delete window.__codexUsageHudUpdate;
    delete window.__codexUsageHudRemove;
    return true;
  };

  window[scheduleName] = () => scheduleForPanels(Object.keys(PANEL), { invalidateTop: true });
  window[resizeHandlerName] = window[scheduleName];
  window[scrollHandlerName] = () => scheduleForPanels(["request"]);
  window.addEventListener("resize", window[resizeHandlerName]);
  window.addEventListener("scroll", window[scrollHandlerName], true);
  const boot = () => {
    const state = window[stateName];
    if (state?.payload) {
      window.__codexUsageHudUpdate(state.payload);
    } else {
      ensureRoot();
      syncPosition();
      refreshLayoutObservers();
    }
  };
  if (document.body) {
    boot();
  } else {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  }
})()
"""


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
    settings_command_status: dict[str, object] = field(default_factory=dict)
    work_overlay_selectable_max: int = 6
    desktop_overlay_dependency: dict[str, object] = field(default_factory=dict)
    support_images: list[dict[str, str]] = field(default_factory=list)
    theme: dict[str, object] = field(default_factory=dict)
    update_state: dict[str, object] = field(default_factory=dict)
    app_version: str = __version__

    def to_json(self) -> dict[str, object]:
        return {
            "topLine": self.top_line,
            "requestLine": self.request_line,
            "session": self.session,
            "model": self.model,
            "source": self.source,
            "requestStatus": self.request_status,
            "lastEvent": self.last_event,
            "refreshedAt": self.refreshed_at,
            "warning": self.warning,
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
            "settingsCommandStatus": dict(self.settings_command_status),
            "workOverlaySelectableMax": int(self.work_overlay_selectable_max),
            "desktopOverlayDependency": dict(self.desktop_overlay_dependency),
            "supportImages": [dict(item) for item in self.support_images],
            "theme": dict(self.theme),
            "updateState": dict(self.update_state),
            "appVersion": self.app_version,
        }


class _RendererActiveSessionBinding:
    """Receive active-session events from the renderer over a CDP binding."""

    def __init__(
        self,
        binding_name: str,
        callback: Any,
        *,
        timeout_seconds: float,
    ) -> None:
        self.binding_name = str(binding_name or "").strip()
        self.callback = callback
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._websocket_url = ""
        self._target_id = ""

    def ensure(self, websocket_url: str, target_id: str) -> None:
        """Start or restart the binding listener for the current page target."""
        if not self.binding_name or not callable(self.callback) or not websocket_url:
            return
        with self._lock:
            thread = self._thread
            if (
                thread is not None
                and thread.is_alive()
                and websocket_url == self._websocket_url
                and target_id == self._target_id
            ):
                return
        self.close(join_timeout=0.3)
        with self._lock:
            self._stop_event = threading.Event()
            self._ready_event = threading.Event()
            self._websocket_url = websocket_url
            self._target_id = target_id
            self._thread = threading.Thread(
                target=self._run,
                args=(websocket_url,),
                name="codex-hud-active-session-cdp",
                daemon=True,
            )
            self._thread.start()
            ready_event = self._ready_event
        ready_event.wait(min(0.35, self.timeout_seconds))

    def close(self, *, join_timeout: float = 1.0) -> None:
        self._stop_event.set()
        with self._lock:
            sock = self._sock
            thread = self._thread
            self._sock = None
            self._thread = None
            self._websocket_url = ""
            self._target_id = ""
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
                if payload.get("method") != "Runtime.bindingCalled":
                    continue
                params = payload.get("params") or {}
                if str(params.get("name") or "") != self.binding_name:
                    continue
                self._handle_binding_payload(str(params.get("payload") or ""))
        except Exception:
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
        settings_poll_seconds: float = DEFAULT_RENDERER_SETTINGS_POLL_SECONDS,
        enabled: bool | None = None,
    ) -> None:
        self.port = int(port or cdp_port_from_env())
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self.target_cache_seconds = max(0.0, float(target_cache_seconds))
        self.settings_poll_seconds = max(0.0, float(settings_poll_seconds))
        self.enabled = renderer_enabled_from_env() if enabled is None else bool(enabled)
        self.last_status = "idle" if self.enabled else "disabled"
        self.last_error = ""
        self._target_id = ""
        self._script_identifier = ""
        self._websocket_url = ""
        self._cached_target_id = ""
        self._cached_websocket_url = ""
        self._target_cache_at = 0.0
        self._next_settings_poll_at = 0.0
        self._support_images_sent = False
        self._active_session_binding: _RendererActiveSessionBinding | None = None
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
        if callable(callback):
            self._active_session_binding = _RendererActiveSessionBinding(
                ACTIVE_SESSION_BINDING_NAME,
                callback,
                timeout_seconds=self.timeout_seconds,
            )

    def update(
        self,
        snapshot: ParsedSession,
        *,
        settings: UserConfig | None = None,
        active_display_mode: str = "renderer",
        settings_path: Path | str | None = None,
        settings_bridge_url: str = "",
        settings_command_status: dict[str, object] | None = None,
        update_state: dict[str, object] | None = None,
        work_overlay_selectable_max: int = 6,
        desktop_overlay_dependency: dict[str, object] | None = None,
    ) -> bool:
        support_images = [] if self._support_images_sent else support_qr_payload()
        theme_snapshot = self._theme_probe.snapshot()
        payload = payload_from_snapshot(
            snapshot,
            settings=settings,
            active_display_mode=active_display_mode,
            settings_path=settings_path,
            settings_bridge_url=settings_bridge_url,
            settings_command_status=settings_command_status,
            support_images=support_images,
            theme=_renderer_theme_payload(theme_snapshot),
            update_state=update_state,
            work_overlay_selectable_max=work_overlay_selectable_max,
            desktop_overlay_dependency=desktop_overlay_dependency,
        ).to_json()
        if self.update_payload(payload):
            if support_images:
                self._support_images_sent = True
            return True
        return False

    def update_payload(self, payload: dict[str, object]) -> bool:
        if not self.enabled:
            self.last_status = "disabled"
            return False
        try:
            target = self._page_target()
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            target_id = str(target.get("id") or websocket_url)
            if not websocket_url:
                raise RuntimeError("CDP target has no websocket URL")
            if target_id != self._target_id or not self._script_identifier:
                self._install(websocket_url, target_id)
            if not self._send_update(websocket_url, payload):
                self._install(websocket_url, target_id, force=True)
                if not self._send_update(websocket_url, payload):
                    raise RuntimeError("renderer update function did not acknowledge payload")
            if self._active_session_binding is not None:
                self._active_session_binding.ensure(websocket_url, target_id)
        except Exception as exc:
            self._clear_target_cache(clear_script=True)
            self.last_status = "failed"
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        self.last_status = "ok"
        self.last_error = ""
        return True

    def take_settings_command(self) -> dict[str, object] | None:
        """Consume one pending settings command from the renderer page."""
        if not self.enabled:
            return None
        now = time.monotonic()
        if now < self._next_settings_poll_at:
            return None
        self._next_settings_poll_at = now + self.settings_poll_seconds
        expression = (
            "(() => {"
            f"const key = {json.dumps(SETTINGS_COMMAND_STORAGE_KEY)};"
            "try {"
            "const raw = localStorage.getItem(key);"
            "if (!raw) return null;"
            "localStorage.removeItem(key);"
            "const value = JSON.parse(raw);"
            "if (value && typeof value === 'object') {"
            "const expiresAt = Number(value.expiresAt || 0);"
            "if (expiresAt && Date.now() > expiresAt) return null;"
            "}"
            "return value && typeof value === 'object' ? value : { action: 'invalid' };"
            "} catch (error) {"
            "return { action: 'invalid', message: String(error && error.message || error) };"
            "}"
            "})()"
        )
        try:
            target = self._page_target()
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            if not websocket_url:
                return None
            result = send_cdp_command(
                websocket_url,
                "Runtime.evaluate",
                _runtime_expression_params(expression),
                self.timeout_seconds,
            )
            value = (
                result.get("result", {})
                .get("result", {})
                .get("value")
            )
        except Exception:
            self._clear_target_cache(clear_script=False)
            return None
        return value if isinstance(value, dict) else None

    def close(self) -> None:
        if self._active_session_binding is not None:
            self._active_session_binding.close()
            self._active_session_binding = None
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
        if (
            not force
            and self._cached_websocket_url
            and self._cached_target_id
            and time.monotonic() - self._target_cache_at <= self.target_cache_seconds
        ):
            return {
                "id": self._cached_target_id,
                "webSocketDebuggerUrl": self._cached_websocket_url,
            }
        targets = list_targets(self.port, self.timeout_seconds)
        target = pick_page_target(targets)
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
        if clear_script:
            self._target_id = ""
            self._websocket_url = ""
            self._script_identifier = ""

    def _install(self, websocket_url: str, target_id: str, *, force: bool = False) -> None:
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
            RENDERER_HUD_SCRIPT,
            self.timeout_seconds,
        )
        self._target_id = target_id
        self._websocket_url = websocket_url
        self._support_images_sent = False

    def _send_update(self, websocket_url: str, payload: dict[str, object]) -> bool:
        expression = (
            "typeof window.__codexUsageHudUpdate === 'function' && "
            f"window.__codexUsageHudUpdate({json.dumps(payload, ensure_ascii=False)})"
        )
        result = send_cdp_command(
            websocket_url,
            "Runtime.evaluate",
            _runtime_expression_params(expression),
            self.timeout_seconds,
        )
        return bool(
            result.get("result", {})
            .get("result", {})
            .get("value", False)
        )


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
    settings_command_status: dict[str, object] | None = None,
    support_images: list[dict[str, str]] | None = None,
    theme: dict[str, object] | None = None,
    update_state: dict[str, object] | None = None,
    work_overlay_selectable_max: int = 6,
    desktop_overlay_dependency: dict[str, object] | None = None,
) -> RendererHudPayload:
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
    if snapshot.request.error:
        request_line = f"本次 Token 出错 | {_compact(snapshot.request.error, 120)}"
    return RendererHudPayload(
        top_line=top_line,
        request_line=request_line,
        session=_session_label(snapshot),
        model=snapshot.request.model or "n/a",
        source=snapshot.selection_source or "activity",
        request_status=snapshot.request.status or "waiting",
        last_event=_format_time(snapshot.last_event_time),
        refreshed_at=_format_time(snapshot.refreshed_at),
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
        settings=(settings or UserConfig.defaults()).to_dict(),
        active_display_mode=str(active_display_mode or "renderer"),
        settings_path=str(settings_path or ""),
        settings_bridge_url=settings_bridge_url,
        settings_command_status=settings_command_status or {},
        work_overlay_selectable_max=max(1, int(work_overlay_selectable_max or 1)),
        desktop_overlay_dependency=desktop_overlay_dependency or {},
        support_images=support_images or [],
        theme=theme or {},
        update_state=update_state or {},
        app_version=__version__,
    )


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
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        if client.update(snapshot_factory()):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)


def _runtime_expression_params(expression: str) -> dict[str, object]:
    return {
        "expression": expression,
        "returnByValue": True,
        "allowUnsafeEvalBlockedByCSP": True,
    }


def _session_label(snapshot: ParsedSession) -> str:
    title = _compact(snapshot.session_title, 36)
    if title:
        return title
    session_id = str(snapshot.session_id or "n/a")
    return session_id[-12:] if len(session_id) > 12 else session_id


def _status_label(value: str) -> str:
    labels = {
        "starting": "启动中",
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
        request_cost, _request_cost_estimated = _request_cost(snapshot)
        if request_cost is not None:
            total_cost = float(total_cost or 0.0) + float(request_cost)
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
    output_tokens = request.output_tokens or 0
    if input_tokens is None or request.estimated:
        input_tokens = max(
            int(input_tokens or 0),
            snapshot.confirmed.last_input
            + snapshot.estimate.input_tokens
            + snapshot.estimate.tool_tokens,
        )
        cached_tokens = min(snapshot.confirmed.last_cached, int(input_tokens or 0))
    cost = _COST_ESTIMATOR.calculate(
        request.model,
        input_tokens,
        cached_tokens,
        output_tokens,
        request.reasoning_tokens or 0,
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
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        estimated=snapshot.request.estimated or total_estimated or cost_estimated,
        cost_usd=cost,
        started_at=snapshot.request.started_at,
        completed_at=snapshot.request.completed_at,
    )


def _task_rows(snapshot: ParsedSession) -> list[RequestRound]:
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
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> float | None:
    return _COST_ESTIMATOR.calculate(
        snapshot.request.model,
        input_tokens,
        cached_tokens,
        output_tokens,
        reasoning_tokens,
    )


def _top_session_composition(snapshot: ParsedSession) -> str:
    confirmed = snapshot.confirmed
    input_tokens = int(confirmed.cumulative_input or 0)
    cached_tokens = max(0, min(int(confirmed.cumulative_cached or 0), input_tokens))
    uncached_tokens = max(0, input_tokens - cached_tokens)
    output_tokens = int(confirmed.cumulative_output or 0)
    reasoning_tokens = int(confirmed.cumulative_reasoning or 0)
    components = [
        (
            "↑↻",
            cached_tokens,
            _component_cost(snapshot, input_tokens=cached_tokens, cached_tokens=cached_tokens),
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
        (
            "◇",
            reasoning_tokens,
            _component_cost(snapshot, reasoning_tokens=reasoning_tokens),
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
        )
        estimated = True
    return cost, estimated


def _session_round_rows(snapshot: ParsedSession) -> list[RequestRound]:
    rows = list(getattr(snapshot, "session_request_history", []) or [])
    if rows:
        return rows
    return _task_rows(snapshot)


def _top_heavy_rounds(snapshot: ParsedSession) -> list[dict[str, str]]:
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

    def add(moment: datetime | None, title: str, detail: str, *, active: bool = False) -> None:
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
    details = {
        "title": _top_expanded_header_title(snapshot),
        "session": (
            f"会话 {snapshot.session_id[-12:]} | "
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
    "SETTINGS_COMMAND_STORAGE_KEY",
    "payload_from_snapshot",
    "remove_renderer_hud_from_pages",
    "renderer_enabled_from_env",
    "set_cost_estimator",
    "wait_for_renderer",
]
