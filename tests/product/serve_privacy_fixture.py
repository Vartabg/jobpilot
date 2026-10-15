"""Synthetic privacy interaction/a11y fixture; never part of the product app."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn
from fastapi.responses import FileResponse, HTMLResponse

from product.api import create_app
from product.config import STATIC
from product.privacy import PrivacyScanError, analyze_fields
from product import privacy_routes

SYNTHETIC = [
    {"label": "Legal Address", "required": True, "description": "Used for hiring.", "options": []},
    {"label": "Gender identity", "required": True, "options": ["Woman", "Man", "Prefer not to answer"]},
    {"label": "Sexual orientation (optional)", "required": False, "options": ["Prefer not to say"]},
    {"label": "Disability", "required": None, "options": []},
]


def fixture_scan(url):
    if "blocked" in url:
        raise PrivacyScanError("This form needs sign-in. Use the page scanner after signing in.")
    report = analyze_fields(SYNTHETIC, source_url=url, coverage="public_ats_form")
    report["title"] = "Synthetic application"
    return report


def app_for_checks(root: Path, axe: Path):
    privacy_routes.scan_greenhouse_url = fixture_scan
    app = create_app(root)

    @app.get("/privacy-audit")
    def privacy_audit():
        source = (STATIC / "privacy.html").read_text().replace(
            "</body>",
            '<button id="run-audit">Run accessibility audit</button>'
            '<pre id="audit-results" role="status"></pre>'
            '<script src="/fixture-axe.js"></script><script src="/fixture-audit.js"></script></body>',
        )
        response = HTMLResponse(source)
        response.set_cookie("jobpilot_session", app.state.token, httponly=True, samesite="strict")
        return response

    @app.get("/fixture-axe.js")
    def axe_script():
        return FileResponse(axe, media_type="application/javascript")

    @app.get("/fixture-audit.js")
    def audit_script():
        from fastapi.responses import Response

        return Response(
            'document.getElementById("run-audit").addEventListener("click",async()=>{'
            'document.getElementById("audit-results").textContent="Running accessibility audit…";'
            'try { const result=await axe.run(document.querySelector("main"),{runOnly:{type:"tag",values:["wcag2a","wcag2aa","wcag21aa","wcag22aa","best-practice"]}});'
            'document.getElementById("audit-results").textContent=JSON.stringify({violations:result.violations.map(v=>({id:v.id,targets:v.nodes.map(n=>n.target)})),passes:result.passes.length});'
            '}catch(e){document.getElementById("audit-results").textContent=JSON.stringify({error:String(e)});}});',
            media_type="application/javascript",
        )

    @app.get("/capture-fixture")
    def capture_fixture():
        return HTMLResponse('''<!doctype html><html lang="en"><head><title>Form capture fixture</title>
<link rel="stylesheet" href="/static/app.css"><script src="/static/privacy-capture.js"></script>
<script src="/capture-fixture.js" defer></script></head><body><a class="skip" href="#main">Skip to content</a>
<main id="main" tabindex="-1"><h1>Synthetic form</h1><form id="application">
<label>Legal Address *<input name="address" required value="PRIVATE STREET MUST NOT LEAK"></label>
<label>Email<input type="email" name="email" value="private@example.test"></label>
<label for="gender">Gender identity *</label><select id="gender" required><option>Woman</option><option>Prefer not to answer</option></select>
<label>Cover letter<textarea>PRIVATE ANSWER MUST NOT LEAK</textarea></label>
<span id="custom-label">Race or ethnicity</span><div id="custom-value" role="combobox" tabindex="0" aria-labelledby="custom-label custom-value" aria-describedby="custom-value"><span>PRIVATE CUSTOM CHOICE</span></div>
<fieldset><legend>Sexual orientation (optional)</legend><label><input type="radio" name="orientation">Prefer not to answer</label><label><input type="radio" name="orientation">Other</label></fieldset>
<fieldset><legend>Pronouns</legend><label><input type="radio" name="pronouns" required>She/her</label><label><input type="radio" name="pronouns">He/him</label><label><input type="radio" name="pronouns">Prefer not to answer</label></fieldset>
<label hidden>Secret hidden field<input name="hidden" value="HIDDEN ANSWER"></label>
<button>Submit application</button></form><p id="submissions" role="status">0 submissions</p>
<button id="capture">Capture visible field metadata</button></main></body></html>''')

    @app.get("/capture-fixture.js")
    def capture_script():
        from fastapi.responses import Response

        return Response('document.getElementById("capture").addEventListener("click",captureApplicationFields);'
            'document.getElementById("application").addEventListener("submit",e=>{e.preventDefault();document.getElementById("submissions").textContent="1 submission";});',
            media_type="application/javascript")

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3189)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--axe", type=Path, required=True)
    args = parser.parse_args()
    if not args.axe.is_file():
        raise SystemExit("axe-core is required for accessibility verification.")
    uvicorn.run(app_for_checks(args.data_dir, args.axe), host="127.0.0.1", port=args.port, access_log=False)
