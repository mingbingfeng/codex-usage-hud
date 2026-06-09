"""GitHub Release update helpers for codex-usage-hud."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_REPOSITORY = "mingbingfeng/codex-usage-hud"
RELEASES_URL = f"https://github.com/{DEFAULT_REPOSITORY}/releases"
LATEST_RELEASE_API_URL = (
    f"https://api.github.com/repos/{DEFAULT_REPOSITORY}/releases/latest"
)
UPDATE_USER_AGENT = "codex-usage-hud-updater"
DEFAULT_UPDATE_TIMEOUT_SECONDS = 12.0
INSTALLER_SUFFIX = "-windows-x64-setup.exe"
_VERSION_RE = re.compile(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


@dataclass(frozen=True)
class UpdateAsset:
    """Installer asset selected from a GitHub Release."""

    name: str
    url: str
    size: int = 0

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "url": self.url, "size": self.size}


@dataclass(frozen=True)
class UpdateInfo:
    """Result of checking the latest GitHub Release."""

    current_version: str
    latest_version: str = ""
    release_url: str = RELEASES_URL
    available: bool = False
    asset: UpdateAsset | None = None
    error: str = ""

    @property
    def asset_name(self) -> str:
        return self.asset.name if self.asset else ""

    @property
    def asset_url(self) -> str:
        return self.asset.url if self.asset else ""

    def to_dict(self) -> dict[str, object]:
        return {
            "currentVersion": self.current_version,
            "latestVersion": self.latest_version,
            "releaseUrl": self.release_url,
            "available": self.available,
            "asset": self.asset.to_dict() if self.asset else None,
            "error": self.error,
        }


def version_key(value: str) -> tuple[int, int, int]:
    """Return a comparable semver key from a tag or version string."""
    match = _VERSION_RE.search(str(value or "").strip())
    if not match:
        return (0, 0, 0)
    major, minor, patch = match.groups(default="0")
    return (int(major), int(minor), int(patch))


def is_newer_version(candidate: str, current: str) -> bool:
    """Return whether ``candidate`` is newer than ``current``."""
    return version_key(candidate) > version_key(current)


def installer_asset_name(version: str) -> str:
    """Return the canonical Windows setup asset name for ``version``."""
    tag = normalize_tag(version)
    return f"codex-usage-hud-{tag}{INSTALLER_SUFFIX}"


def normalize_tag(version: str) -> str:
    """Return a release tag with a leading ``v``."""
    text = str(version or "").strip()
    return text if text.startswith("v") else f"v{text}"


def latest_release_api_url(repository: str = DEFAULT_REPOSITORY) -> str:
    """Return the GitHub REST URL for a repository's latest release."""
    return f"https://api.github.com/repos/{repository}/releases/latest"


def _json_request(url: str, *, timeout_seconds: float) -> Mapping[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": UPDATE_USER_AGENT,
        },
    )
    with urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("release payload is not a JSON object")
    return payload


def select_windows_installer_asset(
    assets: object,
    *,
    latest_version: str = "",
) -> UpdateAsset | None:
    """Select the Windows x64 setup executable from a GitHub assets list."""
    if not isinstance(assets, list):
        return None
    expected_name = installer_asset_name(latest_version).lower() if latest_version else ""
    fallback: UpdateAsset | None = None
    for item in assets:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("browser_download_url") or "").strip()
        if not name or not url:
            continue
        normalized = name.lower()
        if expected_name and normalized == expected_name:
            return UpdateAsset(name=name, url=url, size=_asset_size(item))
        if (
            normalized.endswith(".exe")
            and "setup" in normalized
            and ("windows" in normalized or "win" in normalized)
        ):
            fallback = fallback or UpdateAsset(name=name, url=url, size=_asset_size(item))
    return fallback


def _asset_size(item: Mapping[str, Any]) -> int:
    try:
        return max(0, int(item.get("size") or 0))
    except (TypeError, ValueError):
        return 0


def check_for_update(
    *,
    current_version: str,
    repository: str = DEFAULT_REPOSITORY,
    timeout_seconds: float = DEFAULT_UPDATE_TIMEOUT_SECONDS,
) -> UpdateInfo:
    """Check GitHub Releases and return update metadata."""
    try:
        payload = _json_request(
            latest_release_api_url(repository),
            timeout_seconds=timeout_seconds,
        )
        latest_version = str(
            payload.get("tag_name") or payload.get("name") or ""
        ).strip()
        release_url = str(payload.get("html_url") or RELEASES_URL).strip()
        asset = select_windows_installer_asset(
            payload.get("assets"),
            latest_version=latest_version,
        )
        available = bool(
            latest_version
            and is_newer_version(latest_version, current_version)
            and asset is not None
        )
        return UpdateInfo(
            current_version=current_version,
            latest_version=latest_version,
            release_url=release_url or RELEASES_URL,
            available=available,
            asset=asset,
        )
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return UpdateInfo(current_version=current_version, error=str(exc))


def format_update_info(info: UpdateInfo) -> str:
    """Return a concise human-readable update status."""
    if info.error:
        return f"Update check failed: {info.error}"
    if info.available:
        return (
            f"Update available: {info.current_version} -> {info.latest_version}\n"
            f"Installer: {info.asset_name}\n"
            f"Release: {info.release_url}"
        )
    latest = info.latest_version or "unknown"
    return f"codex-usage-hud is up to date (current {info.current_version}, latest {latest})."


def download_update_asset(
    info: UpdateInfo,
    *,
    destination_dir: Path | None = None,
    timeout_seconds: float = 60.0,
) -> Path:
    """Download the selected update installer and return the local path."""
    if not info.asset:
        raise ValueError("update info does not include an installer asset")
    target_dir = destination_dir or Path(tempfile.gettempdir()) / "codex-usage-hud-updates"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / info.asset.name
    request = Request(info.asset.url, headers={"User-Agent": UPDATE_USER_AGENT})
    with urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
        with target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    return target


def launch_installer(path: Path) -> None:
    """Launch a downloaded Windows installer without waiting for completion."""
    installer = Path(path)
    if not installer.is_file():
        raise FileNotFoundError(str(installer))
    if not sys.platform.startswith("win"):
        raise RuntimeError("automatic installer launch is Windows-only")
    subprocess.Popen(
        [str(installer)],
        cwd=str(installer.parent),
        close_fds=True,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def install_latest_update(
    *,
    current_version: str,
    repository: str = DEFAULT_REPOSITORY,
    download_dir: Path | None = None,
) -> UpdateInfo:
    """Check, download, and launch the latest Windows installer when available."""
    info = check_for_update(current_version=current_version, repository=repository)
    if info.error or not info.available:
        return info
    installer = download_update_asset(info, destination_dir=download_dir)
    launch_installer(installer)
    return info


def installed_executable_path() -> Path:
    """Return the default per-user Windows install location for the executable."""
    root = os.environ.get("LOCALAPPDATA")
    base = Path(root) if root else Path.home() / "AppData" / "Local"
    return base / "Programs" / "codex-usage-hud" / "codex-hud.exe"


__all__ = [
    "DEFAULT_REPOSITORY",
    "INSTALLER_SUFFIX",
    "LATEST_RELEASE_API_URL",
    "RELEASES_URL",
    "UpdateAsset",
    "UpdateInfo",
    "check_for_update",
    "download_update_asset",
    "format_update_info",
    "install_latest_update",
    "installed_executable_path",
    "installer_asset_name",
    "is_newer_version",
    "launch_installer",
    "latest_release_api_url",
    "normalize_tag",
    "select_windows_installer_asset",
    "version_key",
]
