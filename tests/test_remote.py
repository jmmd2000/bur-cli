from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bur import remote
from bur.config import Config
from bur.remote import RemoteError


def _config(tmp_path: Path) -> Config:
    return Config(
        backup_root=tmp_path / "backups",
        age_recipients=("age1unused",),
        r2_remote="r2",
        r2_bucket="burr",
    )


def _fake_run(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str = "", stderr: str = ""
) -> list[list[str]]:
    """Fake every rclone call with one result, and record each command."""
    commands: list[list[str]] = []

    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", fake)
    return commands


def test_upload_never_overwrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands = _fake_run(monkeypatch, 0)

    remote.upload(_config(tmp_path), "giftring", "giftring-20260603T174800Z.dump.age")

    assert commands[0][:5] == [
        "rclone",
        "copy",
        "--immutable",
        "--include",
        "/giftring-20260603T174800Z.dump.age",
    ]
    assert commands[0][6] == "r2:burr/giftring/"
    assert "--timeout" in commands[0]


def test_list_names_reads_one_name_per_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch, 0, stdout="a.dump.age\nb.manifest.json\n")

    assert remote.list_names(_config(tmp_path), "giftring") == ["a.dump.age", "b.manifest.json"]


def test_list_names_is_empty_when_the_app_has_no_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch, 3, stderr="directory not found")

    assert remote.list_names(_config(tmp_path), "giftring") == []


def test_a_failed_command_raises_with_its_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch, 1, stderr="couldn't connect")

    with pytest.raises(RemoteError, match="couldn't connect"):
        remote.list_names(_config(tmp_path), "giftring")


def test_a_missing_rclone_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(RemoteError, match="not installed"):
        remote.check_bucket(_config(tmp_path))
