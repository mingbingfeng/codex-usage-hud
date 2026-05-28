# 🚀 codex-usage-hud v0.1.0 Alpha

**English**  
`codex-usage-hud` is now public in its first alpha form: a local-first usage
HUD for Codex App that stays 100% offline, tracks tokens and cost in real time,
computes cached-token discounts precisely, and ships with zero external
dependencies.

**中文**  
`codex-usage-hud` 现已进入首次公开 Alpha：它是一个面向 Codex App 的
local-first 用量 HUD，100% 离线运行，实时追踪 token 与成本，精确计算
cached token 折扣，并且保持 0 外部依赖。

## ✨ Highlights / 亮点

- 🔒 **Local-first, 100% offline safe**
  - All parsing, aggregation, and display stay on your machine.
  - 所有解析、聚合与展示都只在本地完成。
- 📊 **Real-time Token & Cost Tracker**
  - Live JSONL + SQLite SSE tracking keeps the HUD close to the actual session
    state.
  - JSONL + SQLite SSE 双通道联动，让 HUD 尽量贴近真实会话状态。
- 💸 **Cached Token Discount, calculated correctly**
  - Model-aware pricing includes cached-input discounts, output, and reasoning
    tokens.
  - 支持模型感知定价，准确处理 cached input 折扣、输出 token 和推理 token。
- 🧩 **Zero Dependency**
  - Pure Python standard library, easy to audit and easy to ship.
  - 纯 Python 标准库，实现可审计、可分发、无额外依赖。
- 🖥️ **Cross-platform HUD**
  - Windows / macOS / Linux path discovery, active-session tracking, docking,
    drawer expansion, and auto-scroll marquee behavior.
  - 支持 Windows / macOS / Linux 的路径发现、活动会话跟踪、吸附、抽屉展开
    和长文本滚动动效。
- 🧪 **Regression locked**
  - 39 unit tests pin the parser, pricing, platform adapters, active session
    logic, and UI behavior.
  - 39 项单元测试锁死解析、计费、平台适配、活动会话逻辑与 UI 行为。

## ⚠️ PRIVACY WARNING

> **Do not submit raw Codex JSONL logs, raw SQLite databases, or unredacted
> prompts/responses.**
>
> If you need help, share only minimal redacted snippets or synthetic repro
> data.
>
> **严禁提交原始日志。**
> 如果需要协助，请只提供最小化、脱敏后的片段，或自造的复现样例。
>
> See [docs/PRIVACY.md](docs/PRIVACY.md) for the full redaction rules.

## 🧭 What changed in this alpha / 本次 Alpha 有什么

- From a single-file prototype to a structured `src/` package with `core/`,
  `platforms/`, and `ui/` layers.
- Built a resilient JSONL parser that tolerates partial trailing writes and
  keeps working on live session files.
- Added a read-only SQLite SSE state machine to follow live request creation,
  progress, and completion without locking local databases.
- Added active conversation discovery so the HUD can follow the current Codex
  session more naturally.
- Added smart docking, expanded detail drawers, draggable windows, and animated
  long-text/numeric transitions to the Tk HUD.

## 📚 Reference / 参考

- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Privacy policy: [docs/PRIVACY.md](docs/PRIVACY.md)
