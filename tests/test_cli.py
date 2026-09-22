from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bur import cli, discovery, runner
from bur.cli import app
from bur.config import Config
from bur.discovery import DBTarget, Discovered, DiscoveryError, LabelProblem
from bur.runner import BackupError, BackupResult, DrillError, DrillResult

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


def _drill_result() -> DrillResult:
    return DrillResult(
        app="giftring",
        dump_name="giftring-20260603T174800Z.dump.age",
        globals_name="giftring-20260603T174800Z.globals.sql.age",
        timestamp=datetime(2026, 6, 3, 17, 48, tzinfo=UTC),
        row_counts={"public.widgets": 100},
    )


def _fake_drill(calls: list[dict[str, object]], result: DrillResult) -> Callable[..., DrillResult]:
    def fake(
        config: Config,
        app_name: str,
        identity: str,
        major: str,
        db: str,
        *,
        timestamp: datetime | None = None,
    ) -> DrillResult:
        calls.append(
            {
                "app": app_name,
                "identity": identity,
                "major": major,
                "db": db,
                "timestamp": timestamp,
            }
        )
        return result

    return fake


def _restore_args(*extra: str) -> list[str]:
    return ["restore", "giftring", "--identity", "key.txt", "--major", "16", *extra]


def test_restore_requires_drill(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)

    outcome = _cli.invoke(app, _restore_args())

    assert outcome.exit_code == 1


def test_restore_drills_the_named_app(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "restore_drill", _fake_drill(calls, _drill_result()))

    outcome = _cli.invoke(app, _restore_args("--drill"))

    assert outcome.exit_code == 0
    assert calls[0]["app"] == "giftring"
    assert calls[0]["db"] == "giftring"
    assert calls[0]["major"] == "16"
    assert calls[0]["timestamp"] is None


def test_restore_accepts_a_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "restore_drill", _fake_drill(calls, _drill_result()))

    outcome = _cli.invoke(app, _restore_args("--drill", "--date", "20260603T174800Z"))

    assert outcome.exit_code == 0
    assert calls[0]["timestamp"] == datetime(2026, 6, 3, 17, 48, tzinfo=UTC)


def test_restore_rejects_a_bad_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)

    outcome = _cli.invoke(app, _restore_args("--drill", "--date", "yesterday"))

    assert outcome.exit_code == 1


def test_restore_reports_the_row_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    monkeypatch.setattr(runner, "restore_drill", _fake_drill([], _drill_result()))

    outcome = _cli.invoke(app, _restore_args("--drill"))

    assert "public.widgets" in outcome.output
    assert "100" in outcome.output


def test_a_failed_drill_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)

    def fail(*args: object, **kwargs: object) -> DrillResult:
        raise DrillError("giftring restored no tables")

    monkeypatch.setattr(runner, "restore_drill", fail)

    outcome = _cli.invoke(app, _restore_args("--drill"))

    assert outcome.exit_code == 1


def _target(app_name: str = "giftring", excluded: bool = False) -> DBTarget:
    return DBTarget(
        container=f"{app_name}-db-1",
        app=app_name,
        database="gift",
        user="gift_owner",
        retention_keep=3,
        schedule="weekly",
        excluded=excluded,
    )


def _patch_discover(monkeypatch: pytest.MonkeyPatch, discovered: Discovered) -> None:
    def fake_discover(config: Config) -> Discovered:
        return discovered

    monkeypatch.setattr(discovery, "discover", fake_discover)


def test_dbs_lists_each_target_with_its_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target()], []))

    outcome = _cli.invoke(app, ["dbs"])

    assert outcome.exit_code == 0
    assert "giftring" in outcome.output
    assert "giftring-db-1" in outcome.output
    assert "gift_owner" in outcome.output
    assert "weekly" in outcome.output


def test_dbs_marks_an_excluded_database(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target(excluded=True)], []))

    outcome = _cli.invoke(app, ["dbs"])

    assert "(excluded)" in outcome.output


def test_dbs_says_when_nothing_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([], []))

    outcome = _cli.invoke(app, ["dbs"])

    assert outcome.exit_code == 0
    assert "no databases" in outcome.output


def test_dbs_reports_problems_without_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(
        monkeypatch, Discovered([_target()], [LabelProblem("bad-db-1", "bur.app must match")])
    )

    outcome = _cli.invoke(app, ["dbs"])

    assert outcome.exit_code == 0


def test_dbs_exits_non_zero_when_docker_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)

    def fail(config: Config) -> Discovered:
        raise DiscoveryError("cannot connect to the docker daemon")

    monkeypatch.setattr(discovery, "discover", fail)

    outcome = _cli.invoke(app, ["dbs"])

    assert outcome.exit_code == 1
