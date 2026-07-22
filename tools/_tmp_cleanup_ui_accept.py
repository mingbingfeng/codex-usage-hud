#!/usr/bin/env python3
"""One-shot CDP acceptance for space-cleanup UI. Safe: no execute/delete."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from pathlib import Path

from codex_usage_hud.platforms.cdp_probe import (
    pick_page_target,
    send_cdp_command,
)

PORT = 58803
TIMEOUT = 15.0
OUT = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".")
PREFIX = "codex-usage-hud-cleanup-v35"


def list_targets() -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=3) as resp:
        return json.load(resp)


def ws_url() -> str:
    target = pick_page_target(list_targets())
    print("target", {k: target.get(k) for k in ("id", "title", "url")})
    return str(target["webSocketDebuggerUrl"])


def eval_js(ws: str, script: str, *, await_promise: bool = False):
    params = {
        "expression": script,
        "returnByValue": True,
        "awaitPromise": bool(await_promise),
        "allowUnsafeEvalBlockedByCSP": True,
    }
    result = send_cdp_command(ws, "Runtime.evaluate", params, TIMEOUT)
    body = result.get("result", result)
    if "exceptionDetails" in body:
        raise RuntimeError(body["exceptionDetails"])
    inner = body.get("result", body)
    if isinstance(inner, dict) and "value" in inner:
        return inner["value"]
    if isinstance(body, dict) and "value" in body:
        return body["value"]
    return body


def screenshot(ws: str, path: Path) -> int:
    send_cdp_command(ws, "Page.enable", {}, TIMEOUT)
    res = send_cdp_command(
        ws,
        "Page.captureScreenshot",
        {"format": "png", "fromSurface": True},
        TIMEOUT,
    )
    body = res.get("result", res)
    data = body.get("data")
    if not data and isinstance(body.get("result"), dict):
        data = body["result"].get("data")
    if not data:
        raise RuntimeError(f"no screenshot data: {str(res)[:400]}")
    path.write_bytes(base64.b64decode(data))
    return path.stat().st_size


def wait_until(ws: str, script: str, seconds: float = 25.0, interval: float = 0.4):
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            last = eval_js(ws, script)
            if last:
                return last
        except Exception as exc:  # noqa: BLE001
            last = f"err:{exc}"
        time.sleep(interval)
    raise TimeoutError(f"timeout last={last!r}")


def metrics_script() -> str:
    return r"""
(() => {
  const q = (s) => !!document.querySelector(s);
  const text = (s) => document.querySelector(s)?.textContent?.trim() || null;
  const rect = (s) => {
    const el = document.querySelector(s);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y)};
  };
  const segs = Array.from(document.querySelectorAll('.codex-usage-hud-cleanup-segments button')).map((b) => {
    const r = b.getBoundingClientRect();
    return {text: (b.textContent || '').trim(), active: b.dataset.active, w: Math.round(r.width), h: Math.round(r.height)};
  });
  const headCells = Array.from(document.querySelectorAll('.codex-usage-hud-session-head span')).map((s) => (s.textContent || '').trim());
  const dialog = document.querySelector('.codex-usage-hud-settings-dialog');
  const overflowX = Math.max(
    document.documentElement.scrollWidth - document.documentElement.clientWidth,
    document.body.scrollWidth - document.body.clientWidth,
    0,
  );
  return {
    classes: {
      workspace: q('.codex-usage-hud-cleanup-workspace'),
      pageHead: q('.codex-usage-hud-cleanup-page-head'),
      segments: q('.codex-usage-hud-cleanup-segments'),
      footer: q('.codex-usage-hud-cleanup-footer'),
      empty: q('.codex-usage-hud-cleanup-empty-state'),
      scanMark: q('.codex-usage-hud-cleanup-scan-mark'),
      summary: q('.codex-usage-hud-cleanup-summary-band'),
      cleanupRow: q('.codex-usage-hud-cleanup-row'),
      deepRow: q('.codex-usage-hud-cleanup-row[data-kind="deep"]'),
      protectedNote: q('.codex-usage-hud-cleanup-protected-note'),
      sessionHead: q('.codex-usage-hud-session-head'),
      sessionSearch: q('.codex-usage-hud-session-search'),
      danger: q('[data-tone="danger"]'),
      dangerMark: q('.codex-usage-hud-settings-confirm-danger-mark'),
      confirmSummary: q('.codex-usage-hud-settings-confirm-summary'),
      confirmNote: q('.codex-usage-hud-settings-confirm-note'),
    },
    texts: {
      emptyTitle: text('.codex-usage-hud-cleanup-empty-title'),
      footerMeta: text('.codex-usage-hud-cleanup-footer-meta'),
      summaryValue: text('.codex-usage-hud-cleanup-summary-value'),
      confirmTitle: text('.codex-usage-hud-settings-confirm-title'),
      activeTab: document.querySelector('.codex-usage-hud-settings-tab[data-active="true"]')?.dataset?.tab || null,
      section: document.querySelector('.codex-usage-hud-cleanup-segments button[data-active="true"]')?.dataset?.cleanupSection || null,
    },
    rects: {
      dialog: rect('.codex-usage-hud-settings-dialog'),
      footer: rect('.codex-usage-hud-cleanup-footer'),
      scanMark: rect('.codex-usage-hud-cleanup-scan-mark'),
      segments: rect('.codex-usage-hud-cleanup-segments'),
      summary: rect('.codex-usage-hud-cleanup-summary-band'),
      confirm: rect('.codex-usage-hud-settings-confirm-card'),
    },
    segs,
    headCells,
    overflowX,
    selectedSessions: document.querySelectorAll('.codex-usage-hud-session-row input[type="checkbox"]:checked').length,
  };
})()
"""


def open_storage(ws: str) -> dict:
    return eval_js(
        ws,
        r"""
(() => {
  const click = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return false;
    el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
    return true;
  };
  let opened = click('[data-action="settings-open"]');
  if (!opened) {
    const gears = Array.from(document.querySelectorAll('button, [role="button"]')).filter((el) => {
      const label = (el.getAttribute('aria-label') || '') + (el.getAttribute('title') || '') + (el.textContent || '');
      return label.includes('设置') || label.includes('⚙');
    });
    gears.slice(0, 3).forEach((el) => el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window})));
    opened = gears.length > 0;
  }
  let storage = click('[data-action="settings-tab"][data-tab="storage"]') || click('.codex-usage-hud-settings-tab[data-tab="storage"]');
  // ensure junk section
  click('[data-action="cleanup-section"][data-cleanup-section="junk"]');
  const modal = document.querySelector('#codex-usage-hud-settings-modal, .codex-usage-hud-settings-modal');
  const dialog = document.querySelector('.codex-usage-hud-settings-dialog');
  const r = dialog ? dialog.getBoundingClientRect() : null;
  return {
    opened,
    storage,
    modalHidden: modal ? !!(modal.hidden || modal.hasAttribute('hidden')) : null,
    workspace: !!document.querySelector('.codex-usage-hud-cleanup-workspace'),
    empty: !!document.querySelector('.codex-usage-hud-cleanup-empty-state'),
    scanMark: !!document.querySelector('.codex-usage-hud-cleanup-scan-mark'),
    footer: !!document.querySelector('.codex-usage-hud-cleanup-footer'),
    dialogSize: r ? {w: Math.round(r.width), h: Math.round(r.height)} : null,
    emptyTitle: document.querySelector('.codex-usage-hud-cleanup-empty-title')?.textContent || null,
  };
})()
""",
    )


def click_action(ws: str, action: str) -> bool:
    return bool(
        eval_js(
            ws,
            f"""
(() => {{
  const el = document.querySelector('[data-action="{action}"]');
  if (!el) return false;
  el.dispatchEvent(new MouseEvent('click', {{bubbles: true, cancelable: true, view: window}}));
  return true;
}})()
""",
        )
    )


def select_first_session(ws: str) -> dict:
    return eval_js(
        ws,
        r"""
(() => {
  const rows = Array.from(document.querySelectorAll('.codex-usage-hud-session-row, [data-session-cleanup-id]'));
  let selected = 0;
  for (const row of rows) {
    if (row.classList?.contains('blocked') || row.dataset?.blocked === 'true') continue;
    const box = row.querySelector('input[type="checkbox"]');
    if (!box || box.disabled) continue;
    if (!box.checked) {
      box.checked = true;
      box.dispatchEvent(new Event('change', {bubbles: true}));
      box.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
    }
    selected += 1;
    break;
  }
  // fallback: click row checkbox via data attribute
  if (!selected) {
    const box = document.querySelector('.codex-usage-hud-session-row input[type="checkbox"]:not(:disabled)');
    if (box) {
      box.click();
      selected = 1;
    }
  }
  return {
    selected,
    totalRows: rows.length,
    checked: document.querySelectorAll('.codex-usage-hud-session-row input[type="checkbox"]:checked, [data-session-cleanup-id] input[type="checkbox"]:checked').length,
  };
})()
""",
    )


def main() -> int:
    report: dict = {"shots": {}, "states": {}}
    ws = ws_url()

    # Wait for HUD injection
    probe = wait_until(
        ws,
        r"""
(() => {
  const root = document.querySelector('#codex-usage-hud-root, [id*="codex-usage-hud"]');
  const styleHit = Array.from(document.querySelectorAll('style')).some((s) => (s.textContent || '').includes('cleanup-scan-mark'));
  if (!root && !styleHit) return null;
  return {root: !!root, rootId: root && root.id, styleHit};
})()
""",
        seconds=40,
    )
    print("probe", probe)
    report["probe"] = probe

    # 1) Pre-scan
    open_info = open_storage(ws)
    print("open", open_info)
    report["open"] = open_info
    if not open_info.get("workspace"):
        # retry once after short wait for re-inject
        time.sleep(2)
        open_info = open_storage(ws)
        print("open_retry", open_info)
        report["open_retry"] = open_info

    m1 = eval_js(ws, metrics_script())
    report["states"]["prescan"] = m1
    p1 = OUT / f"{PREFIX}-prescan.png"
    report["shots"]["prescan"] = {"path": str(p1), "bytes": screenshot(ws, p1)}
    print("prescan metrics", json.dumps(m1, ensure_ascii=False))
    print("prescan shot", report["shots"]["prescan"])

    # 2) Scan results (safe scan only; no execute)
    if not m1.get("classes", {}).get("summary"):
        clicked = click_action(ws, "safe-cleanup-scan")
        print("clicked scan", clicked)
        try:
            wait_until(
                ws,
                r"""
(() => {
  if (document.querySelector('.codex-usage-hud-cleanup-summary-band')) return true;
  if (document.querySelector('.codex-usage-hud-cleanup-row')) return true;
  const title = document.querySelector('.codex-usage-hud-cleanup-empty-title')?.textContent || '';
  if (title && !title.includes('尚未') && !title.includes('扫描')) return true;
  return null;
})()
""",
                seconds=90,
            )
        except TimeoutError as exc:
            print("scan wait timeout", exc)
    m2 = eval_js(ws, metrics_script())
    report["states"]["scan"] = m2
    p2 = OUT / f"{PREFIX}-scan.png"
    report["shots"]["scan"] = {"path": str(p2), "bytes": screenshot(ws, p2)}
    print("scan metrics", json.dumps(m2, ensure_ascii=False))
    print("scan shot", report["shots"]["scan"])

    # 3) Session management
    click_action(ws, "cleanup-section")  # may hit first; prefer explicit
    eval_js(
        ws,
        r"""
(() => {
  const el = document.querySelector('[data-action="cleanup-section"][data-cleanup-section="sessions"]');
  if (!el) return false;
  el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
  return true;
})()
""",
    )
    # scan sessions if needed
    m_session_pre = eval_js(ws, metrics_script())
    if not m_session_pre.get("classes", {}).get("sessionHead"):
        click_action(ws, "session-cleanup-scan")
        try:
            wait_until(
                ws,
                r"""
(() => {
  if (document.querySelector('.codex-usage-hud-session-head')) return true;
  if (document.querySelector('.codex-usage-hud-session-row, [data-session-cleanup-id]')) return true;
  return null;
})()
""",
                seconds=90,
            )
        except TimeoutError as exc:
            print("session scan wait timeout", exc)
    m3 = eval_js(ws, metrics_script())
    report["states"]["sessions"] = m3
    p3 = OUT / f"{PREFIX}-sessions.png"
    report["shots"]["sessions"] = {"path": str(p3), "bytes": screenshot(ws, p3)}
    print("sessions metrics", json.dumps(m3, ensure_ascii=False))
    print("sessions shot", report["shots"]["sessions"])

    # 4) Permanent delete confirm (preview only; cancel after shot)
    sel = select_first_session(ws)
    print("select session", sel)
    report["select"] = sel
    # re-render may be needed; click a selectable checkbox path already done
    time.sleep(0.4)
    preview_clicked = click_action(ws, "session-cleanup-preview")
    print("preview clicked", preview_clicked)
    try:
        wait_until(
            ws,
            r"""
(() => {
  if (document.querySelector('.codex-usage-hud-settings-confirm-card[data-tone="danger"]')) return true;
  if (document.querySelector('.codex-usage-hud-settings-confirm-danger-mark')) return true;
  return null;
})()
""",
            seconds=60,
        )
    except TimeoutError as exc:
        print("confirm wait timeout", exc)
    m4 = eval_js(ws, metrics_script())
    report["states"]["confirm"] = m4
    p4 = OUT / f"{PREFIX}-delete-confirm.png"
    report["shots"]["confirm"] = {"path": str(p4), "bytes": screenshot(ws, p4)}
    print("confirm metrics", json.dumps(m4, ensure_ascii=False))
    print("confirm shot", report["shots"]["confirm"])

    # Cancel confirm; never execute
    click_action(ws, "session-cleanup-confirm-cancel")
    click_action(ws, "settings-close")

    # Acceptance checklist against design tokens
    def ok_prescan(m: dict) -> list[str]:
        issues = []
        c = m.get("classes") or {}
        r = m.get("rects") or {}
        if not c.get("workspace"):
            issues.append("missing workspace")
        if not c.get("segments"):
            issues.append("missing segments")
        if not c.get("footer"):
            issues.append("missing footer")
        if not c.get("empty") and not c.get("scanMark"):
            issues.append("missing empty/scan-mark")
        segs = m.get("segs") or []
        if segs:
            heights = [s.get("h") or 0 for s in segs]
            widths = [s.get("w") or 0 for s in segs]
            if any(h < 28 or h > 40 for h in heights):
                issues.append(f"segment height off: {heights}")
            if any(w < 90 for w in widths):
                issues.append(f"segment width too narrow: {widths}")
        footer = r.get("footer") or {}
        if footer.get("h") and not (45 <= footer["h"] <= 70):
            issues.append(f"footer height off: {footer.get('h')}")
        dialog = r.get("dialog") or {}
        if dialog.get("h") and dialog["h"] > 700:
            issues.append(f"dialog still too tall: {dialog.get('h')}")
        if m.get("overflowX", 0) > 0:
            issues.append(f"horizontal overflow {m.get('overflowX')}")
        return issues

    def ok_scan(m: dict) -> list[str]:
        issues = []
        c = m.get("classes") or {}
        if not c.get("summary"):
            issues.append("missing green summary band")
        if not c.get("cleanupRow") and not c.get("summary"):
            issues.append("missing cleanup rows")
        if m.get("overflowX", 0) > 0:
            issues.append(f"horizontal overflow {m.get('overflowX')}")
        return issues

    def ok_sessions(m: dict) -> list[str]:
        issues = []
        c = m.get("classes") or {}
        if not c.get("sessionHead"):
            issues.append("missing session head")
        heads = m.get("headCells") or []
        needed = ["会话", "工作目录", "最后活动", "状态", "占用"]
        for label in needed:
            if label not in heads:
                issues.append(f"missing head {label}")
        if not c.get("sessionSearch"):
            issues.append("missing session search")
        if m.get("overflowX", 0) > 0:
            issues.append(f"horizontal overflow {m.get('overflowX')}")
        return issues

    def ok_confirm(m: dict) -> list[str]:
        issues = []
        c = m.get("classes") or {}
        if not c.get("danger"):
            issues.append("missing danger tone")
        if not c.get("dangerMark"):
            issues.append("missing danger mark")
        if not c.get("confirmSummary"):
            issues.append("missing confirm summary")
        if not c.get("confirmNote"):
            issues.append("missing confirm note")
        return issues

    report["issues"] = {
        "prescan": ok_prescan(m1),
        "scan": ok_scan(m2),
        "sessions": ok_sessions(m3),
        "confirm": ok_confirm(m4),
    }
    report_path = OUT / f"{PREFIX}-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT", report_path)
    print("ISSUES", json.dumps(report["issues"], ensure_ascii=False, indent=2))
    total_issues = sum(len(v) for v in report["issues"].values())
    return 1 if total_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
