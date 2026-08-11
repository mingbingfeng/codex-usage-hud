"""Maintain Codex ``model_providers`` without rewriting unrelated TOML."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import ctypes
import os
from pathlib import Path
import re
from typing import Any


PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
ENVIRONMENT_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROVIDER_SECTION_PATTERN = re.compile(
    r"(?m)^[\t ]*\[model_providers\.(?P<id>[A-Za-z0-9_-]+)\][\t ]*(?:#.*)?(?:\r?\n|$)"
)
TABLE_HEADER_PATTERN = re.compile(
    r"(?m)^[\t ]*\[\[?[^\r\n]+\]\]?\s*(?:#.*)?$"
)
QUOTED_STRING_PATTERN = re.compile(
    r"(?m)^[\t ]*(?P<key>[A-Za-z0-9_-]+)[\t ]*=[\t ]*"
    r"\"(?P<value>(?:\\.|[^\"\\\r\n])*)\"[\t ]*(?:#.*)?\r?$"
)


@dataclass(frozen=True, slots=True)
class CodexProviderDefinition:
    """Non-secret provider metadata exposed to the settings renderer."""

    provider_id: str
    name: str = ""
    base_url: str = ""
    env_key: str = ""
    wire_api: str = "responses"
    has_api_key: bool = False
    section_text: str = ""


def default_codex_config_path() -> Path:
    codex_home = str(os.environ.get("CODEX_HOME") or "").strip()
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def _user_environment_value(name: str) -> str:
    key = str(name or "").strip()
    if not key:
        return ""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                value, _kind = winreg.QueryValueEx(handle, key)
            return str(value or "")
        except (FileNotFoundError, OSError):
            pass
    return str(os.environ.get(key) or "")


def _set_user_environment_value(name: str, value: str) -> None:
    key = str(name or "").strip()
    if not key:
        raise ValueError("用户环境变量名称不能为空")
    if os.name != "nt":
        # The feature is intentionally Windows-compatible with the existing
        # PowerShell helper.  Updating os.environ alone would not persist the
        # key for a subsequently launched Codex process on Unix.
        raise RuntimeError("用户环境变量持久化目前仅支持 Windows。")
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment") as handle:
        winreg.SetValueEx(handle, key, 0, winreg.REG_SZ, str(value))
    _broadcast_environment_change()


def _broadcast_environment_change() -> None:
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32
        result = ctypes.c_size_t(0)
        user32.SendMessageTimeoutW(
            ctypes.c_void_p(0xFFFF),
            ctypes.c_uint(0x001A),
            ctypes.c_void_p(0),
            ctypes.c_wchar_p("Environment"),
            ctypes.c_uint(0x0002),
            ctypes.c_uint(5000),
            ctypes.byref(result),
        )
    except (AttributeError, OSError):
        # Registry persistence is the important operation.  A process that
        # already has its environment block cannot always receive the hint.
        return


def _toml_string(value: object) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _preferred_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _normalize_body(body: str, newline: str) -> str:
    return re.sub(r"\r\n|\r|\n", newline, body).rstrip("\r\n")


def _read_text_exact(path: Path) -> str:
    """Read text without Python's universal-newline conversion."""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_text_exact(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _section_range(text: str, provider_id: str) -> tuple[int, int, str] | None:
    target = str(provider_id or "").strip()
    match = next(
        (
            item
            for item in PROVIDER_SECTION_PATTERN.finditer(text)
            if item.group("id").casefold() == target.casefold()
        ),
        None,
    )
    if match is None:
        return None
    body_start = match.end()
    next_header = re.search(
        r"(?m)^[\t ]*\[\[?[^\r\n]+\]\]?\s*(?:#.*)?(?:\r?\n|$)",
        text[body_start:],
    )
    body_end = body_start + next_header.start() if next_header else len(text)
    return body_start, body_end, text[body_start:body_end]


def _provider_section_text(text: str, provider_id: str) -> str:
    target = str(provider_id or "").strip()
    match = next(
        (
            item
            for item in PROVIDER_SECTION_PATTERN.finditer(text)
            if item.group("id").casefold() == target.casefold()
        ),
        None,
    )
    section = _section_range(text, target)
    if match is None or section is None:
        return ""
    _body_start, body_end, _body = section
    return text[match.start() : body_end]


def _get_quoted_value(body: str, key: str) -> str:
    for match in QUOTED_STRING_PATTERN.finditer(body):
        if match.group("key") == key:
            return match.group("value")
    return ""


def _set_quoted_value(body: str, key: str, value: str, newline: str) -> str:
    escaped = _toml_string(value)
    pattern = re.compile(
        r"(?m)^(?P<prefix>[\t ]*"
        + re.escape(key)
        + r"[\t ]*=[\t ]*)(?P<value>[^\r\n]*)(?P<ending>\r?)$"
    )
    match = pattern.search(body)
    if match:
        replacement = (
            match.group("prefix")
            + '"'
            + escaped
            + '"'
            + match.group("ending")
        )
        return body[: match.start()] + replacement + body[match.end() :]
    separator = newline if body and not body.endswith(("\r", "\n")) else ""
    return body + separator + f'{key} = "{escaped}"'


def _replace_section_body(text: str, provider_id: str, body: str) -> str:
    section = _section_range(text, provider_id)
    if section is None:
        raise ValueError(f"[model_providers.{provider_id}] 不存在。")
    if TABLE_HEADER_PATTERN.search(_normalize_body(body, _preferred_newline(text))):
        raise ValueError("Provider 内容不能包含其他 TOML 表头。")
    start, end, _old_body = section
    newline = _preferred_newline(text)
    clean_body = _normalize_body(body, newline)
    replacement = f"{clean_body}{newline}" if clean_body else ""
    if end < len(text):
        replacement += newline
    return text[:start] + replacement + text[end:]


def _provider_section_body_from_editor(section_text: str, provider_id: str) -> str:
    raw = str(section_text or "").strip("\r\n")
    match = PROVIDER_SECTION_PATTERN.match(raw)
    expected = str(provider_id or "").strip()
    if match is None or match.group("id").casefold() != expected.casefold():
        raise ValueError(
            f"配置文本必须以 [model_providers.{expected}] 开头，且 Provider ID 必须一致。"
        )
    body = raw[match.end() :]
    if TABLE_HEADER_PATTERN.search(_normalize_body(body, "\n")):
        raise ValueError("Provider 配置文本不能包含其它 TOML 表头。")
    return body


def _add_provider_section(text: str, provider_id: str, base_url: str, env_key: str) -> str:
    newline = _preferred_newline(text)
    body = newline.join(
        (
            f"[model_providers.{provider_id}]",
            f'name = "{_toml_string(provider_id)}"',
            f'base_url = "{_toml_string(base_url)}"',
            f'env_key = "{_toml_string(env_key)}"',
            'wire_api = "responses"',
        )
    )
    suffix = "" if text.endswith(("\r", "\n")) else newline
    return f"{text}{suffix}{newline}{body}{newline}"


def _add_provider_section_text(text: str, provider_id: str, body: str) -> str:
    newline = _preferred_newline(text)
    clean_body = _normalize_body(body, newline)
    block = f"[model_providers.{provider_id}]"
    if clean_body:
        block = f"{block}{newline}{clean_body}"
    suffix = "" if text.endswith(("\r", "\n")) else newline
    return f"{text}{suffix}{newline}{block}{newline}"


def _validate_toml(text: str) -> None:
    try:
        import tomllib  # type: ignore[attr-defined]
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return
    try:
        tomllib.loads(text)
    except Exception as exc:
        raise ValueError(f"编辑后的 Codex config.toml 不是有效 TOML：{exc}") from exc


def _parse_toml_mapping(text: str) -> Mapping[str, Any]:
    try:
        import tomllib  # type: ignore[attr-defined]
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return {}
    try:
        candidate = tomllib.loads(text)
    except Exception:
        return {}
    return candidate if isinstance(candidate, Mapping) else {}


def _write_text_atomically(path: Path, text: str, expected: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{id(text)}")
    try:
        _write_text_exact(temporary, text)
        current = _read_text_exact(path)
        if current != expected:
            raise RuntimeError("config.toml 在保存前发生了变化，请重新打开设置后再试。")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_new_text_atomically(path: Path, text: str) -> bool:
    if path.exists():
        return False
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{id(text)}")
    try:
        _write_text_exact(temporary, text)
        try:
            # ``os.replace`` would overwrite a profile created by another
            # process.  ``os.rename`` keeps the create-only contract on both
            # Windows and POSIX.
            os.rename(temporary, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_provider_definitions(
    config_path: str | Path | None = None,
) -> dict[str, CodexProviderDefinition]:
    """Read provider metadata and editable TOML section text."""
    path = Path(config_path).expanduser() if config_path is not None else default_codex_config_path()
    try:
        text = _read_text_exact(path)
    except OSError:
        return {}
    parsed = _parse_toml_mapping(text)
    if not parsed:
        return {}
    raw_providers = parsed.get("model_providers")
    if not isinstance(raw_providers, Mapping):
        return {}
    result: dict[str, CodexProviderDefinition] = {}
    for raw_id, raw_value in raw_providers.items():
        provider_id = str(raw_id or "").strip()
        if not provider_id or not isinstance(raw_value, Mapping):
            continue
        env_key = str(raw_value.get("env_key") or "").strip()
        result[provider_id.casefold()] = CodexProviderDefinition(
            provider_id=provider_id,
            name=str(raw_value.get("name") or "").strip(),
            base_url=str(raw_value.get("base_url") or "").strip(),
            env_key=env_key,
            wire_api=str(raw_value.get("wire_api") or "responses").strip(),
            has_api_key=bool(_user_environment_value(env_key)) if env_key else False,
            section_text=_provider_section_text(text, provider_id),
        )
    return result


def _validate_request(update: Mapping[str, Any]) -> tuple[str, str, str, str, bool]:
    provider_id = str(update.get("provider_id") or update.get("providerId") or "").strip()
    base_url = str(update.get("base_url") or update.get("baseUrl") or "").strip()
    env_key = str(update.get("env_key") or update.get("envKey") or "").strip()
    api_key = str(update.get("api_key") or update.get("apiKey") or "")
    is_new = bool(update.get("is_new", update.get("isNew", False)))
    if not PROVIDER_ID_PATTERN.fullmatch(provider_id) or (
        is_new and provider_id.casefold() == "custom"
    ):
        raise ValueError("Provider ID 只能使用字母、数字、连字符或下划线，且不能是 custom。")
    if not base_url or "\r" in base_url or "\n" in base_url:
        raise ValueError("base_url 不能为空且必须是单行文本。")
    if env_key and not ENVIRONMENT_KEY_PATTERN.fullmatch(env_key):
        raise ValueError("请输入有效的用户环境变量名称。")
    if is_new and not env_key:
        raise ValueError("新增供应商时请输入用户环境变量名称。")
    if api_key and not env_key:
        raise ValueError("填写 API key 时必须同时填写用户环境变量名称。")
    if "\r" in api_key or "\n" in api_key:
        raise ValueError("API key 不能包含换行。")
    return provider_id, base_url, env_key, api_key, is_new


def save_provider_configs(
    updates: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    *,
    config_path: str | Path | None = None,
) -> dict[str, object]:
    """Apply provider sections, user env keys, and new provider profiles.

    The TOML editor follows the existing PowerShell helper: it changes only
    the selected ``[model_providers.<id>]`` body, validates the candidate,
    writes atomically, creates ``<id>.config.toml`` only for a new profile,
    and rolls back changes made by this operation when verification fails.
    """
    if isinstance(updates, Mapping):
        if "provider_id" in updates or "providerId" in updates:
            normalized_updates = [updates]
        else:
            normalized_updates = []
            for provider_id, value in updates.items():
                if not isinstance(value, Mapping):
                    continue
                item = dict(value)
                item.setdefault("provider_id", provider_id)
                normalized_updates.append(item)
    elif isinstance(updates, Sequence) and not isinstance(updates, (str, bytes, bytearray)):
        normalized_updates = [item for item in updates if isinstance(item, Mapping)]
    else:
        normalized_updates = []
    if not normalized_updates:
        return {"changed": False, "providerIds": []}
    path = Path(config_path).expanduser() if config_path is not None else default_codex_config_path()
    if not path.exists():
        raise FileNotFoundError(f"Codex config was not found: {path}")
    original_text = _read_text_exact(path)
    candidate_text = original_text
    definitions = read_provider_definitions(path)
    parsed_config = _parse_toml_mapping(original_text)
    default_provider = str(parsed_config.get("model_provider") or "").strip().casefold()
    seen_ids: set[str] = set()
    env_before: dict[str, str] = {}
    env_after: dict[str, str] = {}
    profiles_to_create: list[tuple[Path, str]] = []
    provider_ids: list[str] = []

    for update in normalized_updates:
        raw_section_text = str(
            update.get("section_text") or update.get("sectionText") or ""
        )
        section_body = None
        validation_update: Mapping[str, Any] = update
        requested_provider_id = str(
            update.get("provider_id") or update.get("providerId") or ""
        ).strip()
        if raw_section_text:
            section_body = _provider_section_body_from_editor(
                raw_section_text,
                requested_provider_id,
            )
            validation_update = dict(update)
            validation_update["base_url"] = _get_quoted_value(section_body, "base_url")
            validation_update["env_key"] = _get_quoted_value(section_body, "env_key")
        provider_id, base_url, env_key, api_key, is_new = _validate_request(
            validation_update
        )
        normalized_id = provider_id.casefold()
        if normalized_id in seen_ids:
            raise ValueError(f"Provider {provider_id} 在同一次保存中重复。")
        if default_provider and normalized_id == default_provider:
            raise ValueError("默认 Codex App Provider 不支持在这里编辑供应商配置。")
        seen_ids.add(normalized_id)
        provider_ids.append(provider_id)
        existing = definitions.get(normalized_id)
        section = _section_range(candidate_text, provider_id)
        if section is None:
            if section_body is not None:
                candidate_text = _add_provider_section_text(
                    candidate_text, provider_id, section_body
                )
            else:
                candidate_text = _add_provider_section(
                    candidate_text, provider_id, base_url, env_key
                )
            profile_path = path.parent / f"{provider_id}.config.toml"
            newline = _preferred_newline(candidate_text)
            profiles_to_create.append(
                (profile_path, f'model_provider = "{_toml_string(provider_id)}"{newline}')
            )
        else:
            _start, _end, body = section
            if section_body is not None:
                next_body = section_body
            else:
                newline = _preferred_newline(candidate_text)
                next_body = _set_quoted_value(body, "base_url", base_url, newline)
                if env_key or re.search(r"(?m)^[\t ]*env_key[\t ]*=", body):
                    next_body = _set_quoted_value(next_body, "env_key", env_key, newline)
            candidate_text = _replace_section_body(candidate_text, provider_id, next_body)

        previous_env_key = existing.env_key if existing else ""
        previous_env_value = _user_environment_value(previous_env_key)
        target_env_value = _user_environment_value(env_key)
        if env_key not in env_before:
            env_before[env_key] = target_env_value
        if api_key:
            env_after[env_key] = api_key
        elif not target_env_value:
            if previous_env_key and previous_env_value and previous_env_key == env_key:
                env_after[env_key] = previous_env_value
            elif is_new or not existing or previous_env_key != env_key:
                raise ValueError(f"请为 Provider {provider_id} 填写 API key。")

    _validate_toml(candidate_text)
    config_changed = candidate_text != original_text
    config_written = False
    created_profiles: list[Path] = []
    env_written: list[str] = []
    try:
        if config_changed:
            _write_text_atomically(path, candidate_text, original_text)
            config_written = True
        for profile_path, profile_text in profiles_to_create:
            if _write_new_text_atomically(profile_path, profile_text):
                created_profiles.append(profile_path)
        for env_key, value in env_after.items():
            if _user_environment_value(env_key) == value:
                continue
            _set_user_environment_value(env_key, value)
            env_written.append(env_key)
        if config_changed and _read_text_exact(path) != candidate_text:
            raise RuntimeError("Codex config 写入后校验失败。")
        for profile_path in created_profiles:
            expected_profile = next(
                text for candidate_path, text in profiles_to_create
                if candidate_path == profile_path
            )
            if _read_text_exact(profile_path) != expected_profile:
                raise RuntimeError("Codex provider profile 写入后校验失败。")
        for env_key, value in env_after.items():
            if _user_environment_value(env_key) != value:
                raise RuntimeError("用户环境变量写入后校验失败。")
    except Exception:
        rollback_failed = False
        for env_key in reversed(env_written):
            try:
                _set_user_environment_value(env_key, env_before.get(env_key, ""))
            except Exception:
                rollback_failed = True
        for profile_path in reversed(created_profiles):
            try:
                profile_path.unlink()
            except OSError:
                rollback_failed = True
        if config_written:
            try:
                _write_text_atomically(path, original_text, candidate_text)
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise RuntimeError(
                "Provider 保存失败，且之前的部分值无法完全恢复，请检查 config.toml 和用户环境变量。"
            ) from None
        raise

    return {
        "changed": bool(config_changed or created_profiles or env_written),
        "providerIds": provider_ids,
        "configPath": str(path),
        "profilePaths": [str(item) for item in created_profiles],
        "environmentKeys": list(env_written),
    }


__all__ = [
    "CodexProviderDefinition",
    "default_codex_config_path",
    "read_provider_definitions",
    "save_provider_configs",
]
