# Update Delivery and Verification

Windows auto-update has two distinct trust boundaries:

1. GitHub Release API determines the version, canonical asset name, byte size,
   and SHA-256 digest.
2. The installer can be transferred through the official GitHub asset URL or
   a fallback transport URL when a regional network cannot reach GitHub.

The fallback transport is not trusted to choose a release or provide a valid
file. Before an installer becomes visible to the user, HUD verifies its size
and SHA-256 against the GitHub release metadata, then atomically renames the
`.part` download to the final `.exe`. A failed or tampered download is never
launched.

## Runtime behavior

- Checks run in the background every six hours. A newly available installer is
  downloaded in the background but is only installed after the user chooses
  the install action.
- Downloads use HTTP Range when a partial file is present. The next source can
  continue the same partial file when it supports Range; otherwise it restarts
  that partial download safely.
- The transport order is official GitHub, then `ghproxy.net`, then
  `gh-proxy.com`. These endpoints are availability fallbacks, not release
  metadata sources. A source failure moves to the next candidate; a digest
  mismatch deletes the partial file and tries the next source.
- The updater refuses assets without a GitHub SHA-256 digest. It never treats
  a filename or a successful HTTP response as proof that an installer is safe.
- The Windows installer stops the running HUD before it replaces files. The
  updater only starts the installer after the verified final file exists.

## Release checklist

Run `python tools/build_installer.py`. The command creates:

- `codex-usage-hud-vX.Y.Z-windows-x64-setup.exe`
- `codex-usage-hud-vX.Y.Z-windows-x64-setup.exe.sha256`

Upload both files to the GitHub Release. Confirm the Release API exposes a
`sha256:<hex>` `digest` value for the installer asset before announcing the
release. The `.sha256` sidecar is also the checksum users can apply when they
download through a regional mirror or an enterprise cache.

Do not redirect the GitHub Release metadata endpoint through a public mirror:
the metadata is the trust root for the file digest. If GitHub API access itself
needs a regional alternative, use a project-controlled, signed manifest before
enabling it in the client.
