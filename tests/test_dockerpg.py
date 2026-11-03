from __future__ import annotations

import subprocess

import pytest

from bur import dockerpg, processes
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
    assert call.args[-1] == 'PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -q -U appuser -d postgres'
    assert call.kwargs["stdin"] is subprocess.PIPE


def test_user_and_db_are_shell_quoted(popen_calls: list[_FakePopen]) -> None:
    dockerpg.dump("app-db", "a; rm -rf /", "ro le")
    inner = popen_calls[-1].args[-1]
    assert inner.endswith("pg_dump -Fc -U 'ro le' 'a; rm -rf /'")


def test_check_accepts_a_zero_exit() -> None:
    dockerpg.check(processes.start(["sh", "-c", "exit 0"]))  # no raise


def test_check_raises_with_the_commands_stderr() -> None:
    running = processes.start(["sh", "-c", "echo 'database \"x\" does not exist' >&2; exit 3"])

    with pytest.raises(DockerCommandError) as excinfo:
        dockerpg.check(running)

    assert excinfo.value.returncode == 3
    assert str(excinfo.value) == 'database "x" does not exist'


def test_check_falls_back_to_the_exit_code_without_stderr() -> None:
    running = processes.start(["sh", "-c", "exit 3"])

    with pytest.raises(DockerCommandError) as excinfo:
        dockerpg.check(running)

    assert str(excinfo.value) == "docker exec command failed with exit code 3"


def test_database_size_queries_pg_database_size(popen_calls: list[_FakePopen]) -> None:
    dockerpg.database_size("app-db", "app", "appuser")
    call = popen_calls[-1]
    assert call.args[-1] == (
        'PGPASSWORD="${POSTGRES_PASSWORD:-}" '
        "psql -tAc 'SELECT pg_database_size(current_database())' -U appuser -d app"
    )
    assert call.kwargs["stdout"] is subprocess.PIPE


def test_database_size_shell_quotes_user_and_db(popen_calls: list[_FakePopen]) -> None:
    dockerpg.database_size("app-db", "a; rm -rf /", "ro le")
    inner = popen_calls[-1].args[-1]
    assert inner.endswith("-U 'ro le' -d 'a; rm -rf /'")


def test_table_names_lists_the_user_tables(popen_calls: list[_FakePopen]) -> None:
    dockerpg.table_names("app-db", "app", "appuser")
    call = popen_calls[-1]
    inner = call.args[-1]
    assert "FROM pg_stat_user_tables" in inner
    assert "NOT LIKE '\"'\"'pg\\_temp\\_%'\"'\"'" in inner
    assert "quote_ident" in inner
    assert inner.endswith("-U appuser -d app")
    assert call.kwargs["stdout"] is subprocess.PIPE


def test_count_rows_counts_one_table(popen_calls: list[_FakePopen]) -> None:
    dockerpg.count_rows("app-db", "app", "appuser", "public.widgets")
    call = popen_calls[-1]
    assert call.args[-1] == (
        'PGPASSWORD="${POSTGRES_PASSWORD:-}" '
        "psql -tAc 'SELECT count(*) FROM public.widgets' -U appuser -d app"
    )
    assert call.kwargs["stdout"] is subprocess.PIPE


def test_count_rows_shell_quotes_user_and_db(popen_calls: list[_FakePopen]) -> None:
    dockerpg.count_rows("app-db", "a; rm -rf /", "ro le", "public.widgets")
    inner = popen_calls[-1].args[-1]
    assert inner.endswith("-U 'ro le' -d 'a; rm -rf /'")


def test_probe_runs_a_trivial_query(popen_calls: list[_FakePopen]) -> None:
    dockerpg.probe("app-db", "app", "appuser")
    call = popen_calls[-1]
    assert call.args[-1] == (
        "PGPASSWORD=\"${POSTGRES_PASSWORD:-}\" psql -tAc 'SELECT 1' -U appuser -d app"
    )
    assert call.kwargs["stdout"] is subprocess.DEVNULL


def test_probe_shell_quotes_user_and_db(popen_calls: list[_FakePopen]) -> None:
    dockerpg.probe("app-db", "a; rm -rf /", "ro le")
    inner = popen_calls[-1].args[-1]
    assert inner.endswith("-U 'ro le' -d 'a; rm -rf /'")
