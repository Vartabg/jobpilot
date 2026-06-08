#!/usr/bin/env python3
"""Generate a human paste-ready sheet from a JobPilot answers markdown file.

Part of the "automation drafts, human submits" flow: tailored answers are
drafted into ``data/answers/<company>/<role>.md`` (from verified evidence only),
then this turns that file into a clean, labeled ``PASTE_SHEET.txt`` the user
copies into their *own* browser by hand.

Why human-submit: driving the live application form with an automated browser
(Playwright / a fresh "Chrome for Testing" profile) trips ATS spam filters — the
``navigator.webdriver`` flag, a cookieless profile, and instant field fills all
read as a bot. So no automation touches the live form; the human pastes and
submits. This script only reads a local markdown file and writes a local text
file.

Usage:
    make_paste_sheet.py <answers.md> [-o OUTPUT.txt]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def parse_answers(md_text: str) -> dict:
    """Parse a JobPilot answers markdown file into structured parts.

    Args:
        md_text: Full text of the answers markdown file.

    Returns:
        Dict with keys ``title`` (str), ``source`` (str),
        ``form_values`` (list of ``(key, value)`` tuples), and
        ``sections`` (list of ``(question, answer)`` tuples).
    """
    title = ""
    source = ""
    form_values: list[tuple[str, str]] = []
    sections: list[tuple[str, str]] = []

    cur_header: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal cur_header, buf
        if cur_header is None:
            buf = []
            return
        body = "\n".join(buf).strip()
        if cur_header.lower().startswith("form values"):
            for line in body.splitlines():
                m = re.match(r"^([^:]+):\s*(.*)$", line.strip())
                if m:
                    form_values.append((m.group(1).strip(), m.group(2).strip()))
        else:
            sections.append((cur_header, body))
        cur_header, buf = None, []

    for line in md_text.splitlines():
        if re.match(r"^#\s+", line):
            title = line[2:].strip()
            continue
        if re.match(r"^##\s+", line):
            flush()
            cur_header = line[3:].strip()
            continue
        msrc = re.match(r"^Source:\s*(.*)$", line, re.IGNORECASE)
        if msrc and cur_header is None:
            source = msrc.group(1).strip()
            continue
        if cur_header is not None:
            buf.append(line)
    flush()

    return {"title": title, "source": source, "form_values": form_values, "sections": sections}


def render_sheet(parsed: dict) -> str:
    """Render a clean, labeled paste sheet from parsed answers.

    Markdown code-backticks are stripped from answer bodies so they paste as
    clean plain text into form textareas.
    """
    lines: list[str] = [
        f"{parsed['title']} — PASTE SHEET",
        "Submit from your OWN normal browser (real profile), as a human.",
        "Do NOT let automation touch the live form — it trips ATS spam filters.",
        "",
    ]
    if parsed["source"]:
        lines += [f"URL: {parsed['source']}", ""]
    for key, value in parsed["form_values"]:
        lines.append(f"{key}: {value}")
    if parsed["form_values"]:
        lines.append("")
    for question, answer in parsed["sections"]:
        clean = answer.replace("`", "")
        lines.append(f"{'=' * 8}  {question}  {'=' * 8}")
        lines.append(clean)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a human paste sheet from a JobPilot answers markdown file."
    )
    parser.add_argument("answers", type=Path, help="Path to data/answers/<company>/<role>.md")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output path (default: PASTE_SHEET.txt beside the answers file)",
    )
    args = parser.parse_args(argv)

    if not args.answers.exists():
        print(f"error: answers file not found: {args.answers}", file=sys.stderr)
        return 1

    parsed = parse_answers(args.answers.read_text())
    if not parsed["sections"] and not parsed["form_values"]:
        print(f"error: no form values or Q&A sections parsed from {args.answers}", file=sys.stderr)
        return 1

    sheet = render_sheet(parsed)
    out = args.output or (args.answers.parent / "PASTE_SHEET.txt")
    out.write_text(sheet)
    print(f"wrote {out} ({len(sheet)} chars, {len(parsed['sections'])} answer sections)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
