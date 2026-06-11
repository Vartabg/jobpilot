import html
import re
import sys
from pathlib import Path


def inline(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", escaped)


def render(markdown_path: Path) -> Path:
    html_path = markdown_path.with_suffix(".html")
    body: list[str] = []
    in_ul = False

    for raw in markdown_path.read_text().splitlines():
        line = raw.rstrip()
        if not line:
            if in_ul:
                body.append("</ul>")
                in_ul = False
            continue

        if line.startswith("# "):
            if in_ul:
                body.append("</ul>")
                in_ul = False
            body.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_ul:
                body.append("</ul>")
                in_ul = False
            body.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("**") and line.endswith("**"):
            if in_ul:
                body.append("</ul>")
                in_ul = False
            body.append(f"<h3>{inline(line.strip('*'))}</h3>")
        elif line.startswith("*") and line.endswith("*"):
            if in_ul:
                body.append("</ul>")
                in_ul = False
            body.append(f"<p><em>{inline(line.strip('*'))}</em></p>")
        elif line.startswith("- "):
            if not in_ul:
                body.append("<ul>")
                in_ul = True
            body.append(f"<li>{inline(line[2:])}</li>")
        else:
            if in_ul:
                body.append("</ul>")
                in_ul = False
            body.append(f"<p>{inline(line)}</p>")

    if in_ul:
        body.append("</ul>")

    css = """
body{font-family:Arial,sans-serif;color:#111;max-width:840px;margin:28px auto;line-height:1.34;font-size:13px}
h1{font-size:27px;margin:0 0 4px}
h2{font-size:15px;border-top:1px solid #ccc;padding-top:10px;margin:16px 0 7px;text-transform:uppercase}
h3{font-size:14px;margin:10px 0 2px}
p{margin:3px 0}
ul{margin:4px 0 8px 20px;padding:0}
li{margin:3px 0}
"""
    html_doc = (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<title>Tailored Resume</title>"
        f"<style>{css}</style></head><body>"
        + "\n".join(body)
        + "</body></html>"
    )
    html_path.write_text(html_doc)
    return html_path


if __name__ == "__main__":
    print(render(Path(sys.argv[1])))
