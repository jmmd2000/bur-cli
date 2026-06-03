from datetime import UTC, datetime

import pytest

from bur.naming import InvalidBackupName, build
from bur.retention import plan_retention


def _name(app: str, hour: int) -> str:
    return build(app, datetime(2026, 6, 3, hour, 0, 0, tzinfo=UTC))


def test_keeps_newest_n_and_deletes_the_rest() -> None:
    older = _name("app", 1)
    middle = _name("app", 2)
    newest = _name("app", 3)
    plan = plan_retention([older, newest, middle], keep=2)
    assert plan.keep == (newest, middle)
    assert plan.delete == (older,)


def test_unpacks_as_keep_delete_tuple() -> None:
    keep, delete = plan_retention([_name("app", 1)], keep=1)
    assert keep == (_name("app", 1),)
    assert delete == ()


def test_empty_input() -> None:
    assert plan_retention([], keep=5) == ((), ())


def test_fewer_than_keep_deletes_nothing() -> None:
    names = [_name("app", 1), _name("app", 2)]
    plan = plan_retention(names, keep=5)
    assert set(plan.keep) == set(names)
    assert plan.delete == ()


def test_exactly_keep_deletes_nothing() -> None:
    names = [_name("app", 1), _name("app", 2)]
    plan = plan_retention(names, keep=2)
    assert plan.delete == ()


def test_keep_zero_deletes_everything() -> None:
    names = [_name("app", 1), _name("app", 2)]
    plan = plan_retention(names, keep=0)
    assert plan.keep == ()
    assert set(plan.delete) == set(names)


def test_most_recent_is_never_deleted() -> None:
    newest = _name("app", 9)
    names = [_name("app", hour) for hour in range(1, 9)] + [newest]
    plan = plan_retention(names, keep=3)
    assert newest in plan.keep
    assert newest not in plan.delete


def test_tied_timestamps_are_ordered_deterministically() -> None:
    when = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)
    dump = build("app", when, kind="dump")
    globals_name = build("app", when, kind="globals")
    first = plan_retention([dump, globals_name], keep=1)
    second = plan_retention([globals_name, dump], keep=1)
    assert first == second
    assert len(first.keep) == 1
    assert len(first.delete) == 1


def test_negative_keep_is_rejected() -> None:
    with pytest.raises(ValueError):
        plan_retention([], keep=-1)


def test_unparseable_name_raises() -> None:
    with pytest.raises(InvalidBackupName):
        plan_retention(["not-a-backup.txt"], keep=1)
