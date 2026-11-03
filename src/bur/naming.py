"""Filename helpers for backups.

A backup run writes three files that share one timestamp:

<app>-<YYYYMMDDThhmmssZ>.dump.age
<app>-<YYYYMMDDThhmmssZ>.globals.sql.age
<app>-<YYYYMMDDThhmmssZ>.manifest.json

The timestamp is ISO 8601 *basic* format in UTC, so the names sort lexically in chronological order.
App names could contain hyphens so the parsing uses the timestamp pattern rather than the hyphen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

ArtefactKind = Literal["dump", "globals", "manifest"]

_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

_SUFFIXES: dict[ArtefactKind, str] = {
    "dump": ".dump.age",
    "globals": ".globals.sql.age",
    "manifest": ".manifest.json",
}

_SUFFIX_TO_KIND: dict[str, ArtefactKind] = {
    "dump.age": "dump",
    "globals.sql.age": "globals",
    "manifest.json": "manifest",
}

_NAME_PATTERN = re.compile(
    r"^(?P<app>.+)-(?P<timestamp>\d{8}T\d{6}Z)"
    r"\.(?P<suffix>dump\.age|globals\.sql\.age|manifest\.json)$"
)


class InvalidBackupName(ValueError):
    """Raised when a string is not a valid backup filename."""


@dataclass(frozen=True)
class ParsedName:
    """The app, timestamp, and kind parsed from a backup filename."""

    app: str
    timestamp: datetime
    kind: ArtefactKind


def build(app: str, when: datetime, kind: ArtefactKind = "dump") -> str:
    """Build a backup artefact filename

    :param app: Application name
    :param when: Run timestamp, timezone-aware, normalised to UTC
    :param kind: Which artefact to name, ``"dump"``, ``"globals"`` or ``"manifest"``
    :returns: The artefact filename
    :raises ValueError: if ``app`` is empty or ``when`` is not UTC
    """
    if not app:
        raise ValueError("app name must not be empty")
    return f"{app}-{format_timestamp(when)}{_SUFFIXES[kind]}"


def parse(name: str) -> ParsedName:
    """Parse a backup filename into its parts

    :param name: a filename produced by :func:`build`
    :returns: The app, timestamp and kind
    :raises InvalidBackupName: If ``name`` is not a valid filename
    """
    match = _NAME_PATTERN.match(name)
    if match is None:
        raise InvalidBackupName(f"not a backup name: {name!r}")
    timestamp = datetime.strptime(match["timestamp"], _TIMESTAMP_FORMAT).replace(tzinfo=UTC)

    return ParsedName(app=match["app"], timestamp=timestamp, kind=_SUFFIX_TO_KIND[match["suffix"]])


def format_timestamp(when: datetime) -> str:
    """Format a run timestamp the way filenames carry it

    :param when: Run timestamp, timezone-aware, normalised to UTC
    :returns: The timestamp text, e.g. ``20260603T174800Z``
    :raises ValueError: If ``when`` is not timezone-aware
    """
    if when.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return when.astimezone(UTC).strftime(_TIMESTAMP_FORMAT)


def parse_timestamp(text: str) -> datetime:
    """Parse a backup timestamp in the filename format

    :param text: Timestamp text
    :returns: The timestamp in UTC
    :raises: InvalidBackupName: If the text is not in the filename format"""
    try:
        return datetime.strptime(text, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError as error:
        raise InvalidBackupName(f"not a backup timestamp: {text!r}") from error
