# JobPilot agent contract

JobPilot exposes one model-neutral contract through Python, JSON-only CLI commands, and versioned HTTP routes. Agents should use this contract instead of importing queue internals or scraping the human dashboard.

## Safety boundary

Agents may discover roles, refresh public job sources, assess evidence, inspect search performance, run a preparation preflight, and record a human-confirmed outcome. They may not fill or submit a live ATS form.

Every response reports whether human action is required. `may_submit_automatically` is always `false`; the human verifies the company, reviews the evidence and drafted material, pastes it into their normal browser, and submits it.

## Contract discovery

CLI:

```bash
jobpilot agent capabilities
```

HTTP:

```text
GET /api/agent/v1/capabilities
```

Python:

```python
from jobpilot.core.jobpilot_service import JobPilotService

capabilities = JobPilotService().capabilities().to_dict()
```

The current contract identifier is `jobpilot.agent/v1`. Agents should read the capabilities response instead of assuming an operation exists.

## Response envelope

Successful operations share this shape:

```json
{
  "contract_version": "jobpilot.agent/v1",
  "operation": "opportunities.list",
  "ok": true,
  "generated_at": "2026-08-14T22:00:00+00:00",
  "data": {},
  "warnings": [],
  "human_action_required": false,
  "human_actions": []
}
```

Safe failures use:

```json
{
  "contract_version": "jobpilot.agent/v1",
  "ok": false,
  "error": {
    "code": "missing_job_description",
    "message": "The role cannot be assessed without its full job description.",
    "action": "Provide the full job description text and retry."
  }
}
```

Warnings never silently become points or permission. For example, incomplete configured application history keeps search available but prevents preparation-ready output.

## Operations

| Operation | Side effect | Human confirmation |
|---|---|---|
| `capabilities.read` | None | No |
| `opportunities.list` | None | No |
| `opportunities.refresh` | Public network reads and local evidence update | No |
| `role.assess` | None | No |
| `role.preflight` | None | Always returns human actions |
| `dashboard.read` | None | No |
| `outcome.record` | Local tracker and ledger update | Required |

### Opportunity records

Each record includes the queue evidence fields plus:

- `target_review_state`: neutral review state for the exact company and role;
- `claim_state`: compatibility alias for older agents;
- `action_readiness.state`: `apply_ready`, `target_review_required`, or the evidence decision;
- `action_readiness.may_prepare`: whether the preparation gate passed;
- `action_readiness.may_submit_automatically`: always `false`;
- `human_actions`: the next responsibilities that cannot be delegated.

Relocation remains structured as `offered`, `conditional`, `required_unsupported`, `not_offered`, `not_needed`, or `unknown`, with its destination and exact posting evidence kept separate from role fit.

## CLI examples

```bash
jobpilot agent opportunities --limit 20
jobpilot agent opportunities --ready-only
jobpilot agent opportunities --refresh --limit 100
jobpilot agent assess ./job-description.txt --title "Customer Engineer"
jobpilot agent preflight ROLE_ID
jobpilot agent dashboard
jobpilot agent outcome Acme --title "Customer Engineer" \
  --status interview --human-confirmed --channel direct
```

All `jobpilot agent` commands emit JSON only. Diagnostic failures use the same safe error object and a nonzero exit status.

## HTTP routes

```text
GET  /api/agent/v1/capabilities
GET  /api/agent/v1/opportunities?ready_only=false&limit=50
POST /api/agent/v1/opportunities/refresh?ready_only=false&limit=50
POST /api/agent/v1/assess
GET  /api/agent/v1/preflight/{job_id}
GET  /api/agent/v1/dashboard
POST /api/agent/v1/outcomes
```

The routes inherit JobPilot's loopback and authenticated-Tailscale protections. Remote responses use `Cache-Control: no-store`. A local agent must still be authorized to run the CLI or access the server; the contract does not bypass operating-system permissions.

## Compatibility

Existing `jobpilot queue --json`, `/api/queue`, and `/api/dashboard` consumers keep their legacy top-level shapes. Their opportunity and dashboard data now come from the same service facade. New integrations should use the versioned agent contract.

Legacy `claude-vetted-targets-*.json` reports remain readable. New integrations should use the neutral `agent-reviewed-targets-*.json` name; public responses never assign target-review ownership to a specific model vendor.
