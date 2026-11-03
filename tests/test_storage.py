from __future__ import annotations

import io
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bur import storage
from bur.naming import ArtefactKind, build


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


def test_atomic_write_fsyncs_the_directory_after_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[str] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            synced.append("directory")
        else:
            synced.append("file")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)

    name = build("app", datetime(2026, 1, 1, tzinfo=UTC))
    with storage.atomic_write(tmp_path, "app", name) as handle:
        handle.write(b"ciphertext")

    assert synced == ["file", "directory"]


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


def _write_pair(root: Path, app: str, when: datetime) -> None:
    kinds: tuple[ArtefactKind, ...] = ("dump", "globals", "manifest")
    for kind in kinds:
        storage.write(root, app, build(app, when, kind), io.BytesIO(b"payload"))


def test_find_backup_pair_returns_the_newest_run(tmp_path: Path) -> None:
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 2, 1, tzinfo=UTC)
    _write_pair(tmp_path, "app", older)
    _write_pair(tmp_path, "app", newer)

    pair = storage.find_backup_pair(tmp_path, "app")

    assert pair.timestamp == newer
    assert pair.dump_name == build("app", newer, "dump")
    assert pair.globals_name == build("app", newer, "globals")
    assert pair.manifest_name == build("app", newer, "manifest")


def test_find_backup_pair_finds_a_specific_run(tmp_path: Path) -> None:
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 2, 1, tzinfo=UTC)
    _write_pair(tmp_path, "app", older)
    _write_pair(tmp_path, "app", newer)

    pair = storage.find_backup_pair(tmp_path, "app", older)

    assert pair.timestamp == older
    assert pair.dump_name == build("app", older, "dump")


def test_find_backup_pair_needs_both_artefacts(tmp_path: Path) -> None:
    when = datetime(2026, 1, 1, tzinfo=UTC)
    storage.write(tmp_path, "app", build("app", when, "dump"), io.BytesIO(b"payload"))

    with pytest.raises(storage.BackupNotFound) as excinfo:
        storage.find_backup_pair(tmp_path, "app", when)
    assert "missing its globals" in str(excinfo.value)


def test_find_backup_pair_needs_the_manifest(tmp_path: Path) -> None:
    when = datetime(2026, 1, 1, tzinfo=UTC)
    for kind in ("dump", "globals"):
        storage.write(tmp_path, "app", build("app", when, kind), io.BytesIO(b"payload"))

    with pytest.raises(storage.BackupNotFound, match="manifest"):
        storage.find_backup_pair(tmp_path, "app", when)
    with pytest.raises(storage.BackupNotFound):
        storage.find_backup_pair(tmp_path, "app")


def test_find_backup_pair_raises_when_there_are_none(tmp_path: Path) -> None:
    with pytest.raises(storage.BackupNotFound):
        storage.find_backup_pair(tmp_path, "app")


def test_find_backup_pair_raises_for_an_unknown_timestamp(tmp_path: Path) -> None:
    _write_pair(tmp_path, "app", datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(storage.BackupNotFound):
        storage.find_backup_pair(tmp_path, "app", datetime(2020, 1, 1, tzinfo=UTC))


def test_find_backup_pair_skips_a_newer_incomplete_run(tmp_path: Path) -> None:
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 2, 1, tzinfo=UTC)
    _write_pair(tmp_path, "app", older)
    storage.write(tmp_path, "app", build("app", newer, "dump"), io.BytesIO(b"payload"))

    pair = storage.find_backup_pair(tmp_path, "app")

    assert pair.timestamp == older


def test_find_backup_pair_raises_when_no_run_is_complete(tmp_path: Path) -> None:
    when = datetime(2026, 1, 1, tzinfo=UTC)
    storage.write(tmp_path, "app", build("app", when, "globals"), io.BytesIO(b"payload"))

    with pytest.raises(storage.BackupNotFound, match="no complete backup"):
        storage.find_backup_pair(tmp_path, "app")


def test_list_runs_groups_files_by_timestamp_newest_first(tmp_path: Path) -> None:
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 2, 1, tzinfo=UTC)
    _write_pair(tmp_path, "app", older)
    storage.write(tmp_path, "app", build("app", newer, "dump"), io.BytesIO(b"payload"))

    runs = storage.list_runs(tmp_path, "app")

    assert [run.timestamp for run in runs] == [newer, older]
    assert runs[0].names == (build("app", newer, "dump"),)
    assert runs[0].complete is False
    assert len(runs[1].names) == 3
    assert runs[1].complete is True


def test_delete_run_removes_every_file_of_the_run(tmp_path: Path) -> None:
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 2, 1, tzinfo=UTC)
    _write_pair(tmp_path, "app", older)
    _write_pair(tmp_path, "app", newer)
    older_run = storage.list_runs(tmp_path, "app")[1]

    storage.delete_run(tmp_path, "app", older_run)

    assert [run.timestamp for run in storage.list_runs(tmp_path, "app")] == [newer]


def test_delete_run_removes_the_manifest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_pair(tmp_path, "app", datetime(2026, 1, 1, tzinfo=UTC))
    run = storage.list_runs(tmp_path, "app")[0]
    deleted: list[str] = []
    real_unlink = Path.unlink

    def recording_unlink(path: Path, missing_ok: bool = False) -> None:
        deleted.append(path.name)
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", recording_unlink)

    storage.delete_run(tmp_path, "app", run)

    assert deleted[0].endswith(".manifest.json")
