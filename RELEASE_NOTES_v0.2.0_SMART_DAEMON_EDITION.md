# 🚀 codex-usage-hud v0.2.0 · Smart Daemon Edition

> **Release draft / 发布草稿**
>
> This draft is written for GitHub Releases and can be pasted directly into the
> release body with minimal editing.
>
> 这份草稿面向 GitHub Releases，可直接复制到 Release 页面正文中，再做少量
> 发布前微调。

## ⚡ TL;DR / 一句话总结

`v0.2.0` turns `codex-usage-hud` into a **smart, invisible, persistence-ready
Windows daemon** built on the Python standard library and `ctypes` only. It
watches `Codex.exe` with ultra-light polling, hides its own console noise, and
can persist through either the Startup folder or `HKCU` registry flow.

`v0.2.0` 将 `codex-usage-hud` 进化为一个 **聪明、无声、可持久化的
Windows 守护进程**：完全基于 Python 标准库 + `ctypes`，以超低开销轮询
监控 `Codex.exe`，自动隐藏控制台黑框，并可通过“启动文件夹”或 `HKCU`
注册表路径实现开机即用。

---

## ✨ Headliners / 核心亮点

### 1) **Smart Daemon Mode**

> **Pure stdlib + `ctypes`, ultra-low power polling, and process-anchored
> lifecycle control.**

- **EN:** The daemon is driven by the Python standard library and `ctypes`
  alone, with a **250ms~500ms** low-power polling loop that keeps the footprint
  intentionally small.
- **中文：** 守护模式完全由 Python 标准库和 `ctypes` 驱动，采用
  **250ms~500ms** 的低功耗轮询节奏，把常驻开销压到极低。
- **EN:** It appears automatically when `Codex.exe` starts and exits with the
  process, so the HUD stays attached to the real session lifecycle.
- **中文：** 它会在 `Codex.exe` 启动时自动出现，并在进程关闭后自动退出，
  让 HUD 与真实会话生命周期保持同步。

```text
Codex.exe starts  ──► daemon wakes up
poll every 250-500ms ─► low-cost monitoring
Codex.exe exits    ──► daemon exits with it
```

### 2) **Zero Window Noise**

> **No black console flash. No accidental focus steal. No visual clutter.**

- **EN:** Startup uses Windows API-backed hidden-console behavior so the daemon
  can guard the session without leaving a console window behind.
- **中文：** 启动阶段调用 Windows API 隐身控制台，让守护进程在后台安静
  运行，不留下烦人的黑框。
- **EN:** The result is a genuinely silent guardian: present when needed,
  invisible when not.
- **中文：** 结果就是一个真正“无感守护”的后台代理：需要时存在，不需要时
  完全不打扰。

### 3) **One-Click Persistence**

> **Install once, choose your persistence path, and you are ready for reboot.**

- **EN:** `install.bat` now supports optional startup persistence through either
  a hidden VBS launcher in the Windows Startup folder or a registry-based
  `HKCU` Run entry.
- **中文：** `install.bat` 支持可选持久化：既可以走 Windows 启动文件夹的
  隐藏 VBS 流，也可以写入 `HKCU` Run 注册表项。
- **EN:** That makes the daemon practical for everyday use while still keeping
  the setup flow simple and explicit.
- **中文：** 这样既保留了“一键可用”的体验，也让安装过程保持清晰、可控。

```powershell
.\install.bat
```

---

## 🧪 Verification Snapshot / 验证快照

- **71 unit tests passing**
- **Standard-library-only implementation**
- **Windows-first smart daemon behavior validated**

```powershell
python -m unittest discover -s tests
```

---

## 🔧 What shipped / 本次交付了什么

- Smart daemon loop with lightweight `ctypes`-driven process watching
- Hidden-console startup behavior for zero window noise
- Optional persistence via Startup folder or `HKCU` registry
- Continued emphasis on local-only behavior and no extra dependencies
- Regression coverage expanded to keep the daemon and startup behavior honest

- 基于 `ctypes` 的智能守护循环
- 隐藏控制台启动，消除窗口噪声
- 启动文件夹 / `HKCU` 双路径可选持久化
- 继续坚持本地优先与零额外依赖
- 持续扩展回归测试，锁定守护和启动行为

---

## 🧩 Conventional Commits Draft / Conventional Commits 变更日志草稿

```text
feat(daemon): add smart daemon mode with ctypes polling
feat(windows): hide console window for zero-noise startup
feat(install): add optional Startup folder and HKCU persistence
docs(development): add contributor development guide
test(daemon): expand regression coverage to 71 passing unit tests
chore(release): prepare v0.2.0 Smart Daemon Edition
```

If you want a fuller changelog body, you can extend each commit message with a
short narrative, but the subjects above already form a clean Conventional
Commits release trail.

如果你希望把它扩展成完整的变更日志正文，可以在每条 commit subject 下再
补一两句背景说明；但上面的 subject 已经足够构成一份干净的
Conventional Commits 发布轨迹。

---

## 📎 Notes for maintainers / 给维护者的小提示

- Keep the release body punchy: this edition is about daemon presence,
  stealth, and persistence.
- Mention the Windows-centric nature clearly, while leaving room for future
  platform work.
- Keep privacy / local-first language intact. It is a core promise, not a
  marketing garnish.

- 发布正文建议保持“短、狠、准”：这一版的关键词就是守护进程、隐身和持久化。
- 请明确说明它当前是 Windows-first，同时为后续跨平台演进留出空间。
- 请保留 privacy / local-first 表述，这不是装饰，而是本项目的核心承诺。
