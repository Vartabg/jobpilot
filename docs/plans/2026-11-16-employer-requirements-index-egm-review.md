# EGM Design Review Report: Employer, HR Team, and Pre-Submit Requirement Index

- **Plan:** [`2026-11-16-employer-requirements-index.md`](2026-11-16-employer-requirements-index.md)
- **Source baseline:** `43fe913461cea5b528e5c34aa8e1e616a2e95fad` (`codex/employer-requirements-index`)
- **Finalized plan revision SHA-256:** `dccd1418efe85054194f898ff33c9f04aedf7b7ee53979f8b24ea233096d68bf`

---

## Round 1 — Initial Independent Review

- **Reviewer:** Isolated `research` subagent (`conversationId: 9711d017-8cad-4ddb-ace7-5c09b3732076`)
- **Reviewed plan SHA-256:** `e603fd8fbe19b73ed0b9eb3aa8306b05829f0d8d728fe0ff24cd54e54247bb7f`
- **Reading list:** `docs/plans/2026-11-16-employer-requirements-index.md`, `docs/plans/2026-09-25-one-core.md`, `tests/engine/test_architecture.py`, `engine/ports.py`, `engine/adapters/sqlite_ledger.py`, `engine/adapters/ats_boards.py`, `bridge/cli.py`, `cli.py` (plus additional test/config files verified in `$WT`).
- **Completion evidence:** Subagent completed at `2026-11-16T19:31:45Z` with explicit list of read files and 6 `blocker` + 3 `optional` findings.

### Round 1 Findings & Dispositions

1. **`[blocker]` Domain types, port signatures, classification taxonomy, and `seed_board_targets` unspecified** -> **Accepted & fixed:** Specified exact `StrEnum` members (`GateKind`, `ChannelKind`, `FrictionTier`, `RequirementCategory`), `is_beyond_standard` & `ROUTINE_POLICY_CATEGORIES` tiering rules, all dataclass fields, `EmployerStore` / `FormSchemas` protocol signatures, and replaced `seed_board_targets` with non-mutating `sample_board_opportunities`.
2. **`[blocker]` Schemas, subdirectory layout, and read-only DB handling for 15+ `--job-search-dir` files and `--gmail-jsonl` undocumented** -> **Accepted & fixed:** Documented relative subdirectory layout and keys for every campaign source, mandated `?mode=ro` SQLite URI access, and defined warn-and-skip on optional files vs exit `1` on explicit `--gmail-jsonl`.
3. **`[blocker]` SQLite schema allowed double-counted HR contacts, lost `(provider, token)` pairs, URL-less ID collisions, and destructive snapshot overwrites** -> **Accepted & fixed:** Added `hr_contact_messages(message_key PRIMARY KEY, ...)` deduplication, `"provider:token"` `board_tokens_json`, URL-less `role_key` (`employer:{employer_key}:{slug}:{source_kind}`), non-destructive `form_snapshots` upserts protecting `"ok"` rows from `"error"`, JSON-serialized SHA-256 `[:24]` IDs, and `_MIGRATIONS` + `version > SCHEMA_VERSION` guard.
4. **`[blocker]` Candidate PII, verification tokens, and sensitive URL parameters could leak into DB/exports** -> **Accepted & fixed:** Auto-loaded `candidate_emails` from `jobpilot.toml` + `profile.json` + `--candidate-email`, discarded `To`/`Cc`/`Delivered-To`/`snippet`/`body`, broadened `redact_sensitive_text`, added `sanitize_role_url`, and set default `--out-dir` to `DATA_DIR / "employer-intel"` with `atomic_write_text` (`0o600`).
5. **`[blocker]` ATS provider coverage (`smartrecruiters`/`workable`/`jobvite`/`jazzhr`/`bamboohr`/`paylocity`), `identity.py` reuse, custom-domain tokens, and HTTP error/CAPTCHA mapping gaps** -> **Accepted & fixed:** Reused `identify_role` for `greenhouse`/`lever`/`ashby`, mapped all 17 providers in one table, enforced allowlisted ATS hosts (`boards-api.greenhouse.io`, `jobs.ashbyhq.com`, `jobs.lever.co`), specified Ashby GraphQL query & Lever HTML parser, and defined `3xx`/`403`/`404`/`429` handling.
6. **`[blocker]` CLI subcommand `--store`/`--ledger`/`--portals`/`--dry-run` flags & worktree `PYTHONPATH` acceptance check** -> **Accepted & fixed:** Standardized `--store` across all `employers` subcommands, added `--ledger`/`--portals`/`--dry-run` (`TemporaryDirectory` + `sqlite3.backup`) on `collect`, exported `app` aliased as `employers_app`, and updated worktree verification commands to pass `PYTHONPATH="$(dirname "$PWD")"`.
7. **`[optional]` `ruff.toml` `B008` requires `Annotated[..., typer.Option(...)]`** -> **Accepted & fixed.**
8. **`[optional]` Reuse bounded HTTP helpers, `board_targets`, `read_tracker`, `atomic_write_text`, and `SQLiteEmployerStore.in_data_dir`** -> **Accepted & fixed.**
9. **`[optional]` Export CSV formula injection neutralization & Lever `<script>`/`<style>`/`hidden` stripping** -> **Accepted & fixed.**

---

## Round 2 — Targeted Re-Review

- **Reviewer:** Isolated `research` subagent (`conversationId: 59aec18a-869d-4410-bcdd-eada48f5b823`)
- **Reviewed plan SHA-256:** `e8f41f95c334a6dbafe1f5cc2da03cc02e910d6e0d58c90b61f1255799dff4da`
- **Reading list:** Revised `docs/plans/2026-11-16-employer-requirements-index.md` plus 13 source/config/test files in `$WT`.
- **Completion evidence:** Subagent completed at `2026-11-16T19:36:11Z` reporting 3 targeted blockers and 5 optional refinements.

### Round 2 Findings & Dispositions

1. **`[blocker]` `redact_sensitive_text` OTP regex lacked leading `\b` (corrupted `"Pinpoint"`, `"opinions"`, `"Security code from..."`) and missed `"code is <OTP>"`** -> **Accepted & fixed** in plan SHA-256 `dccd1418...`: anchored with `\b`, required `:|#|-|is` or digit-bearing token, preserved surrounding text, and required unconditional redaction on `display_name` and `sample_subject`.
2. **`[blocker]` SQLite `MIN(first_seen, excluded.first_seen)` would overwrite timestamps with `""` for undated records** -> **Accepted & fixed:** Adopted empty-string-guard `CASE` expression matching `sqlite_ledger.py` on both `employers` and `hr_contacts`.
3. **`[blocker]` Running `identify_role` before `sanitize_role_url` could hash/leak sensitive `token=` params into `role_key`** -> **Accepted & fixed:** `infer_ats_provider_and_gate` calls `sanitize_role_url` first and strips sensitive query params (`token`, `access_token`, `auth`, `code`, `email`, `key`, `secret`, `session`, `sig`, `signature`) while retaining numeric Greenhouse embed `token=` when `for=` is present.
4. **`[optional]` `demographics_eeo` / `documents` / `beyond_standard_count` wording in `summarize_employer`** -> **Accepted & fixed:** Aligned `CUSTOM_QUESTIONS` condition to `obs.is_beyond_standard and obs.requiredness != "optional" and obs.category not in ROUTINE_POLICY_CATEGORIES`.
5. **`[optional]` Pre-request `tenant`/`job_id` guard for Ashby/Lever, `unsupported` for non-ATS URLs, fallback to `Opportunity.tenant`/`provider_job_id`, hostname inspection** -> **Accepted & fixed.**
6. **`[optional]` Remove inline Q&A notes; adopt `read_opportunities` (`?mode=ro`) + `extra_targets`, rely on `mkstemp` `0o600` + `os.chmod` rather than non-existent `mode` kwarg** -> **Accepted & fixed.**
7. **`[optional]` Confirm `--job-search-dir` defaults to `None`, resolve `--store`/`--out-dir` inside command bodies, `--limit` counts HTTP attempts** -> **Accepted & fixed.**
8. **`[optional]` Clarify `hr_contacts` `message_count` initialization when `message_key != ""`, protect `"ok"` snapshots from `"unsupported"`, `ON CONFLICT(observation_id) DO UPDATE`, and empty-gate handling for non-API portals** -> **Accepted & fixed.**

## Remaining Limitations

- Enterprise login-walled portals (`workday`, `ukg`, `icims`, `oracle`, `paycom`, `successfactors`) and non-API portals (`smartrecruiters`, `workable`, `jobvite`, `jazzhr`, `bamboohr`, `paylocity`) are indexed via public campaign preflights, holds, and URL-inferred provider gates (`HttpFormSchemas` does not automate login flows or CAPTCHA walls).
- Gmail contact extraction relies on `From`, `Reply-To`, `Subject`, and `Date` headers, so company aliases not explicitly present in the sender display name, subject, or `alias_map` may retain domain-derived company keys until matched against `opportunities.db`/`applications.db` domains.
