from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bur import discovery
from bur.config import Config
from bur.discovery import ContainerInfo, DiscoveryError, InvalidLabel

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


def _container(labels: dict[str, str], name: str = "giftring-db-1") -> ContainerInfo:
    return ContainerInfo(name=name, image="postgres:16", labels=labels)


def _config(exclude: tuple[str, ...] = (), keep: int = 10, image_match: str = "postgres") -> Config:
    return Config(
        backup_root=Path("/backups"),
        age_recipients=("age1abc",),
        retention_keep=keep,
        postgres_image_match=image_match,
        exclude=exclude,
    )


def test_defaults_come_from_the_container_and_the_config() -> None:
    target = discovery.resolve(_container({"bur.enable": "true"}), _config(keep=7))

    assert target.container == "giftring-db-1"
    assert target.app == "giftring-db-1"
    assert target.database == "giftring-db-1"
    assert target.user == "postgres"
    assert target.retention_keep == 7
    assert target.schedule == "daily"
    assert target.excluded is False


def test_labels_override_the_defaults() -> None:
    labels = {
        "bur.enable": "true",
        "bur.app": "giftring",
        "bur.database": "gift",
        "bur.user": "gift_owner",
        "bur.retention.keep": "3",
        "bur.schedule": "weekly",
    }

    target = discovery.resolve(_container(labels), _config())

    assert target.app == "giftring"
    assert target.database == "gift"
    assert target.user == "gift_owner"
    assert target.retention_keep == 3
    assert target.schedule == "weekly"


def test_a_traversing_app_name_is_rejected() -> None:
    with pytest.raises(InvalidLabel) as excinfo:
        discovery.resolve(_container({"bur.app": "../../etc"}), _config())
    assert "bur.app" in str(excinfo.value)


def test_a_hostile_database_or_user_is_rejected() -> None:
    with pytest.raises(InvalidLabel):
        discovery.resolve(_container({"bur.database": "a; drop database x"}), _config())
    with pytest.raises(InvalidLabel):
        discovery.resolve(_container({"bur.user": "../root"}), _config())


def test_a_container_name_that_is_not_a_valid_app_name_is_rejected() -> None:
    with pytest.raises(InvalidLabel):
        discovery.resolve(_container({}, name="MyApp.db"), _config())


def test_an_excluded_app_is_resolved_and_marked() -> None:
    target = discovery.resolve(_container({"bur.app": "giftring"}), _config(exclude=("giftring",)))

    assert target.app == "giftring"
    assert target.excluded is True


def test_a_bad_retention_keep_is_rejected() -> None:
    with pytest.raises(InvalidLabel):
        discovery.resolve(_container({"bur.retention.keep": "loads"}), _config())
    with pytest.raises(InvalidLabel):
        discovery.resolve(_container({"bur.retention.keep": "0"}), _config())


def test_discover_filters_on_the_enable_label(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    _patch_docker(monkeypatch, calls, "abc123\n", _INSPECT)

    targets = discovery.discover(_config())

    assert calls[0] == ["docker", "ps", "-q", "--filter", "label=bur.enable=true"]
    assert [target.app for target in targets] == ["giftring"]


_MIXED = json.dumps(
    [
        {
            "Name": "/giftring-db-1",
            "Config": {"Image": "postgres:16", "Labels": {"bur.enable": "true"}},
        },
        {
            "Name": "/forgotten-db-1",
            "Config": {"Image": "postgres:15-alpine", "Labels": {}},
        },
        {
            "Name": "/optedout-db-1",
            "Config": {"Image": "postgres:16", "Labels": {"bur.enable": "false"}},
        },
        {
            "Name": "/jenkins",
            "Config": {"Image": "jenkins/jenkins:lts", "Labels": {}},
        },
    ]
)


def test_audit_finds_only_the_unlabelled_postgres_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    _patch_docker(monkeypatch, calls, "a\nb\nc\nd\n", _MIXED)

    unlabelled = discovery.audit(_config())

    assert [container.name for container in unlabelled] == ["forgotten-db-1"]
    assert calls[0] == ["docker", "ps", "-q"]


def test_audit_ignores_a_deliberate_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_docker(monkeypatch, [], "a\nb\nc\nd\n", _MIXED)

    names = [container.name for container in discovery.audit(_config())]

    assert "optedout-db-1" not in names


def test_audit_uses_the_configured_image_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_docker(monkeypatch, [], "a\nb\nc\nd\n", _MIXED)

    unlabelled = discovery.audit(_config(image_match="jenkins"))

    assert [container.name for container in unlabelled] == ["jenkins"]
