"""The engine's layering rules, checked on every test run.

- The domain is pure: standard library only, and nothing that touches files,
  processes, or the network.
- Ports depend only on the domain.
- Nothing in the engine imports the legacy lanes it will replace.
"""

import ast
import sys
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[2] / "engine"
LEGACY = ("core", "gigs", "ui", "product", "learning", "cli", "scripts")
IO_MODULES = {
    "http",
    "io",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "socket",
    "sqlite3",
    "subprocess",
    "tempfile",
    "urllib.request",
}


def imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def modules(folder: str = "") -> list[Path]:
    return sorted((ENGINE / folder).rglob("*.py"))


def test_engine_has_modules_to_check():
    assert modules("domain") and modules("adapters")


@pytest.mark.parametrize("path", modules("domain"), ids=lambda p: p.name)
def test_domain_is_pure(path):
    for name in imports(path):
        if name.startswith("jobpilot."):
            assert name.startswith("jobpilot.engine.domain"), (
                f"{path.name} imports {name}"
            )
            continue
        top = name.split(".")[0]
        assert top in sys.stdlib_module_names or top == "__future__", (
            f"{path.name} imports {name}"
        )
        assert name not in IO_MODULES and top not in IO_MODULES, (
            f"{path.name} imports {name}, which does I/O"
        )


def test_ports_depend_only_on_the_domain():
    for name in imports(ENGINE / "ports.py"):
        if name.startswith("jobpilot."):
            assert name.startswith("jobpilot.engine.domain"), f"ports.py imports {name}"


@pytest.mark.parametrize("path", modules(), ids=lambda p: str(p.relative_to(ENGINE)))
def test_engine_never_imports_legacy_lanes(path):
    for name in imports(path):
        parts = name.split(".")
        legacy = parts[0] in LEGACY or (
            parts[0] == "jobpilot" and len(parts) > 1 and parts[1] in LEGACY
        )
        assert not legacy, f"{path.relative_to(ENGINE)} imports legacy module {name}"
