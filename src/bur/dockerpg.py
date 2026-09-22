"""Run the Postgres client tools inside their own container.

``docker exec`` into the container that runs each database, so the dump
comes from a client of the right major version. If the container uses a
password, ``${POSTGRES_PASSWORD:-}`` picks it up from the container.

Each wrapper is thin, it returns a :class:`subproces.Popen` with one pipe
open and leaves the streaming and exit-code checking to the caller.
"""

from __future__ import annotations

import shlex
import subprocess

_PGPASSWORD_PREFIX = 'PGPASSWORD="${POSTGRES_PASSWORD:-}"'
_TABLE_NAMES_SQL = (
    "SELECT quote_ident(schemaname) || '.' || quote_ident(relname) "
    "FROM pg_stat_user_tables ORDER BY 1"
)


class DockerCommandError(RuntimeError):
    """A Postgres tool run via ``docker exec`` exited with a non-zero code"""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        super().__init__(f"docker exec command failed with exit code {returncode}")


def dump(container: str, db: str, user: str) -> subprocess.Popen[bytes]:
    """``pg_dump`` one db in a custom format, streamed to stdout

    :param container: Container running the database
    :param db: Database to dump
    :param user: Role to connect as
    :returns: The running process, dump to ``stdout``
    """
    inner = f"pg_dump -Fc -U {shlex.quote(user)} {shlex.quote(db)}"
    command = _exec_command(container, inner)
    return subprocess.Popen(command, stdout=subprocess.PIPE)


def dump_globals(container: str, user: str) -> subprocess.Popen[bytes]:
    """``pg_dumpall --globals-only``, streamed to stdout

    Grabs the roles, passwords and grants that a single-DB dump skips, so the
    restored database is one the app can actually log into.

    :param container: Container running the cluster
    :param user: Role to connect as
    :returns: The running process, globals SQL on ``stdout``
    """
    inner = f"pg_dumpall --globals-only -U {shlex.quote(user)}"
    command = _exec_command(container, inner)
    return subprocess.Popen(command, stdout=subprocess.PIPE)


def database_size(container: str, db: str, user: str) -> subprocess.Popen[bytes]:
    """Query a database's on-disk size in bytes, streamed to stdout

    Reports the raw ``pg_database_size`` of the database.

    :param container: Container running the db
    :param db: Database to check
    :param user: Role to connect as
    :returns: The running process, the size in bytes on ``stdout``
    """
    inner = (
        f"psql -tAc 'SELECT pg_database_size(current_database())' "
        f"-U {shlex.quote(user)} -d {shlex.quote(db)}"
    )
    command = _exec_command(container, inner)
    return subprocess.Popen(command, stdout=subprocess.PIPE)


def probe(container: str, db: str, user: str) -> subprocess.Popen[bytes]:
    """Run a trivial query to confirm the database accepts a connection

    :param container: Container running the database
    :param db: Database to connect to
    :param user: Role to connect as
    :returns: The running process
    """
    inner = f"psql -tAc 'SELECT 1' -U {shlex.quote(user)} -d {shlex.quote(db)}"
    command = _exec_command(container, inner)
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def table_names(container: str, db: str, user: str) -> subprocess.Popen[bytes]:
    """List a database's user tables, one per line, streamed to stdout

    Names come back quoted, ready to use in a follow-up query.

    :param container: Container running the database
    :param db: Database to list tables from
    :param user: Role to connect as
    :returns: The running process, one table per line on ``stdout``
    """
    inner = f"psql -tAc {shlex.quote(_TABLE_NAMES_SQL)} -U {shlex.quote(user)} -d {shlex.quote(db)}"
    command = _exec_command(container, inner)
    return subprocess.Popen(command, stdout=subprocess.PIPE)


def count_rows(container: str, db: str, user: str, table: str) -> subprocess.Popen[bytes]:
    """Count the rows in one table, streamed to stdout

    :param container: Container running the database
    :param db: Database the table lives in
    :param user: Role to connect as
    :param table: Table to count, as returned by :func:`table_names`
    :returns: The running process, the row count on ``stdout``
    """
    inner = (
        f"psql -tAc {shlex.quote(f'SELECT count(*) FROM {table}')} "
        f"-U {shlex.quote(user)} -d {shlex.quote(db)}"
    )
    command = _exec_command(container, inner)
    return subprocess.Popen(command, stdout=subprocess.PIPE)


def restore(container: str, db: str, user: str) -> subprocess.Popen[bytes]:
    """``pg_restore`` a custom format dump from stdin

    ``--single-transaction`` so a failed restore rolls back instead of leaving a
    half-dropped DB. ``--clean --if-exists`` so it can replace what's there.

    :param container: Container to restore into
    :param db: Target database, must already exist
    :param user: Role to connect as
    :returns: The running process
    """
    inner = (
        f"pg_restore --clean --if-exists --single-transaction "
        f"-U {shlex.quote(user)} -d {shlex.quote(db)}"
    )
    command = _exec_command(container, inner, interactive=True)
    return subprocess.Popen(command, stdin=subprocess.PIPE)


def restore_globals(container: str, user: str) -> subprocess.Popen[bytes]:
    """``psql`` the cluster globals from stdin

    Talks to the ``postgres`` database since the target may not exist yet, and
    skips ``ON_ERROR_STOP`` so re-running is fine.
    """
    inner = f"psql -q -U {shlex.quote(user)} -d postgres"
    command = _exec_command(container, inner, interactive=True)
    return subprocess.Popen(command, stdin=subprocess.PIPE)


def check(process: subprocess.Popen[bytes]) -> None:
    """Error if a finished process exited with a non-zero code

    :param process: A process that's already done
    :raises ValueError: if it hasn't finished
    :raises DockerCommandError: If it exited non-zero
    """
    if process.returncode is None:
        raise ValueError("process has not finished")
    if process.returncode != 0:
        raise DockerCommandError(process.returncode)


def _exec_command(container: str, inner: str, *, interactive: bool = False) -> list[str]:
    """Build the ``docker exec`` argv, wrapping ``inner`` in a shell that sets the password"""
    exec_flags = ["-i"] if interactive else []
    shell_command = f"{_PGPASSWORD_PREFIX} {inner}"
    return ["docker", "exec", *exec_flags, container, "sh", "-c", shell_command]
