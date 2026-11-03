from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bur import remote, storage
from bur.config import Config
from bur.discovery import DBTarget
from bur.naming import ArtefactKind, build
from bur.remote import RemoteError
from bur.status import app_runs, database_status, undiscovered_apps

_NOW = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)


def _config(root: Path, with_remote: bool = False) -> Config:
    return Config(
        backup_root=root,
        age_recipients=("age1unused",),
        r2_remote="r2" if with_remote else "",
        r2_bucket="burr" if with_remote else "",
    )


def _run_names(app: str, when: datetime) -> list[str]:
    kinds: tuple[ArtefactKind, ...] = ("dump", "globals", "manifest")
    return [build(app, when, kind) for kind in kinds]


def _target(app: str = "giftring") -> DBTarget:
    return DBTarget(
        container=f"{app}-db-1",
        app=app,
        database=app,
        user="postgres",
        keep_last=3,
        schedule="daily",
        interval=timedelta(days=1),
        excluded=False,
    )


def _store_run(root: Path, app: str, when: datetime, dump: bytes, complete: bool = True) -> None:
    kinds: tuple[ArtefactKind, ...] = ("dump", "globals", "manifest") if complete else ("dump",)
    for kind in kinds:
        payload = dump if kind == "dump" else b"other"
        storage.write(root, app, build(app, when, kind), io.BytesIO(payload))


def test_a_database_with_no_backups_is_never_backed_up(tmp_path: Path) -> None:
    result = database_status(_config(tmp_path), _target(), _NOW)

    assert result.health.level == "never"
    assert result.dump_size is None


def test_status_reads_the_newest_complete_run(tmp_path: Path) -> None:
    complete = _NOW - timedelta(hours=5)
    _store_run(tmp_path, "giftring", complete, b"x" * 300)
    _store_run(tmp_path, "giftring", _NOW - timedelta(hours=1), b"y", complete=False)

    result = database_status(_config(tmp_path), _target(), _NOW)

    assert result.health.level == "ok"
    assert result.health.last_success == complete
    assert result.dump_size == 300


def test_undiscovered_apps_are_the_other_folders(tmp_path: Path) -> None:
    for app in ("giftring", "issues", "old-blog"):
        (tmp_path / app).mkdir()
    (tmp_path / "bur.log").write_text("")

    assert undiscovered_apps(_config(tmp_path), {"giftring", "issues"}) == ["old-blog"]


def test_undiscovered_apps_is_empty_without_a_backup_root(tmp_path: Path) -> None:
    assert undiscovered_apps(_config(tmp_path / "missing"), set()) == []


def test_app_runs_lists_every_run_newest_first(tmp_path: Path) -> None:
    older = _NOW - timedelta(days=1)
    newer = _NOW - timedelta(hours=1)
    _store_run(tmp_path, "giftring", older, b"x" * 300)
    _store_run(tmp_path, "giftring", newer, b"y" * 20, complete=False)

    runs = app_runs(_config(tmp_path), "giftring")

    assert [(run.timestamp, run.complete, run.dump_size) for run in runs] == [
        (newer, False, 20),
        (older, True, 300),
    ]


@pytest.mark.parametrize(("on_remote", "level"), [(True, "ok"), (False, "local_only")])
def test_status_checks_the_newest_run_is_on_the_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_remote: bool, level: str
) -> None:
    when = _NOW - timedelta(hours=5)
    _store_run(tmp_path, "giftring", when, b"x")
    names = _run_names("giftring", when) if on_remote else _run_names("giftring", when)[:2]
    monkeypatch.setattr(remote, "list_names", lambda config, app: names)

    result = database_status(_config(tmp_path, with_remote=True), _target(), _NOW)

    assert result.health.level == level


def test_status_is_local_only_when_the_remote_cannot_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_run(tmp_path, "giftring", _NOW - timedelta(hours=5), b"x")

    def fail(config: Config, app: str) -> list[str]:
        raise RemoteError("couldn't connect")

    monkeypatch.setattr(remote, "list_names", fail)

    result = database_status(_config(tmp_path, with_remote=True), _target(), _NOW)

    assert result.health.level == "local_only"
    assert result.remote_error == "couldn't connect"


def test_app_runs_shows_where_each_run_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    only_remote = _NOW - timedelta(days=3)
    both = _NOW - timedelta(days=2)
    only_local = _NOW - timedelta(days=1)
    _store_run(tmp_path, "giftring", both, b"x")
    _store_run(tmp_path, "giftring", only_local, b"x")
    names = _run_names("giftring", only_remote) + _run_names("giftring", both)
    monkeypatch.setattr(remote, "list_names", lambda config, app: names)

    runs = app_runs(_config(tmp_path, with_remote=True), "giftring")

    assert [(run.timestamp, run.location) for run in runs] == [
        (only_local, "local"),
        (both, "both"),
        (only_remote, "r2"),
    ]
