# codex-usage-hud

![Release](https://img.shields.io/github/v/release/mingbingfeng/codex-usage-hud?include_prereleases&label=release)
[Release Notes](https://github.com/mingbingfeng/codex-usage-hud/releases/tag/v0.1.0)

> `v0.1.0 Alpha` is live. `codex-usage-hud` is a local-first, 100% offline HUD for Codex App with real-time token and cost tracking, exact cached-token discounting, and zero external dependencies.
>
> `v0.1.0 Alpha` 已发布。`codex-usage-hud` 是一个面向 Codex App 的 local-first HUD，100% 离线运行，支持实时 token 与成本追踪、精确 cached token 折扣计算，并保持 0 外部依赖。

`codex-usage-hud` 是一个面向 Codex App 的 local-first 监控工具，目标是把 `Codex App token usage`、`cached token cost`、会话级成本变化和实时状态，以轻量 HUD 的方式直接显示在本地终端里。

项目第一阶段聚焦基础骨架：零第三方依赖、纯 Python 标准库、可安装 CLI、清晰的隐私边界，以及为后续 `Codex usage tracker`、`real-time token HUD`、`cost tracker` 能力预留稳定结构。

## 为什么做这个项目

很多使用者希望看到更实时、更可信的用量信息，而不是等到事后再估算：

- 实时观察 token 消耗，而不是离线猜测。
- 准确区分普通 token 与缓存 token，并正确计算缓存费用打折。
- 保持 `local-first privacy`，不把日志、不把 prompt、不把响应内容发送到任何外部服务。
- 在不引入额外依赖的前提下，提供可审计、可扩展、可长期维护的开源实现。

## 当前阶段特性

- Local-first：设计目标是只读本地日志与本地数据库，不依赖云端。
- 实时 HUD 基础：已提供 CLI 骨架，后续可演进到真正的实时终端 HUD。
- 成本跟踪预留：围绕 `cost tracker` 和 `cached token cost` 的准确计算进行结构预设。
- 零第三方依赖：当前全部实现仅使用 Python 标准库。
- 命令入口就绪：支持 `codex-hud` 脚本入口，以及 `python -m codex_usage_hud` 模块入口。
- 隐私文档先行：仓库自带严格隐私说明，默认把安全边界写清楚。

## SEO 关键词

本项目围绕以下主题构建文档与命名，便于搜索发现：

- Codex App token usage
- Codex usage tracker
- real-time token HUD
- cost tracker
- cached token cost
- local-first privacy

## 快速说明

当前仓库处于第一阶段初始化状态，已经具备：

- 可打包的 `pyproject.toml`
- 标准 `src/` 布局
- 最小可运行 CLI
- 基础隐私文档

CLI 冒烟测试示例：

```bash
python -m src.codex_usage_hud.cli --once
```

安装后也可通过脚本入口调用：

```bash
codex-hud --once
```

## 隐私优先

这个项目的核心承诺不是“稍后补上隐私”，而是从第一天开始就以隐私为默认值：

- 只读本地 JSONL / SQLite
- 不联网
- 不上传
- 不做遥测
- 不偷偷同步任何会话内容

详细说明见 [docs/PRIVACY.md](docs/PRIVACY.md)。
