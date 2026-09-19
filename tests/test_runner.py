from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bur import crypto, dockerpg, runner, storage
from bur.config import Config
from bur.runner import BackupError


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


def _config(root: Path, recipient: str) -> Config:
    return Config(backup_root=root, age_recipients=(recipient,))


def _shell(command: str) -> subprocess.Popen[bytes]:
    """A process standing in for a pg tool, e.g. echoing a size."""
    return subprocess.Popen(["sh", "-c", command], stdout=subprocess.PIPE)


def _emitting(data: bytes, tmp_path: Path, tag: str) -> subprocess.Popen[bytes]:
    """A process that streams data and exits 0, standing in for a dump."""
    source = tmp_path / f"emit-{tag}.bin"
    source.write_bytes(data)
    return subprocess.Popen(["cat", str(source)], stdout=subprocess.PIPE)


def _decrypt(path: Path, identity: Path) -> bytes:
    with path.open("rb") as cipher:
        process = crypto.decrypt(cipher, str(identity))
        assert process.stdout is not None
        data = process.stdout.read()
        process.wait()
    return data


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


def test_dry_run_writes_nothing(
    keypair: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipient, _ = keypair
    root = tmp_path / "backups"
    config = _config(root, recipient)
    when = datetime(2026, 6, 3, 17, 48, tzinfo=UTC)

    # dump / dump_globals are left un-faked on purpose, a dry run must not call them
    monkeypatch.setattr(dockerpg, "database_size", lambda c, d, u: _shell("echo 4096"))

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
    monkeypatch.setattr(dockerpg, "dump", lambda c, d, u: _shell("printf partial; exit 1"))

    with pytest.raises(crypto.CryptoError):
        runner.backup_database(config, "app", "app", "app", "app", now=when)

    app_dir = root / "app"
    leftovers = list(app_dir.iterdir()) if app_dir.exists() else []
    assert leftovers == []
