"""Read each database's backup status from the backups on disk and on the remote

The newest complete run under ``<backup_root>/<app>/`` is the last success.
When a remote is configured, a run is on it once all of its files are listed there.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from bur import health, remote, storage
from bur.config import Config
from bur.discovery import DBTarget
from bur.health import Health
from bur.naming import parse
from bur.remote import RemoteError

Location = Literal["local", "r2", "both"]


@dataclass(frozen=True)
class DatabaseStatus:
    """One discovered database's health and the size of its newest stored dump

    ``remote_error`` holds why the remote couldn't be listed, if it couldn't.
    """

    target: DBTarget
    health: Health
    dump_size: int | None
    remote_error: str | None = None


@dataclass(frozen=True)
class RunSummary:
    """One stored backup run, as ``bur status APP`` shows it"""

    timestamp: datetime
    complete: bool
    dump_size: int | None
    location: Location


def database_status(config: Config, target: DBTarget, now: datetime) -> DatabaseStatus:
    """Work out one database's status from its stored backups

    :param config: Loaded configuration (backup root and ``overdue_hours``)
    :param target: The discovered database
    :param now: The current time, timezone-aware
    :returns: Its health, the size of the dump in its newest complete run, and why
        the remote couldn't be listed if it couldn't
    """
    newest = _newest_complete_run(config, target.app)
    if newest is None:
        never = health.evaluate(None, target.interval, config.overdue_hours, now)
        return DatabaseStatus(target, never, None)

    offsite: bool | None = None
    remote_error: str | None = None
    if remote.is_configured(config):
        try:
            offsite = newest.timestamp in _remote_complete_timestamps(config, target.app)
        except RemoteError as error:
            offsite = False
            remote_error = str(error)

    evaluated = health.evaluate(
        newest.timestamp, target.interval, config.overdue_hours, now, offsite
    )
    dump_size = _dump_size(newest, _dump_sizes(config, target.app))
    return DatabaseStatus(target, evaluated, dump_size, remote_error)


def last_success(config: Config, app: str) -> datetime | None:
    """Find the timestamp of an app's newest complete backup

    :param config: Loaded configuration (backup root)
    :param app: Application to look up
    :returns: The timestamp, or None if the app has no complete backup
    """
    newest = _newest_complete_run(config, app)
    if newest is None:
        return None
    return newest.timestamp


def app_runs(config: Config, app: str) -> list[RunSummary]:
    """List every backup run for one app, local or on the remote, newest first

    A run only on the remote is listed when it is complete there. Its dump size is
    not known.

    :param config: Loaded configuration (backup root and remote)
    :param app: Application whose runs to list
    :returns: Each run's timestamp, whether it is complete, its dump size and where it is
    :raises RemoteError: If a remote is configured and can't be listed
    """
    remote_timestamps: set[datetime] = set()
    if remote.is_configured(config):
        remote_timestamps = _remote_complete_timestamps(config, app)

    dump_sizes = _dump_sizes(config, app)
    summaries: list[RunSummary] = []
    local_timestamps: set[datetime] = set()
    for run in storage.list_runs(config.backup_root, app):
        local_timestamps.add(run.timestamp)
        location: Location = "both" if run.timestamp in remote_timestamps else "local"
        summaries.append(
            RunSummary(run.timestamp, run.complete, _dump_size(run, dump_sizes), location)
        )
    for timestamp in remote_timestamps - local_timestamps:
        summaries.append(RunSummary(timestamp, True, None, "r2"))

    summaries.sort(key=lambda summary: summary.timestamp, reverse=True)
    return summaries


def undiscovered_apps(config: Config, discovered: Collection[str]) -> list[str]:
    """List the app folders under the backup root that no discovered database uses

    :param config: Loaded configuration (backup root)
    :param discovered: The apps of every discovered database, excluded ones included
    :returns: The folder names, sorted
    """
    if not config.backup_root.is_dir():
        return []
    return sorted(
        entry.name
        for entry in config.backup_root.iterdir()
        if entry.is_dir() and entry.name not in discovered
    )


def _newest_complete_run(config: Config, app: str) -> storage.BackupRun | None:
    for run in storage.list_runs(config.backup_root, app):
        if run.complete:
            return run
    return None


def _remote_complete_timestamps(config: Config, app: str) -> set[datetime]:
    """The timestamps of the runs that are complete on the remote"""
    runs = storage.group_runs(remote.list_names(config, app))
    return {run.timestamp for run in runs if run.complete}


def _dump_sizes(config: Config, app: str) -> dict[str, int]:
    """Map each stored dump's filename to its size"""
    return {
        backup.name: backup.size
        for backup in storage.list_backups(config.backup_root, app)
        if parse(backup.name).kind == "dump"
    }


def _dump_size(run: storage.BackupRun, dump_sizes: dict[str, int]) -> int | None:
    for name in run.names:
        if name in dump_sizes:
            return dump_sizes[name]
    return None
