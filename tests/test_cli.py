from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bur import cli, runner
from bur.cli import app
from bur.config import Config
from bur.runner import BackupError, BackupResult

_cli = CliRunner()


def _result(dry_run: bool = False) -> BackupResult:
    return BackupResult(
        app="giftring",
        dump_name="giftring-20260603T174800Z.dump.age",
        globals_name="giftring-20260603T174800Z.globals.sql.age",
        database_size=4096,
        dry_run=dry_run,
    )


def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(backup_root=Path("/backups"), age_recipients=("age1example",))

    def fake_load_config(path: Path | None = None) -> Config:
        return config

    monkeypatch.setattr(cli, "load_config", fake_load_config)


def _fake_backup(
    calls: list[dict[str, object]], result: BackupResult
) -> Callable[..., BackupResult]:
    def fake(
        config: Config,
        app_name: str,
        container: str,
        db: str,
        user: str,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
    ) -> BackupResult:
        calls.append(
            {
                "app": app_name,
                "container": container,
                "db": db,
                "user": user,
                "dry_run": dry_run,
            }
        )
        return result

    return fake


def test_backup_defaults_container_db_and_user_to_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(monkeypatch)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "backup_database", _fake_backup(calls, _result()))

    outcome = _cli.invoke(app, ["backup", "--only", "giftring"])

    assert outcome.exit_code == 0
    assert calls == [
        {
            "app": "giftring",
            "container": "giftring",
            "db": "giftring",
            "user": "giftring",
            "dry_run": False,
        }
    ]


def test_backup_accepts_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "backup_database", _fake_backup(calls, _result()))

    outcome = _cli.invoke(
        app,
        [
            "backup",
            "--only",
            "giftring",
            "--container",
            "giftring-db",
            "--db",
            "gift",
            "--user",
            "postgres",
        ],
    )

    assert outcome.exit_code == 0
    assert calls[0]["container"] == "giftring-db"
    assert calls[0]["db"] == "gift"
    assert calls[0]["user"] == "postgres"


def test_dry_run_reports_what_it_would_write(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "backup_database", _fake_backup(calls, _result(dry_run=True)))

    outcome = _cli.invoke(app, ["backup", "--only", "giftring", "--dry-run"])

    assert outcome.exit_code == 0
    assert calls[0]["dry_run"] is True
    assert "would write" in outcome.output


def test_a_failed_backup_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)

    def fail(*args: object, **kwargs: object) -> BackupResult:
        raise BackupError("not enough free space for giftring")

    monkeypatch.setattr(runner, "backup_database", fail)

    outcome = _cli.invoke(app, ["backup", "--only", "giftring"])

    assert outcome.exit_code == 1
