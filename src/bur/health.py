"""Work out whether a database's backups are up to date

Pure logic: the caller passes the newest complete backup and the current time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

HealthLevel = Literal["ok", "overdue", "never"]


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
