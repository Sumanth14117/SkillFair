# SkillFair

**Hiring on What You Can Do, Not Where You've Been.**

SkillFair is a hackfest prototype exploring a skills-first recruitment workflow. It helps recruiters inspect skills supported by resume evidence, compare them with role requirements, review potential wording concerns, and keep human review central. It is an advisory prototype, not an automated hiring system.

## Problem and approach

Traditional screening can over-rely on career history, credentials, and resume keywords. That can make relevant capabilities harder to recognize, including transferable skills and skills gained outside a conventional career path. SkillFair demonstrates a workflow that surfaces evidence and potential concerns for recruiter review. It does not prove competence, determine fairness, rank candidates, or make hiring decisions.

## Technology

- Frontend: React, TypeScript, Vite, Tailwind CSS, Recharts, Lucide icons.
- Backend: Python, FastAPI, Pydantic.
- Persistence: SQLAlchemy 2.x, PostgreSQL/psycopg and Alembic. The migration chain is `202610310001` → `202611190001` → `202611190002`, adding the base schema, saved standalone Bias Audits and candidate feedback, then account-owned workflow notifications. Registered account-owned analysis, workflow, review, and impact routes use PostgreSQL. In-memory stores remain only for isolated domain/test adapters.
- Resume extraction: PDF and DOCX text extraction with temporary multipart spooling as needed; uploads are not intentionally retained as permanent application files.
- Optional Skills Discovery providers: Google Gemini or Groq, configured only on the backend. Local deterministic analysis remains the default.
- Backend tests: pytest. Frontend checks: TypeScript type checking, ESLint, Node-based frontend tests, and the Vite production build.

### Migration status by environment

- **Isolated PostgreSQL integration-test database** (`127.0.0.1:5440/skillfair_test_db`): revision `202611190002` was previously verified. This database is reserved for the guarded PostgreSQL integration suite; it is not the Playwright/browser E2E database.
- **Development database:** migration revision unknown / unverified.
- **Hosted Neon database:** migration revision unknown / unverified.

Endpoints backed by M2.6 or M2.7 tables require the corresponding migrations to be applied in the specific environment being used. Do not infer one environment's schema state from another.

## Implemented capabilities

### Skills Discovery and Skill Passport

`POST /api/skills/discover` accepts a PDF or DOCX resume (up to the configured upload limit), extracts text, and can use an explicitly configured Gemini provider to identify skills with supporting excerpts. Explicit evidence and inferred, unverified skills are distinguished. Without a configured provider, deterministic local analysis is available. The response returns a character count, not the full extracted resume text. Validated skill evidence is temporarily associated with an opaque `analysis_id` for later backend requests; the provenance store does not retain the full resume text.

Job Audit requests use the shared authenticated API client, including cookies, CSRF, account-change cancellation, and a bounded request timeout. Resume uploads retain upload-progress reporting and use the same timeout bound; a timeout is not automatically retried because the server may have completed the analysis. Check saved Skills Discovery results before manually retrying a timed-out upload.

The orchestrator presents a compact Skill Passport summary from that validated analysis. It is not a persistent candidate profile or verified credential wallet.

### Job Matching

`POST /api/jobs/match` compares a referenced analysis with recruiter-entered required skills using deterministic, case-insensitive normalized exact-name matching. It returns supported evidence, missing-from-analysis requirements, counts, and an evidence coverage calculation. Coverage is not a competence score, qualification judgment, or hiring outcome. Potentially adjacent skills are handled separately by the transferability and inclusive matching features.

### Job Requirement Audit / Bias Audit

`POST /api/jobs/audit` runs deterministic checks for selected credential requirements, potentially exclusionary wording, vague requirements, experience thresholds, and explicit employment-continuity restrictions such as continuous-experience requirements or prohibitions on employment gaps. Findings identify potential concerns for human review. A finding is not proof of discrimination or unfairness and does not establish legal compliance; absence of findings does not establish fairness. The authenticated Bias Audit workspace can save the deterministic result and per-finding review selections. It retains the result snapshot and job title, not the full job description; saved audit history is owner-scoped and can be restored or deleted.

### Transferable Skills

`POST /api/skills/transferability` can compare a source role with a target role and optional target requirements. With an `analysis_id`, it uses server-held validated evidence; without one, it provides general role exploration without candidate-specific claims. Exact name matches are separate from a small set of curated adjacent-capability relationships. A potential transfer does not mean two skills are equivalent or prove qualification.

### Inclusive Matching

`POST /api/jobs/inclusive-match` evaluates essential and preferred requirements against server-held evidence and returns requirement-level evidence statuses, explanations, applicable advisory audit findings, and human-review prompts. It does not infer sensitive characteristics, rank candidates, automatically reject candidates, or establish that a process is fair.

### Learning Pathways

`POST /api/learning/pathways` returns deterministic practice exercises, project prompts, and ways to demonstrate requested skills. Recommendations are advisory activities, not verified courses, certifications, completed training, or proof of competence. When linked to an analysis they distinguish explicit, inferred, and not-evidenced states; missing resume evidence does not prove missing ability.

### Market Intelligence

`POST /api/market/intelligence` accepts a job title, a provider-supported country code (India is the default), optional city/region, and up to five recruiter-entered skill keywords. `GET /api/market/provider-status` reports whether an approved provider is configured and reachable without returning credentials. Adzuna is implemented as an optional provider, but is disabled by default and requires server-side `SKILLFAIR_ADZUNA_APP_ID`, `SKILLFAIR_ADZUNA_APP_KEY`, `SKILLFAIR_MARKET_PROVIDER=adzuna`, and an explicit `SKILLFAIR_ADZUNA_USAGE_APPROVED=true` operator attestation. Its current API can report role-title and role-plus-entered-keyword ad counts for supported country codes; counts are limited provider index observations, not market censuses. No live provider was configured or called during this implementation. The typed boundary validates source attribution, timezone-aware retrieval times, observation periods, calculation methods, and data-quality labels. Valid snapshots are cached in bounded process memory for 15 minutes; after a provider outage, eligible results may be served stale for up to 24 hours with an explicit warning. Cache entries disappear on restart and are not shared across workers.

The module explicitly reports that emerging/declining skill histories, provider-verified related/transferable-skill relationships, and candidate skill gaps are unavailable from current Adzuna search counts. Those claims are not inferred from keyword co-occurrence or vacancy counts. The existing separate Transferable Skills and Inclusive Matching modules remain the routes for curated adjacent-capability exploration and analysis-linked requirement evidence, respectively. Provider use remains blocked until the operator has confirmed the applicable Adzuna terms; those terms require written agreement for ongoing commercial analysis. Official provider documentation: [API overview](https://developer.adzuna.com/overview), [search endpoint](https://developer.adzuna.com/docs/search), [supported country codes in the live OpenAPI specification](https://developer.adzuna.com/swagger/spec/test2.json), and [terms of service](https://developer.adzuna.com/docs/terms_of_service).

Potential future sources include O*NET occupational skills data, BLS employment projections and wage statistics, and ESCO occupation-to-skill classifications. Taxonomies and projections must not be presented as live job-posting demand. The optional Adzuna adapter is disabled until server-side credentials and the operator's permitted-use confirmation are configured. Its terms require written permission for ongoing commercial analysis; trial evaluation by an organization is limited to 14 days. India is a supported API country code, but actual India listing coverage and quality remain unverified. No live provider request was made during this implementation or testing. See [Adzuna API overview](https://developer.adzuna.com/overview), [search API](https://developer.adzuna.com/docs/search), [country-code specification](https://developer.adzuna.com/swagger/spec/test2.json), and [terms](https://developer.adzuna.com/docs/terms_of_service), as well as [O*NET licensing](https://www.onetcenter.org/database.html), [BLS projections](https://www.bls.gov/emp/documentation/data-overview.htm), and [ESCO API documentation](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/esco-api).

### Impact Measurement

Impact Measurement shows authenticated, per-account aggregates. Review timestamps, decisions and confirmed Learning Pathways outcomes are derived from persisted workflow records; voluntary recruiter ratings, candidate feedback and manually entered baselines are associated with the signed-in account. Feedback and baselines are eligible for reporting for up to 90 days. Candidate feedback records application experience, process accessibility, job-requirements clarity and perceived fairness as separate 1–5 measures, plus optional controlled improvement themes. No free-text comments or candidate identifiers are collected. Feedback is not anonymous, and SkillFair does not verify that a respondent is a candidate. Candidate dimensions and theme counts are reported separately, suppressed below the five-submission threshold, and scoped to the signed-in account. These self-reported measures do not affect hiring decisions and do not establish fairness, causation or improved hiring outcomes.

Impact metrics are descriptive account-level prototype measurements. Account association is not independent real-world identity verification, and the dashboard is not an organization-wide view. Expired recruiter feedback and baseline rows are excluded and pruned on that account's next Impact API access; a dormant account's expired rows may remain stored until then. No resume, evidence excerpt, candidate identifier, reviewer notes, or hiring outcome is collected in measurement records. Metrics do not prove fairness, causation, improved hiring outcomes, or candidate competence. No real pilot impact is established.

### Career Orchestrator

`POST /api/career/orchestrator/runs` coordinates the Skill Passport summary, Market Intelligence status, Job Audit, Inclusive Matching, pending Human Review, and blocked Learning Pathways. Authenticated workflows and review decisions are stored in PostgreSQL and scoped to the account session. `GET /api/career/orchestrator/runs?limit=20` lists up to 20 of the signed-in account's unexpired workflows, newest first, with only the persisted run ID, job title, status, review decision and timestamps. It omits the job description, skills lists and detailed workflow snapshot. The Career Orchestrator's Recent Workflows section lets the owner reopen a selected run; reopening fetches that exact owner-scoped `run_id` through `GET /api/career/orchestrator/runs/{run_id}`, which returns the detailed workflow and saved inputs needed to continue that exact run. There is no latest-workflow fallback. `POST /api/career/orchestrator/runs/{run_id}/review` accepts an explicit `proceed`, `revise`, or `stop` decision and reviewed requirement identifiers. Learning activities are generated only after a committed `proceed`; revise and stop do not generate them. The decision is associated with the authenticated account, but this is not independent recruiter identity verification or an immutable production audit record. Free-text review notes are not persisted.

The signed-in Overview uses `GET /api/dashboard/overview`. It reports counts of unexpired saved analyses, unexpired workflows, pending reviews, and recorded review decisions, plus distributions from persisted workflow and review states. Recent activity is limited to persisted workflow creation and review start/completion timestamps; recent workflow cards show persisted title, state, and timestamps. Queries are scoped to the authenticated account. The application has no candidate entity, so saved analysis counts are not presented as unique candidates. No resume text, job description, internal database IDs, or other account's records are returned. Empty accounts receive zero counts and empty lists; database failures return a safe unavailable response. There is no durable event log, so activity outside these workflow/review timestamps is not shown.

Recruiter Review presents the current backend-backed Career Orchestrator workflow and reuses its review controls. It is not a multi-candidate queue. Overview displays account-owned persisted analysis and workflow counts, workflow/review-state distributions, and workflow/review activity only. It does not show unique candidate counts, candidate profiles, matching outcomes, or business impact because those are not supported by the persisted data model.

## API endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Backend health |
| POST | `/api/skills/discover` | Upload a resume and return validated skill evidence; full extracted text is not returned |
| GET | `/api/skills/analyses/latest` | Retrieve the authenticated account's latest saved analysis and validated evidence |
| GET | `/api/skills/analyses/{analysis_id}` | Retrieve one exact owner-scoped saved analysis and validated evidence |
| GET | `/api/search?q=...` | Search bounded account-owned saved skill labels, workflow titles and Bias Audit titles |
| GET | `/api/notifications?limit=20` | List the authenticated account's persisted workflow-event notifications |
| POST | `/api/notifications/{notification_id}/read` | Mark an owned notification as read (Origin and CSRF protected) |
| POST | `/api/jobs/match` | Deterministic exact-name requirement matching |
| POST | `/api/jobs/audit` | Advisory deterministic job-criteria audit |
| POST | `/api/jobs/audits` | Authenticated, CSRF-protected deterministic audit save; stores the result snapshot and review selections, not the full job description |
| GET | `/api/jobs/audits?limit=20` | Owner-scoped saved audit history (up to 30 entries) |
| GET | `/api/jobs/audits/{audit_id}` | Owner-scoped saved audit result and review selections |
| PATCH | `/api/jobs/audits/{audit_id}/findings/{finding_index}` | Owner-scoped, CSRF-protected review selection update |
| DELETE | `/api/jobs/audits/{audit_id}` | Owner-scoped, CSRF-protected saved audit deletion |
| POST | `/api/skills/transferability` | Exact and curated role-to-role skill exploration |
| POST | `/api/jobs/inclusive-match` | Evidence-aware essential/preferred requirement review |
| POST | `/api/learning/pathways` | Deterministic learning activity suggestions |
| POST | `/api/market/intelligence` | Return verified market data when a provider exists; currently unavailable |
| GET | `/api/market/provider-status` | Report configured provider availability without returning credentials |
| GET | `/api/impact/summary` | Retrieve authenticated account-only aggregate metrics with optional date and category filters |
| POST | `/api/impact/reviews/{run_id}/start` | Record an idempotent authenticated Human Review start on the persistent workflow |
| POST | `/api/impact/feedback/recruiter` | Persist voluntary recruiter ratings with the authenticated account for aggregate reporting |
| POST | `/api/impact/feedback/candidate` | Authenticated, CSRF-protected candidate-experience survey; account-associated, idempotent, aggregate-only response |
| POST | `/api/impact/baseline-observations` | Persist an authenticated account's manually entered baseline duration for optional aggregate comparison |
| POST | `/api/career/orchestrator/runs` | Create a recruiter-led workflow run |
| GET | `/api/career/orchestrator/runs` | List the authenticated account's saved workflows |
| GET | `/api/career/orchestrator/runs/{run_id}` | Retrieve an owner-scoped workflow |
| POST | `/api/career/orchestrator/runs/{run_id}/review` | Record proceed, revise, or stop for a workflow run |
| GET | `/api/dashboard/overview` | Retrieve authenticated account-scoped saved analysis/workflow metrics and recent workflow/review activity |
| GET | `/api/account/data` | List paged metadata for the signed-in account's saved analyses and workflows |
| GET | `/api/account/export` | Download a bounded JSON export of supported account-owned records |
| DELETE | `/api/account/analyses/{analysis_id}` | Permanently delete an owned analysis when no workflows still reference it |
| DELETE | `/api/account/workflows/{workflow_id}` | Permanently delete an owned workflow and its schema-linked review records |

Global Search is a bounded substring search of saved skill names and saved record titles; it does not search resume excerpts, full resume text or job descriptions. Selecting a saved analysis opens that exact owner-scoped analysis; workflow results load the selected `run_id`, and saved Bias Audit results load the selected audit. Search results are capped at 20.

Notifications are created transactionally from actual persisted Human Review decisions. A Learning Pathways notification is added only for a completed result committed with Proceed. Notifications are owner-scoped, duplicate event keys are constrained per owner, the list is capped at 100 retained rows per account, and the API exposes only notification summaries. Mark-as-read is an authenticated CSRF-protected operation.

Settings currently support only a browser-local display name, stored under a key scoped to the signed-in account ID. It is not a server-side account profile setting and does not synchronize across browsers. Theme and notification preferences, password recovery, MFA and email verification are not implemented. There is one personal workspace per account; organization/workspace switching is not supported. Help Center guidance is in-app documentation and describes prototype limitations.

Skills Discovery, analysis lookup, matching against saved candidate evidence, Learning Pathways with an analysis reference, Transferable Skills with an analysis reference, and Career Orchestrator routes require an authenticated account. State-changing analysis/workflow requests also require the existing CSRF and exact-origin checks. Analysis and run IDs are locators, not authentication.

Request and response field definitions are maintained in `backend/app/models.py`; implementation details and current behavior are documented in [backend/README.md](backend/README.md). The frontend calls the `/api` prefix and the Vite development server proxies it to the local FastAPI service.

## Local setup

Prerequisites: Python 3.14.7 (the version used to resolve and test the backend dependency lock), Node.js 22 with npm, and a modern browser. The backend runtime dependency ranges support Python 3.11+, but the pinned test lock is validated on Python 3.14.7; regenerate and validate the lock separately before using another Python version.

### Backend

From the repository root in PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-lock.txt
.\run-local.ps1
```

The backend reads `DATABASE_URL` for the runtime role and `SKILLFAIR_MIGRATION_DATABASE_URL` for Alembic. Configure the first with the restricted application role and the second with the migration role; neither URL belongs in frontend configuration or version control. The local launcher uses the absolute backend `.env` path and sets the exact Vite development origins for its process, avoiding stale inherited origin configuration. It is local-development-only; production must use an explicit deployment origin allowlist. Never replace an existing `.env` file as part of setup. The API does not create tables at startup. Before applying a migration, verify the migration URL's host, port, and database, then review migration history and the target revision.

### Frontend

In a second terminal:

```powershell
cd C:\SkillFair
npm ci
npm run dev
```

Open the local address printed by Vite. The API is available at `http://127.0.0.1:8000`; health check: `http://127.0.0.1:8000/api/health`.

## Optional external AI configuration

Skills Discovery defaults to local deterministic analysis and works without an AI provider. An administrator may select `SKILLFAIR_LLM_PROVIDER=gemini` or `SKILLFAIR_LLM_PROVIDER=groq` in the backend environment. For Gemini, use `SKILLFAIR_LLM_BASE_URL`, `SKILLFAIR_LLM_API_KEY`, and `SKILLFAIR_LLM_MODEL`. For Groq, use the separate `SKILLFAIR_GROQ_API_KEY`, optional `SKILLFAIR_GROQ_MODEL`, and optional `SKILLFAIR_GROQ_MAX_COMPLETION_TOKENS`; the Groq endpoint is fixed by the backend. Never reuse one provider's key for the other. Keep credentials exclusively in the backend environment; never put them in frontend variables, source code, chat, screenshots, or version control. Restart the backend after changing configuration.

The frontend identifies the configured provider without receiving credentials. Before each AI-assisted analysis, the recruiter must explicitly consent to sending the extracted resume text to that selected provider. No provider switch or local fallback is silent. Automated provider tests are mocked; they do not establish that a live account, model, quota, or generation request works. See [backend/README.md](backend/README.md) and [backend/.env.example](backend/.env.example) for the complete variable names and setup details.

The repository ignores `.env` files and resume documents. Do not commit real resumes or other personal data.

## Validation

Install dependencies first: run `npm ci` from the repository root and `python -m pip install -r requirements-lock.txt` from `backend` (or follow the backend setup above). The range-based `requirements-dev.txt` is available for local dependency resolution; CI uses the pinned lock. Then run the backend checks from `backend`, followed by the frontend checks from the repository root.

Backend checks:

```powershell
python -m compileall -q app tests
python -c "import app.main"
python -m pytest -q tests/test_auth.py tests/test_error_handlers.py tests/test_protections.py tests/test_security_headers.py
python -m pytest -q tests --ignore=tests/test_postgres_persistence_integration.py
python -m pytest -q tests
```

The individual frontend scripts (`test:auth`, `test:api-client`, `test:identity`, `test:session`, `test:workflow-restoration`, `test:workflow-history`, `test:review`, and `test:impact`) remain available for focused runs. These Node tests exercise client/state logic and server-rendered UI states; they are not interactive browser tests. Playwright browser tests run with `npm run test:e2e` and use deterministic synthetic API responses.

Frontend checks, from the repository root:

```powershell
npm run typecheck
npm run lint
npm test
npm run build
```

### Browser E2E tests

Install the pinned Playwright Chromium browser once with `npm run test:e2e:install`, then run `npm run test:e2e`. Playwright starts a dedicated Vite server at `127.0.0.1:4174` using `vite.e2e.config.ts`; dotenv loading is disabled and no backend proxy is configured. E2E fixtures intercept app APIs and provide deterministic synthetic account-scoped responses; unhandled requests cannot reach a backend. Journeys cover the signed-out smoke screen, authentication/session lifecycle, Skills Discovery, Career Orchestrator creation/history/review, Bias Audit save/restore, Impact Measurement states and feedback, notifications, Market Intelligence, and account switching. These tests validate frontend behavior against mocks; they do not verify FastAPI auth/CSRF middleware, database transactions, or PostgreSQL persistence. The Playwright harness does not connect to a database or fall back to `DATABASE_URL`. Any future backend-backed browser suite must require a separately selected E2E configuration and fail closed after validating its exact host, port, database, runtime role, and migration revision; the PostgreSQL integration-test database is not that target.

The focused backend command runs authentication and API security tests; the following command runs database-independent tests without the PostgreSQL integration module. The final command runs the full default suite, whose integration module is disabled unless `SKILLFAIR_RUN_POSTGRES_INTEGRATION=1` is explicitly set. CI explicitly sets `SKILLFAIR_RUN_POSTGRES_INTEGRATION=0`. CI does not configure an external PostgreSQL URL or service, and does not run migrations; tests may use isolated in-memory SQLite. To run PostgreSQL integration locally, follow the test module's safety guard and use only the separately provisioned dedicated SkillFair test database; its configuration must identify the approved host, port, database and runtime role before any test writes. Never point this suite at development, production, or Permission-Aware RAG databases. PostgreSQL integration should be invoked separately from the default checks only after that target is explicitly verified. This integration database is not approved or configured for Playwright/browser E2E; use a separate isolated E2E database.

GitHub Actions CI runs on pushes and pull requests. It installs npm dependencies with `npm ci`, installs the pinned backend test environment, performs Python syntax/import checks, runs focused backend security tests and the full default backend suite, and runs frontend type checking, lint, tests and production build. CI sets PostgreSQL integration to `0`, has no external PostgreSQL service or connection URL, and performs no migrations, deployments, or artifact publishing. Its tests may use isolated SQLite. Local backup and database artifacts are ignored by Git; the frontend build emits Vite application assets and is not a database or backup packaging step.

## Prototype limitations and responsible use

- **Identity assurance:** authentication, owner-scoped records, and durable workflow decisions are implemented. Account registration does not independently verify a person's real-world identity; a workflow review attests to the signed-in account only. Review notes are deliberately not persisted, and the review record is not an immutable production audit trail.
- **Persistent data retention:** validated skill evidence, analyses, workflow snapshots, and review decisions have no automatic expiry. Signed-in users can now export supported records and permanently delete their own analyses and workflows. An analysis cannot be deleted while workflows reference it; delete those workflows first. Deleting a workflow removes its review, workflow-linked recruiter feedback, and review-duration observation through the existing foreign-key cascades, but leaves its analysis and independent baseline observations intact. Deletion removes the user's review history for that workflow and is not reversible. Recruiter feedback and baselines retain their existing 90-day reporting/expiry behavior; this is not an automatic expiry policy for analyses/workflows. Account deletion and scheduled session cleanup are not implemented. Original resume files and complete extracted resume text are not stored.
- **Impact Measurement is prototype-level:** summaries are authenticated and scoped to one account, not organization-wide. Candidate feedback is self-reported and linked to an account, but SkillFair does not verify respondent identity as a candidate. Recruiter ratings, candidate survey responses and baseline observations are eligible for reporting for up to 90 days; expired rows are removed by owner-scoped cleanup when the account submits feedback or requests a summary. Candidate question dimensions are reported separately and suppressed until five valid responses exist within that account and selected reporting window. Workflow review timestamps and decisions follow workflow-history retention and are not subject to the 90-day feedback window.
- **Market Intelligence scope:** the Adzuna provider is optional and disabled by default. Its current search counts are limited observations, not census estimates. No live account, credentials, quota, response, India listing coverage, or commercial-use permission was verified. Emerging/declining skill histories, market-derived related/transferable relationships, and candidate skill gaps remain unavailable rather than inferred. An operator must confirm permitted use and configure server-side credentials before enabling the adapter.
- **No SAP integration:** no SAP connection, credentials, service permissions, or SAP-backed data has been configured or demonstrated. SAP remains future scope.
- **No proven fairness or impact:** the deterministic audit has limited rule coverage; it does not measure disparate outcomes or prove fairness/legal compliance. The newly available candidate survey is voluntary, self-reported, account-scoped and may not represent verified candidates. No recruiter pilot, baseline, candidate-feedback study, shortlist-quality study, screening-time study, or selection-outcome analysis has established hiring impact.
- **Evidence limits:** extracted excerpts and inferred skills require human interpretation. Missing evidence is not proof of missing ability. Exact skill-name matching is not semantic matching or a proficiency assessment.
- **Resume processing:** uploads are not intentionally stored as permanent application files. Starlette's multipart parser uses a spooled temporary file with an approximately 1 MiB in-memory threshold; larger uploads may temporarily spill to the operating system's temporary directory. The endpoint closes upload files in its cleanup path on completion or endpoint-side failure, and Starlette handles cleanup if multipart parsing fails. Deployment operators should use a suitably permission-restricted temporary directory and review its cleanup and storage policies. The application does not claim that temporary storage is encrypted. The Skills Discovery response does not return full extracted resume text, and the provenance store does not store it; validated evidence excerpts may be returned and retained temporarily.
- **Prototype security:** account authentication and ownership checks are present, but production protections still require review, including verified recruiter identity, account recovery, data-retention/deletion controls, security monitoring, and deployment-level safeguards.

### Retention and deletion recommendations

Current enforcement and recommendations are distinct. Recruiter feedback and baseline observations are limited to 90 days and lazily pruned on that account's next Impact request. Analyses/evidence, workflow inputs/snapshots, and review decisions have no automatic expiry but can be manually deleted by their authenticated owner through Data Management. Analysis deletion is blocked while any workflow references it. Workflow deletion cascades to the linked review, review-duration observations, and workflow-linked recruiter feedback; independent baselines are retained. The JSON export is capped at 250 rows per table, 1,000 total rows, and 2 MiB; requests exceeding a cap are rejected rather than returned partially. The export includes account profile, persisted analysis results and redacted evidence excerpts, workflow inputs/summaries/review decisions, and stored feedback/impact observations. It excludes original resume files and complete extracted resume text, passwords/password hashes, session and CSRF token material, internal database IDs, and non-persisted review notes. Exported excerpts use the application's targeted PII redaction, which cannot reliably detect all names, addresses, or identifiers. Sessions are time-limited but expired/revoked session rows do not have a scheduled purge. These controls do not implement account deletion or a legally determined retention schedule.

### Deployment requirements

`run-local.ps1` and Vite's development proxy are for local demonstrations only. A deployment must use HTTPS, exact HTTPS origins, Secure cookies, and a same-origin proxy or explicitly reviewed credentialed CORS. Vercel sets frontend-document security headers; the backend also adds browser security headers and `Cache-Control: no-store` to API responses, including direct service access. A TLS-terminating proxy must provide HSTS; the backend does not trust forwarded-proto headers. The backend rate limiter uses the ASGI socket peer address and intentionally ignores forwarded-client-IP headers. Behind Vercel/Render this can group requests under a proxy peer rather than identify each browser, so configure edge rate limiting; do not enable blanket forwarded-header trust. Configure edge request-body limits and read/idle timeouts too. The app's body cap and rate limiter are bounded and process-local; they are not multi-worker or edge protections.

### Controlled portfolio/demo deployment (not production)

The repository includes starter configuration for a controlled, non-commercial portfolio demonstration: Vercel Hobby serves the React frontend, same-origin `/api/*` requests are rewritten to a Render Free FastAPI service in Singapore, and the API uses a Neon PostgreSQL database in Singapore. The AI provider is disabled initially. This setup is not production hosting and is not intended for commercial or sensitive workloads.

Before deployment, verify that the destination in [`vercel.json`](vercel.json) uses the actual Render service hostname. The Render service is configured in [`render.yaml`](render.yaml) and tracks `main`; its start command uses the FastAPI `app` exported by `app.main`.

### Preview and production deployment control

Vercel's Production Branch is configured in the Vercel project dashboard; `vercel.json` does not select it. Git pushes and pull requests from other branches are Preview deployments, so a successful Preview does not make that commit live on the production domain. Before release, verify the dashboard's Production Branch and Git deployment behavior. Render is a separate deployment: the checked-in Blueprint specifies `branch: main` and `autoDeploy: false`, so merging to `main` does not by itself update the Render backend. Confirm the live Render service has been synchronized with the Blueprint, and manually deploy the intended backend revision only after the database migration and backend smoke checks are complete. Coordinate Vercel production deployment so new frontend routes do not reach an older backend. Keep production deployment gated until the target database revision and backup/recovery plan are verified.

Set these values in the Render service dashboard, without adding credentials to this repository:

- `PYTHON_VERSION`: `3.14.7`.
- `SKILLFAIR_AUTH_COOKIE_SECURE`: `true`.
- `SKILLFAIR_LLM_PROVIDER`: `none`.
- `SKILLFAIR_AUTH_ALLOWED_ORIGINS`: exact HTTPS frontend origin(s), including the deployed Vercel origin. Use comma-separated exact origins supported by the backend; do not use a wildcard.
- `DATABASE_URL`: the Neon pooled connection URL for the runtime role. Keep this value secret and do not place it in Vercel frontend variables.

Do not set `SKILLFAIR_MIGRATION_DATABASE_URL` on the Render service. For a future schema change, run Alembic from a trusted local machine or controlled CI job using a separately supplied Neon direct connection URL for the migration role. Verify its host and database before running a migration. The runtime uses the pooled URL; migration tooling should use the direct URL. No migration is run by Render startup or this deployment configuration.

After configuration, verify `GET /api/health` through the deployed same-origin `/api` route. Check `GET /api/health/database` for database reachability, then perform an authentication smoke test: unauthenticated `GET /api/auth/me` should return 401, followed by registration/login and logout using a dedicated demo account to verify cookies, CSRF handling, and session restoration. Do not use real candidate data.

Render Free services can spin down after 15 minutes without traffic and may take about a minute to wake. Frontend API requests allow up to 90 seconds for this wake-up; they are not automatically retried, and the UI advises checking state before resubmitting a timed-out action. The filesystem is ephemeral and the service may restart; do not rely on local files for persistence. Free-tier resource and availability limits make this suitable only for a controlled demo, not production. [Render documents these free-service limits](https://render.com/docs/free). Vercel's rewrite sends browser requests through the frontend origin; the `/api/*` responses are marked `Cache-Control: no-store` and rewrite caching is explicitly disabled. The Vercel frontend also receives a CSP allowing same-origin app assets and API requests plus the Google Fonts hosts used by `index.html`. See [Vercel rewrite documentation](https://vercel.com/docs/routing/rewrites) and [cache-control guidance](https://vercel.com/docs/caching/cache-control-headers).

### Dependency reproducibility and scanning

`backend/requirements.txt` contains runtime dependency ranges; `backend/requirements-dev.txt` adds test dependencies. `backend/requirements-lock.txt` pins the combined development/test environment resolved for CPython 3.14.7. The frontend lockfile is `package-lock.json`; use `npm ci`. A recommended CI check is `pip-audit --no-deps -r backend/requirements-lock.txt` plus the organization's approved npm dependency audit; review findings before updating dependencies.

SAP or another market-data provider may later connect through the market-data provider boundary after access, permissions, data provenance and source quality are established. That future integration must not be represented as available until it is actually connected and verified.

### SAP future-readiness boundary

There is no SAP adapter, SAP credential setting, tenant connection, or SAP-backed workflow in this repository. The only existing provider boundary that is directly reusable is `MarketDataProvider`, whose response must satisfy the typed `MarketDataSnapshot` provenance contract in `backend/app/market_intelligence.py`. It is not an SAP connector. A future approved SAP integration would need the specific SAP product and API, tenant/environment, OAuth or other supported credentials and scopes, data access approvals, rate limits, and source-retention rules before implementation. It would also need an explicit mapping from approved SAP requisition or market data into SkillFair's existing request and source models, with validation and attribution tests. Candidate/profile import, ownership, authentication, and tenant isolation are not defined by the current prototype and require a separate security design. Without SAP access, the team can prepare only documented field mappings, synthetic contract fixtures, and adapter tests; none should be described as a live integration.

## License

No open-source license has been added. All rights are reserved unless a license is provided.
