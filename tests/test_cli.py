from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bur import cli, discovery, runner
from bur.cli import app
from bur.config import Config
from bur.discovery import ContainerInfo, DBTarget, Discovered, DiscoveryError, LabelProblem
from bur.health import Health, HealthLevel
from bur.runner import BackupError, BackupResult, DrillError, DrillResult
from bur.status import DatabaseStatus
from bur.storage import BackupRun

_cli = CliRunner()


def _result(dry_run: bool = False) -> BackupResult:
    return BackupResult(
        app="giftring",
        timestamp=datetime(2026, 6, 3, 17, 48, tzinfo=UTC),
        dump_name="giftring-20260603T174800Z.dump.age",
        globals_name="giftring-20260603T174800Z.globals.sql.age",
        manifest_name="giftring-20260603T174800Z.manifest.json",
        database_size=4096,
        dry_run=dry_run,
    )


def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(backup_root=Path("/backups"), age_recipients=("age1example",))

    def fake_load_config(path: Path | None = None) -> Config:
        return config

    monkeypatch.setattr(cli, "load_config", fake_load_config)


def _fake_backup(
    calls: list[dict[str, object]], failing_app: str | None = None
) -> Callable[..., BackupResult]:
    """Record each backup call, and raise for ``failing_app``."""

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
        if app_name == failing_app:
            raise BackupError(f"not enough free space for {app_name}")
        return _result(dry_run)

    return fake


def _patch_backup(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, object]],
    failing_app: str | None = None,
    pruned: tuple[BackupRun, ...] = (),
) -> list[dict[str, object]]:
    """Fake the backup and the prune, and return the recorded prune calls."""
    prune_calls: list[dict[str, object]] = []

    def fake_prune(
        config: Config, app_name: str, keep: int, newest: datetime, *, dry_run: bool = False
    ) -> tuple[BackupRun, ...]:
        prune_calls.append({"app": app_name, "keep": keep, "dry_run": dry_run})
        return pruned

    monkeypatch.setattr(runner, "backup_database", _fake_backup(calls, failing_app))
    monkeypatch.setattr(runner, "prune", fake_prune)
    return prune_calls


def test_backup_backs_up_every_discovered_database(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target("giftring"), _target("issues")], []))
    calls: list[dict[str, object]] = []
    _patch_backup(monkeypatch, calls)

    outcome = _cli.invoke(app, ["backup"])

    assert outcome.exit_code == 0
    assert [call["app"] for call in calls] == ["giftring", "issues"]
    assert calls[0] == {
        "app": "giftring",
        "container": "giftring-db-1",
        "db": "gift",
        "user": "gift_owner",
        "dry_run": False,
    }


def test_one_failed_backup_does_not_stop_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target("giftring"), _target("issues")], []))
    calls: list[dict[str, object]] = []
    _patch_backup(monkeypatch, calls, failing_app="giftring")

    outcome = _cli.invoke(app, ["backup"])

    assert outcome.exit_code == 1
    assert [call["app"] for call in calls] == ["giftring", "issues"]


def test_backup_skips_an_excluded_database(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(
        monkeypatch, Discovered([_target("giftring", excluded=True), _target("issues")], [])
    )
    calls: list[dict[str, object]] = []
    _patch_backup(monkeypatch, calls)

    outcome = _cli.invoke(app, ["backup"])

    assert outcome.exit_code == 0
    assert [call["app"] for call in calls] == ["issues"]


def test_a_label_problem_fails_a_full_backup(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    problem = LabelProblem("broken-db-1", "bur.app is required")
    _patch_discover(monkeypatch, Discovered([_target("issues")], [problem]))
    calls: list[dict[str, object]] = []
    _patch_backup(monkeypatch, calls)

    outcome = _cli.invoke(app, ["backup"])

    assert outcome.exit_code == 1
    assert [call["app"] for call in calls] == ["issues"]


def test_only_backs_up_the_named_app(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    problem = LabelProblem("broken-db-1", "bur.app is required")
    _patch_discover(monkeypatch, Discovered([_target("giftring"), _target("issues")], [problem]))
    calls: list[dict[str, object]] = []
    _patch_backup(monkeypatch, calls)

    outcome = _cli.invoke(app, ["backup", "--only", "issues"])

    assert outcome.exit_code == 0
    assert [call["app"] for call in calls] == ["issues"]


@pytest.mark.parametrize("excluded", [False, True])
def test_only_fails_without_a_usable_match(monkeypatch: pytest.MonkeyPatch, excluded: bool) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target("giftring", excluded=excluded)], []))
    calls: list[dict[str, object]] = []
    _patch_backup(monkeypatch, calls)

    target_app = "giftring" if excluded else "missing"
    outcome = _cli.invoke(app, ["backup", "--only", target_app])

    assert outcome.exit_code == 1
    assert calls == []


def test_dry_run_reports_what_it_would_write(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target("giftring")], []))
    calls: list[dict[str, object]] = []
    _patch_backup(monkeypatch, calls)

    outcome = _cli.invoke(app, ["backup", "--dry-run"])

    assert outcome.exit_code == 0
    assert calls[0]["dry_run"] is True
    assert "would write" in outcome.output


def test_backup_prunes_each_app_it_backed_up(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target("giftring"), _target("issues")], []))
    calls: list[dict[str, object]] = []
    prune_calls = _patch_backup(monkeypatch, calls, failing_app="giftring")

    outcome = _cli.invoke(app, ["backup", "--dry-run"])

    assert outcome.exit_code == 1
    assert prune_calls == [{"app": "issues", "keep": 3, "dry_run": True}]


def test_backup_lists_the_runs_it_pruned(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target("giftring")], []))
    old_run = BackupRun(datetime(2026, 1, 1, tzinfo=UTC), (), complete=True)
    _patch_backup(monkeypatch, [], pruned=(old_run,))

    outcome = _cli.invoke(app, ["backup"])

    assert outcome.exit_code == 0
    assert "20260101T000000Z" in outcome.output


def test_a_failed_prune_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target("giftring")], []))
    _patch_backup(monkeypatch, [])

    def fail(*args: object, **kwargs: object) -> tuple[BackupRun, ...]:
        raise PermissionError("permission denied")

    monkeypatch.setattr(runner, "prune", fail)

    outcome = _cli.invoke(app, ["backup"])

    assert outcome.exit_code == 1


def test_backup_exits_non_zero_when_docker_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)

    def fail(config: Config) -> Discovered:
        raise DiscoveryError("cannot connect to the Docker daemon")

    monkeypatch.setattr(discovery, "discover", fail)

    outcome = _cli.invoke(app, ["backup"])

    assert outcome.exit_code == 1


def _drill_result() -> DrillResult:
    return DrillResult(
        app="giftring",
        dump_name="giftring-20260603T174800Z.dump.age",
        globals_name="giftring-20260603T174800Z.globals.sql.age",
        timestamp=datetime(2026, 6, 3, 17, 48, tzinfo=UTC),
        major="16",
        row_counts={"public.widgets": 100},
    )


def _fake_drill(calls: list[dict[str, object]], result: DrillResult) -> Callable[..., DrillResult]:
    def fake(
        config: Config,
        app_name: str,
        identity: str,
        major: str | None,
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

    outcome = _cli.invoke(app, _restore_args("--drill", "--latest"))

    assert outcome.exit_code == 0
    assert calls[0]["app"] == "giftring"
    assert calls[0]["db"] == "giftring"
    assert calls[0]["major"] == "16"
    assert calls[0]["timestamp"] is None


def test_restore_leaves_the_major_to_the_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "restore_drill", _fake_drill(calls, _drill_result()))

    outcome = _cli.invoke(
        app, ["restore", "giftring", "--identity", "key.txt", "--drill", "--latest"]
    )

    assert outcome.exit_code == 0
    assert calls[0]["major"] is None


def test_restore_accepts_a_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "restore_drill", _fake_drill(calls, _drill_result()))

    outcome = _cli.invoke(app, _restore_args("--drill", "--date", "20260603T174800Z"))

    assert outcome.exit_code == 0
    assert calls[0]["timestamp"] == datetime(2026, 6, 3, 17, 48, tzinfo=UTC)


def test_restore_needs_a_date_or_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "restore_drill", _fake_drill(calls, _drill_result()))

    outcome = _cli.invoke(app, _restore_args("--drill"))

    assert outcome.exit_code == 1
    assert "--date" in outcome.output
    assert "--latest" in outcome.output
    assert calls == []


def test_restore_rejects_both_a_date_and_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "restore_drill", _fake_drill(calls, _drill_result()))

    outcome = _cli.invoke(app, _restore_args("--drill", "--latest", "--date", "20260603T174800Z"))

    assert outcome.exit_code == 1
    assert "not both" in outcome.output
    assert calls == []


def test_restore_rejects_a_bad_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)

    outcome = _cli.invoke(app, _restore_args("--drill", "--date", "yesterday"))

    assert outcome.exit_code == 1


def test_restore_reports_the_row_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    monkeypatch.setattr(runner, "restore_drill", _fake_drill([], _drill_result()))

    outcome = _cli.invoke(app, _restore_args("--drill", "--latest"))

    assert "public.widgets" in outcome.output
    assert "100" in outcome.output


def test_a_failed_drill_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)

    def fail(*args: object, **kwargs: object) -> DrillResult:
        raise DrillError("giftring restored no tables")

    monkeypatch.setattr(runner, "restore_drill", fail)

    outcome = _cli.invoke(app, _restore_args("--drill", "--latest"))

    assert outcome.exit_code == 1


def _target(app_name: str = "giftring", excluded: bool = False) -> DBTarget:
    return DBTarget(
        container=f"{app_name}-db-1",
        app=app_name,
        database="gift",
        user="gift_owner",
        keep_last=3,
        schedule="weekly",
        interval=timedelta(weeks=1),
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


def _patch_audit(monkeypatch: pytest.MonkeyPatch, containers: list[ContainerInfo]) -> None:
    def fake_audit(config: Config) -> list[ContainerInfo]:
        return containers

    monkeypatch.setattr(discovery, "audit", fake_audit)


def _patch_probe(
    monkeypatch: pytest.MonkeyPatch, failure: str | None, probed: list[str] | None = None
) -> None:
    def fake_probe_auth(target: DBTarget) -> str | None:
        if probed is not None:
            probed.append(target.app)
        return failure

    monkeypatch.setattr(discovery, "probe_auth", fake_probe_auth)


def test_check_passes_when_everything_is_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target()], []))
    _patch_audit(monkeypatch, [])
    _patch_probe(monkeypatch, None)

    outcome = _cli.invoke(app, ["check"])

    assert outcome.exit_code == 0
    assert "giftring: ok" in outcome.output


def test_check_fails_on_a_label_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([], [LabelProblem("bad-db-1", "bur.app must match")]))
    _patch_audit(monkeypatch, [])
    _patch_probe(monkeypatch, None)

    assert _cli.invoke(app, ["check"]).exit_code == 1


def test_check_fails_when_a_database_cannot_be_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target()], []))
    _patch_audit(monkeypatch, [])
    _patch_probe(monkeypatch, 'role "gift_owner" does not exist')

    assert _cli.invoke(app, ["check"]).exit_code == 1


def test_check_fails_on_an_unlabelled_postgres_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([], []))
    _patch_audit(monkeypatch, [ContainerInfo("forgotten-db-1", "postgres:16", {})])
    _patch_probe(monkeypatch, None)

    assert _cli.invoke(app, ["check"]).exit_code == 1


def test_check_does_not_probe_an_excluded_database(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target(excluded=True)], []))
    _patch_audit(monkeypatch, [])
    probed: list[str] = []
    _patch_probe(monkeypatch, None, probed)

    outcome = _cli.invoke(app, ["check"])

    assert outcome.exit_code == 0
    assert probed == []


def _patch_status(
    monkeypatch: pytest.MonkeyPatch, levels: dict[str, HealthLevel], undiscovered: list[str]
) -> None:
    """Fake each app's health by name, and the undiscovered folders."""

    def fake_status(config: Config, target: DBTarget, now: datetime) -> DatabaseStatus:
        level = levels[target.app]
        if level == "never":
            return DatabaseStatus(target, Health("never", None, None), None)
        last_success = now - timedelta(hours=5)
        health = Health(level, last_success, timedelta(hours=5))
        return DatabaseStatus(target, health, 4096)

    def fake_undiscovered(config: Config, discovered: object) -> list[str]:
        return undiscovered

    monkeypatch.setattr(cli, "database_status", fake_status)
    monkeypatch.setattr(cli, "undiscovered_apps", fake_undiscovered)


def test_status_passes_when_every_database_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target("giftring"), _target("issues")], []))
    _patch_status(monkeypatch, {"giftring": "ok", "issues": "ok"}, ["old-blog"])

    outcome = _cli.invoke(app, ["status"])

    assert outcome.exit_code == 0
    assert "old-blog" in outcome.output


@pytest.mark.parametrize("level", ["overdue", "never"])
def test_status_fails_when_a_database_is_not_ok(
    monkeypatch: pytest.MonkeyPatch, level: HealthLevel
) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target("giftring"), _target("issues")], []))
    _patch_status(monkeypatch, {"giftring": "ok", "issues": level}, [])

    outcome = _cli.invoke(app, ["status"])

    assert outcome.exit_code == 1


def test_status_ignores_an_excluded_database(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target("giftring", excluded=True)], []))
    _patch_status(monkeypatch, {"giftring": "never"}, [])

    outcome = _cli.invoke(app, ["status"])

    assert outcome.exit_code == 0


def test_status_fails_on_a_label_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    problem = LabelProblem("broken-db-1", "bur.schedule must be hourly, daily, weekly")
    _patch_discover(monkeypatch, Discovered([_target("giftring")], [problem]))
    _patch_status(monkeypatch, {"giftring": "ok"}, [])

    outcome = _cli.invoke(app, ["status"])

    assert outcome.exit_code == 1


def test_status_prints_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch)
    _patch_discover(monkeypatch, Discovered([_target("giftring"), _target("issues")], []))
    _patch_status(monkeypatch, {"giftring": "ok", "issues": "never"}, ["old-blog"])

    outcome = _cli.invoke(app, ["status", "--json"])

    content = json.loads(outcome.stdout)
    assert outcome.exit_code == 1
    assert [database["health"] for database in content["databases"]] == ["ok", "never"]
    assert content["databases"][0]["age_seconds"] == 5 * 3600
    assert content["databases"][1]["last_success"] is None
    assert content["undiscovered"] == ["old-blog"]
