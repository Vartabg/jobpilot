from jobpilot.core.form_filler import _detect_disallowed_form_location


def test_form_location_guard_blocks_uk_work_authorization():
    text = "Are you authorized to work in the UK?"

    assert _detect_disallowed_form_location(text) == "authorized to work in the uk"


def test_form_location_guard_blocks_munich_requirement():
    text = "Candidates must be based in Germany and within commuting distance of Munich."

    assert _detect_disallowed_form_location(text) == "based in germany"
