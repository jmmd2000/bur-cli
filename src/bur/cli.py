"""The bur command line"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from bur import discovery, health, remote, runner
from bur.config import Config, ConfigError, load_config
from bur.container import ContainerError
from bur.crypto import CryptoError
from bur.discovery import DBTarget, DiscoveryError, LabelProblem
from bur.dockerpg import DockerCommandError
from bur.manifest import ManifestError
from bur.naming import InvalidBackupName, format_timestamp, parse_timestamp
from bur.remote import RemoteError
from bur.runner import BackupError, DrillError
from bur.status import (
    DatabaseStatus,
    app_runs,
    database_status,
    last_success,
    undiscovered_apps,
)
from bur.storage import BackupNotFound
from bur.units import readable_age, readable_size

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
    due: Annotated[
        bool, typer.Option("--due", help="Back up only the databases due by their schedule")
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

    now = datetime.now(UTC)
    for target in targets:
        if target.excluded:
            typer.echo(f"{target.app}: excluded, skipped")
            continue
        if due and not _is_due(config, target, now):
            if not dry_run and not _upload_new_runs(config, target.app):
                failures += 1
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


def _is_due(config: Config, target: DBTarget, now: datetime) -> bool:
    """Whether a database is due a backup, printing when it is next due if not"""
    previous = last_success(config, target.app)
    if previous is None or health.is_due(previous, target.interval, now):
        return True
    next_time = health.next_due(previous, target.interval)
    typer.echo(f"{target.app}: not due until {format_timestamp(next_time)}, skipped")
    return False


def _back_up_target(config: Config, target: DBTarget, dry_run: bool) -> bool:
    """Back up one database, upload it, prune its old runs, and print the outcome

    The prune is skipped when the upload fails, so a run that never reached the
    remote is never pruned.

    :returns: True if the backup, the upload and the prune all succeeded
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

    if not dry_run and not _upload_new_runs(config, target.app):
        return False

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


def _upload_new_runs(config: Config, app_name: str) -> bool:
    """Upload an app's new runs to the remote, if one is configured, and print the outcome

    :returns: True if the upload succeeded or no remote is configured
    """
    if not remote.is_configured(config):
        return True
    try:
        uploaded = runner.upload_new_runs(config, app_name)
    except (RemoteError, OSError) as error:
        typer.secho(f"{app_name}: upload to R2 failed, {error}", fg=typer.colors.RED, err=True)
        return False

    for run in uploaded:
        typer.echo(f"{app_name}: uploaded the backup from {format_timestamp(run.timestamp)} to R2")
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
def status(
    app_name: Annotated[
        str | None,
        typer.Argument(metavar="APP", help="List every backup run for this app instead"),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print the status as JSON")] = False,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Path to bur.toml, defaults to $BUR_CONFIG")
    ] = None,
) -> None:
    """Show whether each discovered database has a recent backup"""
    config = _load_config_or_exit(config_path)

    if app_name is not None:
        _print_app_runs(config, app_name, json_output)
        return

    try:
        discovered = discovery.discover(config)
    except DiscoveryError as error:
        typer.secho(f"discovery failed: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    now = datetime.now(UTC)
    statuses = [database_status(config, target, now) for target in discovered.targets]
    undiscovered = undiscovered_apps(config, {target.app for target in discovered.targets})

    if json_output:
        _print_status_json(statuses, undiscovered, discovered.problems)
    else:
        _print_status_table(statuses, undiscovered, discovered.problems)

    unhealthy = [
        database
        for database in statuses
        if not database.target.excluded and database.health.level != "ok"
    ]
    if unhealthy or discovered.problems:
        raise typer.Exit(code=1)


def _print_app_runs(config: Config, app_name: str, json_output: bool) -> None:
    """Print every backup run for one app, exiting 1 if it has none or R2 can't be listed"""
    try:
        runs = app_runs(config, app_name)
    except RemoteError as error:
        typer.secho(f"could not list R2 for {app_name}: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error

    if json_output:
        content = {
            "app": app_name,
            "runs": [
                {
                    "timestamp": format_timestamp(run.timestamp),
                    "complete": run.complete,
                    "dump_size": run.dump_size,
                    "location": run.location,
                }
                for run in runs
            ],
        }
        typer.echo(json.dumps(content, indent=2))
    else:
        if runs:
            typer.echo(f"{'TIMESTAMP':<18} {'COMPLETE':<9} {'WHERE':<6} SIZE")
        for run in runs:
            size = readable_size(run.dump_size) if run.dump_size is not None else "-"
            complete = "yes" if run.complete else "no"
            typer.echo(
                f"{format_timestamp(run.timestamp):<18} {complete:<9} {run.location:<6} {size}"
            )

    if not runs:
        typer.secho(f"no backups for {app_name}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


def _print_status_table(
    statuses: list[DatabaseStatus], undiscovered: list[str], problems: list[LabelProblem]
) -> None:
    """Print one line per database, then the undiscovered apps and any label problems"""
    if statuses:
        typer.echo(f"{'APP':<22} {'HEALTH':<11} {'LAST SUCCESS':<18} {'AGE':<9} SIZE")
    for database in statuses:
        last_success = database.health.last_success
        age = database.health.age
        size = database.dump_size
        marker = "  (excluded)" if database.target.excluded else ""
        line = (
            f"{database.target.app:<22} {database.health.level:<11} "
            f"{format_timestamp(last_success) if last_success else '-':<18} "
            f"{readable_age(age) if age is not None else '-':<9} "
            f"{readable_size(size) if size is not None else '-'}{marker}"
        )
        if database.target.excluded or database.health.level == "ok":
            typer.echo(line)
        else:
            typer.secho(line, fg=typer.colors.RED)
        if database.remote_error is not None:
            typer.secho(
                f"{database.target.app}: could not list R2, {database.remote_error}",
                fg=typer.colors.RED,
                err=True,
            )

    for app_name in undiscovered:
        typer.secho(f"{app_name}: has backups but is not discovered", fg=typer.colors.YELLOW)

    for problem in problems:
        typer.secho(f"{problem.container}: {problem.problem}", fg=typer.colors.RED, err=True)


def _print_status_json(
    statuses: list[DatabaseStatus], undiscovered: list[str], problems: list[LabelProblem]
) -> None:
    """Print the same status as one JSON object"""
    databases = []
    for database in statuses:
        last_success = database.health.last_success
        age = database.health.age
        databases.append(
            {
                "app": database.target.app,
                "container": database.target.container,
                "health": database.health.level,
                "excluded": database.target.excluded,
                "last_success": format_timestamp(last_success) if last_success else None,
                "age_seconds": int(age.total_seconds()) if age is not None else None,
                "dump_size": database.dump_size,
                "remote_error": database.remote_error,
            }
        )
    content = {
        "databases": databases,
        "undiscovered": undiscovered,
        "problems": [
            {"container": problem.container, "problem": problem.problem} for problem in problems
        ],
    }
    typer.echo(json.dumps(content, indent=2))


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

    if remote.is_configured(config):
        try:
            remote.check_bucket(config)
            typer.echo(f"R2 bucket {config.r2_bucket}: ok")
        except RemoteError as error:
            typer.secho(f"R2 bucket {config.r2_bucket}: {error}", fg=typer.colors.RED, err=True)
            failures += 1

    if failures:
        typer.secho(f"{failures} problem(s) found", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    typer.echo("all good")
