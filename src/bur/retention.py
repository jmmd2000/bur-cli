"""Retention for backups, keep last N.

Sort the given backups newest first, keep the newest and mark the rest for deletion.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from bur.naming import parse


class RetentionPlan(NamedTuple):
    """Backup names to keep vs delete."""

    keep: tuple[str, ...]
    delete: tuple[str, ...]


def plan_retention(names: Sequence[str], keep: int) -> RetentionPlan:
    """Plan keep last N retention over a set of backups

    :param names: Backup artefact filenames of one kind
    :param keep: How many of the newest backups to retain, >= 0
    :returns: The names to keep (newest first) and the names to delete
    :raises ValueError: if ``keep`` is < 0
    :raises bur.naming.InvalidBackupName: If any name cannot be parsed
    """
    if keep < 0:
        raise ValueError("keep must not be negative")
    ordered = sorted(names, key=lambda name: (parse(name).timestamp, name), reverse=True)
    return RetentionPlan(keep=tuple(ordered[:keep]), delete=tuple(ordered[keep:]))
