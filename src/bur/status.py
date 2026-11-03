"""Read each database's backup status from the backups on disk

The newest complete run under ``<backup_root>/<app>/`` is the last success.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from bur import health, storage
from bur.config import Config
from bur.discovery import DBTarget
from bur.health import Health
from bur.naming import parse


@dataclass(frozen=True)
class DatabaseStatus:
    """One discovered database's health and the size of its newest stored dump"""

    target: DBTarget
    health: Health
    dump_size: int | None


def database_status(config: Config, target: DBTarget, now: datetime) -> DatabaseStatus:
    """Work out one database's status from its stored backups

    :param config: Loaded configuration (backup root and ``overdue_hours``)
    :param target: The discovered database
    :param now: The current time, timezone-aware
    :returns: Its health and the size of the dump in its newest complete run
    """
    newest = _newest_complete_run(config, target.app)
    if newest is None:
        never = health.evaluate(None, target.interval, config.overdue_hours, now)
        return DatabaseStatus(target, never, None)

    evaluated = health.evaluate(newest.timestamp, target.interval, config.overdue_hours, now)
    return DatabaseStatus(target, evaluated, _dump_size(config, target.app, newest))


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


def _dump_size(config: Config, app: str, run: storage.BackupRun) -> int | None:
    for backup in storage.list_backups(config.backup_root, app):
        if backup.name in run.names and parse(backup.name).kind == "dump":
            return backup.size
    return None
