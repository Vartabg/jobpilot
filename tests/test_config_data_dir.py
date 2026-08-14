"""Runtime data-path selection must preserve user state across installs."""

from pathlib import Path

import jobpilot.core.config as config
from jobpilot.core.config import _data_dir_from_locations


def _resolve(
    tmp_path: Path,
    *,
    package_checkout: bool = False,
    editable_checkout: bool = False,
    environ: dict[str, str] | None = None,
    platform_name: str = "darwin",
) -> Path:
    package_root = tmp_path / "site-packages" / "jobpilot"
    editable_root = tmp_path / "checkout" if editable_checkout else None
    if package_checkout:
        package_root.mkdir(parents=True)
        (package_root / "pyproject.toml").write_text("[project]\nname='jobpilot'\n")
    return _data_dir_from_locations(
        package_root=package_root,
        editable_root=editable_root,
        environ=environ or {},
        home=tmp_path / "home",
        platform_name=platform_name,
    )


def test_explicit_data_dir_wins(tmp_path: Path) -> None:
    configured = tmp_path / "private-state"
    assert _resolve(
        tmp_path,
        package_checkout=True,
        editable_checkout=True,
        environ={"JOBPILOT_DATA_DIR": str(configured)},
    ) == configured


def test_source_checkout_uses_repo_data(tmp_path: Path) -> None:
    assert _resolve(tmp_path, package_checkout=True) == (
        tmp_path / "site-packages" / "jobpilot" / "data"
    )


def test_editable_install_uses_checkout_data(tmp_path: Path) -> None:
    assert _resolve(tmp_path, editable_checkout=True) == tmp_path / "checkout" / "data"


def test_wheel_install_uses_writable_user_data_not_site_packages(tmp_path: Path) -> None:
    selected = _resolve(tmp_path)
    assert selected == tmp_path / "home" / "Library" / "Application Support" / "JobPilot"
    assert "site-packages" not in selected.parts


def test_relative_policy_paths_resolve_inside_data_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "JobPilot")
    assert config.resolve_data_path("data/gmail_applications.json") == (
        tmp_path / "JobPilot" / "gmail_applications.json"
    )
    assert config.resolve_data_path("exports/history.json") == (
        tmp_path / "JobPilot" / "exports" / "history.json"
    )
