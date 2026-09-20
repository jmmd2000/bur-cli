from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bur import storage
from bur.naming import build


def test_atomic_write_creates_the_file(tmp_path: Path) -> None:
    name = build("app", datetime(2026, 1, 1, tzinfo=UTC))
    with storage.atomic_write(tmp_path, "app", name) as handle:
        handle.write(b"ciphertext")
    assert (tmp_path / "app" / name).read_bytes() == b"ciphertext"


def test_atomic_write_leaves_nothing_on_failure(tmp_path: Path) -> None:
    name = build("app", datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(RuntimeError):
        with storage.atomic_write(tmp_path, "app", name) as handle:
            handle.write(b"partial")
            raise RuntimeError("boom mid-write")

    app_dir = tmp_path / "app"
    assert not (app_dir / name).exists()
    assert list(app_dir.iterdir()) == []  # no leftover temp file either


def test_write_copies_a_stream(tmp_path: Path) -> None:
    name = build("app", datetime(2026, 1, 1, tzinfo=UTC))
    storage.write(tmp_path, "app", name, io.BytesIO(b"streamed bytes"))
    assert (tmp_path / "app" / name).read_bytes() == b"streamed bytes"


def test_list_backups_returns_metadata_newest_first(tmp_path: Path) -> None:
    older = build("app", datetime(2026, 1, 1, tzinfo=UTC))
    newer = build("app", datetime(2026, 6, 1, tzinfo=UTC))
    storage.write(tmp_path, "app", older, io.BytesIO(b"old"))
    storage.write(tmp_path, "app", newer, io.BytesIO(b"newer dump"))

    backups = storage.list_backups(tmp_path, "app")

    assert [backup.name for backup in backups] == [newer, older]
    assert backups[0].size == len(b"newer dump")
    assert backups[0].timestamp == datetime(2026, 6, 1, tzinfo=UTC)
    assert backups[0].modified.tzinfo is UTC


def test_list_backups_skips_non_backup_files(tmp_path: Path) -> None:
    name = build("app", datetime(2026, 1, 1, tzinfo=UTC))
    storage.write(tmp_path, "app", name, io.BytesIO(b"real"))
    (tmp_path / "app" / "README.txt").write_text("not a backup")

    backups = storage.list_backups(tmp_path, "app")

    assert [backup.name for backup in backups] == [name]


def test_list_backups_empty_when_app_dir_missing(tmp_path: Path) -> None:
    assert storage.list_backups(tmp_path, "never-backed-up") == []


def test_open_backup_reads_the_file(tmp_path: Path) -> None:
    name = build("app", datetime(2026, 1, 1, tzinfo=UTC))
    storage.write(tmp_path, "app", name, io.BytesIO(b"payload"))
    with storage.open_backup(tmp_path, "app", name) as handle:
        assert handle.read() == b"payload"


def test_free_space_reports_available_bytes(tmp_path: Path) -> None:
    assert storage.free_space(tmp_path) > 0


def test_free_space_handles_a_root_that_does_not_exist_yet(tmp_path: Path) -> None:
    missing = tmp_path / "not" / "created" / "yet"
    assert storage.free_space(missing) > 0
