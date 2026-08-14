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


def test_dashboard_explains_evidence_without_claiming_personality() -> None:
    source, _parser = parse("ui/dashboard.html")

    assert "personality" not in source.lower()
    assert "Qualification floor" in source
    assert "Work-context evidence" in source
    assert "Evidence coverage" in source
    assert "Posting-source state" in source
    assert "Posting-source grade" in source
    assert "Company legitimacy" in source
    assert "does not verify the employer or guarantee the role" in source
    assert "Investigation reason" in source
    assert "Biggest gap" in source
    assert "Relocation evidence" in source
    assert "Company support confirmed" in source
    assert "relocation-filter" in source
    assert "JobPilot performance dashboard" in source
    assert "Source health" in source
    assert "30-day funnel" in source
    assert "Decision learning" in source
    assert "No automatic ranking changes" in source
    assert "api('/api/dashboard')" in source
    assert "matched_accounts" in source
    assert "Investigate" in source
    assert "isApplyReady" in source
    assert "safePostingUrl" in source
    assert "parsed.protocol === 'https:'" in source
    assert "ready now" in source
    assert "roles to investigate" in source
    assert "if (status === 'queued') return jobs.filter(isApplyReady);" in source
    assert "width: 100vw" not in source


def test_dashboard_normalizes_legacy_html_before_rendering_evidence_text() -> None:
    source, _parser = parse("ui/dashboard.html")

    assert "function evidencePlainText" in source
    assert "new DOMParser().parseFromString(source, 'text/html')" in source
    assert "parsed.querySelectorAll('script, style, template, noscript')" in source
    assert "node.after(parsed.createTextNode(' '))" in source
    assert "/(?:li|p|div|strong|em|span|ul|ol|br|h[1-6])" in source
    assert "(?:\\s+[^>\\n]*)?>" in source
    assert "escape(evidencePlainText(job.biggest_gap, 'Not recorded'))" in source
    assert "evidencePlainText(readableLabel(reason))" in source
    assert "escape(job.biggest_gap || 'Not recorded')" not in source
