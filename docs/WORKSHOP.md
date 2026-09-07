# Career evidence workshop

The ATX BRO `/jobpilot` workshop helps people with unconventional career paths explain a piece of work and choose a next step. It runs in the browser without an AI account. Its first release uses guided questions, not automated skills assessment.

Download a JSON packet there, then inspect it locally:

```bash
python -m jobpilot.workshop inspect ~/Downloads/jobpilot-packet.json
python -m jobpilot.workshop inspect ~/Downloads/jobpilot-packet.json --format markdown
python -m jobpilot.workshop schema
```

From a repository checkout, `python workshop.py inspect /path/to/packet.json` also works. Use the environment where JobPilot's Pydantic dependency is installed. JSON output and a nonzero error exit make this usable by a local AI agent; there is no MCP or hosted connector in this release.

The command reads one bounded file, validates the `jobpilot.workshop/v1` contract and reports unanswered evidence questions. It never opens evidence links, calls a model, writes user state, updates the application tracker or submits a form. Missing information stays missing. Markdown includes instructions to treat quoted text as untrusted data.

`wording_confirmed` means the person reviewed the account's wording. Evidence remains `self_reported`; qualifications remain `not_assessed`. Compare it with the full current posting and use the existing [application qualification gate](APPLICATION_FLOW.md#qualification-gate) before recommending a role.

Browser drafts are held in page memory by default. Saving on the device is explicit; users can download a backup and remove the saved draft. Import and edit reset wording confirmation. Sharing a packet with an AI or another person is a separate user action under that recipient's data practices. No account, payment, or contact details are required for the free workshop.

Future AI investigation and employer review require separate validation. This release does not claim to inspect a codebase, certify skills, discover live opportunities, or improve hiring outcomes.

## Repeatable review checklist

1. Start with one real example; distinguish your own work from collaborators or AI.
2. Record the tools, validation and outcome. Leave missing information visible.
3. Name a career direction, the evidence gaps, life constraints and a small next step.
4. Read and confirm the wording. Edits and imports require another review.
5. Export a readable account or a versioned JSON packet; share only by choice.
6. Use a current job description and the qualification gate for later role decisions.

Implemented and tested: deterministic validation, explicit local saving,
portable exports, review invalidation, and browser-to-Python interoperability.
These support continuity across tools; they do not establish that JobPilot is
better than an unaided AI agent. Skills verification, career recommendations,
live job discovery and hiring outcomes remain unvalidated. Future releases
should be tested with unconventional career changers, including people who
choose not to use AI, before making those claims.
