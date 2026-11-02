"""JobPilot engine: the one core that every screen and command will share.

Layers, innermost first (see docs/plans/2026-09-25-one-core.md):

- ``engine.domain``: pure Python with no I/O. Role identity, opportunities,
  events, and the single status vocabulary derived from events.
- ``engine.ports``: the interfaces the engine needs from the outside world.
- ``engine.adapters``: implementations of those ports, such as the SQLite ledger.

tests/engine/test_architecture.py enforces the boundaries: the domain imports
only side-effect-free standard-library modules, ports import only the domain,
and nothing in the engine imports the legacy lanes (core, gigs, ui, product,
learning).
"""
