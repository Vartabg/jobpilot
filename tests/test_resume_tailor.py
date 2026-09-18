"""Tests for core/resume_tailor.py — ATS-targeted resume drafting."""

import json
import shutil
from pathlib import Path

import pytest

from jobpilot.core import resume_tailor as resume_tailor_module
from jobpilot.core.profile_store import ProfileStore
from jobpilot.core.resume_tailor import ResumeTailor


def _write_minimal_text_pdf(pdf_path: Path, text: str) -> None:
    """Create a small valid PDF containing extractable text for tests."""
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode(
        "latin-1", errors="replace"
    )
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        b"5 0 obj\n<< /Length %d >>\nstream\n%s\nendstream\nendobj\n"
        % (len(stream), stream),
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
# Alex Sample

Senior Frontend Engineer with deep React, TypeScript, and visualization experience.
Built UI systems, internal tools, and AI-assisted workflows.
""".strip()
    )

    store = ProfileStore(data_dir=tmp_path)
    profile = store.load()
    profile.first_name = "Alex"
    profile.last_name = "Sample"
    profile.email = "alex@example.com"
    profile.current_title = "Senior Frontend Engineer"
    profile.current_company = "Example Labs"
    profile.years_of_experience = 8
    profile.linkedin_url = "https://linkedin.com/in/alexsample"
    profile.github_url = "https://github.com/alexsample"
    profile.resume_path = str(resume_source)
    profile.custom_answers = {
        "skills": "React TypeScript Python GitHub Actions visualization AI automation"
    }
    store.save(profile)

    tailor = ResumeTailor(
        profile_store=store, output_dir=tmp_path / "output", use_bro=False
    )
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


def test_tailor_selects_facilities_resume_for_field_service_role(
    tmp_path: Path, monkeypatch
):
    resume_sources = tmp_path / "resume_sources"
    resume_sources.mkdir()
    facilities_resume = resume_sources / "field_service_v1.md"
    facilities_resume.write_text(
        """
# Alex Sample

Field Service Engineer who commissioned smart electrochromic systems across customer sites.
Resolved electro-mechanical failures, controller issues, and BMS integrations under schedule pressure.
""".strip()
    )
    solutions_resume = resume_sources / "software_v1.md"
    solutions_resume.write_text(
        """
# Alex Sample

Solutions Engineer who built React dashboards and Python automation for software teams.
""".strip()
    )
    monkeypatch.setattr(resume_tailor_module, "RESUME_SOURCE_DIR", resume_sources)

    store = ProfileStore(data_dir=tmp_path)
    profile = store.load()
    profile.first_name = "Alex"
    profile.last_name = "Sample"
    profile.current_title = "Independent Contractor"
    profile.resume_path = str(solutions_resume)
    store.save(profile)

    lanes = {"facilities": "field_service_v1", "default": "software_v1"}
    tailor = ResumeTailor(
        profile_store=store,
        output_dir=tmp_path / "output",
        use_bro=False,
        resume_lanes=lanes,
    )
    result = tailor.generate_from_text(
        """
Field Service Technician II
Acme Controls
Requirements
- Maintain field service equipment across customer sites
- Troubleshoot electrical and mechanical systems
- Support building automation and BMS integrations
""",
        title="Field Service Technician II",
        company="Acme Controls",
    )

    content = result.output_path.read_text()
    assert "production software" not in content
    assert "commissioned smart electrochromic systems" in content
    assert "React dashboards" not in content
    assert "Field Service Engineer**" not in content


def test_tailor_keeps_solutions_resume_for_software_role(tmp_path: Path, monkeypatch):
    resume_sources = tmp_path / "resume_sources"
    resume_sources.mkdir()
    facilities_resume = resume_sources / "field_service_v1.md"
    facilities_resume.write_text(
        """
# Alex Sample

Field Service Engineer who commissioned smart electrochromic systems across customer sites.
""".strip()
    )
    solutions_resume = resume_sources / "software_v1.md"
    solutions_resume.write_text(
        """
# Alex Sample

Solutions Engineer who built React dashboards and Python automation for software teams.
Designed human-in-the-loop workflows for ATS sourcing and application review.
""".strip()
    )
    monkeypatch.setattr(resume_tailor_module, "RESUME_SOURCE_DIR", resume_sources)

    store = ProfileStore(data_dir=tmp_path)
    profile = store.load()
    profile.first_name = "Alex"
    profile.last_name = "Sample"
    profile.current_title = "Independent Contractor"
    profile.resume_path = str(solutions_resume)
    store.save(profile)

    lanes = {"facilities": "field_service_v1", "default": "software_v1"}
    tailor = ResumeTailor(
        profile_store=store,
        output_dir=tmp_path / "output",
        use_bro=False,
        resume_lanes=lanes,
    )
    result = tailor.generate_from_text(
        """
Solutions Engineer
Acme AI
Requirements
- Python automation
- React dashboards
- Customer-facing implementation work
""",
        title="Solutions Engineer",
        company="Acme AI",
    )

    content = result.output_path.read_text()
    assert "React dashboards" in content
    assert "smart electrochromic systems" not in content


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
    profile.first_name = "Alex"
    profile.last_name = "Sample"
    profile.current_title = "Frontend Engineer"
    profile.years_of_experience = 5
    profile.resume_path = str(tmp_path / "missing.pdf")
    store.save(profile)

    tailor = ResumeTailor(
        profile_store=store, output_dir=tmp_path / "output", use_bro=False
    )
    result = tailor.generate_from_text(
        "Frontend Engineer\nExample Co\nRequirements\n- React\n- Remote",
        title="Frontend Engineer",
        company="Example Co",
    )

    assert result.output_path.exists()
    assert result.output_path.suffix == ".md"
    assert result.html_path is not None and result.html_path.exists()
    assert result.output_path.read_text()


def test_module_contains_no_author_identity():
    """Resume selection must stay generic — no personal filenames/identity in the module.

    Lane resumes are configured per-user via data/settings.json (gitignored),
    never hardcoded here. This guards against a personal literal creeping back
    into the shipped source.
    """
    source = Path(resume_tailor_module.__file__).read_text()
    for fragment in ("garo", "vartabed"):
        assert fragment not in source.lower(), (
            f"author literal {fragment!r} still in module"
        )
