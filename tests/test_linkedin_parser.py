"""
Tests for linkedin_parser.py — semantic type inference from field labels.
"""

import pytest
from jobpilot.core.linkedin_parser import (
    LinkedInParser,
    FieldType,
    SemanticType,
)


class TestSemanticInference:
    """Test the _infer_semantic_type pattern-matching engine."""

    def setup_method(self):
        self.parser = LinkedInParser.__new__(LinkedInParser)

    def _infer(self, label: str, field_type=FieldType.TEXT, placeholder: str = ""):
        return self.parser._infer_semantic_type(label, placeholder, field_type)

    def test_first_name(self):
        stype, conf = self._infer("First name")
        assert stype == SemanticType.FIRST_NAME

    def test_last_name(self):
        stype, conf = self._infer("Last name")
        assert stype == SemanticType.LAST_NAME

    def test_email(self):
        stype, conf = self._infer("Email address")
        assert stype == SemanticType.EMAIL

    def test_phone(self):
        stype, conf = self._infer("Phone number")
        assert stype == SemanticType.PHONE

    def test_city(self):
        stype, conf = self._infer("City")
        assert stype == SemanticType.CITY

    def test_linkedin_url(self):
        stype, conf = self._infer("LinkedIn Profile URL")
        assert stype == SemanticType.LINKEDIN_URL

    def test_resume(self):
        stype, conf = self._infer("Upload resume", FieldType.FILE)
        assert stype == SemanticType.RESUME

    def test_years_of_experience(self):
        stype, conf = self._infer("Years of experience")
        assert stype == SemanticType.YEARS_EXPERIENCE

    def test_unknown_label(self):
        stype, conf = self._infer("What is your favorite color?")
        assert stype in (SemanticType.UNKNOWN, SemanticType.CUSTOM_QUESTION)

    def test_case_insensitive(self):
        stype, conf = self._infer("EMAIL ADDRESS")
        assert stype == SemanticType.EMAIL

    def test_confidence_above_zero_for_known(self):
        _, conf = self._infer("Email address")
        assert conf > 0.5

    def test_returns_tuple(self):
        result = self._infer("First name")
        assert isinstance(result, tuple)
        assert len(result) == 2


class TestFieldTypeEnum:

    def test_all_values_are_strings(self):
        for ft in FieldType:
            assert isinstance(ft.value, str)

    def test_common_types_exist(self):
        names = {ft.name for ft in FieldType}
        assert "TEXT" in names
        assert "SELECT" in names
        assert "CHECKBOX" in names
        assert "RADIO" in names
        assert "FILE" in names


class TestSemanticTypeEnum:

    def test_known_types_exist(self):
        names = {st.name for st in SemanticType}
        assert "FIRST_NAME" in names
        assert "EMAIL" in names
        assert "PHONE" in names
