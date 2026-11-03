from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bur import runner
from bur.config import Config
from bur.container import throwaway
from bur.crypto import CryptoError

_MAJOR = "16"
_PASSWORD = "roundtrip-password"
_APP = "roundtrip"


def _generate_keypair(identity_path: Path) -> str:
    """Make an age keypair, write the identity, return the public recipient."""
    result = subprocess.run(["age-keygen"], capture_output=True, text=True, check=True)
    identity_path.write_text(result.stdout)
    for line in result.stdout.splitlines():
        if line.startswith("# public key:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("age-keygen produced no public key")


def _seed(container: str, db: str, user: str) -> None:
    """Create a table with rows in a running database."""
    sql = (
        "CREATE TABLE widgets (id serial PRIMARY KEY, name text NOT NULL); "
        "INSERT INTO widgets (name) SELECT 'widget-' || g FROM generate_series(1, 25) g;"
    )
    subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            f"PGPASSWORD={_PASSWORD}",
            container,
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            user,
            "-d",
            db,
            "-c",
            sql,
        ],
        check=True,
        capture_output=True,
    )


def _drill_containers() -> list[str]:
    """Names of any throwaway containers still on the host."""
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", "label=bur.drill=true", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _take_backup(tmp_path: Path) -> tuple[Config, Path]:
    """Seed a source database, back it up, and return the config and identity."""
    identity = tmp_path / "identity.txt"
    recipient = _generate_keypair(identity)
    config = Config(backup_root=tmp_path / "backups", age_recipients=(recipient,))

    with throwaway(f"postgres:{_MAJOR}", _APP, _PASSWORD, _APP) as source:
        _seed(source, _APP, _APP)
        runner.backup_database(config, _APP, source, _APP, _APP)

    return config, identity


@pytest.mark.integration
def test_backup_then_drill_round_trips(tmp_path: Path) -> None:
    config, identity = _take_backup(tmp_path)

    result = runner.restore_drill(config, _APP, str(identity), _MAJOR, _APP)

    assert result.row_counts == {"public.widgets": 25}
    assert _drill_containers() == []


@pytest.mark.integration
def test_a_drill_without_a_major_uses_the_recorded_one(tmp_path: Path) -> None:
    config, identity = _take_backup(tmp_path)

    result = runner.restore_drill(config, _APP, str(identity), None, _APP)

    assert result.major == _MAJOR
    assert result.row_counts == {"public.widgets": 25}


@pytest.mark.integration
def test_a_failed_drill_still_removes_its_container(tmp_path: Path) -> None:
    config, _ = _take_backup(tmp_path)
    wrong_identity = tmp_path / "wrong.txt"
    _generate_keypair(wrong_identity)

    with pytest.raises(CryptoError, match="no identity matched any of the recipients"):
        runner.restore_drill(config, _APP, str(wrong_identity), _MAJOR, _APP)

    assert _drill_containers() == []
