"""Encrypt and decrypt backups with age

A backup pipes the dump straight into age and into a file (pg_dump | age > file).
If the dump breaks mid-stream, age still exits with 0 on the bytes it already got,
so encrypt() also checks the dump's exit code and fails the backup when it died.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import IO

from bur import processes
from bur.processes import RunningProcess


class CryptoError(RuntimeError):
    """age, or the process feeding it, exited non-zero"""


def encrypt(source: RunningProcess, recipients: Sequence[str], out: IO[bytes]) -> None:
    """Pipe source's stdout thru age and write the ciphertext to out

    :param source: A running process emitting the unencrypted dump on its stdout
    :param recipients: age public keys to encrypt to
    :param out: Open binary file the ciphertext is written to
    :raises CryptoError: If there are no recipients, or if source or age
        exits with a non-zero code, carrying what they wrote to stderr
    """
    if not recipients:
        raise CryptoError("no recipients given")
    stdout = source.process.stdout
    if stdout is None:
        raise CryptoError("source has no stdout to read")

    command = ["age"]
    for recipient in recipients:
        command += ["-r", recipient]

    age = processes.start(command, stdin=stdout, stdout=out)
    stdout.close()
    age_stderr = processes.finish(age)
    source_stderr = processes.finish(source)

    failures: list[str] = []
    if source.process.returncode != 0:
        failures.append(_failure_message("backup source", source.process.returncode, source_stderr))
    if age.process.returncode != 0:
        failures.append(_failure_message("age", age.process.returncode, age_stderr))
    if failures:
        raise CryptoError("; ".join(failures))


def decrypt(source: IO[bytes], identity: str) -> RunningProcess:
    """Start an age decrypt reading ciphertext from source

    :param source: Open binary file with the age ciphertext
    :param identity: Path to the age identity file
    :returns: The running process, plaintext on its stdout. Pass it to
        :func:`check` once the plaintext has been read
    """
    command = ["age", "-d", "-i", identity]
    return processes.start(command, stdin=source, stdout=subprocess.PIPE)


def check(running: RunningProcess) -> None:
    """Wait for an age process to finish and error if it exited with a non-zero code

    :param running: An age process
    :raises CryptoError: If age exited non-zero
    """
    stderr = processes.finish(running)
    if running.process.returncode != 0:
        raise CryptoError(_failure_message("age", running.process.returncode, stderr))


def _failure_message(name: str, returncode: int, stderr: str) -> str:
    """Describe a failed process by its stderr, or by its exit code if stderr is empty"""
    if stderr:
        return f"{name} failed: {stderr}"
    return f"{name} failed with exit code {returncode}"
