"""The bur command line"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bur import runner
from bur.config import ConfigError, load_config
from bur.crypto import CryptoError
from bur.dockerpg import DockerCommandError
from bur.runner import BackupError

app = typer.Typer()


@app.callback()
def bur_callback() -> None:
    """Back up and restore dockerised Postgres databases"""


@app.command()
def backup(
    only: Annotated[str, typer.Option("--only", help="Application to back up")],
    container: Annotated[
        str | None, typer.Option(help="Container running the database, defaults to the app name")
    ] = None,
    db: Annotated[
        str | None, typer.Option(help="Database to dump, defaults to the app name")
    ] = None,
    user: Annotated[
        str | None, typer.Option(help="Role to connect as, defaults to the app name")
    ] = None,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to bur.toml, defaults to $BUR_CONFIG")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Check space and report, but write nothing")
    ] = False,
) -> None:
    """Back up one database to encrypted local files"""
    try:
        config = load_config(config_path)
    except ConfigError as error:
        typer.secho(f"config error: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    try:
        result = runner.backup_database(
            config, only, container or only, db or only, user or only, dry_run=dry_run
        )
    except (BackupError, CryptoError, DockerCommandError) as error:
        typer.secho(f"backup failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    verb = "would write" if result.dry_run else "wrote"
    typer.echo(f"{verb} {result.dump_name} and {result.globals_name}")
