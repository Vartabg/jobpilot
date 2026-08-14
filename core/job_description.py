"""Pure job-description normalization shared by scanners and matchers."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "details",
    "div",
    "dl",
    "dt",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "summary",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
}
_IGNORED_TAGS = {"noscript", "script", "style", "svg", "template"}
_RAW_TAG_RE = re.compile(r"</?[A-Za-z][^>\n]*(?:>|$)")
_ORPHAN_TAG_RE = re.compile(
    r"(?:"
    r"(?<![A-Za-z0-9])(?:li|p|div|strong|em|span|ul|ol|br|h[1-6])"
    r"(?:\s+[^>\n]*)?"
    r"|/(?:li|p|div|strong|em|span|ul|ol|br|h[1-6])\s*"
    r")>",
    re.IGNORECASE | re.MULTILINE,
)
_LINE_WHITESPACE_RE = re.compile(r"[^\S\r\n]+")


class _DescriptionHTMLParser(HTMLParser):
    """Extract visible text while retaining semantic block/list boundaries."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0
        self._inline_boundary = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
            self._inline_boundary = False
        else:
            self._inline_boundary = True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if self._ignored_depth or tag in _IGNORED_TAGS:
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
            self._inline_boundary = False
        else:
            self._inline_boundary = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
            self._inline_boundary = False
        else:
            self._inline_boundary = True

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or not data:
            return
        if (
            self._inline_boundary
            and self.parts
            and self.parts[-1]
            and self.parts[-1][-1].isalnum()
            and data[0].isalnum()
        ):
            self.parts.append(" ")
        self.parts.append(data)
        self._inline_boundary = False

    def text(self) -> str:
        return "".join(self.parts)


def normalize_job_description(raw: object) -> str:
    """Return readable, complete plain text from an ATS description value.

    HTML entities and markup are removed, but paragraphs, headings, and list
    items remain separate lines so requirement classification can retain the
    source document's structure. The function intentionally does not truncate.
    """
    if raw is None:
        return ""
    source = str(raw).strip()
    if not source:
        return ""

    # Some feeds escape an already-HTML value. Decode twice at most so those
    # tags are treated as structure without repeatedly transforming prose.
    for _ in range(2):
        decoded = html.unescape(source)
        if decoded == source:
            break
        source = decoded

    parser = _DescriptionHTMLParser()
    parser.feed(source)
    parser.close()
    text = _RAW_TAG_RE.sub(" ", parser.text()).replace("\r\n", "\n").replace("\r", "\n")
    text = _ORPHAN_TAG_RE.sub(" ", text)
    lines = (_LINE_WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def richest_job_description(*values: object) -> str:
    """Choose the most complete normalized variant exposed by an ATS."""
    candidates = [normalize_job_description(value) for value in values]
    return max(candidates, key=len, default="")
