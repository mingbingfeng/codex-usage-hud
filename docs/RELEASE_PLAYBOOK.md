# Release Playbook

This repository keeps release instructions local to the worktree so future
patch releases can follow the same path without rediscovery.

## Patch release flow

1. Confirm the change scope is a small user-facing fix and not a breaking
   feature branch.
2. Bump `__version__` in both `src/codex_usage_hud/__init__.py` and
   `codex_usage_hud/__init__.py`.
3. Update `CHANGELOG.md` with a dated release section at the top.
4. Create a matching `RELEASE_NOTES_vX.Y.Z_*.md` body for GitHub Releases.
5. Update version-sensitive tests or docs that read the package version
   directly.
6. Run the validation trio:
   - `python -m unittest discover -s tests`
   - `python -m compileall -q src tools tests`
   - `python tools/pre_release_check.py`
7. Commit the release work with a Lore-style message, then create the
   annotated tag `vX.Y.Z` with a matching tag message.

## Local conventions

- Keep release notes short and specific to the user-visible fix.
- Keep historical release notes untouched once tagged.
- Keep the release checklist in this file rather than a global skill so it only
  applies to this repository.
- Use `git tag -a vX.Y.Z -m "codex-usage-hud vX.Y.Z"` unless the release says
  otherwise.
