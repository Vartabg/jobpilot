"""
Tests for jd_parser.py — skill extraction regexes.

Regression coverage for the \\b-boundary bug: \\b after a non-word char
(like '+' or '#') requires a following word char, so "C++" and "C#" were
never extracted. We exercise _extract_skills directly (a staticmethod, no
Page needed).
"""

from jobpilot.core.jd_parser import JDParser


class TestExtractSkillsSymbolSuffix:
    """Skills ending in non-word chars must still be detected."""

    def test_cpp_followed_by_space(self):
        skills = JDParser._extract_skills("Experience with C++ and Rust required.")
        assert "C++" in skills

    def test_csharp_followed_by_space(self):
        skills = JDParser._extract_skills("Experience with C++ and C# required.")
        assert "C++" in skills
        assert "C#" in skills

    def test_cpp_at_end_of_text(self):
        skills = JDParser._extract_skills("Must know C++")
        assert "C++" in skills

    def test_csharp_before_punctuation(self):
        skills = JDParser._extract_skills("Languages: Python, C#, Go.")
        assert "C#" in skills
        assert "Python" in skills
        assert "Go" in skills

    def test_cpp_before_period(self):
        skills = JDParser._extract_skills("We write everything in C++.")
        assert "C++" in skills

    def test_cpp_in_parentheses(self):
        skills = JDParser._extract_skills("Systems languages (C++) preferred.")
        assert "C++" in skills

    def test_c_alone_does_not_match_cpp_or_csharp(self):
        skills = JDParser._extract_skills("Experience with C and assembly.")
        assert "C++" not in skills
        assert "C#" not in skills


class TestExtractSkillsWordBoundaries:
    """Normal skills keep strict word-boundary behavior."""

    def test_java_not_matched_inside_javascript(self):
        skills = JDParser._extract_skills("Strong JavaScript skills required.")
        assert "JavaScript" in skills
        assert "Java" not in skills

    def test_java_and_javascript_both_present(self):
        skills = JDParser._extract_skills("We use Java on the backend and JavaScript on the frontend.")
        assert "Java" in skills
        assert "JavaScript" in skills

    def test_no_substring_match_inside_longer_word(self):
        skills = JDParser._extract_skills("We are a Gitlab shop using Reactor patterns.")
        assert "Git" not in skills
        assert "React" not in skills

    def test_dotted_skill_matches(self):
        skills = JDParser._extract_skills("Experience with Node.js and Next.js apps.")
        assert "Node.js" in skills
        assert "Next.js" in skills

    def test_multiword_skill_matches(self):
        skills = JDParser._extract_skills("Familiarity with GitHub Actions and Machine Learning.")
        assert "GitHub Actions" in skills
        assert "Machine Learning" in skills

    def test_slash_skill_matches(self):
        skills = JDParser._extract_skills("You will own our CI/CD pipelines.")
        assert "CI/CD" in skills


class TestExtractSkillsCaseInsensitivity:
    """Matching is case-insensitive but output uses canonical casing."""

    def test_lowercase_input_returns_canonical_casing(self):
        skills = JDParser._extract_skills("experience with python, docker and kubernetes")
        assert "Python" in skills
        assert "Docker" in skills
        assert "Kubernetes" in skills

    def test_uppercase_symbol_skills(self):
        skills = JDParser._extract_skills("PROFICIENT IN c++ AND c# DEVELOPMENT TOOLS")
        assert "C++" in skills
        assert "C#" in skills

    def test_no_duplicates_for_repeated_mentions(self):
        skills = JDParser._extract_skills("Python, python, PYTHON everywhere.")
        assert skills.count("Python") == 1


class TestExtractSkillsEmpty:

    def test_no_skills_in_unrelated_text(self):
        skills = JDParser._extract_skills("We are a friendly bakery looking for a cashier.")
        assert skills == []

    def test_empty_text(self):
        assert JDParser._extract_skills("") == []
