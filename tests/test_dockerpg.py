from __future__ import annotations

import subprocess

import pytest

from bur import dockerpg
from bur.dockerpg import DockerCommandError


class _FakePopen:
    """Records the args and kwargs a Popen was built with."""

    def __init__(self, args: list[str], **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs


@pytest.fixture
def popen_calls(monkeypatch: pytest.MonkeyPatch) -> list[_FakePopen]:
    """Swap Popen for a fake and hand back the list of calls it recorded."""
    calls: list[_FakePopen] = []

    def fake_popen(args: list[str], **kwargs: object) -> _FakePopen:
        call = _FakePopen(args, **kwargs)
        calls.append(call)
        return call

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


def test_dump_runs_streaming_pg_dump(popen_calls: list[_FakePopen]) -> None:
    dockerpg.dump("app-db", "app", "appuser")
    call = popen_calls[-1]
    assert call.args == [
        "docker",
        "exec",
        "app-db",
        "sh",
        "-c",
        'PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump -Fc -U appuser app',
    ]
    assert call.kwargs["stdout"] is subprocess.PIPE


def test_dump_globals_runs_globals_only(popen_calls: list[_FakePopen]) -> None:
    dockerpg.dump_globals("app-db", "appuser")
    call = popen_calls[-1]
    assert call.args[-1] == (
        'PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dumpall --globals-only -U appuser'
    )
    assert call.kwargs["stdout"] is subprocess.PIPE


def test_restore_uses_single_transaction(popen_calls: list[_FakePopen]) -> None:
    dockerpg.restore("app-db", "app", "appuser")
    call = popen_calls[-1]
    assert call.args[:3] == ["docker", "exec", "-i"]
    assert call.args[-1] == (
        'PGPASSWORD="${POSTGRES_PASSWORD:-}" '
        "pg_restore --clean --if-exists --single-transaction -U appuser -d app"
    )
    assert call.kwargs["stdin"] is subprocess.PIPE


def test_restore_globals_targets_postgres_db(popen_calls: list[_FakePopen]) -> None:
    dockerpg.restore_globals("app-db", "appuser")
    call = popen_calls[-1]
    assert call.args[:3] == ["docker", "exec", "-i"]
    assert call.args[-1] == 'PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -U appuser -d postgres'
    assert call.kwargs["stdin"] is subprocess.PIPE


def test_user_and_db_are_shell_quoted(popen_calls: list[_FakePopen]) -> None:
    dockerpg.dump("app-db", "a; rm -rf /", "ro le")
    inner = popen_calls[-1].args[-1]
    assert inner.endswith("pg_dump -Fc -U 'ro le' 'a; rm -rf /'")


def test_check_accepts_a_zero_exit() -> None:
    process = subprocess.Popen(["sh", "-c", "exit 0"])
    process.wait()
    dockerpg.check(process)  # no raise


def test_check_raises_on_a_non_zero_exit() -> None:
    process = subprocess.Popen(["sh", "-c", "exit 3"])
    process.wait()
    with pytest.raises(DockerCommandError) as excinfo:
        dockerpg.check(process)
    assert excinfo.value.returncode == 3


def test_check_rejects_an_unfinished_process() -> None:
    process = subprocess.Popen(["sh", "-c", "sleep 5"])
    try:
        with pytest.raises(ValueError):
            dockerpg.check(process)
    finally:
        process.kill()
        process.wait()
