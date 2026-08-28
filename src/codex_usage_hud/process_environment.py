"""Environment blocks for processes spawned by the HUD."""

from __future__ import annotations

import os
from contextlib import contextmanager
from collections.abc import Mapping
import threading
from collections.abc import Iterator


PYINSTALLER_INTERNAL_ENV_PREFIX = "_pyi_"
_ENVIRONMENT_LOCK = threading.RLock()


def external_process_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an inherited environment safe for an unrelated child process.

    PyInstaller one-file bootloaders use ``_PYI_*`` variables to identify
    legitimate children of the frozen executable.  Passing those variables to
    an unrelated terminal or application makes its bootloader treat the
    process as an internal child and can trigger its parent-executable check.
    Keep the filter case-insensitive because Windows environment names are
    case-insensitive.
    """
    source = os.environ if environment is None else environment
    with _ENVIRONMENT_LOCK:
        return {
            str(name): str(value)
            for name, value in source.items()
            if not str(name).casefold().startswith(PYINSTALLER_INTERNAL_ENV_PREFIX)
        }


def internal_process_environment() -> dict[str, str]:
    """Snapshot the complete environment for a legitimate frozen helper."""
    with _ENVIRONMENT_LOCK:
        return os.environ.copy()


@contextmanager
def external_environment_scope() -> Iterator[None]:
    """Temporarily hide PyInstaller markers for APIs without ``env=``.

    ``ShellExecuteW`` inherits the caller environment and has no environment
    block argument.  The lock prevents a concurrent legitimate helper spawn
    from taking a marker-less snapshot while this scope is active.
    """
    with _ENVIRONMENT_LOCK:
        removed = {
            name: os.environ.pop(name)
            for name in list(os.environ)
            if str(name).casefold().startswith(PYINSTALLER_INTERNAL_ENV_PREFIX)
        }
        try:
            yield
        finally:
            os.environ.update(removed)


__all__ = [
    "PYINSTALLER_INTERNAL_ENV_PREFIX",
    "external_environment_scope",
    "external_process_environment",
    "internal_process_environment",
]
