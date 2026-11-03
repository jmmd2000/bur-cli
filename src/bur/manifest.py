"""Record what one backup run wrote

The manifest is a small JSON file written after every other artefact of a run,
so a run is complete only once its manifest exists. It is plain JSON, not
encrypted. It records each artefact's size and sha256, the Postgres major
version, and how many user tables each database had.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bur import naming, storage
from bur.naming import InvalidBackupName

_CHUNK_SIZE = 1024**2


class ManifestError(ValueError):
    """A manifest file could not be read or is not in the expected shape"""


@dataclass(frozen=True)
class ArtefactRecord:
    """One stored artefact, as the manifest records it"""

    file: str
    size: int
    sha256: str


@dataclass(frozen=True)
class DatabaseRecord:
    """One database in a run: its dump and how many user tables it had"""

    dump: ArtefactRecord
    table_count: int


@dataclass(frozen=True)
class Manifest:
    """What one backup run wrote"""

    app: str
    timestamp: datetime
    postgres_major: int
    globals: ArtefactRecord
    databases: Mapping[str, DatabaseRecord]


def record_artefact(root: Path, app: str, name: str) -> ArtefactRecord:
    """Read a stored artefact's size and sha256

    :param root: Backup root directory
    :param app: Application the backup belongs to
    :param name: Filename within ``<root>/<app>``
    :returns: The artefact's name, size and sha256
    """
    digest = hashlib.sha256()
    size = 0
    with storage.open_backup(root, app, name) as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return ArtefactRecord(file=name, size=size, sha256=digest.hexdigest())


def write(root: Path, manifest: Manifest) -> str:
    """Write a run's manifest beside its artefacts

    :param root: Backup root directory
    :param manifest: The manifest to write
    :returns: The manifest's filename
    """
    name = naming.build(manifest.app, manifest.timestamp, "manifest")
    with storage.atomic_write(root, manifest.app, name) as handle:
        handle.write(encode(manifest))
    return name


def read(root: Path, app: str, name: str) -> Manifest:
    """Read and check a stored manifest

    :param root: Backup root directory
    :param app: Application the backup belongs to
    :param name: The manifest's filename within ``<root>/<app>``
    :returns: The manifest
    :raises ManifestError: If the file is not valid JSON or not in the expected shape
    """
    with storage.open_backup(root, app, name) as handle:
        return decode(handle.read())


def encode(manifest: Manifest) -> bytes:
    """Turn a manifest into its JSON file contents"""
    content = {
        "app": manifest.app,
        "timestamp": naming.format_timestamp(manifest.timestamp),
        "postgres_major": manifest.postgres_major,
        "globals": _encode_artefact(manifest.globals),
        "databases": {
            name: {"dump": _encode_artefact(record.dump), "table_count": record.table_count}
            for name, record in manifest.databases.items()
        },
    }
    return (json.dumps(content, indent=2) + "\n").encode()


def decode(raw: bytes) -> Manifest:
    """Turn JSON file contents into a manifest

    :param raw: The manifest file's contents
    :returns: The manifest
    :raises ManifestError: If ``raw`` is not valid JSON or not in the expected shape
    """
    try:
        content = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ManifestError(f"manifest is not valid JSON: {error}") from error
    top = _require_object(content, "manifest")

    try:
        timestamp = naming.parse_timestamp(_require_str(top, "timestamp"))
    except InvalidBackupName as error:
        raise ManifestError(f"manifest timestamp is not valid: {error}") from error

    databases: dict[str, DatabaseRecord] = {}
    for name, value in _require_object(top.get("databases"), "databases").items():
        database = _require_object(value, f"databases.{name}")
        databases[name] = DatabaseRecord(
            dump=_decode_artefact(database.get("dump"), f"databases.{name}.dump"),
            table_count=_require_int(database, "table_count"),
        )

    return Manifest(
        app=_require_str(top, "app"),
        timestamp=timestamp,
        postgres_major=_require_int(top, "postgres_major"),
        globals=_decode_artefact(top.get("globals"), "globals"),
        databases=databases,
    )


def _encode_artefact(record: ArtefactRecord) -> dict[str, object]:
    return {"file": record.file, "size": record.size, "sha256": record.sha256}


def _decode_artefact(value: object, where: str) -> ArtefactRecord:
    artefact = _require_object(value, where)
    return ArtefactRecord(
        file=_require_str(artefact, "file"),
        size=_require_int(artefact, "size"),
        sha256=_require_str(artefact, "sha256"),
    )


def _require_object(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ManifestError(f"manifest {where} must be an object")
    return value


def _require_str(content: dict[str, object], key: str) -> str:
    value = content.get(key)
    if not isinstance(value, str) or not value:
        raise ManifestError(f"manifest {key} must be a non-empty string")
    return value


def _require_int(content: dict[str, object], key: str) -> int:
    value = content.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ManifestError(f"manifest {key} must be a whole number, 0 or more")
    return value
