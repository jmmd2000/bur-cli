from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from bur.manifest import ArtefactRecord, DatabaseRecord, Manifest, ManifestError, decode, encode


def _manifest() -> Manifest:
    return Manifest(
        app="issues",
        timestamp=datetime(2026, 9, 22, 2, 0, tzinfo=UTC),
        postgres_major=16,
        globals=ArtefactRecord("issues-20260922T020000Z.globals.sql.age", 1204, "aa11"),
        databases={
            "issues": DatabaseRecord(
                dump=ArtefactRecord("issues-20260922T020000Z.dump.age", 48213, "bb22"),
                table_count=14,
            )
        },
    )


def _content() -> dict[str, object]:
    loaded = json.loads(encode(_manifest()))
    assert isinstance(loaded, dict)
    return loaded


def test_a_manifest_round_trips() -> None:
    assert decode(encode(_manifest())) == _manifest()


def test_the_timestamp_is_stored_in_the_filename_format() -> None:
    assert _content()["timestamp"] == "20260922T020000Z"


def test_decode_rejects_text_that_is_not_json() -> None:
    with pytest.raises(ManifestError):
        decode(b"not json")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("app", ""),
        ("timestamp", "2026-09-22"),
        ("postgres_major", "16"),
        ("postgres_major", True),
        ("globals", None),
        ("databases", []),
    ],
)
def test_decode_rejects_a_bad_field(key: str, value: object) -> None:
    content = _content()
    content[key] = value

    with pytest.raises(ManifestError):
        decode(json.dumps(content).encode())


def test_decode_rejects_a_bad_table_count() -> None:
    content = _content()
    databases = content["databases"]
    assert isinstance(databases, dict)
    databases["issues"]["table_count"] = -1

    with pytest.raises(ManifestError):
        decode(json.dumps(content).encode())
