"""Renderer-injected Codex HUD driven through local Chrome DevTools Protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import time
from typing import Any

from .. import __version__
from ..config import UserConfig
from ..core.parser import CostEstimator, ParsedSession, RequestRound
from ..platforms.cdp_probe import (
    cdp_port_from_env,
    install_new_document_script,
    list_targets,
    pick_page_target,
    remove_new_document_script,
    send_cdp_command,
)
from ..support_assets import support_qr_payload

RENDERER_HUD_ENV = "CODEX_USAGE_HUD_RENDERER"
RENDERER_HUD_VERSION = "5"
DEFAULT_RENDERER_TIMEOUT_SECONDS = 0.45
DEFAULT_RENDERER_TARGET_CACHE_SECONDS = 2.0
DEFAULT_RENDERER_SETTINGS_POLL_SECONDS = 1.0
TOKEN_LEGEND_TEXT = "↑ 输入  ↻ 缓存  ↓ 输出\n◇ 推理  ∑ 合计  $ 金额\n◎ 缓存率  ~ 估算"
TOP_EXPANDED_HEADER_FALLBACK = "Codex 会话 / 预算"
SETTINGS_COMMAND_STORAGE_KEY = "codexUsageHudSettingsCommand:v1"

_COST_ESTIMATOR = CostEstimator()


def set_cost_estimator(estimator: CostEstimator) -> None:
    """Use the current user-configured price table for renderer formatting."""
    global _COST_ESTIMATOR
    _COST_ESTIMATOR = estimator

RENDERER_HUD_SCRIPT = r"""
(() => {
  const version = "5";
  const rootId = "codex-usage-hud-root";
  const styleId = "codex-usage-hud-style";
  const topClass = "codex-usage-hud-top";
  const requestClass = "codex-usage-hud-request";
  const warningClass = "codex-usage-hud-warning";
  const resizeHandlerName = "__codexUsageHudResize";
  const scrollHandlerName = "__codexUsageHudScroll";
  const mutationObserverName = "__codexUsageHudObserver";
  const scheduleName = "__codexUsageHudSchedule";
  const stateName = "__codexUsageHudState";
  const rafName = "__codexUsageHudRaf";
  const settleTimerName = "__codexUsageHudSettleTimers";
  const composerSettleTimerName = "__codexUsageHudComposerSettleTimer";
  const runningTimerName = "__codexUsageHudRunningTimer";
  const storageKey = "codexUsageHudPanelState:v5";
  const settingsCommandKey = "codexUsageHudSettingsCommand:v1";
  const settingsModalId = "codex-usage-hud-settings-modal";
  let topSlotCache = null;
  let pendingSyncPanels = null;
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
      #${rootId} .codex-usage-hud-resize,
      #${rootId} .codex-usage-hud-settings-button,
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
        grid-template-columns: 20px minmax(0, 1fr) 14px;
        align-items: center;
        gap: 6px;
        padding: 4px 7px 4px 8px;
      }
      #${rootId} .codex-usage-hud-collapsed[data-has-settings="true"] {
        grid-template-columns: 20px minmax(0, 1fr) 22px 14px;
      }
      #${rootId} .codex-usage-hud-expanded-shell {
        display: none;
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
        grid-template-rows: auto auto minmax(0, 1fr);
      }
      #${rootId} button {
        font: inherit;
      }
      #${rootId} .codex-usage-hud-handle,
      #${rootId} .codex-usage-hud-resize,
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
      #${rootId} .codex-usage-hud-resize {
        width: 14px;
        height: 18px;
        background: transparent;
        color: #718095;
        cursor: nwse-resize;
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
        grid-template-columns: 20px minmax(0, auto) minmax(0, 1fr) 14px;
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
        grid-template-columns: 20px minmax(0, auto) minmax(0, 1fr) 22px 14px;
      }
      #${rootId} .${requestClass} .codex-usage-hud-panel-header {
        background: #151d27;
        margin-bottom: 4px;
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
        grid-template-columns: minmax(0, 1fr) minmax(230px, 37%);
        gap: 12px;
        min-height: 100%;
      }
      #${rootId} .codex-usage-hud-top-side {
        border-radius: 5px;
        background: #141b24;
        padding: 5px 8px;
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
      #${rootId} .codex-usage-hud-price-header {
        color: #8492a6;
        font-size: 10px;
        font-weight: 700;
      }
      #${rootId} .codex-usage-hud-settings-status {
        min-width: 0;
        color: #a9bcd2;
        font-size: 11px;
      }
      #${rootId} .codex-usage-hud-settings-status[data-kind="error"] {
        color: #ffb86b;
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
      @media (max-width: 760px) {
        #${rootId} .codex-usage-hud-top-grid {
          grid-template-columns: minmax(0, 1fr);
        }
        #${rootId} .codex-usage-hud-session-meta {
          display: none;
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

  function panelMarkup(name, glyph, ariaLabel) {
    const glyphMarkup = glyph ? `<span class="codex-usage-hud-glyph">${glyph}</span>` : "";
    const settingsButtonMarkup = name === "top"
      ? `<button class="codex-usage-hud-settings-button" data-action="settings-open" title="设置" aria-label="设置">⚙</button>`
      : "";
    return `
      <div class="codex-usage-hud-panel ${PANEL[name].className}" data-panel="${name}" data-expanded="false" role="status" aria-live="polite">
        <div class="codex-usage-hud-collapsed" data-has-settings="${name === "top" ? "true" : "false"}">
          <button class="codex-usage-hud-handle" data-action="move" title="移动" aria-label="移动">⋮⋮</button>
          <button class="codex-usage-hud-main" data-action="toggle" data-has-glyph="${glyph ? "true" : "false"}" aria-label="${ariaLabel}">
            ${glyphMarkup}
            <span class="codex-usage-hud-line" data-field="${name}Line"></span>
          </button>
          ${settingsButtonMarkup}
          <button class="codex-usage-hud-resize" data-action="resize" title="调整大小" aria-label="调整大小">◢</button>
        </div>
        ${name === "top" ? topExpandedMarkup() : requestExpandedMarkup()}
      </div>
    `;
  }

  function topExpandedMarkup() {
    return `
      <div class="codex-usage-hud-expanded-shell">
        <div class="codex-usage-hud-panel-header" data-action="toggle">
          <button class="codex-usage-hud-handle" data-action="move" title="移动" aria-label="移动">⋮⋮</button>
          <div class="codex-usage-hud-title" data-action="toggle" data-field="topTitle"></div>
          <div class="codex-usage-hud-session-meta" data-field="topSession"></div>
          <button class="codex-usage-hud-settings-button" data-action="settings-open" title="设置" aria-label="设置">⚙</button>
          <button class="codex-usage-hud-resize" data-action="resize" title="调整大小" aria-label="调整大小">◢</button>
        </div>
        <div class="codex-usage-hud-top-body">
          <div class="codex-usage-hud-top-grid">
            <div>
              <section class="codex-usage-hud-section">
                <div class="codex-usage-hud-section-title">实时请求</div>
                <div class="codex-usage-hud-value mono accent" data-field="topConfirmed"></div>
              </section>
              <section class="codex-usage-hud-section">
                <div class="codex-usage-hud-value mono" data-field="topCumulative"></div>
              </section>
              <div class="codex-usage-hud-divider"></div>
              <section class="codex-usage-hud-section">
                <div class="codex-usage-hud-section-title">额度</div>
                <div class="codex-usage-hud-value warn" data-field="topBudget"></div>
              </section>
              <section class="codex-usage-hud-section">
                <div class="codex-usage-hud-section-title">当前活动</div>
                <div class="codex-usage-hud-value blue" data-field="topActivity"></div>
              </section>
            </div>
            <div class="codex-usage-hud-top-side">
              <section class="codex-usage-hud-section">
                <div class="codex-usage-hud-section-title">提醒</div>
                <div class="codex-usage-hud-value warn" data-field="topWarnings"></div>
              </section>
              <div class="codex-usage-hud-divider"></div>
              <section class="codex-usage-hud-section">
                <div class="codex-usage-hud-section-title">等待</div>
                <div class="codex-usage-hud-value" data-field="topSlow"></div>
                <div class="codex-usage-hud-value muted" data-field="topGap"></div>
              </section>
              <div class="codex-usage-hud-divider"></div>
              <section class="codex-usage-hud-section">
                <div class="codex-usage-hud-section-title">状态</div>
                <div class="codex-usage-hud-value muted" data-field="topStatus"></div>
              </section>
              <div class="codex-usage-hud-divider"></div>
              <section class="codex-usage-hud-section">
                <div class="codex-usage-hud-section-title">符号说明</div>
                <div class="codex-usage-hud-value" data-field="topLegend"></div>
              </section>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function requestExpandedMarkup() {
    return `
      <div class="codex-usage-hud-expanded-shell">
        <div class="codex-usage-hud-panel-header" data-action="toggle">
          <button class="codex-usage-hud-handle" data-action="move" title="移动" aria-label="移动">⋮⋮</button>
          <div class="codex-usage-hud-title codex-usage-hud-line" data-action="toggle" data-field="requestLineExpanded"></div>
          <div></div>
          <button class="codex-usage-hud-resize" data-action="resize" title="调整大小" aria-label="调整大小">◢</button>
        </div>
        <div class="codex-usage-hud-request-subhead"><span>轮次流水</span><span>最新在上</span></div>
        <div class="codex-usage-hud-request-list" data-field="requestRows"></div>
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
      display_mode: "auto",
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

  function settingsBridgeUrl() {
    return String(currentPayload()?.settingsBridgeUrl || "").replace(/\/+$/, "");
  }

  function settingsPathLabel() {
    return String(currentPayload()?.settingsPath || "");
  }

  function appVersion() {
    return String(currentPayload()?.appVersion || "unknown");
  }

  function thresholdText(settings) {
    const items = Array.isArray(settings.budget_thresholds) ? settings.budget_thresholds : [];
    return items.map((value) => Number(value || 0)).filter((value) => value > 0).join(",");
  }

  function priceRowsHtml(settings) {
    const prices = settings.model_prices && typeof settings.model_prices === "object" ? settings.model_prices : {};
    const entries = Object.entries(prices);
    if (!entries.length) entries.push(["gpt-5.5", { input: 5, cached_input: 0.5, output: 30, reasoning: 30 }]);
    return entries.map(([model, price]) => `
      <div class="codex-usage-hud-price-row" data-price-row="true">
        <input data-price-field="model" value="${escapeHtml(model)}" aria-label="模型">
        <input data-price-field="input" type="number" min="0" step="0.000001" value="${escapeHtml(price?.input ?? 0)}" aria-label="输入单价">
        <input data-price-field="cached_input" type="number" min="0" step="0.000001" value="${escapeHtml(price?.cached_input ?? 0)}" aria-label="缓存输入单价">
        <input data-price-field="output" type="number" min="0" step="0.000001" value="${escapeHtml(price?.output ?? 0)}" aria-label="输出单价">
        <input data-price-field="reasoning" type="number" min="0" step="0.000001" value="${escapeHtml(price?.reasoning ?? 0)}" aria-label="推理单价">
      </div>
    `).join("");
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
            ${activeTab === "settings" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-fetch-prices">拉取价格</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-export">导出 JSON</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-restart" hidden>立即重启 HUD</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-save" data-primary="true">保存</button>' : activeTab === "about" ? '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-check-update">检查更新</button> <button type="button" class="codex-usage-hud-settings-action" data-action="settings-install-update" data-primary="true">安装更新</button>' : '<button type="button" class="codex-usage-hud-settings-action" data-action="settings-close" data-primary="true">关闭</button>'}
          </div>
        </div>
      </div>
    `;
    modal.hidden = false;
  }

  function settingsPanelHtml(settings, bridge, path) {
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
          <label>HUD 显示方案</label>
          <select data-setting-key="display_mode">
            <option value="auto" ${settings.display_mode === "auto" ? "selected" : ""}>自动：优先 renderer 注入，失败回退 Tk</option>
            <option value="renderer" ${settings.display_mode === "renderer" ? "selected" : ""}>优先 renderer 注入，失败回退 Tk</option>
            <option value="tk" ${settings.display_mode === "tk" ? "selected" : ""}>Tk 窗口</option>
          </select>
        </div>
        <div class="codex-usage-hud-settings-field">
          <label>超额提醒阈值</label>
          <input data-setting-key="budget_thresholds" value="${escapeHtml(thresholdText(settings))}">
        </div>
        <div class="codex-usage-hud-settings-field">
          <label>本周补充已使用额度 USD</label>
          <input data-setting-key="weekly_adjustment_usd" type="number" min="0" step="0.01" value="${escapeHtml(settings.weekly_adjustment_usd)}">
        </div>
        <div class="codex-usage-hud-settings-field">
          <label>请作者喝咖啡链接</label>
          <input data-setting-key="support_url" value="${escapeHtml(settings.support_url)}">
        </div>
        <div class="codex-usage-hud-settings-field" style="grid-column:1/-1">
          <label>计费单价获取地址</label>
          <input data-setting-key="pricing_url" value="${escapeHtml(settings.pricing_url)}" placeholder="https://example.com/model-prices.json">
        </div>
        <div class="codex-usage-hud-price-table">
          <div class="codex-usage-hud-price-title">模型单价（USD / 1M tokens）</div>
          <div class="codex-usage-hud-price-header">
            <div>模型</div><div>输入</div><div>缓存</div><div>输出</div><div>推理</div>
          </div>
          <div data-price-rows="true">${priceRowsHtml(settings)}</div>
          <button type="button" class="codex-usage-hud-settings-action" data-action="settings-add-model" style="justify-self:start;margin-top:6px">添加模型</button>
        </div>
        <div class="codex-usage-hud-settings-status" style="grid-column:1/-1">配置文件：${escapeHtml(path || "未提供")} ${bridge ? "" : "（桥接未连接）"}</div>
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

  function showSettingsRestartPrompt(message, kind = "error") {
    setSettingsStatus(`${message} 是否立即重启 HUD？`, kind);
    setSettingsRestartVisible(true);
  }

  function applySettingsCommandStatus(payload) {
    const modal = document.getElementById(settingsModalId);
    const status = payload?.settingsCommandStatus;
    if (!modal || modal.hidden || !status || typeof status !== "object") return;
    setSettingsStatus(status.message || "", status.kind || "");
    setSettingsRestartVisible(!!status.restartVisible);
  }

  function submitSettingsCommand(command, pendingMessage) {
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
    setSettingsStatus(pendingMessage || "设置命令已提交，等待 HUD daemon 写入本地配置...");
    setSettingsRestartVisible(false);
    return true;
  }

  function collectSettingsForm() {
    const modal = document.getElementById(settingsModalId);
    const settings = hudSettingsFromPayload();
    const read = (key) => modal?.querySelector(`[data-setting-key="${key}"]`)?.value;
    const numberValue = (key, fallback) => {
      const value = Number(read(key));
      return Number.isFinite(value) && value >= 0 ? value : fallback;
    };
    const modelPrices = {};
    modal?.querySelectorAll("[data-price-row='true']").forEach((row) => {
      const model = String(row.querySelector("[data-price-field='model']")?.value || "").trim();
      if (!model) return;
      const field = (name) => {
        const value = Number(row.querySelector(`[data-price-field="${name}"]`)?.value);
        return Number.isFinite(value) && value >= 0 ? value : 0;
      };
      modelPrices[model] = {
        input: field("input"),
        cached_input: field("cached_input"),
        output: field("output"),
        reasoning: field("reasoning"),
      };
    });
    return {
      ...settings,
      daily_budget_usd: numberValue("daily_budget_usd", settings.daily_budget_usd),
      weekly_budget_usd: numberValue("weekly_budget_usd", settings.weekly_budget_usd),
      daily_reset_time: String(read("daily_reset_time") || settings.daily_reset_time),
      weekly_reset_weekday: Number(read("weekly_reset_weekday") ?? settings.weekly_reset_weekday),
      weekly_reset_time: String(read("weekly_reset_time") || settings.weekly_reset_time),
      display_mode: String(read("display_mode") || settings.display_mode),
      pricing_url: String(read("pricing_url") || "").trim(),
      budget_thresholds: String(read("budget_thresholds") || "")
        .split(",")
        .map((item) => Number(item.trim()))
        .filter((item) => Number.isFinite(item) && item > 0),
      weekly_adjustment_usd: numberValue("weekly_adjustment_usd", settings.weekly_adjustment_usd),
      support_url: String(read("support_url") || "").trim(),
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

  function checkUpdateFromModal() {
    submitSettingsCommand(
      { action: "checkUpdate" },
      "检查更新请求已提交，等待 HUD daemon 查询 GitHub Release..."
    );
  }

  function installUpdateFromModal() {
    submitSettingsCommand(
      { action: "installUpdate" },
      "安装更新请求已提交，等待 HUD daemon 下载并启动安装器..."
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

  function addModelPriceRow() {
    const rows = document.querySelector(`#${settingsModalId} [data-price-rows="true"]`);
    if (!rows) return;
    const row = document.createElement("div");
    row.className = "codex-usage-hud-price-row";
    row.dataset.priceRow = "true";
    row.innerHTML = `
      <input data-price-field="model" value="" aria-label="模型">
      <input data-price-field="input" type="number" min="0" step="0.000001" value="0" aria-label="输入单价">
      <input data-price-field="cached_input" type="number" min="0" step="0.000001" value="0" aria-label="缓存输入单价">
      <input data-price-field="output" type="number" min="0" step="0.000001" value="0" aria-label="输出单价">
      <input data-price-field="reasoning" type="number" min="0" step="0.000001" value="0" aria-label="推理单价">
    `;
    rows.appendChild(row);
    row.querySelector("input")?.focus?.();
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
      if (action.dataset.action === "settings-save") {
        event.preventDefault();
        event.stopPropagation();
        void saveSettingsFromModal();
        return;
      }
      if (action.dataset.action === "settings-restart") {
        event.preventDefault();
        event.stopPropagation();
        void restartHudFromModal();
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
      event.preventDefault();
      event.stopPropagation();
      const expanded = panel.dataset.expanded !== "true";
      panel.dataset.expanded = String(expanded);
      setPanelState(name, { expanded });
      syncPosition();
      syncPositionSettled();
    });
    root.addEventListener("pointerdown", (event) => {
      const action = event.target?.closest?.("[data-action='move'], [data-action='resize']");
      if (!action || !root.contains(action)) return;
      const panel = action.closest("[data-panel]");
      const name = panel?.dataset.panel;
      if (!name || !PANEL[name]) return;
      event.preventDefault();
      event.stopPropagation();
      beginGesture(event, name, action.dataset.action);
    });
  }

  function beginGesture(event, name, action) {
    const panel = document.querySelector(`#${rootId} [data-panel="${name}"]`);
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    const expanded = panel.dataset.expanded === "true";
    const startState = getPanelState(name);
    const startHeight = desiredHeight(name, startState, expanded, rect.height);
    const startAnchor = name === "top"
      ? topAnchor(startHeight, startState.width)
      : requestAnchor(startHeight, startState.width);
    const gesture = {
      action,
      name,
      expanded,
      anchor: startAnchor,
      startX: event.clientX,
      startY: event.clientY,
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
    };
    const move = (nextEvent) => {
      const dx = nextEvent.clientX - gesture.startX;
      const dy = nextEvent.clientY - gesture.startY;
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
        const maxWidth = gesture.anchor.maxWidth || Math.max(minWidth, innerWidth - gesture.left - 8);
        const width = clamp(gesture.width + dx, minWidth, Math.max(minWidth, maxWidth));
        let height = gesture.height;
        let top = gesture.top;
        if (gesture.expanded) {
          if (name === "request") {
            const bottom = gesture.top + gesture.height;
            height = clamp(gesture.height - dy, minHeight, Math.max(minHeight, bottom - 8));
            top = bottom - height;
          } else {
            height = clamp(gesture.height + dy, minHeight, Math.max(minHeight, innerHeight - gesture.top - 8));
          }
        }
        applyRect(panel, gesture.left, top, width, height);
        const current = getPanelState(name);
        const patch = manualPatchFor(name, gesture.left, top, width, gesture.anchor, {
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
    const surface = document.querySelector('[data-testid="app-shell-header-context-menu-surface"]');
    const surfaceHeader = surface?.closest?.("header.app-header-tint, header, .app-header-tint");
    if (visible(surfaceHeader)) return surfaceHeader;
    return candidateHeaders()
      .map((node, index) => ({ node, index, score: scoreHeader(node) }))
      .sort((left, right) => (right.score - left.score) || (left.index - right.index))[0]?.node || null;
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
    return visible(best) ? best : null;
  }

  function composerRect() {
    const best = composerElement();
    return visible(best) ? best.getBoundingClientRect() : null;
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
      if (node.classList.contains("codex-usage-hud-line")) {
        if (field === "requestLine" || field === "requestLineExpanded") {
          setAnimatedLineText(node, value);
        } else {
          cancelNumericAnimation(node);
          applyLineText(node, value);
        }
        return;
      }
      node.textContent = value || "";
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

  function renderTopDetails(root, payload) {
    const details = payload?.topDetails || {};
    const copies = payload?.topCopies || {};
    const mapping = {
      topTitle: details.title || "Codex 会话 / 预算",
      topSession: details.session || "",
      topConfirmed: details.confirmed || "",
      topCumulative: details.cumulative || "",
      topBudget: details.budget || "",
      topWarnings: details.warnings || "",
      topActivity: details.activity || "",
      topSlow: details.slow || "",
      topGap: details.gap || "",
      topStatus: details.status || "",
      topLegend: details.legend || "",
    };
    for (const [field, value] of Object.entries(mapping)) setText(root, field, value);
    configureCopy(root, "topSlow", copies.slow || "", "点击复制最慢工具命令", "slow");
    configureCopy(root, "topGap", copies.gap || "", "点击复制最长等待详情", "gap");
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
    const root = ensureRoot();
    if (!root) return false;
    setText(root, "topLine", nextPayload?.topLine || "codex-usage-hud 等待数据");
    setText(root, "requestLine", nextPayload?.requestLine || "本次请求 等待");
    setText(root, "requestLineExpanded", nextPayload?.requestLine || "最近模型请求轮次");
    root.querySelectorAll('[data-field="topLine"]').forEach((node) => {
      node.classList.toggle(warningClass, !!nextPayload?.warning);
    });
    root.querySelectorAll('[data-field="requestLine"], [data-field="requestLineExpanded"]').forEach((node) => {
      node.classList.toggle(warningClass, nextPayload?.requestStatus === "error");
    });
    renderTopDetails(root, nextPayload || {});
    renderRequestRows(root, nextPayload?.requestRows || [], nextPayload?.requestRowDetails || []);
    applySettingsCommandStatus(nextPayload || {});
    syncPosition();
    syncPositionSettled();
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
    cancelAnimationFrame(window[rafName] || 0);
    clearInterval(window[runningTimerName] || 0);
    clearTimeout(window[composerSettleTimerName] || 0);
    for (const timer of (window[settleTimerName] || [])) clearTimeout(timer);
    delete window[mutationObserverName];
    delete window[resizeHandlerName];
    delete window[scrollHandlerName];
    delete window[scheduleName];
    delete window[rafName];
    delete window[runningTimerName];
    delete window[composerSettleTimerName];
    delete window[settleTimerName];
    delete window.__codexUsageHudUpdate;
    delete window.__codexUsageHudRemove;
    return true;
  };

  window[scheduleName] = () => scheduleForPanels(Object.keys(PANEL), { invalidateTop: true });
  window[resizeHandlerName] = window[scheduleName];
  window[scrollHandlerName] = () => scheduleForPanels(["request"]);
  window.addEventListener("resize", window[resizeHandlerName]);
  window.addEventListener("scroll", window[scrollHandlerName], true);
  window[mutationObserverName] = new MutationObserver((mutations) => {
    const touchesHeader = mutations.some(mutationTouchesHeaderScope);
    if (touchesHeader) {
      scheduleForPanels(Object.keys(PANEL), { invalidateTop: true });
      return;
    }
    if (!mutations.some(mutationTouchesComposerScope)) return;
    if (mutations.some(mutationTouchesTextInput)) {
      scheduleRequestAfterComposerSettles();
      return;
    }
    scheduleForPanels(["request"]);
  });
  window[mutationObserverName].observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["aria-label", "title", "data-thread-title", "class"],
  });
  const boot = () => {
    const state = window[stateName];
    if (state?.payload) {
      window.__codexUsageHudUpdate(state.payload);
    } else {
      ensureRoot();
      syncPosition();
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
    top_details: dict[str, str] = field(default_factory=dict)
    top_copies: dict[str, str] = field(default_factory=dict)
    request_rows: list[str] = field(default_factory=list)
    request_row_details: list[dict[str, object]] = field(default_factory=list)
    settings: dict[str, object] = field(default_factory=dict)
    settings_path: str = ""
    settings_bridge_url: str = ""
    settings_command_status: dict[str, object] = field(default_factory=dict)
    support_images: list[dict[str, str]] = field(default_factory=list)
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
            "topCopies": dict(self.top_copies),
            "requestRows": list(self.request_rows),
            "requestRowDetails": [dict(item) for item in self.request_row_details],
            "settings": dict(self.settings),
            "settingsPath": self.settings_path,
            "settingsBridgeUrl": self.settings_bridge_url,
            "settingsCommandStatus": dict(self.settings_command_status),
            "supportImages": [dict(item) for item in self.support_images],
            "appVersion": self.app_version,
        }


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

    def update(
        self,
        snapshot: ParsedSession,
        *,
        settings: UserConfig | None = None,
        settings_path: Path | str | None = None,
        settings_bridge_url: str = "",
        settings_command_status: dict[str, object] | None = None,
    ) -> bool:
        support_images = [] if self._support_images_sent else support_qr_payload()
        payload = payload_from_snapshot(
            snapshot,
            settings=settings,
            settings_path=settings_path,
            settings_bridge_url=settings_bridge_url,
            settings_command_status=settings_command_status,
            support_images=support_images,
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
        if not self.enabled:
            return
        try:
            target = self._page_target(force=True)
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            if websocket_url:
                send_cdp_command(
                    websocket_url,
                    "Runtime.evaluate",
                    _runtime_expression_params(
                        "typeof window.__codexUsageHudRemove === 'function' && "
                        "window.__codexUsageHudRemove()"
                    ),
                    self.timeout_seconds,
                )
                remove_new_document_script(
                    websocket_url,
                    self._script_identifier,
                    self.timeout_seconds,
                )
        except Exception:
            return
        finally:
            self._clear_target_cache(clear_script=True)

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
    settings_path: Path | str | None = None,
    settings_bridge_url: str = "",
    settings_command_status: dict[str, object] | None = None,
    support_images: list[dict[str, str]] | None = None,
) -> RendererHudPayload:
    session_cost = _session_cost(snapshot)
    top_line = (
        f"{_top_session_usage_summary(snapshot, session_cost)} | "
        f"今日 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)} | "
        f"本周 {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)} | "
        f"状态 {_budget_status(snapshot)}"
    )
    if snapshot.error and snapshot.status in {"missing", "error"}:
        top_line = f"{_status_label(snapshot.status)} | {_compact(snapshot.error, 120)}"
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
            or snapshot.budget_warnings
        ),
        top_details=_top_details(snapshot, session_cost),
        top_copies=_top_copy_texts(snapshot),
        request_rows=_request_rows(snapshot),
        request_row_details=_request_row_details(snapshot),
        settings=(settings or UserConfig.defaults()).to_dict(),
        settings_path=str(settings_path or ""),
        settings_bridge_url=settings_bridge_url,
        settings_command_status=settings_command_status or {},
        support_images=support_images or [],
        app_version=__version__,
    )


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


def _top_session_usage_summary(snapshot: ParsedSession, session_cost: float | None) -> str:
    total_tokens = int(snapshot.confirmed.cumulative_total or 0)
    total_cost = session_cost
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
    return f"本会话 {_format_usage_money(total_tokens, total_cost)}/{_session_cache_hit_rate_label(snapshot)}"


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
    if value is None:
        return "◎-"
    clamped = max(0.0, min(float(value), 1.0))
    return f"◎{'~' if estimated else ''}{clamped:.0%}"


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


def _round_cache_hit_rate_label(item: RequestRound) -> str:
    input_tokens = item.input_tokens
    if input_tokens is None or int(input_tokens) <= 0:
        return _format_rate_marker(None, item.estimated)
    cached_tokens = max(0, min(int(item.cached_tokens or 0), int(input_tokens)))
    return _format_rate_marker(cached_tokens / max(1, int(input_tokens)), item.estimated)


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
    index_width: int | None = None,
    money_width: int | None = None,
    total_width: int | None = None,
    now: datetime | None = None,
) -> str:
    parts = _round_entry_parts(
        item,
        fallback_model,
        index_width=index_width,
        money_width=money_width,
        total_width=total_width,
        now=now,
    )
    return f"{parts['prefix']}{parts['time']}{parts['suffix']}"


def _round_entry_parts(
    item: RequestRound,
    fallback_model: str,
    *,
    index_width: int | None = None,
    money_width: int | None = None,
    total_width: int | None = None,
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
    if index_width is not None:
        index_text = index_text.rjust(index_width)
    if money_width is not None:
        money_text = money_text.rjust(money_width)
    if total_width is not None:
        total_text = total_text.rjust(total_width)
    return {
        "prefix": f"#{index_text} {money_text} ",
        "time": time_text,
        "suffix": (
            f" ↑{_short_num(item.input_tokens)} "
            f"{_round_cache_hit_rate_label(item)} "
            f"↓{_short_num(item.output_tokens)} ◇{_short_num(item.reasoning_tokens)} "
            f"↻{_short_num(item.cached_tokens)} ∑{total_text}"
        ),
    }


def _round_entry_widths(
    rows: list[RequestRound],
    fallback_model: str,
) -> tuple[int, int, int]:
    index_width = max((len(str(item.index)) for item in rows), default=1)
    money_width = 1
    total_width = 1
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
    return index_width, money_width, total_width


def _request_rows(snapshot: ParsedSession) -> list[str]:
    display_rows, index_width, money_width, total_width = _display_request_rows(snapshot)
    return [
        _round_entry(
            item,
            snapshot.request.model,
            index_width=index_width,
            money_width=money_width,
            total_width=total_width,
        )
        for item in display_rows
    ]


def _display_request_rows(
    snapshot: ParsedSession,
) -> tuple[list[RequestRound], int, int, int]:
    rows = _task_rows(snapshot)[-30:]
    if not rows:
        rows = [_round_from_snapshot(snapshot)]
    display_rows = list(reversed(rows))
    index_width, money_width, total_width = _round_entry_widths(
        display_rows,
        snapshot.request.model,
    )
    return display_rows, index_width, money_width, total_width


def _request_row_details(snapshot: ParsedSession) -> list[dict[str, object]]:
    display_rows, index_width, money_width, total_width = _display_request_rows(snapshot)
    details: list[dict[str, object]] = []
    for item in display_rows:
        parts = _round_entry_parts(
            item,
            snapshot.request.model,
            index_width=index_width,
            money_width=money_width,
            total_width=total_width,
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


def _format_budget(snapshot: ParsedSession) -> str:
    day_ratio = (
        snapshot.today_cost_usd / snapshot.daily_limit_usd
        if snapshot.daily_limit_usd > 0
        else 0.0
    )
    week_ratio = (
        snapshot.week_cost_usd / snapshot.weekly_limit_usd
        if snapshot.weekly_limit_usd > 0
        else 0.0
    )
    text = (
        f"今日累计  {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)}  "
        f"额度 {snapshot.today_cost_usd:.2f}/{snapshot.daily_limit_usd:.0f} USD "
        f"({day_ratio:.0%})  起点 {_format_start(snapshot.day_start)}\n"
        f"本周累计  {_format_usage_money(snapshot.week_tokens, snapshot.week_cost_usd)}  "
        f"额度 {snapshot.week_cost_usd:.2f}/{snapshot.weekly_limit_usd:.0f} USD "
        f"({week_ratio:.0%})  起点 {_format_start(snapshot.week_start)}"
    )
    if snapshot.week_before_today_cost_usd > 0:
        text += (
            "\n本周拆分  "
            f"今日前 {_format_usage_money(snapshot.week_before_today_tokens, snapshot.week_before_today_cost_usd)}"
            f" + 当前日窗 {_format_usage_money(snapshot.today_tokens, snapshot.today_cost_usd)}"
        )
    if snapshot.week_adjustment_usd > 0:
        text += f" + 人工补充 {_format_money(snapshot.week_adjustment_usd)}"
    return text


def _format_warnings(snapshot: ParsedSession) -> str:
    if snapshot.budget_error:
        return snapshot.budget_error
    if snapshot.budget_warnings:
        return "提醒  " + "；".join(snapshot.budget_warnings)
    return "提醒  暂无额度提醒"


def _format_notice(snapshot: ParsedSession) -> str:
    notice = _format_warnings(snapshot)
    if snapshot.error:
        notice = f"{notice}  |  错误 {_compact(snapshot.error, 80)}"
    if snapshot.request.error:
        notice = f"{notice}  |  请求 {_compact(snapshot.request.error, 80)}"
    return notice


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


def _top_details(snapshot: ParsedSession, session_cost: float | None) -> dict[str, str]:
    confirmed = snapshot.confirmed
    return {
        "title": _top_expanded_header_title(snapshot),
        "session": (
            f"会话 {snapshot.session_id[-12:]} | "
            f"行 {snapshot.line_count} | 确认 {snapshot.token_events}"
        ),
        "confirmed": "本次请求  " + _request_counter(snapshot),
        "cumulative": (
            "累计确认  "
            f"总 {confirmed.cumulative_total:,}   "
            f"输入 {confirmed.cumulative_input:,}   "
            f"缓存 {confirmed.cumulative_cached:,}   "
            f"缓存率 {_session_cache_hit_rate_label(snapshot)}\n"
            f"输出 {confirmed.cumulative_output:,}   "
            f"推理 {confirmed.cumulative_reasoning:,}   "
            f"金额 {_format_money(session_cost)}"
        ),
        "budget": _format_budget(snapshot),
        "warnings": _format_notice(snapshot),
        "activity": (
            f"{_activity_label(snapshot.activity.kind)}："
            f"{_compact(snapshot.activity.detail, 135)}"
        ),
        "legend": TOKEN_LEGEND_TEXT,
        "slow": _format_slow_panel(snapshot),
        "gap": _format_gap_panel(snapshot),
        "status": (
            f"{_budget_status(snapshot)}\n"
            f"最后 {_format_time(snapshot.last_event_time)}  刷新 {_format_time(snapshot.refreshed_at)}"
        ),
    }


__all__ = [
    "DEFAULT_RENDERER_TIMEOUT_SECONDS",
    "RENDERER_HUD_ENV",
    "RENDERER_HUD_SCRIPT",
    "RendererHudClient",
    "RendererHudPayload",
    "SETTINGS_COMMAND_STORAGE_KEY",
    "payload_from_snapshot",
    "renderer_enabled_from_env",
    "set_cost_estimator",
    "wait_for_renderer",
]
