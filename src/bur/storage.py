"""Store backups on local disk

A write goes to a temp file in the target directory and is renamed into place only
once it's fully written and fsynced, so a crashed or killed run never leaves a half-file
that looks real. Files live under <root>/<app>/
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Generator
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
    runs: dict[datetime, dict[str, str]] = {}
    for backup in list_backups(root, app):
        parsed = parse(backup.name)
        runs.setdefault(parsed.timestamp, {})[parsed.kind] = backup.name

    if not runs:
        raise BackupNotFound(f"no backups for {app} under {root}")

    if timestamp is None:
        complete = [when for when, artefacts in runs.items() if not _missing_kinds(artefacts)]
        if not complete:
            raise BackupNotFound(f"no complete backup for {app} under {root}")
        chosen = max(complete)
    else:
        chosen = timestamp

    artefacts = runs.get(chosen)
    if artefacts is None:
        raise BackupNotFound(f"no backup found for {app} at {chosen.isoformat()}")

    missing = _missing_kinds(artefacts)
    if missing:
        raise BackupNotFound(
            f"backup for {app} at {chosen.isoformat()} is missing its {' and '.join(missing)}"
        )
    return BackupPair(artefacts["dump"], artefacts["globals"], artefacts["manifest"], chosen)


def _missing_kinds(artefacts: dict[str, str]) -> list[str]:
    """The artefact kinds a run doesn't have yet"""
    return [kind for kind in _RUN_KINDS if kind not in artefacts]
