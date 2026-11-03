"""Back up one database to encrypted local files, and carry out a restore drill

Backup names the files for a run, checks there's room for the dump, then
streams the dump and the cluster globals through age into atomic writes under
``<backup_root>/<app>/``. It writes the run's manifest last. The drill restores
a stored pair into a throwaway container, counts what came back and checks it
against the manifest.
"""

from __future__ import annotations

import secrets
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bur import crypto, dockerpg, manifest, naming, retention, storage
from bur.config import Config
from bur.container import throwaway
from bur.processes import RunningProcess


class BackupError(RuntimeError):
    """A backup could not be taken"""


@dataclass(frozen=True)
class BackupResult:
    """What one backup run produced"""

    app: str
    timestamp: datetime
    dump_name: str
    globals_name: str
    manifest_name: str
    database_size: int
    dry_run: bool


_DRILL_SUPERUSER = "drillsuper"
_BYTES_PER_MB = 1024**2
_BYTES_PER_GB = 1024**3
_VERSION_NUMBERS_PER_MAJOR = 10000


class DrillError(RuntimeError):
    """A restore drill did not produce a usable database"""


@dataclass(frozen=True)
class DrillResult:
    """What a restore drill restored"""

    app: str
    dump_name: str
    globals_name: str
    timestamp: datetime
    major: str
    row_counts: dict[str, int]


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

    Creates three files that share one timestamp: the dump, the cluster
    globals it needs to be usable, and then the manifest that records both.

    :param config: Loaded configuration (backup root and age recipients)
    :param app: Application the backup belongs to, names the artefacts
    :param container: Container running the database
    :param db: Database to dump
    :param user: Role to connect as
    :param now: Run timestamp, defaults to the current UTC time
    :param dry_run: If true, check space but write nothing
    :returns: What the run produced, or would have on a dry run
    :raises BackupError: If the dump could leave less than ``min_free_space_gb`` free
    """
    when = now or datetime.now(UTC)
    dump_name = naming.build(app, when, "dump")
    globals_name = naming.build(app, when, "globals")
    manifest_name = naming.build(app, when, "manifest")

    size = _read_database_size(container, db, user)
    free = storage.free_space(config.backup_root)
    reserve = int(config.min_free_space_gb * _BYTES_PER_GB)
    if free - size < reserve:
        raise BackupError(
            f"not enough free space for {app}: the dump can take up to {_readable_size(size)} "
            f"and {_readable_size(free)} is free under {config.backup_root}, which would leave "
            f"less than min_free_space_gb ({config.min_free_space_gb:g} GB)"
        )

    if dry_run:
        return BackupResult(app, when, dump_name, globals_name, manifest_name, size, dry_run=True)

    postgres_major = _read_postgres_major(container, db, user)
    table_count = len(_list_tables(container, db, user))

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
    manifest.write(
        config.backup_root,
        manifest.Manifest(
            app=app,
            timestamp=when,
            postgres_major=postgres_major,
            globals=manifest.record_artefact(config.backup_root, app, globals_name),
            databases={
                db: manifest.DatabaseRecord(
                    dump=manifest.record_artefact(config.backup_root, app, dump_name),
                    table_count=table_count,
                )
            },
        ),
    )
    return BackupResult(app, when, dump_name, globals_name, manifest_name, size, dry_run=False)


def prune(
    config: Config, app: str, keep: int, newest: datetime, *, dry_run: bool = False
) -> tuple[storage.BackupRun, ...]:
    """Delete an app's backup runs that keep last N retention doesn't keep

    :param config: Loaded configuration (backup root)
    :param app: Application whose runs to prune
    :param keep: How many complete runs to keep
    :param newest: Timestamp of the run just written. A dry run writes nothing,
        so it plans as if this run were stored and complete
    :param dry_run: If true, plan but delete nothing
    :returns: The runs deleted, or that would be deleted on a dry run, newest first
    """
    runs = storage.list_runs(config.backup_root, app)
    if dry_run:
        runs.append(storage.BackupRun(newest, (), complete=True))

    plan = retention.plan_retention(runs, keep)
    if not dry_run:
        for run in plan.delete:
            storage.delete_run(config.backup_root, app, run)
    return plan.delete


def restore_drill(
    config: Config,
    app: str,
    identity: str,
    major: str | None,
    db: str,
    *,
    timestamp: datetime | None = None,
) -> DrillResult:
    """Restore a stored backup into a throwaway container and verify

    Applies the cluster globals, then the dump, then counts the rows in every
    restored table and checks the number of tables against the run's manifest.

    :param config: Loaded configuration (backup root)
    :param app: Application whose backup to drill
    :param identity: Path to the age identity file
    :param major: Postgres major version for the throwaway container, or None for
        the version the manifest records
    :param db: Database name to restore into
    :param timestamp: Backup to drill, defaults to the newest
    :returns: What the drill restored
    :raises DrillError: If no tables were restored, the number of tables differs from
        the manifest, or every table is empty
    :raises ManifestError: If the run's manifest cannot be read
    """
    pair = storage.find_backup_pair(config.backup_root, app, timestamp)
    recorded = manifest.read(config.backup_root, app, pair.manifest_name)
    expected_tables = _recorded_table_count(recorded, pair.dump_name)
    image_major = major or str(recorded.postgres_major)
    password = secrets.token_hex(16)

    with throwaway(f"postgres:{image_major}", _DRILL_SUPERUSER, password, db) as name:
        _restore_artefact(
            config.backup_root,
            app,
            pair.globals_name,
            identity,
            dockerpg.restore_globals(name, _DRILL_SUPERUSER),
        )
        _restore_artefact(
            config.backup_root,
            app,
            pair.dump_name,
            identity,
            dockerpg.restore(name, db, _DRILL_SUPERUSER),
        )
        row_counts = _count_every_table(name, db, _DRILL_SUPERUSER)

    if not row_counts:
        raise DrillError(f"{app} restored no tables")
    if len(row_counts) != expected_tables:
        raise DrillError(
            f"{app} restored {len(row_counts)} tables but the manifest records "
            f"{expected_tables}. A schema change while the backup ran can also cause this"
        )
    if not any(row_counts.values()):
        raise DrillError(f"{app} restored {len(row_counts)} tables, all of them empty")

    return DrillResult(
        app, pair.dump_name, pair.globals_name, pair.timestamp, image_major, row_counts
    )


def _recorded_table_count(recorded: manifest.Manifest, dump_name: str) -> int:
    """Find the table count the manifest records for one dump"""
    for database in recorded.databases.values():
        if database.dump.file == dump_name:
            return database.table_count
    raise DrillError(f"the manifest for {recorded.app} does not list {dump_name}")


def _readable_size(size: int) -> str:
    """Format a size in bytes as MB below a gigabyte, GB from there up"""
    if size < _BYTES_PER_GB:
        return f"{size / _BYTES_PER_MB:.1f} MB"
    return f"{size / _BYTES_PER_GB:.1f} GB"


def _read_database_size(container: str, db: str, user: str) -> int:
    """Read the ``pg_database_size`` for the given database"""
    raw = _read_output(dockerpg.database_size(container, db, user))
    try:
        return int(raw)
    except ValueError as error:
        raise BackupError(f"could not read database size, got {raw!r}") from error


def _read_postgres_major(container: str, db: str, user: str) -> int:
    """Read the server's Postgres major version, e.g. 16 for 16.4"""
    raw = _read_output(dockerpg.server_version(container, db, user))
    try:
        return int(raw) // _VERSION_NUMBERS_PER_MAJOR
    except ValueError as error:
        raise BackupError(f"could not read the Postgres version, got {raw!r}") from error


def _encrypt_to_file(
    source: RunningProcess,
    recipients: Sequence[str],
    root: Path,
    app: str,
    name: str,
) -> None:
    """Stream a dump process thru age into one atomic backup file"""
    with storage.atomic_write(root, app, name) as out:
        crypto.encrypt(source, recipients, out)


def _restore_artefact(
    root: Path,
    app: str,
    name: str,
    identity: str,
    target: RunningProcess,
) -> None:
    """Decrypt one stored artefact into a running restore process"""
    with storage.open_backup(root, app, name) as ciphertext:
        source = crypto.decrypt(ciphertext, identity)
        _pipe(source, target)


def _pipe(source: RunningProcess, target: RunningProcess) -> None:
    """Copy a decrypt's output into a restore's input, checking both exit codes

    If the restore stopped reading part way, its error is checked first, otherwise the decrypt's is.
    """
    source_stdout = source.process.stdout
    target_stdin = target.process.stdin
    if source_stdout is None or target_stdin is None:
        raise DrillError("restore pipeline is missing a pipe")

    target_stopped_reading = False
    try:
        shutil.copyfileobj(source_stdout, target_stdin)
    except BrokenPipeError:
        target_stopped_reading = True

    target_stdin.close()
    source_stdout.close()

    if target_stopped_reading:
        dockerpg.check(target)
        crypto.check(source)
    else:
        crypto.check(source)
        dockerpg.check(target)


def _list_tables(container: str, db: str, user: str) -> list[str]:
    """List a db's user tables"""
    listing = _read_output(dockerpg.table_names(container, db, user))
    return [line.strip() for line in listing.splitlines() if line.strip()]


def _count_every_table(container: str, db: str, user: str) -> dict[str, int]:
    """Count the rows in each of a db's user tables"""
    row_counts: dict[str, int] = {}
    for table in _list_tables(container, db, user):
        counted = _read_output(dockerpg.count_rows(container, db, user, table))
        row_counts[table] = int(counted)
    return row_counts


def _read_output(running: RunningProcess) -> str:
    """Read a query's stdout, error if it exited non-zero"""
    stream = running.process.stdout
    if stream is None:
        raise RuntimeError("query process has no stdout")
    output = stream.read().decode()
    stream.close()
    dockerpg.check(running)
    return output.strip()
