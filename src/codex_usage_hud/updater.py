"""GitHub Release update helpers for codex-usage-hud."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_REPOSITORY = "mingbingfeng/codex-usage-hud"
RELEASES_URL = f"https://github.com/{DEFAULT_REPOSITORY}/releases"
LATEST_RELEASE_API_URL = (
    f"https://api.github.com/repos/{DEFAULT_REPOSITORY}/releases/latest"
)
UPDATE_USER_AGENT = "codex-usage-hud-updater"
DEFAULT_UPDATE_TIMEOUT_SECONDS = 12.0
DEFAULT_UPDATE_DOWNLOAD_TIMEOUT_SECONDS = 60.0
DEFAULT_AUTO_UPDATE_CHECK_INTERVAL_SECONDS = 6 * 60 * 60
DEFAULT_AUTO_UPDATE_RETRY_SECONDS = 15 * 60
DEFAULT_UPDATE_CHUNK_BYTES = 256 * 1024
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


@dataclass(frozen=True)
class AutoUpdateState:
    """Snapshot of automatic update discovery and download state."""

    phase: str = "idle"
    current_version: str = ""
    latest_version: str = ""
    release_url: str = RELEASES_URL
    asset_name: str = ""
    asset_size: int = 0
    downloaded_bytes: int = 0
    progress: float = 0.0
    installer_path: str = ""
    visible: bool = False
    icon: str = ""
    message: str = ""
    error: str = ""

    @property
    def progress_text(self) -> str:
        if self.asset_size > 0:
            return (
                f"{_format_byte_amount(self.downloaded_bytes)} / "
                f"{_format_byte_amount(self.asset_size)} ({self.progress:.0%})"
            )
        if self.downloaded_bytes > 0:
            return _format_byte_amount(self.downloaded_bytes)
        return ""

    @property
    def title(self) -> str:
        if not self.visible:
            return ""
        if self.phase == "ready":
            return f"已下载 {self.asset_name}，点击打开安装程序。"
        if self.phase == "downloading":
            progress = self.progress_text or "正在准备下载"
            return f"正在下载 {self.asset_name}：{progress}，点击暂停。"
        if self.phase == "paused":
            progress = self.progress_text or "已暂停"
            return f"下载已暂停：{progress}，点击继续。"
        if self.phase == "error":
            details = self.error or self.message or "下载失败"
            return f"{details}，点击重试。"
        return self.message

    def to_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "currentVersion": self.current_version,
            "latestVersion": self.latest_version,
            "releaseUrl": self.release_url,
            "assetName": self.asset_name,
            "assetSize": self.asset_size,
            "downloadedBytes": self.downloaded_bytes,
            "progress": self.progress,
            "progressText": self.progress_text,
            "installerPath": self.installer_path,
            "visible": self.visible,
            "icon": self.icon,
            "message": self.message,
            "error": self.error,
            "title": self.title,
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


def _format_byte_amount(value: int) -> str:
    amount = max(0, int(value or 0))
    if amount >= 1024 * 1024:
        return f"{amount / (1024 * 1024):.1f} MB"
    if amount >= 1024:
        return f"{amount / 1024:.0f} KB"
    return f"{amount} B"


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


def default_update_download_dir() -> Path:
    """Return the per-user directory that stores downloaded installers."""
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    else:
        base = Path.home() / ".cache"
    return base / "codex-usage-hud" / "updates"


def _download_response_total_bytes(
    response: Any,
    *,
    offset: int,
    fallback_size: int,
) -> int:
    content_range = ""
    headers = getattr(response, "headers", None)
    if headers is not None:
        content_range = str(headers.get("Content-Range") or "").strip()
    if content_range:
        match = re.search(r"/(\d+)$", content_range)
        if match:
            try:
                return max(0, int(match.group(1)))
            except ValueError:
                pass
    content_length = ""
    if headers is not None:
        content_length = str(headers.get("Content-Length") or "").strip()
    if content_length:
        try:
            size = max(0, int(content_length))
        except ValueError:
            size = 0
        if offset > 0 and _response_status_code(response) == 206:
            return offset + size
        return size
    return max(0, int(fallback_size or 0), int(offset or 0))


def _response_status_code(response: Any) -> int:
    value = getattr(response, "status", None)
    if isinstance(value, int):
        return value
    getter = getattr(response, "getcode", None)
    if callable(getter):
        try:
            result = getter()
            if isinstance(result, int):
                return result
        except Exception:
            return 0
    return 0


def download_update_asset(
    info: UpdateInfo,
    *,
    destination_dir: Path | None = None,
    timeout_seconds: float = DEFAULT_UPDATE_DOWNLOAD_TIMEOUT_SECONDS,
) -> Path:
    """Download the selected update installer and return the local path."""
    if not info.asset:
        raise ValueError("update info does not include an installer asset")
    target_dir = destination_dir or default_update_download_dir()
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


class AutoUpdateManager:
    """Check for updates in the background and manage a resumable installer download."""

    def __init__(
        self,
        *,
        current_version: str,
        repository: str = DEFAULT_REPOSITORY,
        check_interval_seconds: float = DEFAULT_AUTO_UPDATE_CHECK_INTERVAL_SECONDS,
        retry_interval_seconds: float = DEFAULT_AUTO_UPDATE_RETRY_SECONDS,
        download_dir: Path | None = None,
        check_timeout_seconds: float = DEFAULT_UPDATE_TIMEOUT_SECONDS,
        download_timeout_seconds: float = DEFAULT_UPDATE_DOWNLOAD_TIMEOUT_SECONDS,
        check_func: Callable[..., UpdateInfo] | None = None,
        download_opener: Callable[..., Any] | None = None,
        launch_func: Callable[[Path], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.current_version = str(current_version or "").strip()
        self.repository = repository
        self.check_interval_seconds = max(30.0, float(check_interval_seconds))
        self.retry_interval_seconds = max(5.0, float(retry_interval_seconds))
        self.download_dir = download_dir or default_update_download_dir()
        self.check_timeout_seconds = max(1.0, float(check_timeout_seconds))
        self.download_timeout_seconds = max(1.0, float(download_timeout_seconds))
        self._check_func = check_func or check_for_update
        self._download_opener = download_opener or urlopen
        self._launch_func = launch_func or launch_installer
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._check_thread: threading.Thread | None = None
        self._download_thread: threading.Thread | None = None
        self._next_check_at = 0.0
        self._latest_info: UpdateInfo | None = None
        self._status = AutoUpdateState(current_version=self.current_version)

    def status(self) -> AutoUpdateState:
        with self._lock:
            return self._status

    def tick(self) -> AutoUpdateState:
        with self._lock:
            if self._stop_event.is_set():
                return self._status
            if self._should_start_check_locked():
                self._start_check_locked()
            return self._status

    def handle_click(self) -> AutoUpdateState:
        installer_to_open: Path | None = None
        with self._lock:
            phase = self._status.phase
            if phase == "ready" and self._status.installer_path:
                installer_to_open = Path(self._status.installer_path)
            elif phase == "downloading":
                self._pause_event.set()
                self._status = replace(
                    self._status,
                    phase="paused",
                    message="已暂停更新下载。",
                )
                return self._status
            elif phase in {"paused", "error"} and self._latest_info and self._latest_info.available:
                self._pause_event.clear()
                self._start_download_locked(self._latest_info)
                return self._status
            elif installer_to_open is None:
                return self._status
        try:
            self._launch_func(installer_to_open)
        except Exception as exc:
            with self._lock:
                self._status = replace(
                    self._status,
                    phase="error",
                    visible=True,
                    icon="download",
                    message=f"打开安装程序失败：{exc}",
                    error=str(exc),
                )
                return self._status
        with self._lock:
            self._status = replace(
                self._status,
                message=f"已启动 {installer_to_open.name}。",
            )
            return self._status

    def close(self) -> None:
        self._stop_event.set()
        self._pause_event.set()
        threads = [self._check_thread, self._download_thread]
        for thread in threads:
            if thread is not None and thread.is_alive():
                thread.join(timeout=0.5)

    def _should_start_check_locked(self) -> bool:
        if self._latest_info and self._latest_info.available and self._status.visible:
            return False
        if self._check_thread is not None and self._check_thread.is_alive():
            return False
        return self._monotonic() >= self._next_check_at

    def _start_check_locked(self) -> None:
        self._status = AutoUpdateState(
            phase="checking",
            current_version=self.current_version,
            message="正在检查更新...",
        )
        self._check_thread = threading.Thread(
            target=self._check_worker,
            name="codex-usage-hud-update-check",
            daemon=True,
        )
        self._check_thread.start()

    def _check_worker(self) -> None:
        info = self._check_func(
            current_version=self.current_version,
            repository=self.repository,
            timeout_seconds=self.check_timeout_seconds,
        )
        now = self._monotonic()
        with self._lock:
            self._check_thread = None
            if self._stop_event.is_set():
                return
            self._latest_info = info
            if info.error:
                self._next_check_at = now + self.retry_interval_seconds
                self._status = AutoUpdateState(
                    phase="error",
                    current_version=self.current_version,
                    latest_version=info.latest_version,
                    release_url=info.release_url,
                    message=f"检查更新失败：{info.error}",
                    error=info.error,
                )
                return
            self._next_check_at = now + self.check_interval_seconds
            if not info.available:
                self._status = AutoUpdateState(
                    phase="up_to_date",
                    current_version=self.current_version,
                    latest_version=info.latest_version,
                    release_url=info.release_url,
                    message=format_update_info(info),
                )
                return
            self._pause_event.clear()
            self._start_download_locked(info)

    def _start_download_locked(self, info: UpdateInfo) -> None:
        if not info.asset:
            self._status = AutoUpdateState(
                phase="error",
                current_version=self.current_version,
                latest_version=info.latest_version,
                release_url=info.release_url,
                visible=True,
                icon="download",
                message="未找到可下载的安装包。",
                error="missing installer asset",
            )
            return
        target = self.download_dir / info.asset.name
        downloaded = self._existing_file_bytes(target, info.asset.size)
        if info.asset.size > 0 and downloaded >= info.asset.size:
            self._status = AutoUpdateState(
                phase="ready",
                current_version=self.current_version,
                latest_version=info.latest_version,
                release_url=info.release_url,
                asset_name=info.asset.name,
                asset_size=info.asset.size,
                downloaded_bytes=info.asset.size,
                progress=1.0,
                installer_path=str(target),
                visible=True,
                icon="install",
                message="更新安装包已下载完成。",
            )
            return
        if self._download_thread is not None and self._download_thread.is_alive():
            return
        progress = (
            min(1.0, downloaded / info.asset.size)
            if info.asset.size > 0
            else 0.0
        )
        self._status = AutoUpdateState(
            phase="downloading",
            current_version=self.current_version,
            latest_version=info.latest_version,
            release_url=info.release_url,
            asset_name=info.asset.name,
            asset_size=info.asset.size,
            downloaded_bytes=downloaded,
            progress=progress,
            installer_path=str(target),
            visible=True,
            icon="download",
            message="正在后台下载更新安装包。",
        )
        self._download_thread = threading.Thread(
            target=self._download_worker,
            args=(info, target),
            name="codex-usage-hud-update-download",
            daemon=True,
        )
        self._download_thread.start()

    def _download_worker(self, info: UpdateInfo, target: Path) -> None:
        assert info.asset is not None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            offset = self._existing_file_bytes(target, info.asset.size)
            headers = {"User-Agent": UPDATE_USER_AGENT}
            if offset > 0:
                headers["Range"] = f"bytes={offset}-"
            request = Request(info.asset.url, headers=headers)
            with self._download_opener(
                request,
                timeout=max(1.0, float(self.download_timeout_seconds)),
            ) as response:
                status_code = _response_status_code(response)
                append = offset > 0 and status_code == 206
                if not append and offset > 0:
                    offset = 0
                total_bytes = _download_response_total_bytes(
                    response,
                    offset=offset,
                    fallback_size=info.asset.size,
                )
                downloaded = offset
                mode = "ab" if append else "wb"
                with target.open(mode) as handle:
                    while True:
                        if self._stop_event.is_set():
                            return
                        if self._pause_event.is_set():
                            with self._lock:
                                self._download_thread = None
                                self._status = AutoUpdateState(
                                    phase="paused",
                                    current_version=self.current_version,
                                    latest_version=info.latest_version,
                                    release_url=info.release_url,
                                    asset_name=info.asset.name,
                                    asset_size=total_bytes or info.asset.size,
                                    downloaded_bytes=downloaded,
                                    progress=(
                                        min(1.0, downloaded / max(1, total_bytes))
                                        if total_bytes > 0
                                        else 0.0
                                    ),
                                    installer_path=str(target),
                                    visible=True,
                                    icon="download",
                                    message="已暂停更新下载。",
                                )
                            return
                        chunk = response.read(DEFAULT_UPDATE_CHUNK_BYTES)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        with self._lock:
                            self._status = AutoUpdateState(
                                phase="downloading",
                                current_version=self.current_version,
                                latest_version=info.latest_version,
                                release_url=info.release_url,
                                asset_name=info.asset.name,
                                asset_size=total_bytes or info.asset.size,
                                downloaded_bytes=downloaded,
                                progress=(
                                    min(1.0, downloaded / max(1, total_bytes))
                                    if total_bytes > 0
                                    else 0.0
                                ),
                                installer_path=str(target),
                                visible=True,
                                icon="download",
                                message="正在后台下载更新安装包。",
                            )
                if total_bytes > 0 and downloaded < total_bytes:
                    raise IOError("download ended before the installer was fully written")
        except Exception as exc:
            with self._lock:
                self._download_thread = None
                self._status = AutoUpdateState(
                    phase="error",
                    current_version=self.current_version,
                    latest_version=info.latest_version,
                    release_url=info.release_url,
                    asset_name=info.asset.name,
                    asset_size=info.asset.size,
                    downloaded_bytes=self._existing_file_bytes(target, info.asset.size),
                    progress=0.0,
                    installer_path=str(target),
                    visible=True,
                    icon="download",
                    message=f"下载更新失败：{exc}",
                    error=str(exc),
                )
                return
        with self._lock:
            final_size = max(self._existing_file_bytes(target, info.asset.size), info.asset.size)
            self._download_thread = None
            self._status = AutoUpdateState(
                phase="ready",
                current_version=self.current_version,
                latest_version=info.latest_version,
                release_url=info.release_url,
                asset_name=info.asset.name,
                asset_size=final_size,
                downloaded_bytes=final_size,
                progress=1.0,
                installer_path=str(target),
                visible=True,
                icon="install",
                message="更新安装包已下载完成。",
            )

    @staticmethod
    def _existing_file_bytes(path: Path, expected_size: int) -> int:
        try:
            size = max(0, int(path.stat().st_size))
        except OSError:
            return 0
        if expected_size > 0 and size > expected_size:
            try:
                path.unlink()
            except OSError:
                return 0
            return 0
        return size


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
    "AutoUpdateManager",
    "AutoUpdateState",
    "DEFAULT_REPOSITORY",
    "DEFAULT_AUTO_UPDATE_CHECK_INTERVAL_SECONDS",
    "DEFAULT_AUTO_UPDATE_RETRY_SECONDS",
    "DEFAULT_UPDATE_DOWNLOAD_TIMEOUT_SECONDS",
    "INSTALLER_SUFFIX",
    "LATEST_RELEASE_API_URL",
    "RELEASES_URL",
    "UpdateAsset",
    "UpdateInfo",
    "check_for_update",
    "default_update_download_dir",
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
