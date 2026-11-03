"""Retention for backups, keep the last N complete runs.

Plans over whole runs, never single files, so a dump is never kept without its
globals. Nothing here deletes anything: the plan says what to delete.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from bur.storage import BackupRun


class RetentionPlan(NamedTuple):
    """Backup runs to keep vs delete."""

    keep: tuple[BackupRun, ...]
    delete: tuple[BackupRun, ...]


def plan_retention(runs: Sequence[BackupRun], keep: int) -> RetentionPlan:
    """Plan keep last N retention over one app's backup runs

    Keeps the newest ``keep`` complete runs. An incomplete run newer than every
    complete run is kept too, since it may still be in progress. Every other run
    is deleted.

    :param runs: One app's backup runs, in any order
    :param keep: How many complete runs to retain, at least 1
    :returns: The runs to keep and the runs to delete, each newest first
    :raises ValueError: If ``keep`` is less than 1
    """
    if keep < 1:
        raise ValueError("keep must be at least 1")

    newest_first = sorted(runs, key=lambda run: run.timestamp, reverse=True)
    kept: list[BackupRun] = []
    deleted: list[BackupRun] = []
    complete_kept = 0

    for run in newest_first:
        seen_a_complete_run = complete_kept > 0
        if run.complete and complete_kept < keep:
            kept.append(run)
            complete_kept += 1
        elif not run.complete and not seen_a_complete_run:
            kept.append(run)
        else:
            deleted.append(run)

    return RetentionPlan(keep=tuple(kept), delete=tuple(deleted))
