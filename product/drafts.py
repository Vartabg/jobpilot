"""Useful application preparation from the user's actual source material."""

from .matching import match
from .models import Draft, Listing, Profile


def prepare(profile: Profile, job: Listing) -> Draft:
    if not profile.resume_text.strip():
        raise ValueError(
            "Add your resume text in Your profile before preparing a draft."
        )
    fit = match(profile, job)
    lines = [
        f"Application draft: {job.title} — {job.company}",
        f"Posting: {job.url or 'Added by hand'}",
        "",
        "DRAFT — Review before using. Source material is self-reported and unverified.",
        "",
        "MESSAGE STARTER",
        f"Hello {job.company or 'hiring'} team,",
        "",
        f"I'm interested in the {job.title} position.",
    ]
    if fit.excerpts:
        lines += ["Some relevant background from my resume:"] + [
            "• " + line for line in fit.excerpts
        ]
    lines += [
        "",
        "Thank you for considering my application.",
        profile.full_name,
        "",
        "REQUIREMENTS TO CHECK",
    ]
    lines += ["• " + line for line in fit.questions] or [
        "Review the full posting, including required qualifications and location restrictions."
    ]
    lines += ["", "YOUR SOURCE RESUME", profile.resume_text]
    return Draft(job_id=job.id, text="\n".join(lines))
