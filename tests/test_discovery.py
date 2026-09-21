from __future__ import annotations

import json
import subprocess

import pytest

from bur import discovery
from bur.discovery import DiscoveryError

_INSPECT = json.dumps(
    [
        {
            "Name": "/giftring-db-1",
            "Config": {
                "Image": "postgres:16",
                "Labels": {"bur.enable": "true", "bur.app": "giftring"},
            },
        }
    ]
)


class _FakeResult:
    """Stands in for a finished subprocess.run."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_docker(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], ids: str, inspected: str
) -> None:
    """Fake docker, returning ids from `ps` and canned JSON from `inspect`."""

    def fake_run(command: list[str], **kwargs: object) -> _FakeResult:
        calls.append(command)
        if command[:2] == ["docker", "ps"]:
            return _FakeResult(stdout=ids)
        return _FakeResult(stdout=inspected)

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_filters_by_label(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    _patch_docker(monkeypatch, calls, "abc123\n", _INSPECT)

    discovery.inspect_running("bur.enable=true")

    assert calls[0] == ["docker", "ps", "-q", "--filter", "label=bur.enable=true"]
    assert calls[1] == ["docker", "inspect", "abc123"]


def test_lists_every_container_without_a_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    _patch_docker(monkeypatch, calls, "abc123\n", _INSPECT)

    discovery.inspect_running()

    assert calls[0] == ["docker", "ps", "-q"]


def test_reads_the_name_image_and_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_docker(monkeypatch, [], "abc123\n", _INSPECT)

    container = discovery.inspect_running()[0]

    assert container.name == "giftring-db-1"
    assert container.image == "postgres:16"
    assert container.labels["bur.app"] == "giftring"


def test_no_matching_containers_skips_inspect(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    _patch_docker(monkeypatch, calls, "", _INSPECT)

    assert discovery.inspect_running() == []
    assert len(calls) == 1


def test_a_container_without_labels_reads_as_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps([{"Name": "/plain", "Config": {"Image": "postgres:16", "Labels": None}}])
    _patch_docker(monkeypatch, [], "abc123\n", body)

    assert discovery.inspect_running()[0].labels == {}


def test_a_docker_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> _FakeResult:
        return _FakeResult(returncode=1, stderr="cannot connect to the docker daemon")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(DiscoveryError) as excinfo:
        discovery.inspect_running()
    assert "docker daemon" in str(excinfo.value)
