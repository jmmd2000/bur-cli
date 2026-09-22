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

from bur import dockerpg
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


@dataclass(frozen=True)
class LabelProblem:
    """A container whose labels could not be resolved"""

    container: str
    problem: str


@dataclass(frozen=True)
class Discovered:
    """The resolved targets, and the containers that could not be resolved"""

    targets: list[DBTarget]
    problems: list[LabelProblem]


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


def discover(config: Config) -> Discovered:
    """Find every database that opted in to backups

    :param config: Loaded configuration, for the fallback retention and exclusions
    :returns: The resolved targets and any containers that could not be resolved
    :raises DiscoveryError: If docker could not be listed or inspected
    """
    targets: list[DBTarget] = []
    problems: list[LabelProblem] = []

    for container in inspect_running(f"{_ENABLE_LABEL}=true"):
        try:
            targets.append(resolve(container, config))
        except InvalidLabel as error:
            problems.append(LabelProblem(container.name, str(error)))

    return Discovered(targets, problems)


def audit(config: Config) -> list[ContainerInfo]:
    """Find running Postgres containers that never opted in

    Containers named in the config's ``exclude`` list are skipped.

    :param config: Loaded configuration, for ``postgres_image_match`` and ``exclude``
    :returns: Containers whose image matches but which carry no ``bur.enable`` label
    :raises DiscoveryError: If docker could not be listed or inspected
    """
    return [
        container for container in inspect_running() if _is_unlabelled_database(container, config)
    ]


def _is_unlabelled_database(container: ContainerInfo, config: Config) -> bool:
    """Whether a container looks like a database that never opted in"""
    if config.postgres_image_match not in container.image:
        return False
    if _ENABLE_LABEL in container.labels:
        return False
    return container.name not in config.exclude


def probe_auth(target: DBTarget) -> str | None:
    """Check that bur can connect to a target

    :param target: A resolved database target
    :returns: None if the connection works, otherwise why it didn't
    """
    process = dockerpg.probe(target.container, target.database, target.user)
    _, errors = process.communicate()
    if process.returncode == 0:
        return None
    return errors.decode().strip() or f"exit code {process.returncode}"


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
