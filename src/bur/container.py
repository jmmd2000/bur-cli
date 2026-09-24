"""Run throwaway Postgres containers

Starts a container, waits for it to accept connections, and removes it again.
The context manager removes the container however the block ends.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from uuid import uuid4

_LABEL = "bur.drill=true"
_NAME_PREFIX = "bur-drill-"
_READY_TIMEOUT = 30.0
_POLL_SECONDS = 0.5


class ContainerError(RuntimeError):
    """A throwaway container could not be started, reached or removed"""


def start(image: str, name: str, superuser: str, password: str, db: str) -> None:
    """Start a detached Postgres container

    :param image: Image to run, e.g. ``postgres:16``
    :param name: Name to give the container
    :param superuser: Role the container initialises as its superuser
    :param password: Password for that role
    :param db: Database the container creates on startup
    :raises ContainerError: If docker refuses to start the container
    """

    command = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--label",
        _LABEL,
        "-e",
        f"POSTGRES_USER={superuser}",
        "-e",
        f"POSTGRES_PASSWORD={password}",
        "-e",
        f"POSTGRES_DB={db}",
        image,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ContainerError(f"could not start {image}: {result.stderr.strip()}")


def wait_until_ready(name: str, user: str, db: str, timeout: float = _READY_TIMEOUT) -> None:
    """Poll a container until Postgres accepts connections

    :param name: Container to poll
    :param user: Role to check as
    :param db: Database to check
    :param timeout: Seconds to keep polling for
    :raises ContainerError: If the database is still unreachable at the timeout
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", name, "pg_isready", "-U", user, "-d", db],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        time.sleep(_POLL_SECONDS)
    raise ContainerError(f"{name} did not accept connections within {timeout} seconds")


def remove(name: str) -> None:
    """Remove a container, running or not

    :param name: Container to remove
    :raises ContainerError: If docker could not remove it
    """
    result = subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
    if result.returncode != 0:
        raise ContainerError(f"could not remove {name}: {result.stderr.strip()}")


@contextmanager
def throwaway(image: str, superuser: str, password: str, db: str) -> Generator[str, None, None]:
    """Run a uniquely named Postgres container.

    :param image: Image to run, e.g. ``postgres:16``
    :param superuser: Role the container initialises as its superuser
    :param password: Password for that role
    :param db: Database the container creates on startup
    :returns: The container's name, ready to accept connections
    """
    name = f"{_NAME_PREFIX}{uuid4().hex[:12]}"
    start(image, name, superuser, password, db)
    try:
        wait_until_ready(name, superuser, db)
        yield name
    finally:
        remove(name)
