# codex-usage-hud

[Tag](https://img.shields.io/github/v/tag/mingbingfeng/codex-usage-hud?label=tag)
[Release Notes](RELEASE_NOTES_v0.2.0_SMART_DAEMON_EDITION.md)
[Changelog](CHANGELOG.md)
[Privacy](docs/PRIVACY.md)

> `v0.2.0` is the current git tag in this repository. `codex-usage-hud` is a local-first, 100% offline HUD for Codex App with real-time token and cost tracking, exact cached-token discounting, and zero external dependencies.
>
> `v0.2.0` 是当前标签版本。`codex-usage-hud` 是一个面向 Codex App 的 local-first HUD，100% 离线运行，支持实时 token 与成本追踪、精确 cached token 折扣计算，并保持 0 外部依赖。

`codex-usage-hud` is a local-first monitoring tool for Codex App. It reads local JSONL and SQLite logs, tracks token usage and cost, and keeps the current session visible without sending data to external services.

`codex-usage-hud` 是一个面向 Codex App 的本地监控工具：只读取本地 JSONL / SQLite 数据，跟踪 token 与成本变化，并把当前会话状态保留在本地 HUD 里，不向外部服务发送任何内容。

## What this repo is / 这是什么

- An installable Python package with a standard `src/` layout.
- A one-shot CLI snapshot and a live Tk HUD for local usage review.
- A Windows-first daemon mode that can attach the HUD when Codex starts.
- A repo that keeps tags, changelog, release notes, and docs in one maintenance loop.

- 一个可安装的 Python 包，使用标准 `src/` 布局。
- 一个可一次性查看的 CLI 快照，以及一个本地运行的 Tk HUD。
- 一个 Windows 优先的守护进程模式，可在 Codex 启动时自动挂上 HUD。
- 一个把 tag、changelog、release notes 和文档放在同一维护闭环里的仓库。

## Start here / 快速开始

### Requirements / 环境要求

- Python 3.10 or newer.
- A local Codex App data directory with session logs.
- Windows is required for daemon mode; snapshot and HUD usage still stay local.

- Python 3.10 或更高版本。
- 本地可访问的 Codex App 数据目录和会话日志。
- 守护模式仅限 Windows；但快照和 HUD 读取仍然是本地完成的。

### Install / 安装

#### Windows

Run the bundled installer from the repository root:

```powershell
.\install.bat
```

The script creates a local `.venv`, installs the package in editable mode, offers optional PATH registration, and can register startup persistence if you choose it.

从仓库根目录运行这个脚本：

```powershell
.\install.bat
```

脚本会创建本地 `.venv`、以 editable 模式安装当前项目、可选注册 PATH，并按需提供开机自启动注册。

#### Manual install / 手动安装

```bash
python -m pip install -e .
```

### First run / 首次运行

```bash
codex-hud --once
```

or / 或：

```bash
python -m codex_usage_hud --once
```

### Common commands / 常用命令

| Surface | Command | When to use |
| --- | --- | --- |
| Snapshot | `codex-hud --once` | Print the current local usage summary and exit. |
| Live HUD | `codex-hud` | Open the interactive HUD and keep it refreshing. |
| Windows daemon | `codex-hud --daemon` | Wait for Codex, then attach the HUD automatically. |
| Stop | `codex-hud --stop` | Clear the local PID lock and stop the running HUD. |

## Mental model / 心智模型

- The app resolves a session from explicit file path, session id, active conversation, or activity-based fallback.
- It parses local JSONL and SQLite logs into a single usage snapshot.
- It keeps privacy boundaries local by design: no network, no telemetry, no upload.
- It renders either a CLI snapshot or a Tk HUD from the same local data model.

- 它会从显式文件、会话 id、当前活动会话或活动回退路径里定位一个会话。
- 它把本地 JSONL 和 SQLite 日志解析成同一份用量快照。
- 它的隐私边界默认只在本地：不联网、不遥测、不上传。
- 它会基于同一份本地数据模型渲染 CLI 快照或 Tk HUD。

## Project structure / 仓库结构

- `src/codex_usage_hud/` runtime code and entry points.
- `tests/` regression coverage for parser, pricing, platforms, daemon, and UI behavior.
- `docs/` contributor and privacy notes.
- `.github/ISSUE_TEMPLATE/` issue guardrails.
- `CHANGELOG.md` canonical history.
- `RELEASE_NOTES_v*.md` version-specific release body drafts.

- `src/codex_usage_hud/` 运行时代码和入口。
- `tests/` 覆盖解析、计费、平台、守护进程和 UI 行为的回归测试。
- `docs/` 贡献与隐私说明。
- `.github/ISSUE_TEMPLATE/` 问题反馈约束。
- `CHANGELOG.md` 长期维护的正式历史。
- `RELEASE_NOTES_v*.md` 按版本组织的 release 正文草稿。

## Releases and maintenance / 发布与维护

- Tags follow semantic versioning: `vX.Y.Z`.
- Current latest tag: `v0.2.0`.
- `v0.1.0` was the first alpha release; `v0.2.0` is the Smart Daemon Edition.
- `CHANGELOG.md` is the long-form history that should stay aligned with tags.
- The release-note files are maintained as ready-to-paste GitHub release bodies.
- When a release lands, update the version string, changelog, README release section, and release note file together.

- 标签遵循语义化版本：`vX.Y.Z`。
- 当前最新 tag：`v0.2.0`。
- `v0.1.0` 是首个 alpha 版本；`v0.2.0` 是 Smart Daemon Edition。
- `CHANGELOG.md` 是长期历史记录，应该和 tag 保持一致。
- release note 文件用于维护可直接贴到 GitHub Release 的正文。
- 每次发布时，建议同步更新版本号、changelog、README 发布区和 release note 文件。

## Development / 开发

- Read [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the contributor flow.
- The standard validation path is `python -m unittest discover -s tests`.
- A fast syntax check is `python -m compileall src tests`.
- Keep diffs small and reviewable, and update tests when behavior changes.

- 贡献者流程请看 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。
- 标准验证命令是 `python -m unittest discover -s tests`。
- 快速语法检查命令是 `python -m compileall src tests`。
- 请保持 diff 小而清晰，并在行为变化时同步更新测试。

## Privacy / 隐私

- Read [docs/PRIVACY.md](docs/PRIVACY.md) before sharing logs or screenshots.
- Never attach raw JSONL logs, raw SQLite databases, or unredacted prompts/responses in issues.
- Use the bug template when reporting problems so sensitive fields stay redacted.

- 在分享日志或截图前，请先阅读 [docs/PRIVACY.md](docs/PRIVACY.md)。
- 提交 issue 时不要附上原始 JSONL、原始 SQLite，或未脱敏的 prompt / response。
- 报 bug 时请使用 issue 模板，保持敏感字段脱敏。

## Contributing / 贡献

- Start with [CONTRIBUTING.md](CONTRIBUTING.md).
- If a change affects behavior, update tests and docs together.
- If a change affects release messaging, update the release note draft and changelog together.

- 请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 任何会改变行为的修改都应同步更新测试与文档。
- 任何会影响发布叙述的修改都应同步更新 release note 和 changelog。

## License / 许可证

- MIT License.
