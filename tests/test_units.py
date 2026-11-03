from __future__ import annotations

from datetime import timedelta

import pytest

from bur.units import readable_age, readable_size


@pytest.mark.parametrize(
    ("size", "text"),
    [(512, "0.5 KB"), (5 * 1024**2, "5.0 MB"), (3 * 1024**3, "3.0 GB")],
)
def test_readable_size_picks_the_unit(size: int, text: str) -> None:
    assert readable_size(size) == text


@pytest.mark.parametrize(
    ("age", "text"),
    [
        (timedelta(hours=3, minutes=12), "3h 12m"),
        (timedelta(days=2, hours=4, minutes=30), "2d 4h"),
        (timedelta(minutes=-5), "0h 0m"),
    ],
)
def test_readable_age_picks_the_unit(age: timedelta, text: str) -> None:
    assert readable_age(age) == text
