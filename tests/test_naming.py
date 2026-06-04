from datetime import UTC, datetime, timedelta, timezone

import pytest

from bur.naming import InvalidBackupName, build, parse


def test_round_trips_app_and_timestamp() -> None:
    when = datetime(2026, 6, 3, 17, 48, 0, tzinfo=UTC)
    parsed = parse(build("giftring", when))
    assert parsed.app == "giftring"
    assert parsed.timestamp == when
    assert parsed.kind == "dump"


def test_hyphenated_app_name_round_trips() -> None:
    when = datetime(2026, 6, 3, 17, 48, 0, tzinfo=UTC)
    parsed = parse(build("album-reviews", when))
    assert parsed.app == "album-reviews"
    assert parsed.timestamp == when


def test_globals_kind_round_trips() -> None:
    when = datetime(2026, 6, 3, 17, 48, 0, tzinfo=UTC)
    name = build("giftring", when, kind="globals")
    assert name == "giftring-20260603T174800Z.globals.sql.age"
    assert parse(name).kind == "globals"


def test_non_utc_timestamp_is_normalised_to_utc() -> None:
    eastern = timezone(timedelta(hours=-5))
    when = datetime(2026, 6, 3, 12, 48, 0, tzinfo=eastern)
    name = build("giftring", when)
    assert name == "giftring-20260603T174800Z.dump.age"
    assert parse(name).timestamp == when


def test_names_sort_lexically_by_time() -> None:
    earlier = build("app", datetime(2026, 6, 3, 1, 0, 0, tzinfo=UTC))
    later = build("app", datetime(2026, 6, 3, 2, 0, 0, tzinfo=UTC))
    assert sorted([later, earlier]) == [earlier, later]


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError):
        build("app", datetime(2026, 6, 3, 17, 48, 0))


def test_empty_app_is_rejected() -> None:
    with pytest.raises(ValueError):
        build("", datetime(2026, 6, 3, 17, 48, 0, tzinfo=UTC))
    eastern = timezone(timedelta(hours=-5))
    when = datetime(2026, 6, 3, 12, 48, 0, tzinfo=eastern)
    name = build("giftring", when)
    assert name == "giftring-20260603T174800Z.dump.age"
    assert parse(name).timestamp == when


@pytest.mark.parametrize(
    "name",
    [
        "giftring.dump.age",
        "giftring-2026-06-03.dump.age",
        "giftring-20260603T174800Z.dump",
        "giftring-20260603T174800Z.tar.age",
        "giftring-20260603T1748Z.dump.age",
        "",
    ],
)
def test_malformed_names_raise(name: str) -> None:
    with pytest.raises(InvalidBackupName):
        parse(name)
