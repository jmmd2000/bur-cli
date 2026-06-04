"""Encrypt and decrypt backups with age

A backup pipes the dump straight into age and into a file (pg_dump | age > file).
If the dump breaks mid-stream, age still exits with 0 on the bytes it already got,
so encrypt() also checks the dump's exit code and fails the backup when it died.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import IO


class CryptoError(RuntimeError):
    """age, or the process feeding it, exited non-zero"""


def encrypt(source: subprocess.Popen[bytes], recipients: Sequence[str], out: IO[bytes]) -> None:
    """Pipe source's stdout thru age and write the ciphertext to out

    :param source: A running process emitting the unencrypted dump on its stdout
    :param recipients: age public keys to encrypt to
    :param out: Open binary file the ciphertext is written to
    :raises CryptoError: If there are no recipients, or source
    or age exists with a non-zero exit code.
    """
    if not recipients:
        raise CryptoError("no recipients given")
    stdout = source.stdout
    if stdout is None:
        raise CryptoError("source has no stdout to read")

    command = ["age"]
    for recipient in recipients:
        command += ["-r", recipient]

    age = subprocess.Popen(command, stdin=stdout, stdout=out)
    stdout.close()
    age.wait()
    source.wait()

    if source.returncode != 0:
        raise CryptoError(f"backup source failed with exit code {source.returncode}")
    if age.returncode != 0:
        raise CryptoError(f"age failed with exit code {age.returncode}")


def decrypt(source: IO[bytes], identity: str) -> subprocess.Popen[bytes]:
    """Start an age decrypt reading ciphertext from source

    :param source: Open binary file with the age ciphertext
    :param identity: Path to the age identity file
    :returns: The running process, plaintext on its stdout
    """
    command = ["age", "-d", "-i", identity]
    return subprocess.Popen(command, stdin=source, stdout=subprocess.PIPE)
