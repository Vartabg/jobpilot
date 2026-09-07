"""Normalize external postings as plain, untrusted text."""

import hashlib
import html
import re
from typing import Any

from .models import Listing

MAX_DESC = 100000


class SourceError(Exception):
    """A safe, actionable source failure."""


_BLOCK_RE = re.compile(
    r"</?(?:p|div|br|li|ul|ol|h[1-6]|tr|table|section|article)[^>]*>", re.I
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


def html_to_text(raw: str) -> str:
    """Plain-text conversion keeping paragraphs on separate lines."""
    if not raw:
        return ""
    text = _TAG_RE.sub("", _BLOCK_RE.sub("\n", html.unescape(raw)))
    text = html.unescape(text)
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("location") or "").strip()
    return str(value or "").strip()


def _with_remote(location: str, item: dict[str, Any]) -> str:
    location = location.strip()
    scopes = (item, item.get("categories"))
    remote = item.get("isRemote") is True or any(
        str(s.get("workplaceType") or s.get("workplace_type") or "").strip().lower()
        == "remote"
        for s in scopes
        if isinstance(s, dict)
    )
    if remote and "remote" not in location.lower():
        location = f"{location} (Remote)" if location else "Remote"
    return location


def _listing(
    provider: str,
    slug: str,
    pid: Any,
    company: Any,
    title: Any,
    url: Any,
    location: str,
    desc: Any,
) -> Listing | None:
    title = _text(title)[:200]
    url = _text(url)
    if not title or not url or len(url) > 2048:
        return None
    seed = _text(pid) or url
    lid = hashlib.sha256(f"{provider}:{slug}:{seed}".encode()).hexdigest()
    try:
        return Listing(
            id=lid,
            company=(_text(company) or slug)[:200],
            title=title,
            url=url,
            location=location.strip()[:1000],
            description=html_to_text(_text(desc))[:MAX_DESC],
            provider=provider,
            board=slug,
        )
    except Exception:
        return None


def _lever_text(item: dict[str, Any]) -> str:
    parts = [_text(item.get("descriptionPlain")), _text(item.get("additionalPlain"))]
    for key in ("lists", "additional"):
        secs = item.get(key)
        if isinstance(secs, str):
            parts.append(html_to_text(secs))
            continue
        if not isinstance(secs, list):
            continue
        for sec in secs:
            if isinstance(sec, dict):
                head = _text(sec.get("text") or sec.get("title"))
                body = html_to_text(_text(sec.get("content") or sec.get("body")))
                combo = f"{head}\n{body}".strip() if head else body
                if combo:
                    parts.append(combo)
            elif _text(sec):
                parts.append(_text(sec))
    parts = [p for p in parts if p]
    return "\n\n".join(parts) if parts else _text(item.get("description"))
