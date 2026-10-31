# SkillFair

**Hiring on What You Can Do, Not Where You've Been.**

SkillFair is a hackfest prototype exploring a skills-first recruitment workflow. It helps recruiters inspect skills supported by resume evidence, compare them with role requirements, review potential wording concerns, and keep human review central. It is an advisory prototype, not an automated hiring system.

## Problem and approach

Traditional screening can over-rely on career history, credentials, and resume keywords. That can make relevant capabilities harder to recognize, including transferable skills and skills gained outside a conventional career path. SkillFair demonstrates a workflow that surfaces evidence and potential concerns for recruiter review. It does not prove competence, determine fairness, rank candidates, or make hiring decisions.

## Technology

- Frontend: React, TypeScript, Vite, Tailwind CSS, Recharts, Lucide icons.
- Backend: Python, FastAPI, Pydantic.
- Database foundation: SQLAlchemy 2.x, PostgreSQL/psycopg and Alembic initial schema are prepared; the migration has not been applied and application features still use their current in-memory stores.
- Resume extraction: PDF and DOCX text extraction with temporary multipart spooling as needed; uploads are not intentionally retained as permanent application files.
- Optional Skills Discovery providers: Google Gemini or Groq, configured only on the backend. Local deterministic analysis remains the default.
- Backend tests: pytest. Frontend checks: ESLint and TypeScript/Vite production build.

## Implemented capabilities

### Skills Discovery and Skill Passport

`POST /api/skills/discover` accepts a PDF or DOCX resume (up to the configured upload limit), extracts text, and can use an explicitly configured Gemini provider to identify skills with supporting excerpts. Explicit evidence and inferred, unverified skills are distinguished. Without a configured provider, deterministic local analysis is available. The response returns a character count, not the full extracted resume text. Validated skill evidence is temporarily associated with an opaque `analysis_id` for later backend requests; the provenance store does not retain the full resume text.

The orchestrator presents a compact Skill Passport summary from that validated analysis. It is not a persistent candidate profile or verified credential wallet.

### Job Matching

`POST /api/jobs/match` compares a referenced analysis with recruiter-entered required skills using deterministic, case-insensitive normalized exact-name matching. It returns supported evidence, missing-from-analysis requirements, counts, and an evidence coverage calculation. Coverage is not a competence score, qualification judgment, or hiring outcome. Potentially adjacent skills are handled separately by the transferability and inclusive matching features.

### Job Requirement Audit / Bias Audit

`POST /api/jobs/audit` runs deterministic checks for selected credential requirements, potentially exclusionary wording, vague requirements, and experience thresholds. Findings identify potential concerns for human review. A finding is not proof of discrimination or unfairness and does not establish legal compliance; absence of findings does not establish fairness.

### Transferable Skills

`POST /api/skills/transferability` can compare a source role with a target role and optional target requirements. With an `analysis_id`, it uses server-held validated evidence; without one, it provides general role exploration without candidate-specific claims. Exact name matches are separate from a small set of curated adjacent-capability relationships. A potential transfer does not mean two skills are equivalent or prove qualification.

### Inclusive Matching

`POST /api/jobs/inclusive-match` evaluates essential and preferred requirements against server-held evidence and returns requirement-level evidence statuses, explanations, applicable advisory audit findings, and human-review prompts. It does not infer sensitive characteristics, rank candidates, automatically reject candidates, or establish that a process is fair.

### Learning Pathways

`POST /api/learning/pathways` returns deterministic practice exercises, project prompts, and ways to demonstrate requested skills. Recommendations are advisory activities, not verified courses, certifications, completed training, or proof of competence. When linked to an analysis they distinguish explicit, inferred, and not-evidenced states; missing resume evidence does not prove missing ability.

### Market Intelligence

`POST /api/market/intelligence` accepts a job title and optional geography. The endpoint currently returns `data_availability: "unavailable"`, empty sources and insights, and an explanatory message because no verified market-data provider is connected. It does not fabricate statistics, salary information, job counts, demand trends, or employer details. A provider interface is available for a future source integration.

Potential future source inputs include O*NET occupational skills data, BLS employment projections and wage statistics, and ESCO occupation-to-skill classifications. These sources have different geographic coverage, update cycles, licensing and meanings; occupational taxonomies or projections must not be presented as live job-posting demand. Any adapter must preserve source dates, attribution, coverage and limitations. See the [O*NET database and license information](https://www.onetcenter.org/database.html), [BLS employment projections data](https://www.bls.gov/emp/documentation/data-overview.htm), and [ESCO API documentation](https://esco.ec.europa.eu/en/about-esco/escopedia/escopedia/esco-api). No source is currently connected.

### Impact Measurement

Impact Measurement now shows authenticated, per-account aggregates. Review timestamps, decisions and confirmed Learning Pathways outcomes are derived from persisted workflow records; voluntary recruiter ratings and manually entered baselines are persisted with the signed-in account. Feedback and baselines are eligible for reporting for up to 90 days. Minimum-sample and unavailable states are preserved. Candidate feedback persistence is deferred until a separate candidate identity or reviewed anonymous-submission design exists. Free-text comments are not accepted or stored.

Impact metrics are descriptive account-level prototype measurements. Account association is not independent real-world identity verification, and the dashboard is not an organization-wide view. Expired recruiter feedback and baseline rows are excluded and pruned on that account's next Impact API access; a dormant account's expired rows may remain stored until then. No resume, evidence excerpt, candidate identifier, reviewer notes, or hiring outcome is collected in measurement records. Metrics do not prove fairness, causation, improved hiring outcomes, or candidate competence. No real pilot impact is established.

### Career Orchestrator

`POST /api/career/orchestrator/runs` coordinates the Skill Passport summary, Market Intelligence status, Job Audit, Inclusive Matching, pending Human Review, and blocked Learning Pathways. Authenticated workflows and review decisions are stored in PostgreSQL and scoped to the account session. `GET /api/career/orchestrator/runs` lists the current account's saved workflows for recovery after login; `GET /api/career/orchestrator/runs/{run_id}` retrieves one owner-scoped workflow. `POST /api/career/orchestrator/runs/{run_id}/review` accepts an explicit `proceed`, `revise`, or `stop` decision and reviewed requirement identifiers. Learning activities are generated only after a committed `proceed`; revise and stop do not generate them. The decision is associated with the authenticated account, but this is not independent recruiter identity verification or an immutable production audit record. Free-text review notes are not persisted.

Recruiter Review presents the current backend-backed Career Orchestrator workflow and reuses its review controls. It is not a multi-candidate queue. The Overview dashboard is illustrative: its names, figures, chart points, activity, and profiles are sample content, not live candidate records or measured outcomes.

## API endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Backend health |
| POST | `/api/skills/discover` | Upload a resume and return validated skill evidence; full extracted text is not returned |
| GET | `/api/skills/analyses/latest` | Retrieve the authenticated account's latest saved analysis and validated evidence |
| POST | `/api/jobs/match` | Deterministic exact-name requirement matching |
| POST | `/api/jobs/audit` | Advisory deterministic job-criteria audit |
| POST | `/api/skills/transferability` | Exact and curated role-to-role skill exploration |
| POST | `/api/jobs/inclusive-match` | Evidence-aware essential/preferred requirement review |
| POST | `/api/learning/pathways` | Deterministic learning activity suggestions |
| POST | `/api/market/intelligence` | Return verified market data when a provider exists; currently unavailable |
| GET | `/api/impact/summary` | Retrieve authenticated account-only aggregate metrics with optional date and category filters |
| POST | `/api/impact/reviews/{run_id}/start` | Record an idempotent authenticated Human Review start on the persistent workflow |
| POST | `/api/impact/feedback/recruiter` | Persist voluntary recruiter ratings with the authenticated account for aggregate reporting |
| POST | `/api/impact/feedback/candidate` | Return 410; persistent candidate feedback is deferred pending a candidate identity or reviewed anonymous design |
| POST | `/api/impact/baseline-observations` | Persist an authenticated account's manually entered baseline duration for optional aggregate comparison |
| POST | `/api/career/orchestrator/runs` | Create a recruiter-led workflow run |
| GET | `/api/career/orchestrator/runs` | List the authenticated account's saved workflows |
| GET | `/api/career/orchestrator/runs/{run_id}` | Retrieve an owner-scoped workflow |
| POST | `/api/career/orchestrator/runs/{run_id}/review` | Record proceed, revise, or stop for a workflow run |

Skills Discovery, analysis lookup, matching against saved candidate evidence, Learning Pathways with an analysis reference, Transferable Skills with an analysis reference, and Career Orchestrator routes require an authenticated account. State-changing analysis/workflow requests also require the existing CSRF and exact-origin checks. Analysis and run IDs are locators, not authentication.

Request and response field definitions are maintained in `backend/app/models.py`; implementation details and current behavior are documented in [backend/README.md](backend/README.md). The frontend calls the `/api` prefix and the Vite development server proxies it to the local FastAPI service.

## Local setup

Prerequisites: Python 3.11 or newer, Node.js with npm, and a modern browser.

### Backend

From the repository root in PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
.\run-local.ps1
```

The local launcher uses the absolute backend `.env` path and sets the exact Vite development origins for its process, avoiding stale inherited origin configuration. It is local-development-only; production must use an explicit deployment origin allowlist. Never replace an existing `.env` file as part of setup.

### Frontend

In a second terminal:

```powershell
cd C:\SkillFair
npm install
npm run dev
```

Open the local address printed by Vite. The API is available at `http://127.0.0.1:8000`; health check: `http://127.0.0.1:8000/api/health`.

## Optional external AI configuration

Skills Discovery defaults to local deterministic analysis and works without an AI provider. An administrator may select `SKILLFAIR_LLM_PROVIDER=gemini` or `SKILLFAIR_LLM_PROVIDER=groq` in the backend environment. For Gemini, use `SKILLFAIR_LLM_BASE_URL`, `SKILLFAIR_LLM_API_KEY`, and `SKILLFAIR_LLM_MODEL`. For Groq, use the separate `SKILLFAIR_GROQ_API_KEY`, optional `SKILLFAIR_GROQ_MODEL`, and optional `SKILLFAIR_GROQ_MAX_COMPLETION_TOKENS`; the Groq endpoint is fixed by the backend. Never reuse one provider's key for the other. Keep credentials exclusively in the backend environment; never put them in frontend variables, source code, chat, screenshots, or version control. Restart the backend after changing configuration.

The frontend identifies the configured provider without receiving credentials. Before each AI-assisted analysis, the recruiter must explicitly consent to sending the extracted resume text to that selected provider. No provider switch or local fallback is silent. Automated provider tests are mocked; they do not establish that a live account, model, quota, or generation request works. See [backend/README.md](backend/README.md) and [backend/.env.example](backend/.env.example) for the complete variable names and setup details.

The repository ignores `.env` files and resume documents. Do not commit real resumes or other personal data.

## Validation

From the repository root:

```powershell
py -m pytest -q backend\tests
npm run lint
npm run build
```

Frontend Node-based tests are available through `npm run test:session` (session persistence), `npm run test:review` (Recruiter Review logic), and `npm run test:impact` (Impact Measurement data and server-rendered disclosure checks). The impact UI check is not an interactive browser test. `npm run lint` and `npm run build` provide lint and production-build checks. No automated browser-test script is configured.

## Prototype limitations and responsible use

- **Identity assurance:** authentication, owner-scoped records, and durable workflow decisions are implemented. Account registration does not independently verify a person's real-world identity; a workflow review attests to the signed-in account only. Review notes are deliberately not persisted, and the review record is not an immutable production audit trail.
- **Persistent data retention:** validated skill evidence, analyses, workflow snapshots, and review decisions persist in PostgreSQL without an automatic expiry. Account deletion and configurable retention/deletion workflows are not implemented. Original resume files and full extracted resume text are not stored.
- **Impact Measurement is prototype-level:** summaries are authenticated and scoped to one account, but are not organization-wide and do not independently verify a person's real-world identity. Recruiter ratings and baseline observations are reportable for up to 90 days; expired rows are not automatically purged. Candidate feedback persistence is deferred. Workflow review timestamps and decisions follow workflow-history retention and are not subject to the 90-day feedback window.
- **No live market statistics:** no verified market-data provider is configured. The Market Intelligence endpoint reports unavailable rather than inventing data.
- **No SAP integration:** no SAP connection, credentials, service permissions, or SAP-backed data has been configured or demonstrated. SAP remains future scope.
- **No proven fairness or impact:** the deterministic audit has limited rule coverage; it does not measure disparate outcomes or prove fairness/legal compliance. No recruiter pilot, baseline, candidate feedback, shortlist-quality study, screening-time study, or selection-outcome analysis has established hiring impact.
- **Evidence limits:** extracted excerpts and inferred skills require human interpretation. Missing evidence is not proof of missing ability. Exact skill-name matching is not semantic matching or a proficiency assessment.
- **Resume processing:** uploads are not intentionally stored as permanent application files. Starlette's multipart parser uses a spooled temporary file with an approximately 1 MiB in-memory threshold; larger uploads may temporarily spill to the operating system's temporary directory. The endpoint closes upload files in its cleanup path on completion or endpoint-side failure, and Starlette handles cleanup if multipart parsing fails. Deployment operators should use a suitably permission-restricted temporary directory and review its cleanup and storage policies. The application does not claim that temporary storage is encrypted. The Skills Discovery response does not return full extracted resume text, and the provenance store does not store it; validated evidence excerpts may be returned and retained temporarily.
- **Prototype security:** account authentication and ownership checks are present, but production protections still require review, including verified recruiter identity, account recovery, data-retention/deletion controls, security monitoring, and deployment-level safeguards.

SAP or another market-data provider may later connect through the market-data provider boundary after access, permissions, data provenance and source quality are established. That future integration must not be represented as available until it is actually connected and verified.

### SAP future-readiness boundary

There is no SAP adapter, SAP credential setting, tenant connection, or SAP-backed workflow in this repository. The only existing provider boundary that is directly reusable is `MarketDataProvider`, whose response must satisfy the typed `MarketDataSnapshot` provenance contract in `backend/app/market_intelligence.py`. It is not an SAP connector. A future approved SAP integration would need the specific SAP product and API, tenant/environment, OAuth or other supported credentials and scopes, data access approvals, rate limits, and source-retention rules before implementation. It would also need an explicit mapping from approved SAP requisition or market data into SkillFair's existing request and source models, with validation and attribution tests. Candidate/profile import, ownership, authentication, and tenant isolation are not defined by the current prototype and require a separate security design. Without SAP access, the team can prepare only documented field mappings, synthetic contract fixtures, and adapter tests; none should be described as a live integration.

## License

No open-source license has been added. All rights are reserved unless a license is provided.
