from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bur.health import evaluate

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
