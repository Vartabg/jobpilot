from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AccessibilityParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []
        self.label_targets: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        self.elements.append((tag, attributes))
        if tag == "label" and attributes.get("for"):
            self.label_targets.add(attributes["for"])


def parse(name: str) -> tuple[str, AccessibilityParser]:
    source = (ROOT / name).read_text(encoding="utf-8")
    parser = AccessibilityParser()
    parser.feed(source)
    return source, parser


def test_swipe_flow_has_semantic_and_announced_states() -> None:
    source, parser = parse("gigs/swipe.html")

    assert any(tag == "main" and attrs.get("id") == "main" for tag, attrs in parser.elements)
    assert 'href="#main"' in source
    assert 'role="status"' in source
    assert 'role="alert"' in source
    assert '<article class="card"' in source
    assert '<h2 class="role"' in source
    assert "prefers-reduced-motion: reduce" in source


def test_dashboard_labels_controls_and_announces_updates() -> None:
    source, parser = parse("ui/dashboard.html")

    assert any(tag == "main" and attrs.get("id") == "main" for tag, attrs in parser.elements)
    assert 'href="#main"' in source
    assert 'aria-live="polite"' in source
    assert "prefers-reduced-motion: reduce" in source

    for tag, attrs in parser.elements:
        if tag not in {"input", "select"} or attrs.get("type") == "hidden":
            continue
        assert attrs.get("aria-label") or attrs.get("id") in parser.label_targets, (
            f"{tag}#{attrs.get('id', '')} needs a programmatic label"
        )
