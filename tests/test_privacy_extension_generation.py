"""Generated browser privacy rules stay in sync with the product classifier."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from product.privacy import analyze_fields
from scripts.build_extension_privacy import generated_files

ROOT = Path(__file__).resolve().parents[1]


def test_committed_extension_privacy_files_match_product_sources():
    for path, content in generated_files(ROOT).items():
        assert path.read_text() == content, f"Run python3 scripts/build_extension_privacy.py: {path.name}"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for browser-classifier parity")
def test_native_classifier_matches_product_findings_and_validation():
    labels = [
        "Legal Address", "Full address", "Street Address", "Physical address", "Address Line 1", "Address", "Street",
        "Email Address", "Website address", "IP address", "Latitude", "GPS coordinates", "Precise location",
        "Location (City)", "State", "Postal Code", "Where do you live?", "United States work authorization",
        "State your salary expectations", "Country of citizenship", "Country of birth", "Place of birth",
        "Gender identity", "Sex", "Sexual orientation", "Sexual preference", "Race and ethnicity", "Hispanic or Latino",
        "Disability", "Medical history", "Are you pregnant?", "Religion", "Political party affiliation",
        "Trade union membership", "Veteran status", "Social Security number", "Driver's License", "Passport",
        "Date of birth", "Age", "Current salary", "Previous base compensation", "Desired compensation",
        "Bank account", "Credit card", "Marital status", "Do you have children?", "What are your pronouns?",
        "Connect your LinkedIn account", "Sign in with LinkedIn", "LinkedIn URL", "Record a video introduction",
        "Upload an audio response", "Photograph", "Authorize a background check", "Create an account", "Password",
        "Describe your experience supporting applicants with disabilities.",
        "What is your experience treating patients with medical conditions?",
        "Describe your background helping clients with mental health conditions.",
        "Describe your experience supporting applicants with disabilities and your medical history.",
        "Do you have medical history working with applicants with disabilities?",
        "Describe your medical history working with children.",
        "Do you have experience working with children?",
        "Do you have experience working with children, and do you have children?",
        "Describe your experience working with children and your childcare arrangements.",
        "Résumé", "Gender (optional)",
    ]
    fields = [
        {"label": label, "required": required, "options": options, "description": "Employer help text",
         "value": "SECRET-ANSWER", "selected": "SECRET-SELECTED"}
        for label in labels for required, options in [
            (True, []), (False, ["Prefer not to answer"]), (None, ["Decline to disclose"]),
            (True, ["Woman", "Man", "I don’t wish to answer"]),
        ]
    ]
    options = {"source_url": "https://example.test/apply?token=SECRET#SECRET", "coverage": "visible_form",
               "notices": ["Visible form", "Visible form"]}
    expected = analyze_fields(fields, **options)
    script = """
      import {readFileSync} from 'node:fs';
      import {analyzeFields} from './extension/privacy-analyzer.js';
      const {fields,options} = JSON.parse(readFileSync(0, 'utf8'));
      process.stdout.write(JSON.stringify(analyzeFields(fields, options)));
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, text=True,
                            input=json.dumps({"fields": fields, "options": options}), capture_output=True, check=True)
    assert json.loads(result.stdout) == expected
    assert "SECRET" not in result.stdout


def test_generated_collector_is_metadata_only_and_current_document_only():
    source = (ROOT / "extension/privacy-content.js").read_text()
    assert "__jobpilotPrivacyCapture" in source
    assert "return payload;" in source
    for forbidden in ("contentDocument", "createElement", "showModal", "clipboard", "fetch(",
                      "XMLHttpRequest", ".value", ".checked", ".files", "chrome.storage"):
        assert forbidden not in source
