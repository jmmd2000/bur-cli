from __future__ import annotations

import subprocess

from bur import processes


def test_finish_returns_what_the_command_wrote_to_stderr() -> None:
    running = processes.start(["sh", "-c", "echo 'something broke' >&2; exit 3"])

    assert processes.finish(running) == "something broke"
    assert running.process.returncode == 3


def test_finish_returns_nothing_when_stderr_was_empty() -> None:
    running = processes.start(["sh", "-c", "exit 0"])

    assert processes.finish(running) == ""
    assert running.process.returncode == 0


def test_finish_closes_the_stderr_file() -> None:
    running = processes.start(["sh", "-c", "exit 0"])

    processes.finish(running)

    assert running.stderr_file.closed


def test_start_pipes_stdout_when_asked() -> None:
    running = processes.start(["sh", "-c", "printf hello"], stdout=subprocess.PIPE)
    assert running.process.stdout is not None

    output = running.process.stdout.read()
    running.process.stdout.close()
    processes.finish(running)

    assert output == b"hello"


def test_a_lot_of_stderr_does_not_stall_stdout() -> None:
    script = "head -c 1000000 /dev/zero | tr '\\0' e >&2; printf done"
    running = processes.start(["sh", "-c", script], stdout=subprocess.PIPE)
    assert running.process.stdout is not None

    output = running.process.stdout.read()
    running.process.stdout.close()
    stderr = processes.finish(running)

    assert output == b"done"
    assert len(stderr) == 1_000_000
