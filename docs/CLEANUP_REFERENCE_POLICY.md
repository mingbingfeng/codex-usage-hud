# Cleanup reference policy

The Space Cleanup screen is an audited local-cleanup workflow: scan first,
review the exact categories and their sizes, then confirm. It deliberately does
not turn a broad Windows command into an unreviewable one-click delete.

## External references

- [Chris Titus Tech WinUtil](https://github.com/ChrisTitusTech/winutil/blob/50be7390e586f664df76d7fed41fc3c39252288c/config/tweaks.json#L1069-L1093)
  is MIT licensed. Its Temp cleanup coverage informed the explicit user Temp
  and Windows system Temp inventory targets. The HUD implementation is an
  independent Python implementation with revision, path, reparse-point,
  process, and one-use-confirmation checks; no WinUtil script is embedded.
- [BleachBit](https://github.com/bleachbit/bleachbit) is GPL-3.0. It is used
  only as a behavior/safety comparison: preview before deletion, responsive
  cancellation, no junction/symlink traversal, and best-effort skipping of
  busy or inaccessible files. No BleachBit code, XML cleaner definition, or
  protected-path list is copied into this MIT project.

## Product boundaries

Standard scan-and-confirm cleanup can include only exact, user-auditable paths:

- HUD diagnostics and exited HUD command history;
- Codex-approved temporary staging data;
- user Temp, recycle-bin content, shader/thumbnail caches, and user-scoped
  crash or WER reports;
- Windows system Temp and shared diagnostics only as separate consent items.

For the two Temp roots, the scan is descendant-aware: an old file inside a
newly touched directory is still eligible, while files newer than the retention
threshold stay visible as protected active temporary data. A whole subtree is
selected only after every regular file beneath it is old, readable, and free of
reparse points. This keeps mixed live work folders out of deletion without
hiding their expired siblings.

Those age-selected paths are revalidated again when the cleanup plan is made
and immediately before deletion. If a nested file becomes newer in the
meantime, the whole candidate is skipped; the built-in remover also checks the
age threshold while walking it.

Windows Update download caches, `cleanmgr /VERYLOWDISK`, and DISM component
store maintenance are not merged into the default cleanup action. They need a
separate administrator-aware provider and a stronger user contract. In
particular, WinUtil's `Dism /StartComponentCleanup /ResetBase` removes the
ability to uninstall existing updates; see Microsoft's
[WinSxS cleanup guidance](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/clean-up-the-winsxs-folder?view=windows-11).

Any future expert-only provider must retain the same scan/confirmation audit
trail, surface UAC and service-stop/restart state, and never silently sweep a
drive root, user home, project workspace, credential store, session store, or
source repository.
