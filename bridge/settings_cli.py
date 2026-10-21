"""`jobpilot settings`: create the settings file once, then check it."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from jobpilot.bridge.settings_seed import seed_settings
from jobpilot.core.config import DATA_DIR
from jobpilot.engine.adapters.toml_settings import (
    SETTINGS_FILENAME,
    TomlSettings,
    render_settings,
)
from jobpilot.engine.domain import Remote, Settings, SettingsError, parse_settings

app = typer.Typer(
    help="Your settings file: create it once, then check it.",
    no_args_is_help=True,
)
console = Console()

PathOption = Annotated[
    Path | None, typer.Option(help="Settings file (default: data/jobpilot.toml).")
]


def _read_json(path: Path) -> dict[str, Any]:
    """A legacy JSON store, or {} when it's missing or unreadable (with a note)."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        console.print(f"[yellow]Ignoring {path}: {exc}[/yellow]")
        return {}
    return data if isinstance(data, dict) else {}


def _gigs_preferences_path() -> Path:
    from jobpilot.gigs.core.paths import data_dir

    return data_dir() / "preferences.json"


def describe(settings: Settings) -> list[str]:
    """The rules in plain words, one line each."""
    profile, targets, rules = settings.profile, settings.targets, settings.rules
    remote = {Remote.US: "US only", Remote.ANYWHERE: "anywhere", Remote.NONE: "no"}[
        rules.location.remote
    ]
    level = rules.level
    return [
        f"Location: {', '.join(rules.location.home) or 'no home set'} · remote: {remote} · "
        f"relocate: {'yes' if rules.location.relocate else 'no'}",
        f"Skip titles containing: {', '.join(level.exclude_titles)}"
        if level.exclude_titles
        else "Skip titles containing: (nothing)",
        f"Skip postings that require more than {level.max_years_required} years"
        if level.max_years_required
        else "Years required: no limit",
        f"Won't work for: {', '.join(rules.companies.exclude)}"
        if rules.companies.exclude
        else "Won't work for: (nobody listed)",
        "Large companies: ranked lower"
        if rules.companies.avoid_large
        else "Large companies: no preference",
        f"Pay floor: ${rules.pay_floor:,} a year"
        if rules.pay_floor
        else "Pay floor: none",
        f"Travel: up to {rules.max_travel_percent}%",
        f"Languages: {', '.join(profile.languages)}"
        if profile.languages
        else "Languages: (not set)",
        f"Targets: {len(targets.titles)} titles, {len(targets.skills)} skills",
        "Fit check: rules only (no AI)"
        if settings.fit.engine.value == "rules"
        else f"Fit check: {settings.fit.model} through Ollama",
    ]


@app.command("init")
def init(
    path: PathOption = None,
    print_only: Annotated[
        bool, typer.Option("--print", help="Show the file instead of writing it.")
    ] = False,
    profile: Annotated[Path | None, typer.Option(help="Legacy profile.json.")] = None,
    preferences: Annotated[
        Path | None, typer.Option(help="Legacy gigs preferences.json.")
    ] = None,
    policy: Annotated[Path | None, typer.Option(help="Legacy policy.json.")] = None,
) -> None:
    """Create jobpilot.toml from what JobPilot already knows. Never overwrites."""
    target = path or DATA_DIR / SETTINGS_FILENAME
    settings = seed_settings(
        _read_json(profile or DATA_DIR / "profile.json"),
        _read_json(preferences or _gigs_preferences_path()),
        _read_json(policy or DATA_DIR / "policy.json"),
    )
    text = render_settings(settings)
    parse_settings(tomllib.loads(text))  # the file we write must load cleanly

    if print_only:
        typer.echo(text)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # O_EXCL: never replace an existing file, even in a race. 0600: it holds
        # contact details.
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        console.print(
            f"{target} already exists. Edit it, or move it aside to start fresh."
        )
        raise typer.Exit(1) from None
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)
    console.print(
        f"[green]Created {target}[/green]. Review it, then run `jobpilot settings check`."
    )


@app.command("check")
def check(path: PathOption = None) -> None:
    """Validate the settings file and show your rules in plain words."""
    source = TomlSettings(path or DATA_DIR / SETTINGS_FILENAME)
    try:
        settings, warnings = source.load()
    except SettingsError as exc:
        for problem in exc.problems:
            console.print(f"[red]✗[/red] {problem}")
        raise typer.Exit(1) from None
    for warning in warnings:
        console.print(f"[yellow]![/yellow] {warning}")
    for line in describe(settings):
        console.print(line, markup=False, highlight=False)
