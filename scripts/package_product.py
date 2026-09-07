"""Create an allowlisted source download; never archive the private repository."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def package(root: Path, output: Path):
    files = [
        root / name
        for name in [
            "jobpilot_app.py",
            "product-requirements.txt",
            "LICENSE",
            "scripts/build_product.py",
            "scripts/product_notices.py",
            "scripts/package_product.py",
        ]
    ]
    files += sorted((root / "product").glob("*.py"))
    files += sorted((root / "product/static").glob("*"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in files:
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"Unexpected source path: {path.name}")
            archive.write(path, "JobPilot-source/" + str(path.relative_to(root)))
        archive.write(root / "docs/PRODUCT.md", "JobPilot-source/README.md")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=Path("dist/product/JobPilot-source-preview.zip")
    )
    args = parser.parse_args()
    package(Path(__file__).resolve().parents[1], args.output)
    print(args.output.resolve())
