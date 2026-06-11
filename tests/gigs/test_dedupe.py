from jobpilot.gigs.core.dedupe import (
    cross_source_key,
    dedupe_cross_source,
    dedupe_cross_source_with_groups,
    gig_keys,
    listing_keys,
)
from jobpilot.gigs.core.models import Gig


def test_cross_source_key_matches_same_role_different_ids() -> None:
    a = Gig(id="rok-1", source="remoteok", title="AI Engineer", company="Acme", url="https://a")
    b = Gig(id="wwr-2", source="wwr", title="AI Engineer", company="Acme", url="https://b")
    assert cross_source_key(a) == cross_source_key(b)


def test_dedupe_keeps_higher_fit_variant() -> None:
    weak = Gig(
        id="wwr-x",
        source="wwr",
        title="Applied AI Engineer",
        company="Acme",
        url="https://example.com/wwr",
        description="generic",
    )
    strong = Gig(
        id="hn-y",
        source="hn",
        title="Applied AI Engineer",
        company="Acme",
        url="https://example.com/hn",
        description="LLM RAG agent claude mcp workflows",
        pay_hourly_est=120,
        apply_url="mailto:hire@acme.io",
    )
    unique, removed = dedupe_cross_source([weak, strong])
    assert removed == 1
    assert len(unique) == 1
    assert unique[0].id == "hn-y"


def test_dedupe_with_groups_lists_every_collapsed_member() -> None:
    a = Gig(id="rok-1", source="remoteok", title="AI Engineer", company="Acme", url="https://a")
    b = Gig(id="wwr-2", source="wwr", title="AI Engineer", company="Acme", url="https://b")
    c = Gig(id="hn-3", source="hn", title="Data Engineer", company="Beta", url="https://c")
    unique, removed, groups = dedupe_cross_source_with_groups([a, b, c])
    assert removed == 1
    rep = next(g for g in unique if g.id != "hn-3")
    # Both variants are listed under the representative — mark_seen can
    # retire them all so the loser can't resurface as `new` next run.
    assert sorted(groups[rep.id]) == ["rok-1", "wwr-2"]
    assert groups["hn-3"] == ["hn-3"]


def test_gig_keys_match_listing_keys_built_from_a_pipeline_row() -> None:
    # A repost's keys must intersect the keys of the pipeline row that was
    # minted from the original sighting (title truncated, pipes stripped).
    gig = Gig(
        id="wwr-9", source="wwr", title="Senior AI Engineer | Remote | $150K",
        company="Acme", url="https://example.com/x",
    )
    row_side = listing_keys(title="Senior AI Engineer", company="Acme")
    assert gig_keys(gig) & row_side


def test_listing_keys_include_ats_host_form() -> None:
    keys = listing_keys(
        title="AI Engineer",
        company="Acme",
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
    )
    assert "ats:boards.greenhouse.io|ai engineer" in keys
    assert any(k.startswith("co:acme|") for k in keys)