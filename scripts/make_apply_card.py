#!/usr/bin/env python3
"""Generate a one-click "apply card" (local HTML) from a JobPilot answers file.

Same "automation drafts, human submits" flow — but instead of a flat text sheet
the human must highlight by hand (error-prone, easy to grab the wrong chunk),
this renders a local HTML page with one **Copy** button per field, each labeled
with its exact question. The human opens it in their own browser beside the form,
clicks Copy, and pastes. No automation touches the live form.

Clipboard copy uses the async Clipboard API with an ``execCommand`` fallback so it
works even from a ``file://`` page.

Usage:
    make_apply_card.py <answers.md> [-o OUTPUT.html] [--open]
"""
from __future__ import annotations

import argparse
import html
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_paste_sheet import parse_answers  # noqa: E402

CSS = """
:root{--bg:#0f1115;--card:#1a1e26;--ink:#e8eaed;--mut:#9aa3b2;--ok:#2ea043;--accent:#3b82f6}
*{box-sizing:border-box}
body{margin:0 auto;max-width:820px;background:var(--bg);color:var(--ink);
  font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;padding:24px}
h1{font-size:20px;margin:0 0 4px}
.hint{color:var(--mut);font-size:13px;margin:0 0 16px}
.openform{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;
  padding:10px 16px;border-radius:8px;font-weight:600;margin:0 0 18px}
.card{background:var(--card);border:1px solid #272d38;border-radius:10px;padding:12px 14px;margin:10px 0}
.cardhead{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px}
.q{font-weight:600}
.cardhint{color:var(--mut);font-size:12px;margin:0 0 8px}
.copy{background:#2b3340;color:var(--ink);border:1px solid #3a4150;border-radius:7px;
  padding:7px 16px;font-size:13px;cursor:pointer;white-space:nowrap}
.copy:hover{background:#343d4d}
.copy.ok{background:var(--ok);border-color:var(--ok);color:#fff}
textarea.val{width:100%;background:#0d1016;color:var(--ink);border:1px solid #272d38;border-radius:7px;
  padding:9px;font:14px/1.55 ui-monospace,Menlo,monospace;resize:vertical}
"""

JS = """
const flash=(btn,ok)=>{const o=btn.dataset.label;btn.textContent=ok?'\\u2713 Copied':'Copy failed';
  btn.classList.toggle('ok',ok);setTimeout(()=>{btn.textContent=o;btn.classList.remove('ok');},1200);};
async function copyFrom(btn){
  const ta=btn.closest('.card').querySelector('textarea');
  try{await navigator.clipboard.writeText(ta.value);flash(btn,true);return;}catch(e){}
  ta.removeAttribute('readonly');ta.focus();ta.select();
  let ok=false;try{ok=document.execCommand('copy');}catch(e){}
  ta.setAttribute('readonly','');window.getSelection().removeAllRanges();flash(btn,ok);
}
document.querySelectorAll('.copy').forEach(b=>b.dataset.label=b.textContent);
"""


def _card(label: str, value: str, rows: int, hint: str = "") -> str:
    el = html.escape(label)
    ev = html.escape(value)
    hint_html = f'<p class="cardhint">{html.escape(hint)}</p>' if hint else ""
    return (
        '<div class="card"><div class="cardhead">'
        f'<span class="q">{el}</span>'
        '<button class="copy" onclick="copyFrom(this)">Copy</button></div>'
        f"{hint_html}"
        f'<textarea class="val" rows="{rows}" readonly>{ev}</textarea></div>'
    )


def build_html(parsed: dict) -> str:
    title = html.escape(parsed["title"] or "Application")
    url = parsed.get("source") or ""
    open_btn = (
        f'<a class="openform" href="{html.escape(url, quote=True)}" target="_blank" '
        'rel="noopener">Open application form ↗</a>'
        if url
        else ""
    )
    cards: list[str] = []
    for key, value in parsed["form_values"]:
        hint = ""
        if key.strip().lower() == "resume":
            hint = "Upload this file. Tip: in the macOS file picker press ⌘⇧G and paste this path."
        cards.append(_card(key, value, rows=2 if len(value) > 38 else 1, hint=hint))
    for question, answer in parsed["sections"]:
        cards.append(_card(question, answer.replace("`", ""), rows=12))

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title><style>{CSS}</style></head><body>"
        f"<h1>{title}</h1>"
        '<p class="hint">Open the form, then click <b>Copy</b> on each field and paste it into the '
        "matching question. Dropdowns: just select the shown value.</p>"
        f'{open_btn}{"".join(cards)}'
        f"<script>{JS}</script></body></html>"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a one-click apply card (HTML) from an answers file.")
    parser.add_argument("answers", type=Path, help="Path to data/answers/<company>/<role>.md")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output path (default: apply_card.html beside answers)")
    parser.add_argument("--open", action="store_true", help="Open the card in the default browser after writing")
    args = parser.parse_args(argv)

    if not args.answers.exists():
        print(f"error: answers file not found: {args.answers}", file=sys.stderr)
        return 1

    parsed = parse_answers(args.answers.read_text())
    if not parsed["sections"] and not parsed["form_values"]:
        print(f"error: nothing parsed from {args.answers}", file=sys.stderr)
        return 1

    out = args.output or (args.answers.parent / "apply_card.html")
    out.write_text(build_html(parsed))
    n_fields = len(parsed["form_values"]) + len(parsed["sections"])
    print(f"wrote {out} ({n_fields} copy buttons)")
    if args.open:
        subprocess.run(["open", str(out)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
