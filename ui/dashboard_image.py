"""Render a one-glance 'overview of everything' dashboard as a PNG.

Pulls local state only (pipeline.md, the queue, source-health json, the tracker
DB) so it renders instantly without re-scraping. Pair with ui.inline_image to
show it inside iTerm; falls back to ascii_dashboard() elsewhere.

Pillow-only by design — no matplotlib — to keep the tool light to install.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# ---- palette (dark, GitHub-ish) ------------------------------------------
BG = (13, 17, 23)
PANEL = (22, 27, 34)
BORDER = (48, 54, 61)
TRACK = (33, 38, 45)
TEXT = (230, 237, 243)
DIM = (139, 148, 158)
GREEN = (63, 185, 80)
YELLOW = (210, 153, 34)
BLUE = (88, 166, 255)
ORANGE = (219, 109, 40)
RED = (248, 81, 73)
PURPLE = (188, 140, 255)

# Canonical stage orders + colors.
GIG_STAGES = ["new", "saved", "drafted", "sent", "replied", "interview", "hired"]
GIG_COLORS = {
    "new": BLUE, "saved": PURPLE, "drafted": YELLOW, "sent": ORANGE,
    "replied": GREEN, "interview": GREEN, "hired": GREEN,
}
JOB_STAGES = ["applied", "screening", "interview", "offer", "rejected", "withdrawn"]
JOB_COLORS = {
    "applied": BLUE, "screening": YELLOW, "interview": ORANGE,
    "offer": GREEN, "rejected": RED, "withdrawn": DIM,
}

_FONT_CANDIDATES = {
    "regular": [
        ("/System/Library/Fonts/Helvetica.ttc", 0),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", 0),
        ("/Library/Fonts/Arial.ttf", 0),
    ],
    "bold": [
        ("/System/Library/Fonts/Helvetica.ttc", 1),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0),
    ],
    "mono": [
        ("/System/Library/Fonts/Menlo.ttc", 0),
        ("/System/Library/Fonts/Courier.ttc", 0),
    ],
}


def _font(size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    for path, index in _FONT_CANDIDATES.get(weight, []):
        try:
            return ImageFont.truetype(path, size, index=index)
        except Exception:
            continue
    return ImageFont.load_default(size)


@dataclass
class DashboardData:
    name: str = ""
    generated_at: str = ""
    gig_stages: list[tuple[str, int]] = field(default_factory=list)
    job_stages: list[tuple[str, int]] = field(default_factory=list)
    sources: list[tuple[str, bool, int]] = field(default_factory=list)
    income: dict = field(default_factory=dict)
    hot: list[tuple[str, int]] = field(default_factory=list)


# ---- data collection (local state only) ----------------------------------

def collect_dashboard_data(*, fetch: bool = False) -> DashboardData:
    """Snapshot everything worth seeing, from local files. `fetch` is reserved
    for a future live-scrape refresh; the default reads cached state only."""
    data = DashboardData(generated_at=datetime.now().strftime("%a %b %-d  %H:%M"))

    try:
        from jobpilot.core.profile_store import get_profile_store
        data.name = (get_profile_store().load().first_name or "").strip()
    except Exception:
        data.name = ""

    # Gigs pipeline + what's hot (pipeline.md)
    rows = []
    try:
        from jobpilot.gigs.core import pipeline
        rows = pipeline.parse()
    except Exception:
        rows = []
    gig_counts: dict[str, int] = {}
    for r in rows:
        gig_counts[r.status] = gig_counts.get(r.status, 0) + 1
    data.gig_stages = [(s, gig_counts.get(s, 0)) for s in GIG_STAGES if gig_counts.get(s, 0)]
    # surface any non-canonical statuses too, so nothing hides
    for status, n in sorted(gig_counts.items(), key=lambda kv: -kv[1]):
        if status not in GIG_STAGES:
            data.gig_stages.append((status, n))

    fresh = [r for r in rows if r.status in {"new", "saved"}]
    fresh.sort(key=lambda r: -(r.score or 0))
    data.hot = [
        (f"{(r.company or '').strip()} — {(r.role or '').strip()}".strip(" —")[:46], int(r.score or 0))
        for r in fresh[:6]
    ]

    data.income = _income_velocity(rows)

    # Jobs / applications (tracker DB)
    try:
        from jobpilot.core.application_tracker import get_application_tracker
        counts = get_application_tracker().get_status_counts()
    except Exception:
        counts = {}
    ordered = [(s, counts.get(s, 0)) for s in JOB_STAGES if counts.get(s, 0)]
    for status, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if status not in JOB_STAGES:
            ordered.append((status, n))
    data.job_stages = ordered

    # Source health (json)
    try:
        from jobpilot.gigs.core import source_health
        health = source_health._load()
    except Exception:
        health = {}
    srcs = []
    for name, info in health.items():
        if not isinstance(info, dict):
            continue
        srcs.append((name, bool(info.get("last_ok", False)), int(info.get("last_count", 0) or 0)))
    data.sources = srcs

    return data


def _income_velocity(rows: list) -> dict:
    import re
    active = [r for r in rows if r.status in {"saved", "drafted", "sent", "replied", "interview"}]
    sent = sum(1 for r in rows if r.status in {"sent", "replied", "interview"})
    drafted = sum(1 for r in rows if r.status == "drafted")
    potential_hr = 0.0
    for row in active:
        pay = (getattr(row, "pay", "") or "").lower()
        if "hr" in pay:
            m = re.search(r"\$?(\d+)", pay)
            if m:
                potential_hr = max(potential_hr, float(m.group(1)))
    return {
        "active": len(active),
        "drafted": drafted,
        "sent": sent,
        "potential_week": int(potential_hr * 20) if potential_hr >= 30 else 0,
    }


# ---- rendering -----------------------------------------------------------

def render_dashboard_png(data: DashboardData, *, scale: int = 2) -> bytes:
    """Render the dashboard to PNG bytes. `scale` oversamples for crisp output
    on retina displays (2 = render at 2x)."""
    W, H = 840, 600
    img = Image.new("RGB", (W * scale, H * scale), BG)
    d = ImageDraw.Draw(img)

    def s(v: int) -> int:
        return v * scale

    f_title = _font(20 * scale, "bold")
    f_panel = _font(13 * scale, "bold")
    f_label = _font(12 * scale, "regular")
    f_small = _font(10 * scale, "regular")
    f_big = _font(26 * scale, "bold")
    f_mono = _font(11 * scale, "mono")

    def text(x, y, t, font, fill=TEXT, anchor="la"):
        d.text((s(x), s(y)), t, font=font, fill=fill, anchor=anchor)

    def panel(x, y, w, h, title):
        d.rounded_rectangle(
            [s(x), s(y), s(x + w), s(y + h)], radius=s(8),
            fill=PANEL, outline=BORDER, width=max(1, scale),
        )
        text(x + 14, y + 11, title.upper(), f_panel, DIM)

    # Header
    title = "JobPilot — Income Dashboard"
    text(24, 18, title, f_title, TEXT)
    sub = data.name or "your search"
    text(24, 46, f"{sub}", f_label, BLUE)
    text(W - 24, 22, data.generated_at, f_small, DIM, anchor="ra")

    pad, top = 24, 76
    col_w = (W - pad * 3) // 2
    row_h = 176
    gap = 16

    # Panel: Gigs pipeline (top-left)
    _bar_panel(d, s, text, panel, f_label, f_small,
               pad, top, col_w, row_h, "Gigs pipeline",
               data.gig_stages, GIG_COLORS, scale, empty="No gigs yet — run `jobpilot gigs digest`")

    # Panel: Applications (top-right)
    _bar_panel(d, s, text, panel, f_label, f_small,
               pad * 2 + col_w, top, col_w, row_h, "Job applications",
               data.job_stages, JOB_COLORS, scale, empty="No applications logged yet")

    # Panel: Source health (bottom-left)
    y2 = top + row_h + gap
    panel(pad, y2, col_w, row_h, "Source health")
    if data.sources:
        ry = y2 + 40
        for name, ok, count in data.sources[:6]:
            dot = GREEN if ok else RED
            d.ellipse([s(pad + 16), s(ry + 2), s(pad + 24), s(ry + 10)], fill=dot)
            text(pad + 34, ry, name, f_label, TEXT)
            text(pad + col_w - 16, ry, f"{count}", f_label, DIM, anchor="ra")
            ry += 21
    else:
        text(pad + 16, y2 + 44, "No source data yet", f_small, DIM)

    # Panel: Income velocity (bottom-right)
    xr = pad * 2 + col_w
    panel(xr, y2, col_w, row_h, "Income velocity")
    inc = data.income or {}
    stats = [
        ("active", inc.get("active", 0), BLUE),
        ("drafted", inc.get("drafted", 0), YELLOW),
        ("sent", inc.get("sent", 0), GREEN),
    ]
    sx = xr + 18
    for label, val, color in stats:
        text(sx, y2 + 46, str(val), f_big, color)
        text(sx + 4, y2 + 88, label, f_small, DIM)
        sx += (col_w - 36) // 3
    pw = inc.get("potential_week", 0)
    pw_txt = f"~${pw:,}/wk potential" if pw else "pay bands unclear on active rows"
    text(xr + 18, y2 + 120, pw_txt, f_label, GREEN if pw else DIM)

    # Strip: what's hot
    ys = y2 + row_h + gap
    strip_h = H - ys - pad
    panel(pad, ys, W - pad * 2, strip_h, "What's hot (top fresh by score)")
    if data.hot:
        hx, hy = pad + 16, ys + 38
        half = (W - pad * 2) // 2
        for i, (label, score) in enumerate(data.hot):
            col = i % 2
            rowi = i // 2
            cx = hx + col * half
            cy = hy + rowi * 22
            chip = GREEN if score >= 90 else (YELLOW if score >= 70 else DIM)
            d.rounded_rectangle(
                [s(cx), s(cy), s(cx + 34), s(cy + 16)], radius=s(4), fill=chip)
            d.text((s(cx + 17), s(cy + 8)), str(score), font=f_small, fill=BG, anchor="mm")
            text(cx + 44, cy + 1, label, f_mono, TEXT)
    else:
        text(pad + 16, ys + 42, "Nothing fresh in the pipeline right now.", f_small, DIM)

    if scale != 1:
        img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _bar_panel(d, s, text, panel, f_label, f_small, x, y, w, h, title,
               stages, colors, scale, *, empty):
    panel(x, y, w, h, title)
    if not stages:
        text(x + 16, y + 44, empty, f_small, DIM)
        return
    peak = max((n for _, n in stages), default=1) or 1
    by = y + 40
    bar_x = x + 90
    bar_w = w - 90 - 40
    for label, n in stages[:6]:
        text(x + 16, by, label[:9], f_label, TEXT)
        d.rounded_rectangle(
            [s(bar_x), s(by + 1), s(bar_x + bar_w), s(by + 13)], radius=s(3), fill=TRACK)
        fill_w = max(4, int(bar_w * (n / peak)))
        d.rounded_rectangle(
            [s(bar_x), s(by + 1), s(bar_x + fill_w), s(by + 13)],
            radius=s(3), fill=colors.get(label, BLUE))
        text(x + w - 16, by, str(n), f_label, DIM, anchor="ra")
        by += 21


# ---- ascii fallback ------------------------------------------------------

def ascii_dashboard(data: DashboardData, *, width: int = 60) -> str:
    """Plain-text version for terminals without inline-image support."""
    out: list[str] = []
    head = f"JobPilot — Income Dashboard   {data.generated_at}"
    out.append(head)
    out.append("=" * len(head))

    def bars(title, stages):
        out.append("")
        out.append(title)
        if not stages:
            out.append("  (none)")
            return
        peak = max((n for _, n in stages), default=1) or 1
        for label, n in stages:
            filled = int(20 * (n / peak))
            out.append(f"  {label[:10]:<10} {'█' * filled}{'·' * (20 - filled)} {n}")

    bars("Gigs pipeline", data.gig_stages)
    bars("Job applications", data.job_stages)

    out.append("")
    out.append("Source health")
    if data.sources:
        for name, ok, count in data.sources:
            out.append(f"  {'●' if ok else '✗'} {name:<14} {count}")
    else:
        out.append("  (no data)")

    inc = data.income or {}
    out.append("")
    out.append(
        f"Income velocity: {inc.get('active', 0)} active · "
        f"{inc.get('drafted', 0)} drafted · {inc.get('sent', 0)} sent"
        + (f" · ~${inc.get('potential_week'):,}/wk" if inc.get("potential_week") else "")
    )

    out.append("")
    out.append("What's hot")
    if data.hot:
        for label, score in data.hot:
            out.append(f"  [{score:>3}] {label}")
    else:
        out.append("  (nothing fresh)")
    return "\n".join(out)
