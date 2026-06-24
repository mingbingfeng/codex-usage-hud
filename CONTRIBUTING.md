# Contributing to codex-usage-hud

Thanks for helping improve `codex-usage-hud`.

This repository is intentionally small, local-first, and standard-library only.
If you are planning a change, start with [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)
and keep [docs/PRIVACY.md](docs/PRIVACY.md) open whenever you touch logs, issue
reports, or screenshots.

## Before you open a PR

- Run `python -m pytest` for the default fast suite; real Tk/Qt UI lifecycle tests are marked `ui` and skipped by default.
- Run `python -m pytest -m ui` when your change touches real HUD widget behavior.
- Run `python -m compileall -q src tests tools` for a quiet syntax check.
- Keep the diff small and reviewable.
- Update docs and tests together when behavior changes.
- Do not attach raw JSONL logs, raw SQLite databases, or unredacted prompts or
  responses to issues or discussions.

## Good PR shape

- Explain why the change exists.
- Summarize the user-visible effect.
- Mention any validation you ran.
- Call out known limitations or follow-up work.

## Release hygiene

- Follow [docs/RELEASE_PLAYBOOK.md](docs/RELEASE_PLAYBOOK.md) for the release
  checklist before tagging a patch or minor release.
- Keep version tags, `CHANGELOG.md`, and release-note drafts in sync.
- If the release message changes, update the README release section too.
- Prefer one release narrative per version, not multiple competing summaries.
