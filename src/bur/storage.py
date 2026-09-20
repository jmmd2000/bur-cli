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

from bur.naming import InvalidBackupName, parse


@dataclass(frozen=True)
class BackupFile:
    """One stored backup and its metadata"""

    name: str
    size: int
    modified: datetime
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
        success = True
    finally:
        if not success:
            temp_path.unlink(missing_ok=True)


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
    """List an apps stored backups, newest first

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
