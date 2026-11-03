from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bur import storage
from bur.config import Config
from bur.discovery import DBTarget
from bur.naming import ArtefactKind, build
from bur.status import app_runs, database_status, undiscovered_apps

_NOW = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)


def _config(root: Path) -> Config:
    return Config(backup_root=root, age_recipients=("age1unused",))


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
