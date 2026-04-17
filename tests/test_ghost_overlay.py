"""Tests for UI status abstraction in `ui/ghost_overlay.py`."""

from jobpilot.ui.ghost_overlay import (
    build_control_surface_sections,
    build_density_mode_label,
    build_status_presentation,
)


def test_ready_status_maps_to_operator_guidance():
    state = build_status_presentation("Ready")
    assert state.tone == "ready"
    assert state.label == "Ready"
    assert "Easy Apply" in state.guidance


def test_step_status_maps_to_review_state():
    state = build_status_presentation("Step 2/4")
    assert state.tone == "active"
    assert state.label == "Step 2/4"
    assert "Review" in state.guidance


def test_warning_status_maps_to_clear_skip_guidance():
    state = build_status_presentation("⚠ Already Applied")
    assert state.tone == "warn"
    assert "Already applied" in state.label
    assert "skip" in state.guidance.lower()


def test_control_surface_sections_prioritize_review_then_assistant():
    sections = build_control_surface_sections()
    assert [section.key for section in sections] == ["review", "assistant"]
    assert "Approve" in sections[0].hint
    assert "status" in sections[1].hint.lower()


def test_density_mode_labels_are_operator_friendly():
    assert build_density_mode_label(False) == "Expanded"
    assert build_density_mode_label(True) == "Compact"
