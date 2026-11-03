from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bur import remote
from bur.config import Config
from bur.remote import RemoteError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone is not installed"),
]

_NAME = "giftring-20260603T174800Z.dump.age"


def _config(tmp_path: Path) -> Config:
    """A config whose remote is a local folder, through rclone's local backend."""
    return Config(
        backup_root=tmp_path / "backups",
        age_recipients=("age1unused",),
        r2_remote=":local",
        r2_bucket=str(tmp_path / "bucket"),
    )


def _store(config: Config, content: bytes) -> None:
    directory = config.backup_root / "giftring"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / _NAME).write_bytes(content)


def test_upload_then_list(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _store(config, b"ciphertext")

    assert remote.list_names(config, "giftring") == []
    remote.upload(config, "giftring", _NAME)

    assert remote.list_names(config, "giftring") == [_NAME]
    assert (tmp_path / "bucket" / "giftring" / _NAME).read_bytes() == b"ciphertext"


def test_upload_refuses_to_overwrite_a_different_file(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _store(config, b"first")
    remote.upload(config, "giftring", _NAME)
    _store(config, b"second")

    with pytest.raises(RemoteError):
        remote.upload(config, "giftring", _NAME)
    assert (tmp_path / "bucket" / "giftring" / _NAME).read_bytes() == b"first"


def test_uploading_an_unchanged_file_again_succeeds(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _store(config, b"ciphertext")
    remote.upload(config, "giftring", _NAME)

    remote.upload(config, "giftring", _NAME)

    assert remote.list_names(config, "giftring") == [_NAME]
