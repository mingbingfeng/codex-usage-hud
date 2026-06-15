"""One-shot diagnostic: dump candidate Codex error banner nodes via CDP."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from codex_usage_hud.platforms.cdp_probe import (
    evaluate_script,
    list_targets,
    pick_page_target,
)

SCRIPT = r"""
(() => {
  const norm = (s) => String(s||'').replace(/\s+/g,' ').trim();
  const visible = (n) => {
    if (!(n instanceof Element)) return false;
    const r = n.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(n);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && parseFloat(cs.opacity||'1') > 0.05;
  };
  const seen = new WeakSet();
  const trim = (s, n=1600) => {
    s = String(s||'');
    return s.length <= n ? s : s.slice(0, n) + '...[truncated]';
  };
  const describe = (n) => ({
    tag: n.tagName.toLowerCase(),
    role: n.getAttribute('role')||'',
    ariaLive: n.getAttribute('aria-live')||'',
    cls: String(n.className||'').slice(0,260),
    testid: n.getAttribute('data-testid')||'',
    text: norm(n.textContent).slice(0,240),
    outer: trim(n.outerHTML),
  });
  const sels = [
    "[role='alert']","[role='status']","[aria-live]",
    "[data-testid*='toast' i]","[data-testid*='notification' i]","[data-testid*='error' i]",
    "[class*='toast' i]","[class*='notification' i]","[class*='error' i]",
    "[class*='danger' i]","[class*='destructive' i]","[class*='alert' i]",
    "[class*='retry' i]","[class*='warning' i]","[class*='banner' i]","[class*='snack' i]"
  ];
  const selectorMatches = [];
  Array.from(document.querySelectorAll(sels.join(','))).forEach((n) => {
    if (!visible(n)) return;
    if (n.closest('#codex-usage-hud-root')) return;
    if (seen.has(n)) return;
    seen.add(n);
    selectorMatches.push(describe(n));
  });
  // keyword scan over text nodes
  const re = /(exceeded retry limit|too many requests|\b429\b|last status:\s*\d{3}|rate limit|service unavailable)/i;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const keywordMatches = [];
  const seenK = new WeakSet();
  let node;
  while ((node = walker.nextNode())) {
    const t = norm(node.nodeValue);
    if (!t || !re.test(t)) continue;
    let p = node.parentElement, depth = 0;
    while (p && depth < 8) {
      if (visible(p)) break;
      p = p.parentElement; depth += 1;
    }
    if (!p || seenK.has(p)) continue;
    seenK.add(p);
    const ancestors = [];
    let cur = p, d = 0;
    while (cur && d < 10) {
      ancestors.push({
        tag: cur.tagName.toLowerCase(),
        cls: String(cur.className||'').slice(0,260),
        role: cur.getAttribute('role')||'',
        ariaLive: cur.getAttribute('aria-live')||'',
        testid: cur.getAttribute('data-testid')||'',
      });
      cur = cur.parentElement; d += 1;
    }
    keywordMatches.push({...describe(p), ancestors, parentOuter: trim(p.parentElement?.outerHTML || '', 3000)});
  }
  return JSON.stringify({selectorMatches, keywordMatches}, null, 2);
})()
"""


def main() -> int:
    targets = list_targets(9229, 5.0)
    page = pick_page_target(targets)
    ws_url = page["webSocketDebuggerUrl"]
    print(f"# target: {page.get('title')}  url={page.get('url')}", file=sys.stderr)
    result = evaluate_script(ws_url, SCRIPT, 8.0)
    payload = result.get("result", result)
    value = payload.get("value")
    if value is None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
