"""Tests for core/resume_tailor.py — ATS-targeted resume drafting."""

import json
import shutil
from pathlib import Path

import pytest

from jobpilot.core.profile_store import ProfileStore
from jobpilot.core.resume_tailor import ResumeTailor


def _write_minimal_text_pdf(pdf_path: Path, text: str) -> None:
    """Create a small valid PDF containing extractable text for tests."""
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"5 0 obj\n<< /Length %d >>\nstream\n%s\nendstream\nendobj\n" % (len(stream), stream),
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )

    pdf_path.write_bytes(pdf)


def test_tailor_generates_markdown_resume(tmp_path: Path):
    resume_source = tmp_path / "resume.md"
    resume_source.write_text(
        """
# Garo Vartabedian

Senior Frontend Engineer with deep React, TypeScript, and visualization experience.
Built UI systems, internal tools, and AI-assisted workflows.
""".strip()
    )

    store = ProfileStore(data_dir=tmp_path)
    profile = store.load()
    profile.first_name = "Garo"
    profile.last_name = "Vartabedian"
    profile.email = "garo@example.com"
    profile.current_title = "Senior Frontend Engineer"
    profile.current_company = "ATXBro"
    profile.years_of_experience = 8
    profile.linkedin_url = "https://linkedin.com/in/garovartabedian"
    profile.github_url = "https://github.com/Vartabg"
    profile.resume_path = str(resume_source)
    profile.custom_answers = {
        "skills": "React TypeScript Python GitHub Actions visualization AI automation"
    }
    store.save(profile)

    tailor = ResumeTailor(profile_store=store, output_dir=tmp_path / "output", use_bro=False)
    result = tailor.generate_from_text(
        """
Senior Frontend Engineer
Acme AI
Requirements
- React and TypeScript
- GitHub Actions and CI/CD
- Experience building AI-powered user interfaces
- Remote role
""",
        title="Senior Frontend Engineer",
        company="Acme AI",
    )

    assert result.output_path.exists()
    assert result.html_path is not None and result.html_path.exists()
    content = result.output_path.read_text()
    html = result.html_path.read_text()
    assert "Acme AI" in content
    assert "Senior Frontend Engineer" in content
    assert "React" in content
    assert "TypeScript" in content
    assert "<html" in html.lower()
    assert "Tailored Experience Highlights" in html
    assert result.matched_skills

    manifest = json.loads((tmp_path / "output" / "latest_draft.json").read_text())
    assert manifest["markdown_path"] == str(result.output_path)
    assert manifest["html_path"] == str(result.html_path)
    assert manifest["title"] == "Senior Frontend Engineer"
    assert manifest["company"] == "Acme AI"


def test_tailor_reads_text_from_pdf_resume(tmp_path: Path):
    if shutil.which("pdftotext") is None:
        pytest.skip("pdftotext is not available in this environment")

    pdf_resume = tmp_path / "resume.pdf"
    _write_minimal_text_pdf(
        pdf_resume,
        "Built React dashboards and Python automation systems for product teams.",
    )

    extracted = ResumeTailor._load_resume_text(str(pdf_resume))

    assert "React dashboards" in extracted
    assert "Python automation systems" in extracted


def test_tailor_falls_back_without_resume_file(tmp_path: Path):
    store = ProfileStore(data_dir=tmp_path)
    profile = store.load()
    profile.first_name = "Garo"
    profile.last_name = "Vartabedian"
    profile.current_title = "Frontend Engineer"
    profile.years_of_experience = 5
    profile.resume_path = str(tmp_path / "missing.pdf")
    store.save(profile)

    tailor = ResumeTailor(profile_store=store, output_dir=tmp_path / "output", use_bro=False)
    result = tailor.generate_from_text(
        "Frontend Engineer\nExample Co\nRequirements\n- React\n- Remote",
        title="Frontend Engineer",
        company="Example Co",
    )

    assert result.output_path.exists()
    assert result.output_path.suffix == ".md"
    assert result.html_path is not None and result.html_path.exists()
    assert result.output_path.read_text()
