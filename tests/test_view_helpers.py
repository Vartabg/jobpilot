"""Tests for shared terminal view helpers."""

from jobpilot.ui.view_helpers import is_senior_title, materials_ready, score_bar


def test_score_bar():
    assert "75" in str(score_bar(75))


def test_is_senior_title():
    assert is_senior_title("Senior Software Engineer")
    assert not is_senior_title("Senior Engineering Manager")
    assert not is_senior_title("Implementation Engineer")


def test_materials_ready_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("jobpilot.ui.view_helpers.ANSWERS_DIR", tmp_path)
    assert materials_ready("No Such Co") is None


def test_materials_ready_paste_sheet(monkeypatch, tmp_path):
    monkeypatch.setattr("jobpilot.ui.view_helpers.ANSWERS_DIR", tmp_path)
    company_dir = tmp_path / "osano"
    company_dir.mkdir()
    paste = company_dir / "PASTE_SHEET.txt"
    paste.write_text("answers")
    assert materials_ready("Osano") == paste