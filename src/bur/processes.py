"""Start commands with their stderr kept for error messages"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from typing import IO

type Stream = int | IO[bytes] | None


@dataclass(frozen=True)
class RunningProcess:
    """A started command and the temp file its stderr goes to"""

    process: subprocess.Popen[bytes]
    stderr_file: IO[bytes]


def start(command: list[str], *, stdin: Stream = None, stdout: Stream = None) -> RunningProcess:
    """Start a command with its stderr going to a temp file

    :param command: Program and arguments to run
    :param stdin: Where the command reads from
    :param stdout: Where the command writes to
    :returns: The running command and its stderr file
    """
    stderr_file = tempfile.TemporaryFile()
    process = subprocess.Popen(command, stdin=stdin, stdout=stdout, stderr=stderr_file)
    return RunningProcess(process, stderr_file)


def finish(running: RunningProcess) -> str:
    """Wait for a command to exit, then read back and close its stderr file

    :param running: A command from :func:`start`
    :returns: What the command wrote to stderr
    """
    running.process.wait()
    running.stderr_file.seek(0)
    stderr = running.stderr_file.read().decode(errors="replace")
    running.stderr_file.close()
    return stderr.strip()
