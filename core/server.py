"""
JobPilot HTTP server — phone-first job tracker.

No auto-fill, no Mac Chrome control, no CAPTCHAs to dance around.
Just: curated queue + tap to open + mark applied.

Endpoints:
  GET  /                         → dashboard.html (mobile responsive)
  GET  /api/queue                → current queue as JSON
  GET  /api/profile              → profile summary (non-sensitive)
  POST /api/queue/refresh        → rescan all portals (background)
  POST /api/job/<id>/opened      → mark that you tapped Apply (for analytics)
  POST /api/job/<id>/mark-applied → you submitted it, mark done
  POST /api/job/<id>/skip        → not interested
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from jobpilot.core.logger import get_logger
from jobpilot.core.profile_store import get_profile_store
from jobpilot.core.application_tracker import get_application_tracker
from jobpilot.core.queue_builder import (
    QUEUE_PATH, build_queue, get_job, load_queue, save_queue, update_job_status,
)

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
DASHBOARD_PATH = PROJECT_ROOT / "ui" / "dashboard.html"

app = FastAPI(title="JobPilot Remote", version="0.3.0")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    if not DASHBOARD_PATH.exists():
        raise HTTPException(404, "dashboard.html missing")
    return FileResponse(
        DASHBOARD_PATH,
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/install", include_in_schema=False)
async def install_page() -> "Response":
    """Install-the-bookmarklet page — user loads this in Safari, gets
    step-by-step instructions to save the bookmark on their phone."""
    from fastapi.responses import HTMLResponse
    # Generate the bookmarklet URL
    p = get_profile_store().load()
    profile = {
        "first_name": p.first_name,
        "last_name": p.last_name,
        "full_name": f"{p.first_name} {p.last_name}".strip(),
        "email": p.email,
        "phone": p.phone,
        "city": p.city,
        "state": p.state,
        "country": p.country or "United States",
        "zip": p.zip_code,
        "linkedin": p.linkedin_url,
        "portfolio": p.portfolio_url,
        "github": p.github_url,
        "current_title": p.current_title,
        "years_experience": str(p.years_of_experience),
        "custom_answers": p.custom_answers or {},
    }
    import json as _json, urllib.parse as _up
    js = BOOKMARKLET_TEMPLATE.replace("__PROFILE__", _json.dumps(profile))
    js_min = " ".join(js.split())
    href = "javascript:" + _up.quote(js_min)

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0d1117">
<title>Install JobPilot Fill</title>
<style>
  body {{ font:16px -apple-system,sans-serif; background:#0d1117; color:#e6edf3; padding:20px; max-width:560px; margin:0 auto; }}
  h1 {{ color:#58a6ff; font-size:22px; margin-bottom:16px; }}
  h2 {{ font-size:15px; color:#8b949e; text-transform:uppercase; letter-spacing:0.5px; margin:24px 0 8px; }}
  .bookmark {{
    display:inline-block;
    padding:16px 28px;
    background:#238636;
    color:#fff !important;
    border-radius:12px;
    font-size:18px;
    font-weight:600;
    text-decoration:none;
    margin:16px 0;
  }}
  ol li {{ margin:12px 0 12px 20px; line-height:1.5; }}
  code {{ background:#21262d; padding:2px 6px; border-radius:4px; font-family:ui-monospace,monospace; font-size:14px; }}
  .tip {{ background:#161b22; border-left:3px solid #d29922; padding:12px 14px; border-radius:6px; margin:16px 0; font-size:14px; color:#e6edf3; }}
  a.back {{ color:#58a6ff; font-size:14px; }}
</style>
</head><body>
<a href="/" class="back">← Back to JobPilot</a>
<h1>⚡ Install the Fill Button</h1>

<p>The button below fills any job application form with your profile. Works on Greenhouse, Lever, Ashby, Workday, and most ATS forms.</p>

<h2>Step 1 — Save it to bookmarks</h2>
<p>Drag or tap the button below to test:</p>
<a href="{href}" class="bookmark">⚡ Fill Application</a>

<h2>Step 2 — Save as a Safari Favorite (iPhone)</h2>
<ol>
  <li>Tap the <b>Share icon</b> (box with arrow up) at bottom of Safari</li>
  <li>Scroll down → tap <b>Add Bookmark</b></li>
  <li>Change the Location to <b>Favorites</b> → tap Save</li>
  <li>The bookmark is now in your Favorites bar</li>
</ol>

<h2>Step 3 — Use it on any job form</h2>
<ol>
  <li>Tap Apply on a job in JobPilot</li>
  <li>Job page opens in Safari</li>
  <li>Tap the address bar → your Favorites appear → tap <b>⚡ Fill Application</b></li>
  <li>Green confirmation appears: "Filled N fields"</li>
  <li>Upload resume manually (from Files app → iCloud Drive)</li>
  <li>Solve CAPTCHA, tap Submit</li>
</ol>

<div class="tip">
  <b>Heads up:</b> The button embeds your current profile data. If you update your profile in the JobPilot server, revisit this page and re-add the bookmark.
</div>

<h2>What it fills automatically</h2>
<p>Name, email, phone, LinkedIn, portfolio, location, current title, years of experience, work authorization questions ("Yes"), sponsorship questions ("No"), EEOC questions ("Prefer not to say"), veteran status ("Protected veteran"), relocation questions ("Yes"), common "how did you hear" ("LinkedIn"), and required acknowledgment checkboxes.</p>

<p style="margin-top:24px; color:#8b949e; font-size:13px">
  <b>What it can't do:</b> Upload your resume file (iOS blocks programmatic file selection), solve CAPTCHAs (designed to require humans), answer essay questions (that's your voice — do those manually).
</p>
</body></html>"""
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

@app.get("/api/queue")
async def api_queue() -> JSONResponse:
    jobs = load_queue()
    return JSONResponse([asdict(j) for j in jobs])


@app.post("/api/queue/refresh")
async def api_queue_refresh() -> JSONResponse:
    def _run() -> int:
        jobs = build_queue(limit=100)
        save_queue(jobs)
        return len(jobs)
    count = await asyncio.to_thread(_run)
    return JSONResponse({"ok": True, "count": count})


# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------

@app.post("/api/job/{job_id}/opened")
async def api_opened(job_id: str) -> JSONResponse:
    """Track that the user tapped Apply (opened the link). Sets status to
    'viewing' so the UI can show a 'Did you submit?' prompt."""
    if not update_job_status(job_id, "viewing"):
        raise HTTPException(404, f"Job {job_id} not found")
    return JSONResponse({"ok": True})


@app.post("/api/job/{job_id}/mark-applied")
async def api_mark_applied(job_id: str) -> JSONResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    if not update_job_status(job_id, "applied"):
        raise HTTPException(404, f"Job {job_id} not found")
    get_application_tracker().mark_applied(job.url, job.title, job.company)
    return JSONResponse({"ok": True})


@app.post("/api/job/{job_id}/skip")
async def api_skip(job_id: str) -> JSONResponse:
    if not update_job_status(job_id, "skipped"):
        raise HTTPException(404, f"Job {job_id} not found")
    return JSONResponse({"ok": True})


@app.post("/api/job/{job_id}/reset")
async def api_reset(job_id: str) -> JSONResponse:
    """Put a job back in the queue (e.g. you tapped Apply but decided not to
    submit)."""
    if not update_job_status(job_id, "queued"):
        raise HTTPException(404, f"Job {job_id} not found")
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

@app.get("/api/profile")
async def api_profile() -> JSONResponse:
    p = get_profile_store().load()
    return JSONResponse({
        "name": f"{p.first_name} {p.last_name}".strip(),
        "email": p.email,
        "phone": p.phone,
        "city": p.city,
        "state": p.state,
        "current_title": p.current_title,
        "years_experience": p.years_of_experience,
        "linkedin": p.linkedin_url,
        "portfolio": p.portfolio_url,
        "resume": Path(p.resume_path).name if p.resume_path else "",
    })


@app.get("/api/latest-draft")
async def api_latest_draft() -> JSONResponse:
    path = PROJECT_ROOT / "data" / "resumes" / "latest_draft.json"
    if not path.exists():
        return JSONResponse({})
    try:
        return JSONResponse(json.loads(path.read_text()))
    except Exception as exc:
        log.warning("Could not read latest draft manifest: %s", exc)
        return JSONResponse({})


@app.get("/api/bookmarklet")
async def api_bookmarklet() -> JSONResponse:
    """Return a self-contained JS bookmarklet with profile data baked in.
    User saves this as a Safari bookmark, taps it on any job form to auto-fill."""
    p = get_profile_store().load()
    profile = {
        "first_name": p.first_name,
        "last_name": p.last_name,
        "full_name": f"{p.first_name} {p.last_name}".strip(),
        "email": p.email,
        "phone": p.phone,
        "city": p.city,
        "state": p.state,
        "country": p.country or "United States",
        "zip": p.zip_code,
        "linkedin": p.linkedin_url,
        "portfolio": p.portfolio_url,
        "github": p.github_url,
        "current_title": p.current_title,
        "years_experience": str(p.years_of_experience),
        "custom_answers": p.custom_answers or {},
    }
    import json as _json
    js = BOOKMARKLET_TEMPLATE.replace("__PROFILE__", _json.dumps(profile))
    # URL-encode for bookmark use (minify whitespace)
    import urllib.parse as _up
    js_min = " ".join(js.split())
    return JSONResponse({
        "bookmarklet": "javascript:" + _up.quote(js_min),
        "raw": js,
        "preview": js_min[:200] + "…",
    })


BOOKMARKLET_TEMPLATE = r"""
(function(){
  var P = __PROFILE__;
  var filled = 0, skipped = 0;

  function labelOf(el){
    try {
      if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
      if (el.id) {
        var l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
        if (l) return l.innerText.trim();
      }
      var p = el.parentElement;
      for (var i=0; i<5 && p; i++){
        if (p.tagName === 'LABEL') return p.innerText.trim();
        var lbl = p.querySelector('label, legend');
        if (lbl && !lbl.contains(el)) return lbl.innerText.trim();
        p = p.parentElement;
      }
      return el.getAttribute('placeholder') || el.name || el.id || '';
    } catch(e){ return ''; }
  }

  function valueFor(label){
    var l = (label || '').toLowerCase();
    if (/first\s*name|firstname|given name/.test(l)) return P.first_name;
    if (/last\s*name|lastname|family name|surname/.test(l)) return P.last_name;
    if (/preferred name|nickname/.test(l)) return P.first_name;
    if (/full\s*name|your name/.test(l) || l.trim() === 'name') return P.full_name;
    if (/email/.test(l)) return P.email;
    if (/phone|mobile|cell|telephone/.test(l)) return P.phone;
    if (/location.*city|^city$/.test(l)) return P.city;
    if (/^state$|region/.test(l)) return P.state;
    if (/zip|postal/.test(l)) return P.zip;
    if (/country/.test(l)) return P.country;
    if (/linkedin/.test(l)) return P.linkedin;
    if (/github/.test(l)) return P.github;
    if (/portfolio|website|personal site/.test(l)) return P.portfolio;
    if (/current title|current role|job title|headline/.test(l)) return P.current_title;
    if (/years of experience|years experience/.test(l)) return P.years_experience;
    if (/how did you hear|hear about/.test(l)) return 'LinkedIn';
    // Custom answers fuzzy match
    for (var q in P.custom_answers) {
      if (q.toLowerCase().indexOf(l) >= 0 || l.indexOf(q.toLowerCase()) >= 0) return P.custom_answers[q];
    }
    return null;
  }

  function yesNoFor(label){
    var l = (label || '').toLowerCase();
    if (/authorized to work|legally authorized|us citizen|u\.s\. citizen|citizenship|eligible to work/.test(l)) return 'Yes';
    if (/sponsorship|visa sponsor|require sponsorship/.test(l)) return 'No';
    if (/willing to relocate|open to relocation|able to relocate/.test(l)) return 'Yes';
    if (/willing to work|open to work|open to hybrid|on-site|in-office|hub location|days per week|days a week/.test(l)) return 'Yes';
    if (/family member|close personal relationship|outside business|worked for.*past|live within|conflict of interest/.test(l)) return 'No';
    if (/gender identity/.test(l)) return 'Prefer not to say||I don\'t wish to answer||Decline to answer';
    if (/^gender/.test(l) || /\sgender\s/.test(l)) return 'Prefer not to say||I don\'t wish to answer||Decline to answer';
    if (/race|ethnicity/.test(l)) return 'Decline to answer||I don\'t wish to answer||Prefer not to say';
    if (/hispanic|latino/.test(l)) return 'Decline to answer||I don\'t wish to answer||No, not Hispanic';
    if (/veteran/.test(l)) return 'I identify as a protected veteran||Protected veteran||Veteran';
    if (/disability/.test(l)) return 'I don\'t wish to answer||Prefer not to say||Decline to answer';
    return null;
  }

  // Fill text-like inputs
  var textSel = 'input[type="text"]:not([disabled]):not([readonly]), input[type="email"]:not([disabled]):not([readonly]), input[type="tel"]:not([disabled]):not([readonly]), input[type="url"]:not([disabled]):not([readonly]), input[type="number"]:not([disabled]):not([readonly]), input:not([type]):not([disabled]):not([readonly]), textarea:not([disabled]):not([readonly])';
  document.querySelectorAll(textSel).forEach(function(el){
    try {
      if (el.value && el.value.trim()) return;
      if (el.offsetParent === null) return;
      var label = labelOf(el);
      var v = valueFor(label);
      if (v) {
        var proto = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value') || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
        if (proto && proto.set) proto.set.call(el, v); else el.value = v;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        filled++;
      } else { skipped++; }
    } catch(e){}
  });

  // Fill native <select>s (including hidden ones wrapped by Select2/React)
  document.querySelectorAll('select:not([disabled])').forEach(function(sel){
    try {
      if (sel.value && sel.value.trim() && sel.value.toLowerCase() !== 'select') return;
      var label = labelOf(sel);
      var target = valueFor(label) || yesNoFor(label);
      if (!target) { skipped++; return; }
      var targets = target.split('||').map(function(s){return s.trim().toLowerCase();});
      var opts = Array.from(sel.options);
      var match = null;
      for (var i=0; i<targets.length && !match; i++){
        var n = targets[i];
        match = opts.find(function(o){return o.text.trim().toLowerCase() === n;});
        if (!match) match = opts.find(function(o){return o.text.toLowerCase().indexOf(n) >= 0;});
      }
      if (match) {
        sel.value = match.value;
        sel.dispatchEvent(new Event('input', { bubbles: true }));
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        if (window.jQuery && jQuery(sel).data('select2')) jQuery(sel).val(match.value).trigger('change');
        filled++;
      } else { skipped++; }
    } catch(e){}
  });

  // Radio groups (yes/no questions)
  document.querySelectorAll('fieldset, div[role="radiogroup"], ul.application-question').forEach(function(group){
    try {
      var q = (group.querySelector('legend, label, .application-label') || group).innerText.slice(0,200);
      var target = yesNoFor(q);
      if (!target) return;
      var radios = group.querySelectorAll('input[type="radio"]');
      for (var i=0; i<radios.length; i++){
        var r = radios[i];
        var rl = labelOf(r) + ' ' + (r.value || '');
        if (rl.toLowerCase().indexOf(target.toLowerCase()) >= 0) {
          r.checked = true;
          r.dispatchEvent(new Event('change', { bubbles: true }));
          filled++;
          break;
        }
      }
    } catch(e){}
  });

  // Auto-check required acknowledgment checkboxes
  document.querySelectorAll('input[type="checkbox"]:not([disabled])').forEach(function(cb){
    try {
      if (cb.checked) return;
      if (cb.offsetParent === null) return;
      var label = labelOf(cb).toLowerCase();
      var required = cb.required || cb.getAttribute('aria-required') === 'true';
      var isAck = /acknowledge|i agree|agree to|accept|confirm|privacy policy|terms|consent to/.test(label);
      var isMarketing = /marketing|newsletter|promotional|updates about|notifications/.test(label);
      if ((required || isAck) && !isMarketing) {
        cb.checked = true;
        cb.dispatchEvent(new Event('change', { bubbles: true }));
        filled++;
      }
    } catch(e){}
  });

  var msg = '✓ Filled ' + filled + ' fields. Upload resume + solve CAPTCHA manually.';
  var t = document.createElement('div');
  t.style.cssText = 'position:fixed;top:20px;right:20px;background:#238636;color:#fff;padding:12px 18px;border-radius:10px;z-index:999999;font:14px -apple-system;box-shadow:0 4px 12px rgba(0,0,0,0.3);';
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(function(){ t.remove(); }, 4000);
})();
"""


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def get_tailscale_ip() -> str | None:
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=3,
        )
        ip = result.stdout.strip().split("\n")[0]
        if ip and ip.startswith("100."):
            return ip
    except Exception:
        pass
    return None


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def run_server(host: str = "127.0.0.1", port: int = 8766) -> None:
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")
