"""Retain installed dependency notices in redistributable product builds."""

import importlib.metadata as metadata
import shutil
import sys
from pathlib import Path


def notices(destination: Path, root: Path):
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "LICENSE", destination / "JobPilot-LICENSE.txt")
    inventory = [
        "JobPilot build environment dependency notices",
        "",
        "This inventory includes runtime and build tools; not every package is shipped.",
        "",
    ]
    for dist in sorted(
        metadata.distributions(), key=lambda d: d.metadata["Name"].lower()
    ):
        name = dist.metadata["Name"]
        inventory.append(f"{name} {dist.version}")
        for file in dist.files or []:
            if not any(
                word in file.name.lower() for word in ["license", "copying", "notice"]
            ):
                continue
            source = Path(dist.locate_file(file))
            if source.is_file() and source.suffix not in {".py", ".pyc"}:
                target = destination / name / str(file).replace("/", "_")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    (destination / "DEPENDENCIES.txt").write_text("\n".join(inventory) + "\n")
    # Python embeds its complete license in the installed interpreter.
    import builtins

    builtins.license._Printer__setup()
    (destination / "Python-LICENSE.txt").write_text(
        "\n".join(builtins.license._Printer__lines) + "\n"
    )
    (destination / "Python-version.txt").write_text(sys.version + "\n")
