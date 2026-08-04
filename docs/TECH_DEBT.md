# Technical debt

## Oversized legacy modules

The backlog-recovery pass on 2026-08-04 kept new and refactored modules below
200 lines, but these existing files remain substantially over the project
review guideline:

- `cli.py`
- `core/job_scorer.py`
- `core/queue_builder.py`
- `gigs/core/preferences.py`
- `gigs/core/proposals.py`

Before adding another feature to one of these files, extract the affected
command, scoring policy, or rendering concern into a focused module with its
own tests. Avoid a single broad rewrite; preserve behavior one seam at a time.
