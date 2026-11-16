# Employer, HR Team, and Pre-Submit Requirement Index — Design Specification

## Goal & Scope

Build a Company, HR Team, and Pre-Submit Requirement Intelligence subsystem inside JobPilot's hexagonal architecture (`engine/` + `bridge/` + `cli.py`), exposed as `jobpilot employers`.

Each tracked company gets an aggregated profile answering:
1. **ATS Provider & Board Tokens:** Which ATS (`greenhouse`, `ashby`, `lever`, `workday`, `ukg`, `icims`, `oracle`, `paycom`, `successfactors`, `rippling`, `pinpoint`, `smartrecruiters`, `workable`, `jobvite`, `jazzhr`, `bamboohr`, `paylocity`, etc.) hosts its applications, plus its `(provider, token)` board pairs and observed domains.
2. **Pre-Submit Gates & Friction Tier:** Which blocking or step-up gates occur before submission (`none`, `account_required`, `email_verification_code`, `captcha`, `application_limit`, `consent_wall`, `silent_drop_or_rate_limit`) and the resulting `FrictionTier` (`low_friction`, `custom_questions`, `gated`).
3. **Required Pre-Submit Fields:** Exact fields, categories, types, and requiredness—separating standard baseline inputs from **beyond-standard requirements**.
4. **HR & Recruiting Contacts:** Sender emails, display names, domains, and channel classification (`direct_person`, `company_recruiting`, `ats_relay`) extracted from Gmail metadata and mail indexes without storing applicant answers, phone/address, or OTP tokens.

---

## Hexagonal Boundaries

All modules must satisfy `tests/engine/test_architecture.py` and `tests/conftest.py` real-`data/` isolation:

1. **Pure Domain (`engine/domain/employers.py`, re-exported by `engine/domain/__init__.py`):**
   - Standard library only; MUST NOT import any module in `IO_MODULES` (`http`, `io`, `os`, `pathlib`, `requests`, `shutil`, `socket`, `sqlite3`, `subprocess`, `tempfile`, `urllib.request`) or any third-party package.
   - Reuses `identify_role` and `canonicalize_role_url` from `jobpilot.engine.domain.identity`.
2. **Ports (`engine/ports.py`):**
   - Imports only from `jobpilot.engine.domain`. Defines `EmployerStore` and `FormSchemas`.
3. **Service (`engine/service/employers.py`):**
   - Imports only from `jobpilot.engine.domain`, `jobpilot.engine.ports`, and non-I/O stdlib (`collections`, `dataclasses`, `json`).
   - Orchestrates live form schema collection from `Ledger` and `Boards` targets into `EmployerStore` without mutating `opportunities.db`.
4. **Adapters (`engine/adapters/sqlite_employers.py`, `engine/adapters/ats_forms.py`, shared HTTP helper in `engine/adapters/ats_boards.py`):**
   - `SQLiteEmployerStore`: stores state in `DATA_DIR / "employers.db"` (`0o600`, `O_NOFOLLOW`, `PRAGMA foreign_keys = ON`, `_MIGRATIONS` up to `SCHEMA_VERSION = 1`, `version > SCHEMA_VERSION` raises `RuntimeError`, matching `sqlite_ledger.py`), separate from `opportunities.db` (whose `SCHEMA_VERSION` stays `2`). Exposes `SQLiteEmployerStore.in_data_dir(data_dir: Path)`.
   - `HttpFormSchemas`: reuses bounded streaming HTTP helpers factored from `ats_boards.py` (`TIMEOUT_SECONDS = 15`, `MAX_BYTES = 4 * 1024 * 1024`, `allow_redirects=False`, 64 KiB chunk cap, `quote(unquote(...), safe="")`, `html_to_text`). Never connects to arbitrary `role_url` domains—only allowlisted ATS hosts (`boards-api.greenhouse.io`, `jobs.ashbyhq.com`, `jobs.lever.co`). Supports optional `sleep_fn: Callable[[float], None]` and `delay_seconds: float = 0.0` for polite pacing.
5. **Bridge & CLI (`bridge/employer_importers.py`, `bridge/employers_cli.py`, `cli.py`):**
   - `bridge/employer_importers.py` opens legacy SQLite DBs read-only (`?mode=ro` URI mode, reusing `bridge/sources.py:read_tracker` and a read-only `read_opportunities` helper), reads campaign JSON/Markdown/JSONL inputs, never modifies source files, and warns-and-skips missing or malformed optional files.
   - `bridge/employers_cli.py` exports `app` (aliased as `employers_app` in `cli.py` alongside `ledger_app` and `settings_app`), uses `Annotated[..., typer.Option(...)]` / `Annotated[..., typer.Argument(...)]` to satisfy `ruff.toml`'s `B008` rule, reuses `board_targets` from `bridge/cli.py`, and writes exports with `core.atomic_io.atomic_write_text` (whose `tempfile.mkstemp()` creates `0o600` files on POSIX, reinforced with explicit `os.chmod(path, 0o600)` when supported).

---

## Domain Types & Pure Contracts (`engine/domain/employers.py`)

### Enums (`StrEnum`)

- **`GateKind`**:
  - `NONE = "none"`
  - `ACCOUNT_REQUIRED = "account_required"`
  - `EMAIL_VERIFICATION_CODE = "email_verification_code"`
  - `CAPTCHA = "captcha"`
  - `APPLICATION_LIMIT = "application_limit"`
  - `CONSENT_WALL = "consent_wall"`
  - `SILENT_DROP_OR_RATE_LIMIT = "silent_drop_or_rate_limit"`
- **`ChannelKind`**:
  - `DIRECT_PERSON = "direct_person"`
  - `COMPANY_RECRUITING = "company_recruiting"`
  - `ATS_RELAY = "ats_relay"`
- **`FrictionTier`**:
  - `LOW_FRICTION = "low_friction"`
  - `CUSTOM_QUESTIONS = "custom_questions"`
  - `GATED = "gated"`
- **`RequirementCategory`**:
  - `IDENTITY = "identity"`
  - `CONTACT = "contact"`
  - `DOCUMENTS = "documents"`
  - `PROFESSIONAL_LINKS = "professional_links"`
  - `ADDRESS = "address"`
  - `WORK_AUTHORIZATION = "work_authorization"`
  - `CITIZENSHIP_EXPORT = "citizenship_export"`
  - `LOCATION_SCHEDULE = "location_schedule"`
  - `COMPENSATION = "compensation"`
  - `AVAILABILITY = "availability"`
  - `EXPERIENCE_TENURE = "experience_tenure"`
  - `TECHNICAL_SKILLS = "technical_skills"`
  - `EDUCATION_GPA = "education_gpa"`
  - `WORK_SAMPLE_PORTFOLIO = "work_sample_portfolio"`
  - `MEDIA_VOICE_VIDEO = "media_voice_video"`
  - `BESPOKE_ESSAY = "bespoke_essay"`
  - `AI_POLICY = "ai_policy"`
  - `PRIVACY_LEGAL_CONSENT = "privacy_legal_consent"`
  - `BACKGROUND_DRUG_MVR = "background_drug_mvr"`
  - `PHYSICAL_REQUIREMENTS = "physical_requirements"`
  - `REFERENCES = "references"`
  - `DEMOGRAPHICS_EEO = "demographics_eeo"`
  - `OTHER = "other"`

Category sets & `is_beyond_standard` contract:
- Standard baseline inputs (`is_beyond_standard = False`): `IDENTITY`, `CONTACT`, `PROFESSIONAL_LINKS`, `DOCUMENTS` when label is resume/CV or `requiredness == "optional"` cover letter, and `DEMOGRAPHICS_EEO` unless `requiredness == "required"` for non-standard/pronoun asks.
- All other categories (`ADDRESS`, `WORK_AUTHORIZATION`, `CITIZENSHIP_EXPORT`, `LOCATION_SCHEDULE`, `COMPENSATION`, `AVAILABILITY`, `EXPERIENCE_TENURE`, `TECHNICAL_SKILLS`, `EDUCATION_GPA`, `WORK_SAMPLE_PORTFOLIO`, `MEDIA_VOICE_VIDEO`, `BESPOKE_ESSAY`, `AI_POLICY`, `PRIVACY_LEGAL_CONSENT`, `BACKGROUND_DRUG_MVR`, `PHYSICAL_REQUIREMENTS`, `REFERENCES`, `OTHER`), plus required cover letter/portfolio document uploads (`DOCUMENTS` with `requiredness == "required"` and non-resume label), set `is_beyond_standard = True`.
- For `FrictionTier` calculation in `summarize_employer`:
  - `ROUTINE_POLICY_CATEGORIES = frozenset({WORK_AUTHORIZATION, LOCATION_SCHEDULE})` — these are still tracked in `beyond_standard_categories` and `beyond_standard_count`, while `CUSTOM_QUESTIONS` triggers when an employer has non-optional observations with `is_beyond_standard == True` outside `ROUTINE_POLICY_CATEGORIES` (i.e., `obs.is_beyond_standard and obs.requiredness != "optional" and obs.category not in ROUTINE_POLICY_CATEGORIES`).

### Frozen Dataclasses

- **`EmployerRecord`**:
  - `employer_key: str`
  - `display_name: str`
  - `board_tokens: tuple[str, ...] = ()` — canonical `"provider:token"` strings (e.g., `"greenhouse:vercel"`, `"ashby:avallon"`), sorted and deduplicated.
  - `providers: tuple[str, ...] = ()` — distinct sorted provider names.
  - `domains: tuple[str, ...] = ()` — distinct non-relay company email/website domains.
  - `first_seen: str = ""`
  - `last_seen: str = ""`
- **`HrContact`**:
  - `contact_id: str` — SHA-256 hex `[:24]` over `json.dumps([employer_key, email.lower()], separators=(",", ":"))` (auto-computed if empty).
  - `employer_key: str`
  - `email: str`
  - `display_name: str = ""`
  - `domain: str = ""`
  - `channel_kind: ChannelKind = ChannelKind.ATS_RELAY`
  - `first_seen: str = ""`
  - `last_seen: str = ""`
  - `message_count: int = 1`
  - `sample_subject: str = ""`
- **`RequirementObservation`**:
  - `employer_key: str`
  - `employer_name: str`
  - `role_key: str` — canonical `Listing.identity_key()` when URL/job ID exists, else `f"employer:{employer_key}:{slug(role_title) or 'role'}:{source_kind}"`.
  - `role_title: str = ""`
  - `role_url: str = ""` — sanitized via `sanitize_role_url`.
  - `provider: str = ""`
  - `category: RequirementCategory = RequirementCategory.OTHER`
  - `label: str = ""`
  - `field_type: str = ""`
  - `requiredness: str = "unknown"` (`"required"`, `"optional"`, `"unknown"`)
  - `gate_kind: GateKind | None = None`
  - `is_beyond_standard: bool = False`
  - `source_kind: str = ""`
  - `source_ref: str = ""`
  - `observed_at: str = ""`
  - `observation_id: str = ""` — SHA-256 hex `[:24]` over `json.dumps([employer_key, role_key, category.value, label, gate_kind.value if gate_kind else "", source_kind], separators=(",", ":"))` (auto-computed in `__post_init__` when empty).
- **`FormSnapshot`**:
  - `role_key: str`
  - `employer_key: str`
  - `provider: str`
  - `role_title: str`
  - `role_url: str` — sanitized via `sanitize_role_url`.
  - `status: str` — `"ok"`, `"error"`, `"inferred_gate"`, or `"unsupported"`.
  - `error: str = ""`
  - `field_count: int = 0`
  - `beyond_standard_count: int = 0`
  - `gates: tuple[GateKind, ...] = ()`
  - `fetched_at: str = ""`
- **`EmployerSummary`**:
  - `employer: EmployerRecord`
  - `gates: tuple[GateKind, ...]` — sorted non-`NONE` gates across snapshots and observations, or `(GateKind.NONE,)` when clean snapshots exist (`ok` or non-blocking `inferred_gate`) and no blocking/non-`NONE` gate was observed.
  - `role_count: int` — distinct `role_key`s across snapshots and observations.
  - `observation_count: int`
  - `beyond_standard_categories: tuple[str, ...]`
  - `direct_contacts: tuple[HrContact, ...]`
  - `team_contacts: tuple[HrContact, ...]`
  - `ats_contacts: tuple[HrContact, ...]`
  - `friction_tier: FrictionTier`

### Deterministic Domain Functions

1. **`normalize_employer_key(name: str) -> str`**:
   - Unicode-normalize whitespace, lowercase, strip HTML/quotes, strip leading `"team "`.
   - Strip trailing legal/recruiting suffixes (`hiring team`, `talent team`, `recruiting team`, `recruitment team`, `recruiting`, `careers`, `talent`, `hiring`, `team`, `inc.`, `inc`, `llc`, `corp.`, `corp`, `corporation`, `co.`, `co`, `ltd.`, `ltd`), preserving brand tokens like `ai` (`avallon ai`, `bluefish ai`).
   - Collapse punctuation to single spaces; return `""` when blank (callers skip empty keys before DB writes).
2. **`sanitize_role_url(url: str, provider: str = "") -> str`**:
   - Calls `canonicalize_role_url(url, provider)` and strips fragments plus sensitive/session query parameters (`token`, `access_token`, `auth`, `code`, `email`, `key`, `secret`, `session`, `sig`, `signature`), except keeping `token=<digits>` on Greenhouse embed URLs when `for=` is present (since Greenhouse uses `token=<gh_jid>`).
3. **`infer_ats_provider_and_gate(url: str, declared_provider: str = "", tenant: str = "", provider_job_id: str = "") -> tuple[str, str, str, GateKind | None]`**:
   - **Sanitizes URL first:** `sanitized = sanitize_role_url(url, declared_provider)` before calling `identify_role(sanitized, declared_provider, tenant, provider_job_id)`.
   - Inspects `urlsplit(sanitized).hostname` (not full URL substring) for all 17 providers:
     - Public API fetchable (`default_gate=None`): `greenhouse`, `ashby`, `lever`.
     - Enterprise account-walled (`default_gate=GateKind.ACCOUNT_REQUIRED`): `workday` (`myworkdayjobs.com`), `ukg` (`ultipro.com`), `icims` (`icims.com`), `oracle` (`oraclecloud.com`, `enterpriseplatform.dell.com`), `paycom` (`paycomonline.net`), `successfactors` (`successfactors.com`, `successfactors.eu`, `opportunities.lamresearch.com`, `careers.hhs.texas.gov`).
     - Consent-gated (`default_gate=GateKind.CONSENT_WALL`): `rippling` (`ats.rippling.com`), `pinpoint` (`pinpointhq.com`).
     - Recognized non-API portals (`default_gate=None`, recorded via `inferred_gate` only as non-API unless a blocker like CAPTCHA is observed): `smartrecruiters` (`smartrecruiters.com`), `workable` (`apply.workable.com`), `jobvite` (`jobs.jobvite.com`), `jazzhr` (`applytojob.com`), `bamboohr` (`bamboohr.com`), `paylocity` (`recruiting.paylocity.com`).
4. **`redact_sensitive_text(text: str) -> str`**:
   - Scrubs emails (`[\w.+-]+@[\w-]+\.[\w.-]+` -> `[redacted-email]`) and phones (`(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}` -> `[redacted-phone]`).
   - Scrubs OTP/security tokens without corrupting normal words like `"Pinpoint"`, `"opinions"`, or `"Security code from Acme"`:
     Matches `\b(?:security\s+code|verification\s+code|one-time\s+(?:passcode|password|code)|passcode|otp|pin)\s*(?:(?::|#|-|is)\s*([A-Za-z0-9]{4,12})|\s+([A-Za-z0-9]*\d[A-Za-z0-9]{3,11}))\b` and replaces ONLY the captured token with `[redacted-code]`, keeping the surrounding subject phrasing readable!
5. **`extract_hr_contact(from_header: str, *, reply_to_header: str = "", subject: str = "", date_header: str = "", message_id: str = "", candidate_emails: frozenset[str] = frozenset(), alias_map: Mapping[str, str] | None = None) -> tuple[HrContact | None, GateKind | None, str]`**:
   - Consumes ONLY `From`, `Reply-To`, `Subject`, `Date`, `message_id` (no `To`, `Cc`, `Delivered-To`, `snippet`, or `body`).
   - Unconditionally applies `redact_sensitive_text` to `display_name` and `sample_subject`.
   - Ignores senders in `candidate_emails` or known non-job newsletters (`targetnews`, `nvidia.com`, `concourseproject.com`, `lowcostvet.org`, `oriontalent.com`, `match.indeed.com`, `militaryhire.com`, `inceptionlabs.ai`).
   - Detects `GateKind.EMAIL_VERIFICATION_CODE` when `subject` indicates a Greenhouse security code (`Security code for your application to (.+)` or `security code` from `greenhouse`), extracting employer name and returning `(contact, GateKind.EMAIL_VERIFICATION_CODE, employer_key)`.
   - Classifies `ChannelKind`: `ATS_RELAY` (`ashbyhq.com`, `greenhouse-mail.io`, `greenhouse.io`, `hire.lever.co`, `myworkday.com`, `smartrecruiters.com`, `workablemail.com`, `jobvite.com`, `comeet-notifications.com`, `icims.com`, `applytojob.com`, `bamboohr.com`, `paylocity.com`, `rippling.com`), `COMPANY_RECRUITING` (role prefixes `careers`, `jobs`, `talent`, `recruiting`, `recruitment`, `hr`, `hiring`, `no-reply`, `noreply`, `notifications`, `donotreply`, `team`), else `DIRECT_PERSON`.
6. **`summarize_employer(...) -> EmployerSummary`**:
   - Tier precedence:
     1. `GATED`: if `gates` contains any of `{ACCOUNT_REQUIRED, CAPTCHA, EMAIL_VERIFICATION_CODE, APPLICATION_LIMIT, CONSENT_WALL, SILENT_DROP_OR_RATE_LIMIT}`.
     2. `CUSTOM_QUESTIONS`: else if any observation has `obs.is_beyond_standard and obs.requiredness != "optional" and obs.category not in ROUTINE_POLICY_CATEGORIES`.
     3. `LOW_FRICTION`: otherwise.

---

## Ports & Service Contracts (`engine/ports.py`, `engine/service/employers.py`)

### `engine/ports.py`
```python
class EmployerStore(Protocol):
    def upsert_employer(self, employer: EmployerRecord) -> EmployerRecord: ...
    def upsert_contact(self, contact: HrContact, *, message_key: str = "") -> bool: ...
    def record_observation(self, observation: RequirementObservation) -> bool: ...
    def replace_observations_for_role(
        self, role_key: str, source_kind: str, observations: Sequence[RequirementObservation]
    ) -> int: ...
    def record_snapshot(self, snapshot: FormSnapshot) -> None: ...
    def has_snapshot(
        self, role_key: str, *, statuses: tuple[str, ...] = ("ok", "inferred_gate", "unsupported")
    ) -> bool: ...
    def employers(self) -> list[EmployerRecord]: ...
    def get_employer(self, employer_key: str) -> EmployerRecord | None: ...
    def contacts(self, employer_key: str | None = None) -> list[HrContact]: ...
    def observations(self, employer_key: str | None = None) -> list[RequirementObservation]: ...
    def snapshots(self, employer_key: str | None = None) -> list[FormSnapshot]: ...

class FormSchemas(Protocol):
    def fetch(self, opportunity: Opportunity) -> tuple[FormSnapshot, tuple[RequirementObservation, ...]]: ...
```

### `engine/service/employers.py`
- **`CollectReport`**: `inspected: int`, `fetched: int`, `skipped_existing: int`, `inferred_gates: int`, `unsupported: int`, `errors: int`, `observations_added: int`, `failures: list[str]`.
- **`collect_employer_schemas(ledger: Ledger | None, store: EmployerStore, fetcher: FormSchemas, *, now: str, limit: int | None = None, provider: str | None = None, company: str | None = None, refresh_existing: bool = False, extra_targets: Iterable[Opportunity] = ()) -> CollectReport`**:
  - Deduplicates candidate `Opportunity` items by `identify_role` key (`role_key`) after sanitizing `opp.url`.
  - Skips roles where `not refresh_existing and store.has_snapshot(role_key)`.
  - Calls `fetcher.fetch(opp)`; ensures `EmployerRecord` exists first (`upsert_employer`) whenever `employer_key` is non-empty.
  - `--limit` caps **live HTTP attempts** (`fetched` counts HTTP requests attempted for `greenhouse`, `ashby`, `lever`, including `403`/`404`/`429` responses); `inferred_gate` / `unsupported` items without HTTP calls do not consume the HTTP budget.
  - Non-destructive snapshot upsert never overwrites an existing `"ok"` snapshot with `"error"` or `"unsupported"`.
  - Replaces `requirement_observations` for `(role_key, "live_ats_form")` only when `snapshot.status == "ok"`.
- **`sample_board_opportunities(boards: Boards, targets: Iterable[BoardTarget], *, per_board: int = 1) -> tuple[list[Opportunity], list[str]]`**:
  - Fetches up to `per_board` postings from each `BoardTarget` via `Boards.fetch(target)` and returns `(opportunities, failed_board_messages)` in memory without writing to `opportunities.db`.

---

## Live ATS Fetch Contracts (`engine/adapters/ats_forms.py`)

- Allowlisted outgoing HTTP hosts ONLY: `boards-api.greenhouse.io`, `jobs.ashbyhq.com`, `jobs.lever.co`. Path/token parameters encoded with `quote(unquote(...), safe="")`.
- Resolves `(provider, tenant, job_id, default_gate)` using `infer_ats_provider_and_gate(opp.url, opp.provider, opp.tenant, opp.provider_job_id)` so `Opportunity.tenant` and `Opportunity.provider_job_id` recorded in `opportunities.db` work even for custom-domain Greenhouse embeds.
- **Pre-request ID guard for all 3 API providers (`greenhouse`, `ashby`, `lever`):** If `tenant` or `job_id` is empty, make **no HTTP request** and return `status="unsupported"`, `error="missing board token or job id"`.
- For unrecognized hosts (`""`) or aggregators (`indeed`, `adzuna`), make **no HTTP request** and return `status="unsupported"`, `error="unsupported provider"`.
- **Greenhouse**: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}?questions=true`. Parses `questions`, `location_questions`, and `data_compliance`.
- **Ashby**: `POST https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting` using `ApiJobPosting` GraphQL query (`applicationLimitCalloutHtml`, `shouldAskForTextingConsent`, `shouldAskForWhatsAppConsent`, `applicationForm.sections`, `surveyForms.sections`).
- **Lever**: `GET https://jobs.lever.co/{token}/{job_id}/apply` parsed via `html.parser`, stripping `<script>`, `<style>`, `<input type="hidden">`, and submit/button controls; extracts `<li class="application-question...">` items plus `h-captcha` / `g-recaptcha` (`GateKind.CAPTCHA`).
- **HTTP status mapping** (all responses use bounded streaming reader up to 4 MiB):
  - `3xx` -> `status="error"`, `"redirected"`
  - `404` -> `status="error"`, `"HTTP 404 (closed)"`
  - `429` -> `status="error"`, `"HTTP 429 (rate limited)"` (no synthetic observation written; existing `"ok"` snapshot preserved)
  - `403` -> inspect bounded body (`MAX_BYTES`): if `captcha` / `datadome` / `cloudflare` / `challenge` markers appear, return `status="inferred_gate"` with `GateKind.CAPTCHA`; else `status="error"`, `"HTTP 403"`
- **Other recognized providers** (`workday`, `ukg`, `icims`, `oracle`, `paycom`, `successfactors`, `rippling`, `pinpoint`, `smartrecruiters`, `workable`, `jobvite`, `jazzhr`, `bamboohr`, `paylocity`): make no HTTP requests; return `status="inferred_gate"` with `default_gate` (`ACCOUNT_REQUIRED`, `CONSENT_WALL`, or empty/no-gate for recognized non-API portals, avoiding misleading `GateKind.NONE` claims when the form was not actually inspected).

---

## SQLite Storage (`engine/adapters/sqlite_employers.py`)

`DATA_DIR / "employers.db"`, `0o600` (`O_NOFOLLOW`), `PRAGMA foreign_keys = ON`, `_MIGRATIONS` up to `SCHEMA_VERSION = 1`.
- `employers` table upsert:
  - `providers_json`, `board_tokens_json`, `domains_json` merged as sorted deduplicated unions in transaction.
  - `first_seen`: `CASE WHEN first_seen = '' THEN excluded.first_seen WHEN excluded.first_seen = '' THEN first_seen ELSE MIN(first_seen, excluded.first_seen) END` (prevents empty strings from clobbering real timestamps).
  - `last_seen`: `MAX(last_seen, excluded.last_seen)`.
- `hr_contacts` + `hr_contact_messages`:
  - `upsert_contact(contact, message_key="")`: inserts new contact (`message_count=1`) or updates conflict with empty-safe `first_seen`, `MAX(last_seen, ...)`, non-empty `display_name`/`sample_subject`.
  - When `message_key != ""`, executes `INSERT INTO hr_contact_messages (message_key, contact_id) VALUES (?, ?) ON CONFLICT(message_key) DO NOTHING`: ONLY if a new contact was NOT just inserted AND `cursor.rowcount == 1`, increments `message_count = message_count + 1` (so a new contact + new `message_key` starts at `1`, and re-running the same `import` is 100% idempotent!). When `message_key == ""`, never increments `message_count` on conflict.
- `form_snapshots`:
  - `ON CONFLICT(role_key) DO UPDATE` protects existing `status = 'ok'` rows whenever incoming status is `'error'` or `'unsupported'`.
- `requirement_observations`:
  - `ON CONFLICT(observation_id) DO UPDATE SET requiredness = excluded.requiredness, field_type = COALESCE(NULLIF(excluded.field_type, ''), requirement_observations.field_type), observed_at = MAX(requirement_observations.observed_at, excluded.observed_at)`.

---

## Bridge Importers (`bridge/employer_importers.py`)

- Reads all SQLite DBs strictly read-only (`?mode=ro` URI mode with `sqlite_master` checks).
- Optional `--job-search-dir` defaults to `None` (never implicitly touches `~/job-search` in unit tests). Missing/malformed optional files warn-and-skip; missing/unreadable explicit `--gmail-jsonl` exits `1`.
- Auto-resolves `candidate_emails` from `Settings.profile.email` (`--data-dir / jobpilot.toml` if readable), `--data-dir / profile.json`, and repeatable `--candidate-email`.
- Parses `--gmail-jsonl` reading ONLY `id`, `From`, `Reply-To`, `Subject`, `Date` (explicitly discarding `To`, `Cc`, `Delivered-To`, `snippet`, `body`).
- Verified relative paths under `--job-search-dir`:
  1. `DATA_DIR`: `portals.json`, `applications.db`, `opportunities.db`
  2. `five-city-250-2026-11-03/`: `employer-asks.json`, `fourth-gh-form-public-data.json`, `application-holds.json`, `submission-receipts.json`, `history-evidence.json`, `STATUS.md`, `opportunities.db`
  3. `nyc-100-2026-11-11/`: `form-preflight.json`, `follow-up-form-preflight.json`, `STATUS.md`, `FOLLOW-UP-STATUS.md`
  4. `austin-100-2026-11-11/`: `form-preflight.json`, `STATUS.md`
  5. `analysis-2026-09-14/`: `label-mail-index.json`, `complete-mail-index.json`

---

## CLI (`bridge/employers_cli.py`, mounted in `cli.py`)

Every subcommand uses `Annotated[..., typer.Option(...)]` and resolves `--store` inside the function (`store or DATA_DIR / EMPLOYERS_FILENAME`) so `JOBPILOT_DATA_DIR` and `monkeypatch.setattr(..., DATA_DIR)` work reliably.
- `jobpilot employers import`: `--dry-run`, `--store`, `--data-dir`, `--job-search-dir`, `--gmail-jsonl`, `--candidate-email`. Uses `TemporaryDirectory` + `sqlite3.backup` on `--dry-run`.
- `jobpilot employers collect`: `--dry-run`, `--store`, `--ledger`, `--portals`, `--limit`, `--provider`, `--company`, `--delay-ms`, `--refresh-existing`, `--sample-boards/--no-sample-boards`. Always reads `--ledger` (`opportunities.db`) strictly read-only via `read_opportunities(ledger_path)` passed into `collect_employer_schemas(..., ledger=None, extra_targets=...)`, and filters `board_targets` by `--provider`, `--company`, and already-present boards before calling `sample_board_opportunities`.
- `jobpilot employers status`: `--store`.
- `jobpilot employers list`: `--store`, `--gate`, `--provider`, `--tier`, `--has-direct-hr`, `--limit`.
- `jobpilot employers show <company>`: `--store`.
- `jobpilot employers export`: `--store`, `--out-dir` (default resolved inside command as `DATA_DIR / "employer-intel"`). Writes `EMPLOYERS.md`, `employers.json`, `requirements.csv`, `hr-contacts.csv` via `core.atomic_io.atomic_write_text` (`0o600`), prefixing CSV cells beginning with `=`, `+`, `-`, `@`, `\t`, `\r` with `'`.

---

## Acceptance Checks

1. `tests/engine/test_architecture.py` and `tests/test_data_dir.py` (including `SQLiteEmployerStore.in_data_dir`) pass.
2. Offline tests (`tests/engine/test_employers_domain.py`, `tests/engine/test_sqlite_employers.py`, `tests/engine/test_ats_forms.py`, `tests/bridge/test_employers_cli.py`) pass using synthetic `@example.com` data and `tmp_path`, leaving real `data/` untouched.
3. Full checks inside `$WT` with `PYTHONPATH="$(dirname "$PWD")"`:
   - `/Users/vartny/code/jobpilot/.venv/bin/ruff check ...`
   - `/Users/vartny/code/jobpilot/.venv/bin/ruff format --check ...`
   - `PYTHONPATH=/Users/vartny/.codex/worktrees/employer-requirements-index /Users/vartny/code/jobpilot/.venv/bin/python -m pytest tests/ -q`
   - `node --test tests/swipe_actions.test.cjs`
4. `agent-task finish --repo /Users/vartny/.codex/worktrees/employer-requirements-index/jobpilot ...` completes cleanly (`COMPLETE`).
5. Live collection run with explicit `PYTHONPATH=/Users/vartny/.codex/worktrees/employer-requirements-index`, `JOBPILOT_DATA_DIR=/Users/vartny/code/jobpilot/data`, and `--out-dir /Users/vartny/job-search/employer-intel` populates `/Users/vartny/code/jobpilot/data/employers.db` and `/Users/vartny/job-search/employer-intel/{EMPLOYERS.md, employers.json, requirements.csv, hr-contacts.csv}` while keeping `$WT` clean and `COMPLETE`.
