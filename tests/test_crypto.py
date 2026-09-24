from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bur import crypto, processes
from bur.crypto import CryptoError
from bur.processes import RunningProcess


def _generate_keypair(identity_path: Path) -> str:
    """Make an age keypair, write the identity to identity_path, return the public key."""
    result = subprocess.run(["age-keygen"], capture_output=True, text=True, check=True)
    identity_path.write_text(result.stdout)
    for line in result.stdout.splitlines():
        if line.startswith("# public key:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("age-keygen produced no public key")


def _source_emitting(data: bytes, tmp_path: Path) -> RunningProcess:
    """A process that writes data to stdout and exits 0, standing in for pg_dump."""
    source_file = tmp_path / "plaintext.bin"
    source_file.write_bytes(data)
    return processes.start(["cat", str(source_file)], stdout=subprocess.PIPE)


@pytest.fixture
def keypair(tmp_path: Path) -> tuple[str, Path]:
    identity = tmp_path / "identity.txt"
    recipient = _generate_keypair(identity)
    return recipient, identity


def test_encrypt_then_decrypt_round_trips(keypair: tuple[str, Path], tmp_path: Path) -> None:
    recipient, identity = keypair
    plaintext = b"pretend dump \x00\x01\x02 bytes"
    cipher = tmp_path / "backup.age"

    source = _source_emitting(plaintext, tmp_path)
    with cipher.open("wb") as out:
        crypto.encrypt(source, [recipient], out)

    with cipher.open("rb") as ciphertext:
        decrypted = crypto.decrypt(ciphertext, str(identity))
        stream = decrypted.process.stdout
        assert stream is not None
        recovered = stream.read()
        stream.close()
        crypto.check(decrypted)  # no raise

    assert recovered == plaintext


def test_encrypt_writes_a_real_age_file(keypair: tuple[str, Path], tmp_path: Path) -> None:
    recipient, _ = keypair
    cipher = tmp_path / "backup.age"

    source = _source_emitting(b"anything", tmp_path)
    with cipher.open("wb") as out:
        crypto.encrypt(source, [recipient], out)

    assert cipher.read_bytes().startswith(b"age-encryption.org/v1")


def test_mid_stream_failure_fails_despite_age_exiting_zero(
    keypair: tuple[str, Path], tmp_path: Path
) -> None:
    recipient, _ = keypair
    cipher = tmp_path / "backup.age"

    # emits some bytes then dies non-zero; age sees a clean EOF and exits 0
    source = processes.start(
        ["sh", "-c", "printf 'partial'; echo 'pg_dump: error: connection lost' >&2; exit 1"],
        stdout=subprocess.PIPE,
    )
    with cipher.open("wb") as out:
        with pytest.raises(CryptoError) as excinfo:
            crypto.encrypt(source, [recipient], out)
    assert str(excinfo.value) == "backup source failed: pg_dump: error: connection lost"


def test_encrypt_needs_a_recipient(tmp_path: Path) -> None:
    cipher = tmp_path / "backup.age"
    source = _source_emitting(b"data", tmp_path)
    with cipher.open("wb") as out:
        with pytest.raises(CryptoError):
            crypto.encrypt(source, [], out)
    processes.finish(source)


def test_decrypt_with_the_wrong_identity_fails(keypair: tuple[str, Path], tmp_path: Path) -> None:
    recipient, _ = keypair
    wrong_identity = tmp_path / "wrong.txt"
    _generate_keypair(wrong_identity)
    cipher = tmp_path / "backup.age"

    source = _source_emitting(b"secret", tmp_path)
    with cipher.open("wb") as out:
        crypto.encrypt(source, [recipient], out)

    with cipher.open("rb") as ciphertext:
        decrypted = crypto.decrypt(ciphertext, str(wrong_identity))
        stream = decrypted.process.stdout
        assert stream is not None
        stream.read()
        stream.close()
        with pytest.raises(CryptoError) as excinfo:
            crypto.check(decrypted)

    assert "no identity matched any of the recipients" in str(excinfo.value)


def test_encrypt_reports_both_failures_when_source_and_age_fail(tmp_path: Path) -> None:
    cipher = tmp_path / "backup.age"
    source = processes.start(
        ["sh", "-c", "echo 'pg_dump: error: boom' >&2; exit 1"], stdout=subprocess.PIPE
    )

    with cipher.open("wb") as out:
        with pytest.raises(CryptoError) as excinfo:
            crypto.encrypt(source, ["not-an-age-recipient"], out)

    message = str(excinfo.value)
    assert message.startswith("backup source failed: pg_dump: error: boom; age failed: ")
