from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from bur import discovery, dockerpg, processes
from bur.config import Config
from bur.discovery import ContainerInfo, DBTarget, DiscoveryError, InvalidLabel
from bur.processes import RunningProcess

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


def _inspect_body(name: str) -> str:
    return json.dumps([{"Name": f"/{name}", "Config": {"Image": "postgres:16", "Labels": {}}}])


@pytest.mark.parametrize(
    "missing_error",
    ["error: no such object: gone456", "Error: No such object: gone456"],
)
def test_a_container_removed_before_inspect_is_skipped(
    monkeypatch: pytest.MonkeyPatch, missing_error: str
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> _FakeResult:
        calls.append(command)
        if command[:2] == ["docker", "ps"]:
            return _FakeResult(stdout="kept123\ngone456\n")
        if command == ["docker", "inspect", "kept123"]:
            return _FakeResult(stdout=_inspect_body("kept-db-1"))
        if command == ["docker", "inspect", "gone456"]:
            return _FakeResult(returncode=1, stderr=missing_error)
        return _FakeResult(returncode=1, stdout=_inspect_body("kept-db-1"), stderr=missing_error)

    monkeypatch.setattr(subprocess, "run", fake_run)

    containers = discovery.inspect_running()

    assert [container.name for container in containers] == ["kept-db-1"]
    assert ["docker", "inspect", "kept123"] in calls
    assert ["docker", "inspect", "gone456"] in calls


def test_an_inspect_failure_that_is_not_a_missing_container_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = "permission denied while trying to connect to the docker daemon socket"

    def fake_run(command: list[str], **kwargs: object) -> _FakeResult:
        if command[:2] == ["docker", "ps"]:
            return _FakeResult(stdout="abc123\n")
        return _FakeResult(returncode=1, stderr=denied)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(DiscoveryError) as excinfo:
        discovery.inspect_running()
    assert "permission denied" in str(excinfo.value)


def _container(labels: dict[str, str], name: str = "giftring-db-1") -> ContainerInfo:
    return ContainerInfo(name=name, image="postgres:16", labels=labels)


def _config(exclude: tuple[str, ...] = (), keep_last: int = 10) -> Config:
    return Config(
        backup_root=Path("/backups"),
        age_recipients=("age1abc",),
        keep_last=keep_last,
        exclude=exclude,
    )


def test_defaults_come_from_the_app_and_the_config() -> None:
    labels = {"bur.enable": "true", "bur.app": "giftring"}

    target = discovery.resolve(_container(labels), _config(keep_last=7))

    assert target.container == "giftring-db-1"
    assert target.app == "giftring"
    assert target.database == "giftring"
    assert target.user == "postgres"
    assert target.keep_last == 7
    assert target.schedule == "daily"
    assert target.excluded is False


def test_labels_override_the_defaults() -> None:
    labels = {
        "bur.enable": "true",
        "bur.app": "giftring",
        "bur.database": "gift",
        "bur.user": "gift_owner",
        "bur.keep_last": "3",
        "bur.schedule": "weekly",
    }

    target = discovery.resolve(_container(labels), _config())

    assert target.app == "giftring"
    assert target.database == "gift"
    assert target.user == "gift_owner"
    assert target.keep_last == 3
    assert target.schedule == "weekly"


def test_a_traversing_app_name_is_rejected() -> None:
    with pytest.raises(InvalidLabel) as excinfo:
        discovery.resolve(_container({"bur.app": "../../etc"}), _config())
    assert "bur.app" in str(excinfo.value)


def test_a_hostile_database_or_user_is_rejected() -> None:
    hostile_database = {"bur.app": "giftring", "bur.database": "a; drop database x"}
    with pytest.raises(InvalidLabel, match="bur.database"):
        discovery.resolve(_container(hostile_database), _config())
    hostile_user = {"bur.app": "giftring", "bur.user": "../root"}
    with pytest.raises(InvalidLabel, match="bur.user"):
        discovery.resolve(_container(hostile_user), _config())


def test_bur_app_is_required() -> None:
    with pytest.raises(InvalidLabel, match="bur.app is required"):
        discovery.resolve(_container({"bur.enable": "true"}), _config())


def test_a_container_name_in_exclude_excludes_a_labelled_database() -> None:
    labels = {"bur.app": "giftring"}

    target = discovery.resolve(_container(labels), _config(exclude=("giftring-db-1",)))

    assert target.excluded is True


def test_an_excluded_app_is_resolved_and_marked() -> None:
    target = discovery.resolve(_container({"bur.app": "giftring"}), _config(exclude=("giftring",)))

    assert target.app == "giftring"
    assert target.excluded is True


def test_a_bad_keep_last_is_rejected() -> None:
    with pytest.raises(InvalidLabel, match="bur.keep_last"):
        discovery.resolve(_container({"bur.app": "giftring", "bur.keep_last": "loads"}), _config())
    with pytest.raises(InvalidLabel, match="bur.keep_last"):
        discovery.resolve(_container({"bur.app": "giftring", "bur.keep_last": "0"}), _config())


def test_discover_filters_on_the_enable_label(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    _patch_docker(monkeypatch, calls, "abc123\n", _INSPECT)

    discovered = discovery.discover(_config())

    assert calls[0] == ["docker", "ps", "-q", "--filter", "label=bur.enable=true"]
    assert [target.app for target in discovered.targets] == ["giftring"]
    assert discovered.problems == []


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


def test_audit_matches_any_of_the_configured_images(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_docker(monkeypatch, [], "a\nb\nc\nd\n", _MIXED)
    config = dataclasses.replace(_config(), audit_images=("postgres", "jenkins"))

    unlabelled = discovery.audit(config)

    assert [container.name for container in unlabelled] == ["forgotten-db-1", "jenkins"]


_ONE_BAD = json.dumps(
    [
        {
            "Name": "/good-db-1",
            "Config": {"Image": "postgres:16", "Labels": {"bur.app": "giftring"}},
        },
        {
            "Name": "/bad-db-1",
            "Config": {"Image": "postgres:16", "Labels": {"bur.app": "../../etc"}},
        },
    ]
)


def test_one_bad_container_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_docker(monkeypatch, [], "a\nb\n", _ONE_BAD)

    discovered = discovery.discover(_config())

    assert [target.app for target in discovered.targets] == ["giftring"]
    assert [problem.container for problem in discovered.problems] == ["bad-db-1"]
    assert "bur.app" in discovered.problems[0].problem


def _target() -> DBTarget:
    return DBTarget(
        container="giftring-db-1",
        app="giftring",
        database="gift",
        user="gift_owner",
        keep_last=10,
        schedule="daily",
        excluded=False,
    )


def test_probe_auth_passes_when_the_query_works(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_probe(container: str, db: str, user: str) -> RunningProcess:
        return processes.start(["sh", "-c", "exit 0"])

    monkeypatch.setattr(dockerpg, "probe", fake_probe)

    assert discovery.probe_auth(_target()) is None


def test_probe_auth_reports_why_it_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_probe(container: str, db: str, user: str) -> RunningProcess:
        return processes.start(
            ["sh", "-c", "echo 'role \"gift_owner\" does not exist' >&2; exit 2"]
        )

    monkeypatch.setattr(dockerpg, "probe", fake_probe)

    assert discovery.probe_auth(_target()) == 'role "gift_owner" does not exist'


def test_audit_skips_a_container_named_in_exclude(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_docker(monkeypatch, [], "a\nb\nc\nd\n", _MIXED)

    unlabelled = discovery.audit(_config(exclude=("forgotten-db-1",)))

    assert unlabelled == []
