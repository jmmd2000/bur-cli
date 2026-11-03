from datetime import UTC, datetime

import pytest

from bur.retention import plan_retention
from bur.storage import BackupRun


def _run(hour: int, complete: bool = True) -> BackupRun:
    return BackupRun(datetime(2026, 6, 3, hour, tzinfo=UTC), (), complete)


def test_keeps_the_newest_n_complete_runs() -> None:
    older, middle, newest = _run(1), _run(2), _run(3)

    plan = plan_retention([older, newest, middle], keep=2)

    assert plan.keep == (newest, middle)
    assert plan.delete == (older,)


def test_fewer_runs_than_keep_deletes_nothing() -> None:
    runs = [_run(1), _run(2)]

    plan = plan_retention(runs, keep=5)

    assert plan.delete == ()


def test_an_incomplete_run_does_not_count_towards_keep() -> None:
    older, broken, newest = _run(1), _run(2, complete=False), _run(3)

    plan = plan_retention([older, broken, newest], keep=2)

    assert plan.keep == (newest, older)
    assert plan.delete == (broken,)


def test_an_incomplete_run_newer_than_every_complete_run_is_kept() -> None:
    complete, in_progress = _run(1), _run(2, complete=False)

    plan = plan_retention([complete, in_progress], keep=1)

    assert plan.keep == (in_progress, complete)
    assert plan.delete == ()


def test_keep_below_one_is_rejected() -> None:
    with pytest.raises(ValueError):
        plan_retention([_run(1)], keep=0)
