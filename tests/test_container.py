from __future__ import annotations

import subprocess
import time

import pytest

from bur import container
from bur.container import ContainerError


class _FakeResult:
    """Stands in for a finished subprocess.run."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


@pytest.fixture
def docker_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Swap subprocess.run for a fake and hand back the commands it recorded."""
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeResult:
        calls.append(command)
        return _FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_start_runs_a_detached_container_with_the_postgres_env(
    docker_calls: list[list[str]],
) -> None:
    container.start("postgres:16", "drill", "drillsuper", "secret", "app")
    command = docker_calls[-1]
    assert command[:5] == ["docker", "run", "-d", "--name", "drill"]
    assert "POSTGRES_USER=drillsuper" in command
    assert "POSTGRES_PASSWORD=secret" in command
    assert "POSTGRES_DB=app" in command
    assert command[-1] == "postgres:16"


def test_start_raises_when_docker_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> _FakeResult:
        return _FakeResult(returncode=1, stderr="no such image")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ContainerError) as excinfo:
        container.start("postgres:99", "drill", "drillsuper", "secret", "app")
    assert "no such image" in str(excinfo.value)


def test_wait_until_ready_polls_until_postgres_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeResult:
        attempts.append(command)
        return _FakeResult(returncode=0 if len(attempts) > 1 else 1)

    def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(time, "sleep", no_sleep)

    container.wait_until_ready("drill", "drillsuper", "app")

    assert len(attempts) == 2
    assert attempts[-1] == [
        "docker",
        "exec",
        "drill",
        "pg_isready",
        "-U",
        "drillsuper",
        "-d",
        "app",
    ]


def test_wait_until_ready_gives_up_at_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> _FakeResult:
        return _FakeResult(returncode=1)

    def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(time, "sleep", no_sleep)

    with pytest.raises(ContainerError):
        container.wait_until_ready("drill", "drillsuper", "app", timeout=0.01)


def test_remove_forces_removal(docker_calls: list[list[str]]) -> None:
    container.remove("drill")
    assert docker_calls[-1] == ["docker", "rm", "-f", "drill"]


def test_throwaway_names_and_labels_the_container(docker_calls: list[list[str]]) -> None:
    with container.throwaway("postgres:16", "drillsuper", "secret", "app") as name:
        assert name.startswith("bur-drill-")

    run_command = docker_calls[0]
    assert run_command[:3] == ["docker", "run", "-d"]
    assert "bur.drill=true" in run_command


def test_throwaway_removes_the_container_when_the_block_raises(
    docker_calls: list[list[str]],
) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with container.throwaway("postgres:16", "drillsuper", "secret", "app"):
            raise RuntimeError("boom")

    assert docker_calls[-1][:3] == ["docker", "rm", "-f"]
