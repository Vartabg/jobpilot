"""Bridge from the legacy lanes to the engine: an anti-corruption layer.

This is the only package that may import both the legacy lanes (core, gigs)
and the engine. It reads their stores read-only and translates their records
into ledger listings and events. It is deleted along with the legacy lanes in
Phase 4 of docs/plans/2026-09-25-one-core.md.
"""
