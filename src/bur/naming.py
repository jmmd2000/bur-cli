"""Filename helpers for backups.

A backup run writes two artefacts that share one timestamp:

<app>-<YYYYMMDDThhmmssZ>.dump.age
<app>-<YYYYMMDDThhmmssZ>.globals.sql.age

The timestamp is ISO 8601 *basic* format in UTC, so the names sort lexically in chronological order.
App names could contain hyphens so the parsing uses the timestamp pattern rather than the hyphen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

ArtefactKind = Literal["dump", "globals"]

_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

_SUFFIXES: dict[ArtefactKind, str] = {
    "dump": ".dump.age",
    "globals": ".globals.sql.age",
}

_SUFFIX_TO_KIND: dict[str, ArtefactKind] = {
    "dump": "dump",
    "globals.sql": "globals",
}

_NAME_PATTERN = re.compile(
    r"^(?P<app>.+)-(?P<timestamp>\d{8}T\d{6}Z)\.(?P<suffix>dump|globals\.sql)\.age$"
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
    :param kind: Which artefact to name, ``"dump"`` or ``"globals"``
    :returns: The artefact filename
    :raises ValueError: if ``app`` is empty or ``when`` is not UTC
    """
    if not app:
        raise ValueError("app name must not be empty")
    if when.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    stamp = when.astimezone(UTC).strftime(_TIMESTAMP_FORMAT)
    return f"{app}-{stamp}{_SUFFIXES[kind]}"


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


def parse_timestamp(text: str) -> datetime:
    """Parse a backup timestamp in the filename format

    :param text: Timestamp text
    :returns: The timestamp in UTC
    :raises: InvalidBackupName: If the text is not in the filename format"""
    try:
        return datetime.strptime(text, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError as error:
        raise InvalidBackupName(f"not a backup timestamp: {text!r}") from error
