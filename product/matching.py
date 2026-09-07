"""Explainable text signals, not hiring probabilities or skill certification."""

import re

from .models import Fit, Listing, Profile


def contains(text: str, term: str) -> bool:
    return bool(
        term.strip()
        and re.search(r"(?<!\w)" + re.escape(term.strip()) + r"(?!\w)", text, re.I)
    )


def match(profile: Profile, job: Listing) -> Fit:
    posting = job.title + "\n" + job.description
    matched = [skill for skill in profile.skills if contains(posting, skill)]
    lines = [
        line.strip()
        for line in profile.resume_text.splitlines()
        if len(line.strip()) > 15
    ]
    ranked = sorted(
        lines,
        key=lambda line: sum(
            contains(line, word) for word in matched + profile.keywords
        ),
        reverse=True,
    )
    excerpts = [
        line
        for line in ranked
        if any(contains(line, word) for word in matched + profile.keywords)
    ][:6]
    questions = [
        line.strip()[:600]
        for line in job.description.splitlines()
        if re.search(
            r"\b(must|required|requirement|minimum|\d+\+? years)\b", line, re.I
        )
    ][:8]
    if not job.description:
        questions.append(
            "Open the posting to review its full requirements; the board did not include them."
        )
    if not matched:
        questions.append(
            "No supplied skills matched the posting text. Review the role before investing more time."
        )
    if job.provider == "manual":
        questions.append(
            "This posting was added by hand. Check that it is still accepting applications."
        )
    return Fit(
        matched=matched,
        questions=questions,
        excerpts=excerpts,
        signals=len(matched)
        + sum(contains(job.title, word) for word in profile.keywords) * 2,
    )


def relevant(profile: Profile, job: Listing) -> bool:
    text = job.title + " " + job.description
    if profile.keywords and not any(contains(text, term) for term in profile.keywords):
        return False
    if profile.remote_only and not contains(job.location, "remote"):
        return False
    return (
        not profile.location
        or contains(job.location, profile.location)
        or contains(job.location, "remote")
    )
