"""Work out whether a database's backups are up to date

Pure logic: the caller passes the newest complete backup and the current time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

HealthLevel = Literal["ok", "overdue", "never"]

_DUE_FRACTION = 0.9


@dataclass(frozen=True)
class Health:
    """How up to date one database's backups are"""

    level: HealthLevel
    last_success: datetime | None
    age: timedelta | None


def evaluate(
    last_success: datetime | None, interval: timedelta, overdue_hours: float, now: datetime
) -> Health:
    """Work out one database's health from its newest complete backup

    A database is overdue once its newest complete backup is older than its
    interval plus ``overdue_hours`` of grace.

    :param last_success: Timestamp of the newest complete backup, or None if there is none
    :param interval: Time between backups, from the database's schedule
    :param overdue_hours: Grace period on top of the interval
    :param now: The current time, timezone-aware
    :returns: The health level, the last success and its age
    """
    if last_success is None:
        return Health("never", None, None)

    age = now - last_success
    if age > interval + timedelta(hours=overdue_hours):
        return Health("overdue", last_success, age)
    return Health("ok", last_success, age)


def is_due(last_success: datetime | None, interval: timedelta, now: datetime) -> bool:
    """Whether a database needs a new backup

    It is due when it has no complete backup, or its newest one is older than
    90% of its interval. The slack stops a timer that fires at the same time
    every day from skipping every other run, since each backup is stamped a
    little after the tick.

    :param last_success: Timestamp of the newest complete backup, or None if there is none
    :param interval: Time between backups, from the database's schedule
    :param now: The current time, timezone-aware
    :returns: True if the database should be backed up now
    """
    if last_success is None:
        return True
    return now - last_success > interval * _DUE_FRACTION


def next_due(last_success: datetime, interval: timedelta) -> datetime:
    """When a database next becomes due, by the rule in :func:`is_due`"""
    return last_success + interval * _DUE_FRACTION
