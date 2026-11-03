"""The bur command line"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from bur import discovery, runner
from bur.config import Config, ConfigError, load_config
from bur.container import ContainerError
from bur.crypto import CryptoError
from bur.discovery import DBTarget, DiscoveryError, LabelProblem
from bur.dockerpg import DockerCommandError
from bur.manifest import ManifestError
from bur.naming import InvalidBackupName, format_timestamp, parse_timestamp
from bur.runner import BackupError, DrillError
from bur.storage import BackupNotFound

app = typer.Typer()


@app.callback()
def bur_callback() -> None:
    """Back up and restore dockerised Postgres databases"""


def _load_config_or_exit(config_path: Path | None) -> Config:
    """Load the config, reporting and exiting on a config error"""
    try:
        return load_config(config_path)
    except ConfigError as error:
        typer.secho(f"config error: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error


@app.command()
def backup(
    only: Annotated[
        str | None, typer.Option("--only", help="Back up only this app, defaults to every app")
    ] = None,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to bur.toml, defaults to $BUR_CONFIG")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Check space and report, but write nothing")
    ] = False,
) -> None:
    """Back up every discovered database to encrypted local files"""
    config = _load_config_or_exit(config_path)

    try:
        discovered = discovery.discover(config)
    except DiscoveryError as error:
        typer.secho(f"discovery failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    if only is None:
        targets = discovered.targets
        failures = len(discovered.problems)
        for problem in discovered.problems:
            typer.secho(f"{problem.container}: {problem.problem}", fg=typer.colors.RED, err=True)
    else:
        targets = [target for target in discovered.targets if target.app == only]
        failures = 0
        _check_only_target(only, targets, discovered.problems)

    if not targets and failures == 0:
        typer.echo("no databases have opted in")
        return

    for target in targets:
        if target.excluded:
            typer.echo(f"{target.app}: excluded, skipped")
            continue
        if not _back_up_target(config, target, dry_run):
            failures += 1

    if failures:
        typer.secho(f"{failures} problem(s) found", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


def _check_only_target(only: str, targets: list[DBTarget], problems: list[LabelProblem]) -> None:
    """Exit unless ``--only`` matches one discovered database that isn't excluded"""
    if not targets:
        typer.secho(f"no discovered database has app {only}", fg=typer.colors.RED, err=True)
        for problem in problems:
            typer.secho(f"{problem.container}: {problem.problem}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if all(target.excluded for target in targets):
        typer.secho(f"{only} is excluded in the config", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


def _back_up_target(config: Config, target: DBTarget, dry_run: bool) -> bool:
    """Back up one database, prune its old runs, and print the outcome

    :returns: True if the backup and the prune both succeeded
    """
    try:
        result = runner.backup_database(
            config, target.app, target.container, target.database, target.user, dry_run=dry_run
        )
    except (BackupError, CryptoError, DockerCommandError, OSError) as error:
        typer.secho(f"{target.app}: backup failed, {error}", fg=typer.colors.RED, err=True)
        return False

    verb = "would write" if result.dry_run else "wrote"
    typer.echo(
        f"{target.app}: {verb} {result.dump_name}, {result.globals_name} and {result.manifest_name}"
    )

    try:
        pruned = runner.prune(
            config, target.app, target.keep_last, result.timestamp, dry_run=dry_run
        )
    except OSError as error:
        typer.secho(
            f"{target.app}: backed up, but the prune failed, {error}",
            fg=typer.colors.RED,
            err=True,
        )
        return False

    verb = "would prune" if dry_run else "pruned"
    for run in pruned:
        typer.echo(f"{target.app}: {verb} the backup from {format_timestamp(run.timestamp)}")
    return True


@app.command()
def restore(
    app_name: Annotated[
        str, typer.Argument(metavar="APP", help="Application whose backup to restore")
    ],
    identity: Annotated[Path, typer.Option("--identity", help="Path to the age identity file")],
    major: Annotated[
        str | None,
        typer.Option(
            "--major",
            help="Postgres major version for the throwaway container, "
            "defaults to the version the backup was taken from",
        ),
    ] = None,
    drill: Annotated[
        bool, typer.Option("--drill", help="Restore into a throwaway container and check it")
    ] = False,
    db: Annotated[
        str | None, typer.Option(help="Database to restore into, defaults to the app name")
    ] = None,
    date: Annotated[
        str | None, typer.Option("--date", help="Backup to restore, by its timestamp")
    ] = None,
    latest: Annotated[
        bool, typer.Option("--latest", help="Restore the newest complete backup")
    ] = False,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to bur.toml, defaults to $BUR_CONFIG")
    ] = None,
) -> None:
    """Restore a stored backup"""
    if not drill:
        typer.secho("only --drill rn", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    if date is None and not latest:
        typer.secho(
            "choose a backup with --date (e.g. 20260603T174800Z) or --latest",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    if date is not None and latest:
        typer.secho("pass --date or --latest, not both", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    config = _load_config_or_exit(config_path)

    timestamp: datetime | None = None
    if date is not None:
        try:
            timestamp = parse_timestamp(date)
        except InvalidBackupName as error:
            typer.secho(f"bad --date: {error}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from error

    try:
        result = runner.restore_drill(
            config, app_name, str(identity), major, db or app_name, timestamp=timestamp
        )
    except (
        BackupNotFound,
        ContainerError,
        CryptoError,
        DockerCommandError,
        DrillError,
        ManifestError,
    ) as error:
        typer.secho(f"drill failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"restored {result.dump_name} into a throwaway postgres:{result.major}")
    for table, rows in result.row_counts.items():
        typer.echo(f"  {table}: {rows} rows")


@app.command()
def dbs(
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to bur.toml, defaults to $BUR_CONFIG")
    ] = None,
) -> None:
    """List the databases bur has discovered"""
    config = _load_config_or_exit(config_path)

    try:
        discovered = discovery.discover(config)
    except DiscoveryError as error:
        typer.secho(f"discovery failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    if not discovered.targets and not discovered.problems:
        typer.echo("no databases have opted in")
        return

    if discovered.targets:
        typer.echo(
            f"{'APP':<22} {'CONTAINER':<28} {'DATABASE':<18} {'USER':<16} {'KEEP':<6} SCHEDULE"
        )
    for target in discovered.targets:
        marker = "  (excluded)" if target.excluded else ""
        typer.echo(
            f"{target.app:<22} {target.container:<28} {target.database:<18} "
            f"{target.user:<16} {target.keep_last:<6} {target.schedule}{marker}"
        )

    for problem in discovered.problems:
        typer.secho(f"{problem.container}: {problem.problem}", fg=typer.colors.YELLOW, err=True)


@app.command()
def check(
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to bur.toml, defaults to $BUR_CONFIG")
    ] = None,
) -> None:
    """Check every discovered database is usable"""
    config = _load_config_or_exit(config_path)

    try:
        discovered = discovery.discover(config)
        unlabelled = discovery.audit(config)
    except DiscoveryError as error:
        typer.secho(f"discovery failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    failures = 0

    for problem in discovered.problems:
        typer.secho(f"{problem.container}: {problem.problem}", fg=typer.colors.RED, err=True)
        failures += 1

    for target in discovered.targets:
        if target.excluded:
            typer.echo(f"{target.app}: excluded, not checked")
            continue
        failure = discovery.probe_auth(target)
        if failure is None:
            typer.echo(f"{target.app}: ok")
        else:
            typer.secho(f"{target.app}: cannot connect, {failure}", fg=typer.colors.RED, err=True)
            failures += 1

    for container in unlabelled:
        typer.secho(
            f"{container.name}: running {container.image} with no bur.enable label",
            fg=typer.colors.RED,
            err=True,
        )
        failures += 1

    if failures:
        typer.secho(f"{failures} problem(s) found", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.echo("all good")
