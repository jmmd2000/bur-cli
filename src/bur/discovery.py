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
from datetime import timedelta

from bur import dockerpg
from bur.config import Config
from bur.dockerpg import DockerCommandError


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
_SCHEDULE_WORDS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}
_SCHEDULE_PATTERN = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[hd])$")


class InvalidLabel(ValueError):
    """A ``bur.*`` label value is not usable"""


@dataclass(frozen=True)
class DBTarget:
    """One database to back up, resolved from a container's labels"""

    container: str
    app: str
    database: str
    user: str
    keep_last: int
    schedule: str
    interval: timedelta
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
    if inspected.returncode == 0:
        entries: list[dict[str, object]] = json.loads(inspected.stdout)
        return [_read_container(entry) for entry in entries]

    containers: list[ContainerInfo] = []
    for container_id in ids:
        container = _inspect_one(container_id)
        if container is not None:
            containers.append(container)
    return containers


def _inspect_one(container_id: str) -> ContainerInfo | None:
    """Inspect one container, or return None if it no longer exists

    :raises DiscoveryError: If docker fails for any other reason
    """
    inspected = subprocess.run(["docker", "inspect", container_id], capture_output=True, text=True)
    if inspected.returncode == 0:
        entries: list[dict[str, object]] = json.loads(inspected.stdout)
        return _read_container(entries[0])
    if "no such object" in inspected.stderr.lower():
        return None
    raise DiscoveryError(f"could not inspect {container_id}: {inspected.stderr.strip()}")


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

    unique_targets, duplicate_problems = _split_duplicate_apps(targets)
    return Discovered(unique_targets, problems + duplicate_problems)


def _split_duplicate_apps(targets: list[DBTarget]) -> tuple[list[DBTarget], list[LabelProblem]]:
    """Separate targets that share a ``bur.app`` with another non-excluded target

    :param targets: Resolved targets
    :returns: The targets to keep, and a problem for each one sharing its app
    """
    containers_by_app: dict[str, list[str]] = {}
    for target in targets:
        if not target.excluded:
            containers_by_app.setdefault(target.app, []).append(target.container)

    kept: list[DBTarget] = []
    problems: list[LabelProblem] = []
    for target in targets:
        if target.excluded or len(containers_by_app[target.app]) == 1:
            kept.append(target)
            continue
        others = [name for name in containers_by_app[target.app] if name != target.container]
        problems.append(
            LabelProblem(
                target.container, f"bur.app {target.app!r} is also used by {', '.join(others)}"
            )
        )
    return kept, problems


def audit(config: Config) -> list[ContainerInfo]:
    """Find running Postgres containers that never opted in

    Containers named in the config's ``exclude`` list are skipped.

    :param config: Loaded configuration, for ``audit_images`` and ``exclude``
    :returns: Containers whose image matches ``audit_images`` but which carry no
        ``bur.enable`` label
    :raises DiscoveryError: If docker could not be listed or inspected
    """
    return [
        container for container in inspect_running() if _is_unlabelled_database(container, config)
    ]


def _is_unlabelled_database(container: ContainerInfo, config: Config) -> bool:
    """Whether a container looks like a database that never opted in"""
    if not any(image in container.image for image in config.audit_images):
        return False
    if _ENABLE_LABEL in container.labels:
        return False
    return container.name not in config.exclude


def probe_auth(target: DBTarget) -> str | None:
    """Check that bur can connect to a target

    :param target: A resolved database target
    :returns: None if the connection works, otherwise why it didn't
    """
    probe = dockerpg.probe(target.container, target.database, target.user)
    try:
        dockerpg.check(probe)
    except DockerCommandError as error:
        return str(error)
    return None


def resolve(container: ContainerInfo, config: Config) -> DBTarget:
    """Resolve one containers labels into a database target

    :param container: A running container with ```bur.*`` labels
    :param config: Loaded config
    :returns: The resolved target
    :raises InvalidLabel: If ``bur.app`` is missing, or a name, ``bur.keep_last`` or
        ``bur.schedule`` value is not usable
    """
    labels = container.labels
    if "bur.app" not in labels:
        raise InvalidLabel("bur.app is required")
    app = _name(labels["bur.app"], "bur.app")
    database = _name(labels.get("bur.database", app), "bur.database")
    user = _name(labels.get("bur.user", _DEFAULT_USER), "bur.user")
    schedule = labels.get("bur.schedule", _DEFAULT_SCHEDULE)

    return DBTarget(
        container=container.name,
        app=app,
        database=database,
        user=user,
        keep_last=_keep_last(labels, config.keep_last),
        schedule=schedule,
        interval=_interval(schedule),
        excluded=app in config.exclude or container.name in config.exclude,
    )


def _name(value: str, label: str) -> str:
    """Check a name is safe to use in a filename or path"""
    if not _NAME_PATTERN.match(value):
        raise InvalidLabel(f"{label} must match {_NAME_PATTERN.pattern}, got {value!r}")
    return value


def _interval(schedule: str) -> timedelta:
    """Turn a ``bur.schedule`` value into the time between backups

    Accepts ``hourly``, ``daily``, ``weekly``, ``<N>h`` or ``<N>d``, with N at least 1.
    """
    if schedule in _SCHEDULE_WORDS:
        return _SCHEDULE_WORDS[schedule]

    match = _SCHEDULE_PATTERN.match(schedule)
    if match is None:
        raise InvalidLabel(
            f"bur.schedule must be hourly, daily, weekly, <N>h or <N>d, got {schedule!r}"
        )
    count = int(match["count"])
    if match["unit"] == "h":
        return timedelta(hours=count)
    return timedelta(days=count)


def _keep_last(labels: dict[str, str], default: int) -> int:
    """Read ``bur.keep_last``, falling back to the global default"""
    raw = labels.get("bur.keep_last")
    if raw is None:
        return default
    try:
        keep_last = int(raw)
    except ValueError as error:
        raise InvalidLabel(f"bur.keep_last must be a number, got {raw!r}") from error
    if keep_last < 1:
        raise InvalidLabel(f"bur.keep_last must be at least 1, got {keep_last}")
    return keep_last


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
