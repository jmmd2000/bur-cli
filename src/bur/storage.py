"""Store backups on local disk

A write goes to a temp file in the target directory and is renamed into place only
once it's fully written and fsynced, so a crashed or killed run never leaves a half-file
that looks real. Files live under <root>/<app>/
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from bur.naming import ArtefactKind, InvalidBackupName, parse

_RUN_KINDS: tuple[ArtefactKind, ...] = ("dump", "globals", "manifest")


@dataclass(frozen=True)
class BackupFile:
    """One stored backup and its metadata"""

    name: str
    size: int
    modified: datetime
    timestamp: datetime


@dataclass(frozen=True)
class BackupRun:
    """The stored files that share one run timestamp"""

    timestamp: datetime
    names: tuple[str, ...]
    complete: bool


class BackupNotFound(Exception):
    """No complete backup pair for the requested app and timestamp"""


@dataclass(frozen=True)
class BackupPair:
    """The dump, globals and manifest from one backup run"""

    dump_name: str
    globals_name: str
    manifest_name: str
    timestamp: datetime


@contextmanager
def atomic_write(root: Path, app: str, name: str) -> Generator[IO[bytes]]:
    """Open a temp file to write a backup, renamed on clean exit

    :param root: Backup root directory
    :param app: Application the backup belongs to
    :param name: Final filename within ``<root>/<app>``
    :returns: An open binary file to write the backup into
    """
    directory = root / app
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name

    fd, temp_name = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=".tmp")
    temp_path = Path(temp_name)
    success = False
    try:
        with os.fdopen(fd, "wb") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        _fsync_directory(directory)
        success = True
    finally:
        if not success:
            temp_path.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    """Flush a directory's entries to disk"""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write(root: Path, app: str, name: str, stream: IO[bytes]) -> None:
    """Copy a stream into a backup file

    :param root: Backup root directory
    :param app: Application the backup belongs to
    :param name: Final filename within ``<root>/<app>``
    :param stream: Open binary stream to copy in
    """
    with atomic_write(root, app, name) as handle:
        shutil.copyfileobj(stream, handle)


def list_backups(root: Path, app: str) -> list[BackupFile]:
    """List an apps stored backup files, manifests included, newest first

    Files that don't parse as backup names are skipped

    :param root: Backup root directory
    :param app: Application to list backups for
    :returns: The apps backup files with metadata, newest first
    """
    directory = root / app
    if not directory.is_dir():
        return []

    backups: list[BackupFile] = []
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        try:
            parsed = parse(entry.name)
        except InvalidBackupName:
            continue
        stat = entry.stat()
        backups.append(
            BackupFile(
                name=entry.name,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                timestamp=parsed.timestamp,
            )
        )
    backups.sort(key=lambda backup: (backup.timestamp, backup.name), reverse=True)
    return backups


def open_backup(root: Path, app: str, name: str) -> IO[bytes]:
    """Open a stored backup for reading

    :param root: Backup root directory
    :param app: Application the backup belongs to
    :param name: Filename within ``<root>/<app>``
    :returns: An open binary stream for the file
    """
    return (root / app / name).open("rb")


def free_space(root: Path) -> int:
    """Check the free space on the filesystem holding the backup root

    :param root: Directory to check
    :returns: Free space in bytes
    """
    directory = root
    while not directory.exists():
        parent = directory.parent
        if parent == directory:
            break
        directory = parent
    return shutil.disk_usage(directory).free


def list_runs(root: Path, app: str) -> list[BackupRun]:
    """Group an app's stored files into backup runs, newest first

    A run is complete once it has a dump, globals and manifest.

    :param root: Backup root directory
    :param app: Application to list runs for
    :returns: One entry per timestamp, newest first
    """
    names_by_timestamp: dict[datetime, list[str]] = {}
    for backup in list_backups(root, app):
        names_by_timestamp.setdefault(backup.timestamp, []).append(backup.name)

    runs: list[BackupRun] = []
    for timestamp, names in names_by_timestamp.items():
        complete = not _missing_kinds(names)
        runs.append(BackupRun(timestamp, tuple(sorted(names)), complete))
    runs.sort(key=lambda run: run.timestamp, reverse=True)
    return runs


def find_backup_pair(root: Path, app: str, timestamp: datetime | None = None) -> BackupPair:
    """Find one complete run's dump, globals and manifest

    A run is complete once its manifest exists, since the manifest is written last.

    :param root: Backup root directory
    :param app: Application to look up
    :param timestamp: Run to find, or None for the newest complete run
    :returns: The three filenames and the timestamp they share
    :raises BackupNotFound: If there are no complete backups, the timestamp is unknown, or that run
    is missing one of its artefacts
    """
    runs = list_runs(root, app)
    if not runs:
        raise BackupNotFound(f"no backups for {app} under {root}")

    if timestamp is None:
        chosen = _newest_complete(runs)
        if chosen is None:
            raise BackupNotFound(f"no complete backup for {app} under {root}")
    else:
        chosen = _run_at(runs, timestamp)
        if chosen is None:
            raise BackupNotFound(f"no backup found for {app} at {timestamp.isoformat()}")

    missing = _missing_kinds(chosen.names)
    if missing:
        raise BackupNotFound(
            f"backup for {app} at {chosen.timestamp.isoformat()} is missing its "
            f"{' and '.join(missing)}"
        )
    return BackupPair(
        _name_of_kind(chosen.names, "dump"),
        _name_of_kind(chosen.names, "globals"),
        _name_of_kind(chosen.names, "manifest"),
        chosen.timestamp,
    )


def _newest_complete(runs: list[BackupRun]) -> BackupRun | None:
    for run in runs:
        if run.complete:
            return run
    return None


def _run_at(runs: list[BackupRun], timestamp: datetime) -> BackupRun | None:
    for run in runs:
        if run.timestamp == timestamp:
            return run
    return None


def _missing_kinds(names: Sequence[str]) -> list[str]:
    """The artefact kinds a run doesn't have yet"""
    kinds = {parse(name).kind for name in names}
    return [kind for kind in _RUN_KINDS if kind not in kinds]


def _name_of_kind(names: Sequence[str], kind: ArtefactKind) -> str:
    """The filename of one kind in a complete run"""
    for name in names:
        if parse(name).kind == kind:
            return name
    raise BackupNotFound(f"no {kind} among {', '.join(names)}")
