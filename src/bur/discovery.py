"""
Find the databases that opted in to backups
Reads runnning containers and their ``bur.*`` labels
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


class DiscoveryError(RuntimeError):
    """Docker could not be queried for running containers"""


@dataclass(frozen=True)
class ContainerInfo:
    """A running container's name, image and labels"""

    name: str
    image: str
    labels: dict[str, str]


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
