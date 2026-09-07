from pathlib import Path
from zipfile import ZipFile

from scripts.package_product import package


def test_public_source_contains_only_standalone_product(tmp_path):
    root = Path(__file__).resolve().parents[2]
    archive = tmp_path / "source.zip"
    package(root, archive)
    with ZipFile(archive) as source:
        paths = source.namelist()
        assert "JobPilot-source/product/api.py" in paths
        assert "JobPilot-source/README.md" in paths
        assert "JobPilot-source/LICENSE" in paths
        assert not any(
            part in path
            for path in paths
            for part in [
                ".git",
                "core/",
                "profile.json",
                "api-token",
                ".sqlite",
                "__pycache__",
                ".venv",
            ]
        )
        assert all(path.startswith("JobPilot-source/") for path in paths)
