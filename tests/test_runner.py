from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bur import crypto, dockerpg, manifest, processes, runner, storage
from bur.config import Config
from bur.dockerpg import DockerCommandError
from bur.naming import ArtefactKind, build
from bur.processes import RunningProcess
from bur.runner import BackupError, DrillError


def _generate_keypair(identity_path: Path) -> str:
    """Make an age keypair, write the identity, return the public recipient."""
    result = subprocess.run(["age-keygen"], capture_output=True, text=True, check=True)
    identity_path.write_text(result.stdout)
    for line in result.stdout.splitlines():
        if line.startswith("# public key:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("age-keygen produced no public key")


@pytest.fixture
def keypair(tmp_path: Path) -> tuple[str, Path]:
    identity = tmp_path / "identity.txt"
    recipient = _generate_keypair(identity)
    return recipient, identity


_GB = 1024**3
_PLENTY_OF_SPACE = 100 * _GB


def _config(root: Path, recipient: str) -> Config:
    return Config(backup_root=root, age_recipients=(recipient,))


def _shell(command: str) -> RunningProcess:
    """A process standing in for a pg tool, e.g. echoing a size."""
    return processes.start(["sh", "-c", command], stdout=subprocess.PIPE)


def _emitting(data: bytes, tmp_path: Path, tag: str) -> RunningProcess:
    """A process that streams data and exits 0, standing in for a dump."""
    source = tmp_path / f"emit-{tag}.bin"
    source.write_bytes(data)
    return processes.start(["cat", str(source)], stdout=subprocess.PIPE)


def _decrypt(path: Path, identity: Path) -> bytes:
    with path.open("rb") as cipher:
        running = crypto.decrypt(cipher, str(identity))
        stream = running.process.stdout
        assert stream is not None
        data = stream.read()
        stream.close()
        crypto.check(running)
    return data


def _patch_source_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake the version and table queries a backup runs against its source."""
    monkeypatch.setattr(dockerpg, "server_version", lambda c, d, u: _shell("echo 160004"))
    monkeypatch.setattr(
        dockerpg, "table_names", lambda c, d, u: _shell("printf 'public.a\\npublic.b\\n'")
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_backup_writes_both_artefacts(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, identity = keypair
    root = tmp_path / "backups"
    config = _config(root, recipient)
    when = datetime(2026, 6, 3, 17, 48, tzinfo=UTC)
    dump_bytes = b"pretend pg_dump custom format"
    globals_bytes = b"-- roles, passwords and grants"

    monkeypatch.setattr(dockerpg, "database_size", lambda c, d, u: _shell("echo 4096"))
    monkeypatch.setattr(storage, "free_space", lambda r: _PLENTY_OF_SPACE)
    _patch_source_queries(monkeypatch)
    monkeypatch.setattr(dockerpg, "dump", lambda c, d, u: _emitting(dump_bytes, tmp_path, "dump"))
    monkeypatch.setattr(
        dockerpg, "dump_globals", lambda c, u: _emitting(globals_bytes, tmp_path, "globals")
    )

    result = runner.backup_database(
        config, "giftring", "giftring", "giftring", "giftring", now=when
    )  # noqa: E501

    dump_path = root / "giftring" / result.dump_name
    globals_path = root / "giftring" / result.globals_name
    assert dump_path.exists()
    assert globals_path.exists()
    assert dump_path.read_bytes().startswith(b"age-encryption.org/v1")
    assert _decrypt(dump_path, identity) == dump_bytes
    assert _decrypt(globals_path, identity) == globals_bytes
    assert result.database_size == 4096
    assert result.dry_run is False


def test_backup_writes_a_manifest_that_matches_the_stored_files(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, _ = keypair
    root = tmp_path / "backups"
    when = datetime(2026, 6, 3, 17, 48, tzinfo=UTC)

    monkeypatch.setattr(dockerpg, "database_size", lambda c, d, u: _shell("echo 4096"))
    monkeypatch.setattr(storage, "free_space", lambda r: _PLENTY_OF_SPACE)
    monkeypatch.setattr(dockerpg, "dump", lambda c, d, u: _emitting(b"dump", tmp_path, "dump"))
    monkeypatch.setattr(
        dockerpg, "dump_globals", lambda c, u: _emitting(b"globals", tmp_path, "globals")
    )
    _patch_source_queries(monkeypatch)

    result = runner.backup_database(
        _config(root, recipient), "giftring", "giftring-db", "gift", "gift", now=when
    )

    app_dir = root / "giftring"
    recorded = manifest.read(root, "giftring", result.manifest_name)
    assert recorded.app == "giftring"
    assert recorded.timestamp == when
    assert recorded.postgres_major == 16
    assert recorded.globals.file == result.globals_name
    assert recorded.globals.sha256 == _sha256(app_dir / result.globals_name)
    assert recorded.globals.size == (app_dir / result.globals_name).stat().st_size
    dump = recorded.databases["gift"].dump
    assert dump.file == result.dump_name
    assert dump.sha256 == _sha256(app_dir / result.dump_name)
    assert dump.size == (app_dir / result.dump_name).stat().st_size
    assert recorded.databases["gift"].table_count == 2


def test_backup_aborts_loudly_when_short_on_space(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, _ = keypair
    root = tmp_path / "backups"
    config = _config(root, recipient)

    monkeypatch.setattr(dockerpg, "database_size", lambda c, d, u: _shell("echo 1000000000"))
    monkeypatch.setattr(storage, "free_space", lambda r: 10)

    with pytest.raises(BackupError) as excinfo:
        runner.backup_database(config, "app", "app", "app", "app")

    assert "space" in str(excinfo.value).lower()
    assert not (root / "app").exists()


@pytest.mark.parametrize("dry_run", [False, True])
def test_backup_is_refused_when_it_would_eat_into_the_reserve(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    recipient, _ = keypair
    root = tmp_path / "backups"
    config = _config(root, recipient)

    # 2 GB dump with 6 GB free leaves 4 GB, under the default 5 GB
    monkeypatch.setattr(dockerpg, "database_size", lambda c, d, u: _shell(f"echo {2 * _GB}"))
    monkeypatch.setattr(storage, "free_space", lambda r: 6 * _GB)

    with pytest.raises(BackupError) as excinfo:
        runner.backup_database(config, "app", "app", "app", "app", dry_run=dry_run)

    assert "min_free_space_gb (5 GB)" in str(excinfo.value)
    assert not (root / "app").exists()


def test_backup_goes_ahead_when_the_reserve_is_left_free(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, _ = keypair
    config = _config(tmp_path / "backups", recipient)

    # 2 GB dump with 7 GB free leaves exactly the default 5 GB
    monkeypatch.setattr(dockerpg, "database_size", lambda c, d, u: _shell(f"echo {2 * _GB}"))
    monkeypatch.setattr(storage, "free_space", lambda r: 7 * _GB)

    result = runner.backup_database(config, "app", "app", "app", "app", dry_run=True)

    assert result.database_size == 2 * _GB


def test_backup_of_a_missing_database_reports_why(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, _ = keypair
    config = _config(tmp_path / "backups", recipient)
    missing = 'psql: error: FATAL:  database "x" does not exist'

    monkeypatch.setattr(
        dockerpg, "database_size", lambda c, d, u: _shell(f"echo '{missing}' >&2; exit 2")
    )

    with pytest.raises(DockerCommandError) as excinfo:
        runner.backup_database(config, "x", "x", "x", "x")

    assert 'database "x" does not exist' in str(excinfo.value)


def test_dry_run_writes_nothing(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, _ = keypair
    root = tmp_path / "backups"
    config = _config(root, recipient)
    when = datetime(2026, 6, 3, 17, 48, tzinfo=UTC)

    # dump / dump_globals are left un-faked on purpose, a dry run must not call them
    monkeypatch.setattr(dockerpg, "database_size", lambda c, d, u: _shell("echo 4096"))
    monkeypatch.setattr(storage, "free_space", lambda r: _PLENTY_OF_SPACE)

    result = runner.backup_database(
        config, "giftring", "giftring", "giftring", "giftring", now=when, dry_run=True
    )

    assert result.dry_run is True
    assert result.dump_name.endswith(".dump.age")
    assert result.globals_name.endswith(".globals.sql.age")
    assert not (root / "giftring").exists()


def test_truncated_dump_fails_and_leaves_no_file(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, _ = keypair
    root = tmp_path / "backups"
    config = _config(root, recipient)
    when = datetime(2026, 6, 3, 17, 48, tzinfo=UTC)

    monkeypatch.setattr(dockerpg, "database_size", lambda c, d, u: _shell("echo 4096"))
    monkeypatch.setattr(storage, "free_space", lambda r: _PLENTY_OF_SPACE)
    monkeypatch.setattr(dockerpg, "dump", lambda c, d, u: _shell("printf partial; exit 1"))
    _patch_source_queries(monkeypatch)

    with pytest.raises(crypto.CryptoError):
        runner.backup_database(config, "app", "app", "app", "app", now=when)

    app_dir = root / "app"
    leftovers = list(app_dir.iterdir()) if app_dir.exists() else []
    assert leftovers == []


def _sink() -> RunningProcess:
    """A process that swallows stdin and exits 0, standing in for a restore."""
    return processes.start(["sh", "-c", "cat > /dev/null"], stdin=subprocess.PIPE)


def _store_pair(
    root: Path,
    app: str,
    when: datetime,
    recipient: str,
    tmp_path: Path,
    dump: bytes = b"pretend custom format dump",
    table_count: int = 1,
) -> None:
    """Write a real encrypted dump and globals pair for an app, then its manifest."""
    payloads: tuple[tuple[ArtefactKind, bytes], ...] = (
        ("dump", dump),
        ("globals", b"-- roles and grants"),
    )
    for kind, payload in payloads:
        with storage.atomic_write(root, app, build(app, when, kind)) as out:
            crypto.encrypt(_emitting(payload, tmp_path, kind), [recipient], out)

    manifest.write(
        root,
        manifest.Manifest(
            app=app,
            timestamp=when,
            postgres_major=17,
            globals=manifest.record_artefact(root, app, build(app, when, "globals")),
            databases={
                app: manifest.DatabaseRecord(
                    dump=manifest.record_artefact(root, app, build(app, when, "dump")),
                    table_count=table_count,
                )
            },
        ),
    )


def _patch_drill(
    monkeypatch: pytest.MonkeyPatch, events: list[str], tables: list[str], rows: int
) -> None:
    """Fake the container and the restore commands, recording what runs in order."""

    @contextmanager
    def fake_throwaway(image: str, superuser: str, password: str, db: str) -> Iterator[str]:
        events.append(f"start {image}")
        try:
            yield "drill-container"
        finally:
            events.append("remove")

    def fake_restore_globals(container: str, user: str) -> RunningProcess:
        events.append("globals")
        return _sink()

    def fake_restore(container: str, db: str, user: str) -> RunningProcess:
        events.append("dump")
        return _sink()

    def fake_table_names(container: str, db: str, user: str) -> RunningProcess:
        listing = "".join(f"{table}\\n" for table in tables)
        return _shell(f"printf '{listing}'")

    def fake_count_rows(container: str, db: str, user: str, table: str) -> RunningProcess:
        return _shell(f"echo {rows}")

    monkeypatch.setattr(runner, "throwaway", fake_throwaway)
    monkeypatch.setattr(dockerpg, "restore_globals", fake_restore_globals)
    monkeypatch.setattr(dockerpg, "restore", fake_restore)
    monkeypatch.setattr(dockerpg, "table_names", fake_table_names)
    monkeypatch.setattr(dockerpg, "count_rows", fake_count_rows)


def test_drill_restores_globals_then_the_dump(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, identity = keypair
    root = tmp_path / "backups"
    when = datetime(2026, 6, 3, 17, 48, tzinfo=UTC)
    _store_pair(root, "giftring", when, recipient, tmp_path)

    events: list[str] = []
    _patch_drill(monkeypatch, events, ["public.widgets"], 100)

    result = runner.restore_drill(
        _config(root, recipient), "giftring", str(identity), "16", "giftring"
    )

    assert events == ["start postgres:16", "globals", "dump", "remove"]
    assert result.row_counts == {"public.widgets": 100}
    assert result.timestamp == when


def test_drill_defaults_to_the_recorded_major(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, identity = keypair
    root = tmp_path / "backups"
    _store_pair(root, "giftring", datetime(2026, 6, 3, tzinfo=UTC), recipient, tmp_path)

    events: list[str] = []
    _patch_drill(monkeypatch, events, ["public.widgets"], 100)

    result = runner.restore_drill(
        _config(root, recipient), "giftring", str(identity), None, "giftring"
    )

    assert events[0] == "start postgres:17"
    assert result.major == "17"


def test_drill_fails_when_the_table_count_differs_from_the_manifest(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, identity = keypair
    root = tmp_path / "backups"
    when = datetime(2026, 6, 3, tzinfo=UTC)
    _store_pair(root, "giftring", when, recipient, tmp_path, table_count=3)

    events: list[str] = []
    _patch_drill(monkeypatch, events, ["public.widgets", "public.orders"], 100)

    with pytest.raises(DrillError, match="manifest"):
        runner.restore_drill(_config(root, recipient), "giftring", str(identity), "16", "giftring")
    assert events[-1] == "remove"


def test_drill_drills_a_specific_backup(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, identity = keypair
    root = tmp_path / "backups"
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 2, 1, tzinfo=UTC)
    _store_pair(root, "giftring", older, recipient, tmp_path)
    _store_pair(root, "giftring", newer, recipient, tmp_path)

    _patch_drill(monkeypatch, [], ["public.widgets"], 5)

    result = runner.restore_drill(
        _config(root, recipient), "giftring", str(identity), "16", "giftring", timestamp=older
    )

    assert result.timestamp == older


def test_drill_fails_when_no_tables_were_restored(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, identity = keypair
    root = tmp_path / "backups"
    _store_pair(root, "giftring", datetime(2026, 6, 3, tzinfo=UTC), recipient, tmp_path)

    events: list[str] = []
    _patch_drill(monkeypatch, events, [], 0)

    with pytest.raises(DrillError, match="no tables"):
        runner.restore_drill(_config(root, recipient), "giftring", str(identity), "16", "giftring")
    assert events[-1] == "remove"


def test_drill_fails_when_every_table_is_empty(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, identity = keypair
    root = tmp_path / "backups"
    _store_pair(root, "giftring", datetime(2026, 6, 3, tzinfo=UTC), recipient, tmp_path)

    events: list[str] = []
    _patch_drill(monkeypatch, events, ["public.widgets"], 0)

    with pytest.raises(DrillError, match="empty"):
        runner.restore_drill(_config(root, recipient), "giftring", str(identity), "16", "giftring")
    assert events[-1] == "remove"


def test_drill_removes_the_container_when_the_restore_fails(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, identity = keypair
    root = tmp_path / "backups"
    _store_pair(root, "giftring", datetime(2026, 6, 3, tzinfo=UTC), recipient, tmp_path)

    events: list[str] = []
    _patch_drill(monkeypatch, events, ["public.widgets"], 100)

    def failing_restore(container: str, db: str, user: str) -> RunningProcess:
        events.append("dump")
        return processes.start(["sh", "-c", "cat > /dev/null; exit 1"], stdin=subprocess.PIPE)

    monkeypatch.setattr(dockerpg, "restore", failing_restore)

    with pytest.raises(DockerCommandError):
        runner.restore_drill(_config(root, recipient), "giftring", str(identity), "16", "giftring")
    assert events[-1] == "remove"


def test_drill_reports_ages_error_for_the_wrong_identity(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, _ = keypair
    wrong_identity = tmp_path / "wrong.txt"
    _generate_keypair(wrong_identity)
    root = tmp_path / "backups"
    _store_pair(root, "giftring", datetime(2026, 6, 3, tzinfo=UTC), recipient, tmp_path)

    events: list[str] = []
    _patch_drill(monkeypatch, events, ["public.widgets"], 100)

    with pytest.raises(crypto.CryptoError, match="no identity matched any of the recipients"):
        runner.restore_drill(
            _config(root, recipient), "giftring", str(wrong_identity), "16", "giftring"
        )
    assert events[-1] == "remove"


def test_drill_reports_the_restores_error_when_it_stops_reading(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, identity = keypair
    root = tmp_path / "backups"
    # 1 MB dump, far more than a pipe holds
    big_dump = b"x" * 1_000_000
    _store_pair(root, "giftring", datetime(2026, 6, 3, tzinfo=UTC), recipient, tmp_path, big_dump)

    events: list[str] = []
    _patch_drill(monkeypatch, events, ["public.widgets"], 100)

    def restore_that_stops_reading(container: str, db: str, user: str) -> RunningProcess:
        events.append("dump")
        script = "exec 0<&-; echo 'pg_restore: error: could not execute query' >&2; exit 1"
        return processes.start(["sh", "-c", script], stdin=subprocess.PIPE)

    monkeypatch.setattr(dockerpg, "restore", restore_that_stops_reading)

    with pytest.raises(DockerCommandError, match="pg_restore: error: could not execute query"):
        runner.restore_drill(_config(root, recipient), "giftring", str(identity), "16", "giftring")
    assert events[-1] == "remove"
