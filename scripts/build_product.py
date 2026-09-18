"""Build the standalone macOS preview without packaging personal runtime data."""

import argparse
import platform
import subprocess
import sys
from pathlib import Path

from product_notices import notices


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/product"))
    args = parser.parse_args()
    if platform.system() != "Darwin":
        raise SystemExit("Build this macOS app on macOS.")
    root = Path(__file__).resolve().parents[1]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    notice_dir = out / "notices"
    notices(notice_dir, root)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--windowed",
            "--name",
            "JobPilot",
            "--osx-bundle-identifier",
            "com.atxbro.jobpilot",
            "--distpath",
            str(out),
            "--workpath",
            str(out / "build"),
            "--specpath",
            str(out / "spec"),
            "--paths",
            str(root),
            "--add-data",
            f"{root / 'product/static'}:product/static",
            "--add-data",
            f"{notice_dir}:THIRD_PARTY_NOTICES",
            "--collect-submodules",
            "uvicorn",
            "--exclude-module",
            "playwright",
            "--exclude-module",
            "pytest",
            str(root / "jobpilot_app.py"),
        ],
        cwd=root,
        check=True,
    )
    archive = out / f"JobPilot-macOS-{platform.machine()}-preview.zip"
    subprocess.run(
        [
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(out / "JobPilot.app"),
            str(archive),
        ],
        check=True,
    )
    print(archive)


if __name__ == "__main__":
    main()
