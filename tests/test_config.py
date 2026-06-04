from pathlib import Path

import pytest

from bur.config import Config, ConfigError, load_config

_MINIMAL = """
backup_root = "/backups"
age_recipients = ["age1abc"]
"""

_FULL = """
backup_root = "/srv/backups"
retention_keep = 5
age_recipients = ["age1abc", "age1def"]
r2_remote = "r2"
r2_bucket = "burr"
overdue_hours = 12
postgres_image_match = "postgres:16"
"""


def _write(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "bur.toml"
    config_path.write_text(body, encoding="utf-8")
    return config_path


def test_minimal_config_applies_defaults(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, _MINIMAL))
    assert config == Config(
        backup_root=Path("/backups"),
        age_recipients=("age1abc",),
    )
    assert config.retention_keep == 10
    assert config.overdue_hours == 24.0
    assert config.postgres_image_match == "postgres"
    assert config.r2_remote == ""


def test_full_config_overrides_defaults(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, _FULL))
    assert config.backup_root == Path("/srv/backups")
    assert config.retention_keep == 5
    assert config.age_recipients == ("age1abc", "age1def")
    assert config.r2_bucket == "burr"
    assert config.overdue_hours == 12.0
    assert config.postgres_image_match == "postgres:16"


def test_env_var_is_used_when_no_path_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write(tmp_path, _MINIMAL)
    monkeypatch.setenv("BUR_CONFIG", str(config_path))
    assert load_config().backup_root == Path("/backups")


def test_no_path_and_no_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUR_CONFIG", raising=False)
    with pytest.raises(ConfigError):
        load_config()


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.toml")


def test_invalid_toml_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="valid TOML"):
        load_config(_write(tmp_path, "this is = = not toml"))


def test_missing_required_field_errors_by_name(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="backup_root"):
        load_config(_write(tmp_path, 'age_recipients = ["age1abc"]'))


def test_empty_age_recipients_is_rejected(tmp_path: Path) -> None:
    body = 'backup_root = "/backups"\nage_recipients = []'
    with pytest.raises(ConfigError, match="age_recipients"):
        load_config(_write(tmp_path, body))


def test_wrong_type_errors_by_name(tmp_path: Path) -> None:
    body = 'backup_root = "/backups"\nage_recipients = ["age1abc"]\nretention_keep = "ten"'
    with pytest.raises(ConfigError, match="retention_keep"):
        load_config(_write(tmp_path, body))


def test_boolean_is_not_accepted_as_integer(tmp_path: Path) -> None:
    body = 'backup_root = "/backups"\nage_recipients = ["age1abc"]\nretention_keep = true'
    with pytest.raises(ConfigError, match="retention_keep"):
        load_config(_write(tmp_path, body))


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    body = 'backup_root = "/backups"\nage_recipients = ["age1abc"]\nbackup_roott = "/x"'
    with pytest.raises(ConfigError, match="unknown"):
        load_config(_write(tmp_path, body))
