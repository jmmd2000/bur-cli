"""Load and validate the global ``bur.toml`` config"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

_CONFIG_ENV_VAR = "BUR_CONFIG"

_DEFAULT_RETENTION_KEEP = 10
_DEFAULT_OVERDUE_HOURS = 24.0
_DEFAULT_POSTGRES_IMAGE_MATCH = "postgres"

_KNOWN_KEYS = frozenset(
    {
        "backup_root",
        "retention_keep",
        "age_recipients",
        "r2_remote",
        "r2_bucket",
        "overdue_hours",
        "postgres_image_match",
    }
)


class ConfigError(Exception):
    """Raised when the config is missing or invalid"""


@dataclass(frozen=True)
class Config:
    """The validated global configuration, with defaults applied"""

    backup_root: Path
    age_recipients: tuple[str, ...]
    retention_keep: int = _DEFAULT_RETENTION_KEEP
    r2_remote: str = ""
    r2_bucket: str = ""
    overdue_hours: float = _DEFAULT_OVERDUE_HOURS
    postgres_image_match: str = _DEFAULT_POSTGRES_IMAGE_MATCH


def load_config(path: Path | str | None = None) -> Config:
    """Load and validate ``bur.toml``

    :param path: Explicit config path, falls back to ``$BUR_CONFIG``
    :returns: The validated configuration with defaults applied
    :raises ConfigError: If no path is given, the file is missing or not valid 
    TOML, or any field is missing, empty or the wrong type.
    """
    resolved = _resolve_path(path)
    try:
        with resolved.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as error:
        raise ConfigError(f"config file not found: {resolved}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"config file is not valid TOML: {error}") from error
    return _build_config(raw)


def _resolve_path(path: Path | str | None) -> Path:
    if path is not None:
        return Path(path)
    env_value = os.environ.get(_CONFIG_ENV_VAR)
    if env_value:
        return Path(env_value)
    raise ConfigError(f"no config path given and ${_CONFIG_ENV_VAR} is not set")


def _build_config(raw: dict[str, object]) -> Config:
    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        raise ConfigError(f"unknown config field(s): {', '.join(sorted(unknown))}")

    age_recipients = _require_str_list(raw, "age_recipients")
    if not age_recipients:
        raise ConfigError("age_recipients must not be empty")

    return Config(
        backup_root=Path(_require_str(raw, "backup_root")),
        age_recipients=tuple(age_recipients),
        retention_keep=_optional_int(raw, "retention_keep", _DEFAULT_RETENTION_KEEP),
        r2_remote=_optional_str(raw, "r2_remote", ""),
        r2_bucket=_optional_str(raw, "r2_bucket", ""),
        overdue_hours=_optional_number(raw, "overdue_hours", _DEFAULT_OVERDUE_HOURS),
        postgres_image_match=_optional_str(
            raw, "postgres_image_match", _DEFAULT_POSTGRES_IMAGE_MATCH
        ),
    )


def _require_str(raw: dict[str, object], key: str) -> str:
    if key not in raw:
        raise ConfigError(f"missing required field: {key}")
    value = raw[key]
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string")
    return value


def _require_str_list(raw: dict[str, object], key: str) -> list[str]:
    if key not in raw:
        raise ConfigError(f"missing required field: {key}")
    value = raw[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ConfigError(f"{key} must be a list of non-empty strings")
    return value


def _optional_str(raw: dict[str, object], key: str, default: str) -> str:
    if key not in raw:
        return default
    value = raw[key]
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value


def _optional_int(raw: dict[str, object], key: str, default: int) -> int:
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} must be an integer")
    if value < 1:
        raise ConfigError(f"{key} must be at least 1")
    return value


def _optional_number(raw: dict[str, object], key: str, default: float) -> float:
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} must be a number")
    if value <= 0:
        raise ConfigError(f"{key} must be greater than 0")
    return float(value)
