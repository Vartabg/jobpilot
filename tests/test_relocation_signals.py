"""Detection tests for employer-offered relocation signals."""

from jobpilot.core.relocation_signals import detect_relocation_offer, offers_relocation


def test_offer_package_detected() -> None:
    text = "Join our Austin team. We offer a generous relocation package for out-of-state hires."
    assert detect_relocation_offer(text) == "relocation package"
    assert offers_relocation(text)


def test_offer_assistance_detected() -> None:
    assert offers_relocation("Relocation assistance available for the right candidate.")


def test_negation_vetoes_offer() -> None:
    text = "Great role with a relocation package. Note: no relocation assistance for contractors."
    assert detect_relocation_offer(text) is None


def test_candidate_question_is_not_an_offer() -> None:
    assert detect_relocation_offer("Do you require relocation assistance?") is None
    assert detect_relocation_offer("Relocation required: yes / no") is None


def test_willingness_language_is_not_an_offer() -> None:
    assert detect_relocation_offer("Are you willing to relocate to Austin?") is None
    assert detect_relocation_offer("Must be able to relocate on short notice.") is None


def test_offer_elsewhere_in_sentence_with_need_still_counts() -> None:
    text = "A relocation package is available for candidates who need to move to Texas."
    assert detect_relocation_offer(text) == "relocation package"


def test_empty_and_plain_text() -> None:
    assert detect_relocation_offer("") is None
    assert (
        detect_relocation_offer("Remote-first team, async culture, great pay.") is None
    )
