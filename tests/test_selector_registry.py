"""
Tests for selector_registry.py — SelectorChain and known selector definitions.
"""

import pytest
from jobpilot.core.selector_registry import (
    SelectorChain,
    NEXT_BUTTON,
    FILE_INPUTS,
    EASY_APPLY_MODAL,
    SUBMIT_BUTTON,
)


class TestSelectorChain:

    def test_has_selectors_attribute(self):
        chain = SelectorChain(
            name="test",
            selectors=["[data-test='btn']", ".btn-primary", "button.next"],
        )
        assert len(chain.selectors) == 3

    def test_first_selector_has_priority(self):
        chain = SelectorChain(
            name="test",
            selectors=["[aria-label='Submit']", ".fallback"],
        )
        assert chain.selectors[0] == "[aria-label='Submit']"

    def test_name_attribute(self):
        chain = SelectorChain(name="my_chain", selectors=["a"])
        assert chain.name == "my_chain"


class TestKnownSelectors:

    def test_next_button_exists(self):
        assert isinstance(NEXT_BUTTON, SelectorChain)
        assert len(NEXT_BUTTON.selectors) > 0

    def test_file_inputs_exists(self):
        assert isinstance(FILE_INPUTS, SelectorChain)
        assert len(FILE_INPUTS.selectors) > 0

    def test_easy_apply_modal_exists(self):
        assert isinstance(EASY_APPLY_MODAL, SelectorChain)
        assert len(EASY_APPLY_MODAL.selectors) > 0

    def test_submit_button_exists(self):
        assert isinstance(SUBMIT_BUTTON, SelectorChain)
        assert len(SUBMIT_BUTTON.selectors) > 0

    def test_next_button_has_multiple_fallbacks(self):
        """NEXT_BUTTON should have multiple selectors for fallback."""
        assert len(NEXT_BUTTON.selectors) >= 3

    def test_selectors_are_strings(self):
        for sel in NEXT_BUTTON.selectors:
            assert isinstance(sel, str)
            assert len(sel) > 0
