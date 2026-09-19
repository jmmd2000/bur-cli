"""Back up one database to encrypted local files

Names the two artefacts for a run, checks there's room for the dump, then
streams the dump and the cluster globals through age into atomic writes under
``<backup_root>/<app>/``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bur import crypto, dockerpg, naming, storage
from bur.config import Config


class BackupError(RuntimeError):
    """A backup could not be taken"""


@dataclass(frozen=True)
class BackupResult:
    """What one backup run produced"""

    app: str
    dump_name: str
    globals_name: str
    database_size: int
    dry_run: bool


def backup_database(
    config: Config,
    app: str,
    container: str,
    db: str,
    user: str,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> BackupResult:
    """Back up one database to encrypted local files

    Creates two artefacts that share one timestamp: the dump
    and the cluster globals it needs to be usable.

    :param config: Loaded configuration (backup root and age recipients)
    :param app: Application the backup belongs to, names the artefacts
    :param container: Container running the database
    :param db: Database to dump
    :param user: Role to connect as
    :param now: Run timestamp, defaults to the current UTC time
    :param dry_run: If true, check space but write nothing
    :returns: What the run produced, or would have on a dry run
    :raises BackupError: If there isn't enough free space for the dump
    """
    when = now or datetime.now(UTC)
    dump_name = naming.build(app, when, "dump")
    globals_name = naming.build(app, when, "globals")

    size = _read_database_size(container, db, user)
    free = storage.free_space(config.backup_root)
    if free < size:
        raise BackupError(
            f"not enough free space for {app}: needs at least {size} bytes, "
            f"{free} available under {config.backup_root}"
        )

    if dry_run:
        return BackupResult(app, dump_name, globals_name, size, dry_run=True)

    _encrypt_to_file(
        dockerpg.dump(container, db, user),
        config.age_recipients,
        config.backup_root,
        app,
        dump_name,
    )
    _encrypt_to_file(
        dockerpg.dump_globals(container, user),
        config.age_recipients,
        config.backup_root,
        app,
        globals_name,
    )
    return BackupResult(app, dump_name, globals_name, size, dry_run=False)


def _read_database_size(container: str, db: str, user: str) -> int:
    """Read the ``pg_database_size`` for the given database"""
    process = dockerpg.database_size(container, db, user)
    stream = process.stdout
    if stream is None:
        raise BackupError("database size query produced no output")
    raw = stream.read()
    stream.close()
    process.wait()
    dockerpg.check(process)
    try:
        return int(raw.strip())
    except ValueError as error:
        raise BackupError(f"could not read database size, got {raw!r}") from error


def _encrypt_to_file(
    source: subprocess.Popen[bytes],
    recipients: Sequence[str],
    root: Path,
    app: str,
    name: str,
) -> None:
    """Stream a dump process thru age into one atomic backup file"""
    with storage.atomic_write(root, app, name) as out:
        crypto.encrypt(source, recipients, out)
