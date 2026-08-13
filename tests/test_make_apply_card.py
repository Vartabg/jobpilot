"""Safety checks for the local human paste card."""

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts" / "make_apply_card.py"
    spec = importlib.util.spec_from_file_location("make_apply_card_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_apply_card_links_only_to_safe_https_postings() -> None:
    module = _module()
    parsed = {
        "title": "Role",
        "source": "https://jobs.example.test/role",
        "form_values": [],
        "sections": [],
    }

    assert "Open application form" in module.build_html(parsed)

    for unsafe in (
        "javascript:alert(1)",
        "file:///etc/passwd",
        "shortcuts://run/untrusted",
        "https://user:pass@jobs.example.test/role",
    ):
        parsed["source"] = unsafe
        assert "Open application form" not in module.build_html(parsed)
