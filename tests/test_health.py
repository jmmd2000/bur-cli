from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bur.health import evaluate, is_due, next_due

_NOW = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
_DAILY = timedelta(days=1)


def test_no_backup_is_never_backed_up() -> None:
    health = evaluate(None, _DAILY, 24, _NOW)

    assert health.level == "never"
    assert health.age is None


@pytest.mark.parametrize(
    ("age", "level"),
    [
        (timedelta(hours=3), "ok"),
        (timedelta(hours=48), "ok"),
        (timedelta(hours=48, seconds=1), "overdue"),
    ],
)
def test_a_backup_is_overdue_after_its_interval_plus_the_grace(age: timedelta, level: str) -> None:
    health = evaluate(_NOW - age, _DAILY, 24, _NOW)

    assert health.level == level
    assert health.age == age


@pytest.mark.parametrize(
    ("age", "offsite", "level"),
    [
        (timedelta(hours=3), False, "local_only"),
        (timedelta(hours=3), None, "ok"),
        (timedelta(hours=3), True, "ok"),
        (timedelta(hours=49), False, "overdue"),
    ],
)
def test_a_backup_missing_from_the_remote_is_local_only(
    age: timedelta, offsite: bool | None, level: str
) -> None:
    assert evaluate(_NOW - age, _DAILY, 24, _NOW, offsite).level == level


def test_a_database_with_no_backup_is_due() -> None:
    assert is_due(None, _DAILY, _NOW) is True


@pytest.mark.parametrize(
    ("age", "due"),
    [
        (timedelta(hours=21, minutes=36), False),
        (timedelta(hours=21, minutes=36, seconds=1), True),
        (timedelta(hours=23, minutes=59, seconds=57), True),
    ],
)
def test_a_backup_is_due_after_90_percent_of_its_interval(age: timedelta, due: bool) -> None:
    assert is_due(_NOW - age, _DAILY, _NOW) is due


def test_next_due_is_90_percent_of_the_interval_after_the_last_success() -> None:
    assert next_due(_NOW, _DAILY) == _NOW + timedelta(hours=21, minutes=36)
