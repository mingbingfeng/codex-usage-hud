# English Post Copy

For: Hacker News (Show HN) / Reddit (r/ChatGPTCoding, r/OpenAI) / X (Twitter)

---

## Hacker News - Show HN

**Title:** Show HN: Codex Usage HUD – live token usage and cost inside Codex, fully local

**Body:**

```
I built codex-usage-hud, a small open-source tool that shows live token
usage, cache hit rate, and estimated cost *inside* the Codex App UI.

Two problems pushed me to build it:

1. When using third-party API proxies, billing is opaque and background
   requests can quietly run for a long time before you notice the bill.
2. During long tasks the UI just spins — you can't tell if it's working,
   waiting on a tool, or stuck and needs you.

So instead of a separate floating window, it injects a HUD into the Codex
renderer itself. It shows:

- Per-session tokens (input / cached / output / reasoning / total),
  cache hit rate, and live USD estimate
- Custom daily / weekly budgets with alert thresholds
- Work status: whether a request is running, slowest tool, longest wait
- Follows the Codex light/dark theme

It's fully local — it only reads your local Codex JSONL / SQLite logs.
No telemetry, no prompt/response upload, no cloud account. Source is open
so you can audit it.

Windows installer available; macOS is still source/pip only.

https://github.com/mingbingfeng/codex-usage-hud

Feedback and issues welcome.
```

---

## Reddit

**Subreddits:** r/ChatGPTCoding, r/OpenAI, r/LanguageTechnology

**Title:** I built a HUD that shows live token usage and cost *inside* Codex App — fully local, no telemetry

**Body (same as HN, slightly more casual):**

```
I made codex-usage-hud, an open-source tool that injects a live usage HUD
directly into the Codex App renderer (not a separate floating window).

It shows:
• Session tokens (input / cached / output / reasoning / total)
• Cache hit rate and live USD estimate
• Custom daily/weekly budgets with thresholds
• Work status (running? waiting? slowest tool?)

Two pain points motivated this:
1. Third-party API relays have opaque billing and background spending is hard
   to catch until the bill arrives.
2. Long tasks in Codex just spin — you never know if it's working, stuck, or
   needs intervention.

It's fully local: reads your Codex JSONL/SQLite logs only, no telemetry,
no data upload, no cloud account. Code is open for audit.

Windows installer ready; macOS is source/pip only for now.

https://github.com/mingbingfeng/codex-usage-hud
```

---

## X (Twitter) / 微博

**Short version (280 chars):**

```
Built codex-usage-hud: live token usage, cache hit rate, and cost inside
Codex App. Renderer-injected (not a separate window), fully local, zero
telemetry, no data upload. Stop API relay "hidden spending" and blind
waiting on long tasks. Open source.

https://github.com/mingbingfeng/codex-usage-hud

#AI #Codex #OpenSource #LLM #DevTools
```

---

## Best timing to post

### Hacker News
- Weekdays (Mon-Thu), 6-9 AM Pacific Time (9-12 PM Beijing time)
- Use the "Show HN" prefix exactly

### Reddit
- r/ChatGPTCoding: most active during US daytime
- Add a comment from your account after posting to push it into "new" feed
