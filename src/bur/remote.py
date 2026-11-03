"""Copy backups to an rclone remote such as Cloudflare R2, and list what is there

bur runs rclone, so the remote's credentials stay in rclone's own config.
Files live at ``<r2_remote>:<r2_bucket>/<app>/<filename>``. bur only ever adds
files: it never deletes or overwrites anything on the remote.
"""

from __future__ import annotations

import subprocess

from bur.config import Config

_TIMEOUT_FLAGS = (
    "--contimeout",
    "10s",
    "--timeout",
    "30s",
    "--retries",
    "1",
    "--low-level-retries",
    "1",
)
_DIRECTORY_NOT_FOUND = 3


class RemoteError(RuntimeError):
    """rclone is missing, or an rclone command failed"""


def is_configured(config: Config) -> bool:
    """Whether the config names a remote to copy backups to"""
    return bool(config.r2_remote and config.r2_bucket)


def upload(config: Config, app: str, name: str) -> None:
    """Upload one stored backup file to the remote

    ``--immutable`` makes rclone skip a file that is already there with the same
    content, and fail instead of overwriting one that differs.

    :param config: Loaded configuration (backup root and remote)
    :param app: Application the file belongs to
    :param name: Filename within ``<backup_root>/<app>``
    :raises RemoteError: If rclone is missing or the upload fails
    """
    source = config.backup_root / app / name
    _check(_run(["copyto", "--immutable", str(source), _remote_path(config, app, name)]))


def list_names(config: Config, app: str) -> list[str]:
    """List the filenames stored on the remote for one app

    :param config: Loaded configuration (remote)
    :param app: Application to list
    :returns: The filenames, or an empty list if the app has no folder yet
    :raises RemoteError: If rclone is missing or the listing fails
    """
    result = _run(["lsf", "--files-only", _remote_path(config, app, "")])
    if result.returncode == _DIRECTORY_NOT_FOUND:
        return []
    _check(result)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def check_bucket(config: Config) -> None:
    """Check that rclone is installed and the bucket can be listed

    :param config: Loaded configuration (remote)
    :raises RemoteError: If rclone is missing, the remote can't be reached, or the
        bucket doesn't exist
    """
    _check(_run(["lsf", "--max-depth", "1", f"{config.r2_remote}:{config.r2_bucket}"]))


def _remote_path(config: Config, app: str, name: str) -> str:
    return f"{config.r2_remote}:{config.r2_bucket}/{app}/{name}"


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one rclone command with the timeout flags, without checking its exit code"""
    command = ["rclone", *arguments, *_TIMEOUT_FLAGS]
    try:
        return subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise RemoteError("rclone is not installed") from error


def _check(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RemoteError(stderr or f"rclone failed with exit code {result.returncode}")
