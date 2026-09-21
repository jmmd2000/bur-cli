"""
Find the databases that opted in to backups
Reads running containers and their ``bur.*`` labels from Docker, and resolves
those labels into targets with defaults applied and names validated.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from bur.config import Config


class DiscoveryError(RuntimeError):
    """Docker could not be queried for running containers"""


@dataclass(frozen=True)
class ContainerInfo:
    """A running container's name, image and labels"""

    name: str
    image: str
    labels: dict[str, str]


_ENABLE_LABEL = "bur.enable"
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_DEFAULT_USER = "postgres"
_DEFAULT_SCHEDULE = "daily"


class InvalidLabel(ValueError):
    """A ``bur.*`` label value is not usable"""


@dataclass(frozen=True)
class DBTarget:
    """One database to back up, resolved from a container's labels"""

    container: str
    app: str
    database: str
    user: str
    retention_keep: int
    schedule: str
    excluded: bool


def inspect_running(label_filter: str | None = None) -> list[ContainerInfo]:
    """Inspect the running containers, optionally only those with a label

    :param label_filter: A label to filter on, or None for all
    :returns: One entry per running container
    :raises DiscoveryError: If docker could not be listed or inspected
    """
    command = ["docker", "ps", "-q"]
    if label_filter is not None:
        command += ["--filter", f"label={label_filter}"]

    listed = subprocess.run(command, capture_output=True, text=True)
    if listed.returncode != 0:
        raise DiscoveryError(f"could not list containers: {listed.stderr.strip()}")

    ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if not ids:
        return []

    inspected = subprocess.run(["docker", "inspect", *ids], capture_output=True, text=True)
    if inspected.returncode != 0:
        raise DiscoveryError(f"could not inspect containers: {inspected.stderr.strip()}")

    entries: list[dict[str, object]] = json.loads(inspected.stdout)
    return [_read_container(entry) for entry in entries]


def discover(config: Config) -> list[DBTarget]:
    """Find every database that opted into backups

    :param config: Loaded config
    :returns: One target per labelled container
    :raises InvalidLabel: If any containers labels are not usable
    """
    containers = inspect_running(f"{_ENABLE_LABEL}=true")
    return [resolve(container, config) for container in containers]


def resolve(container: ContainerInfo, config: Config) -> DBTarget:
    """Resolve one containers labels into a database target

    :param container: A running container with ```bur.*`` labels
    :param config: Loaded config
    :returns: The resolved target
    :raises InvalidLabel: If a name or retention value is not usable
    """
    labels = container.labels
    app = _name(labels.get("bur.app", container.name), "bur.app")
    database = _name(labels.get("bur.database", app), "bur.database")
    user = _name(labels.get("bur.user", _DEFAULT_USER), "bur.user")

    return DBTarget(
        container=container.name,
        app=app,
        database=database,
        user=user,
        retention_keep=_keep(labels, config.retention_keep),
        schedule=labels.get("bur.schedule", _DEFAULT_SCHEDULE),
        excluded=app in config.exclude,
    )


def _name(value: str, label: str) -> str:
    """Check a name is safe to use in a filename or path"""
    if not _NAME_PATTERN.match(value):
        raise InvalidLabel(f"{label} must match {_NAME_PATTERN.pattern}, got {value!r}")
    return value


def _keep(labels: dict[str, str], default: int) -> int:
    """Read ``bur.retention.keep``, falling back to the global default"""
    raw = labels.get("bur.retention.keep")
    if raw is None:
        return default
    try:
        keep = int(raw)
    except ValueError as error:
        raise InvalidLabel(f"bur.retention.keep must be a number, got {raw!r}") from error
    if keep < 1:
        raise InvalidLabel(f"bur.retention.keep must be at least 1, got {keep}")
    return keep


def _read_container(entry: dict[str, object]) -> ContainerInfo:
    """Pull the name, image and labels out of one ``docker inspect`` entry"""
    config = entry.get("Config")
    settings: dict[str, object] = config if isinstance(config, dict) else {}

    raw_labels = settings.get("Labels")
    labels: dict[str, object] = raw_labels if isinstance(raw_labels, dict) else {}

    name = entry.get("Name")
    image = settings.get("Image")

    return ContainerInfo(
        name=name.lstrip("/") if isinstance(name, str) else "",
        image=image if isinstance(image, str) else "",
        labels={str(key): str(value) for key, value in labels.items()},
    )
