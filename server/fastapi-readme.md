# AgentLab FastAPI bridge

## 1. What this backend does

This backend connects a separately built frontend to Postgres and a separately supplied evaluator. It validates requests, stores project/version/test/trace records, prepares evaluator inputs, manages temporary encrypted credentials, and delivers job and chat events. The API does not call models, run retrieval, execute assertions, diagnose failures, generate repairs, or decide verification acceptance. Those responsibilities belong to the evaluator team. Missing integration returns 503 `evaluator_not_configured`.

## 2. Big picture

```mermaid
flowchart LR
    UI[Frontend or Insomnia] --> API[FastAPI]
    API <--> DB[(Postgres / Neon)]
    API --> EV[Team evaluator]
    EV --> MODEL[Agent / model under test]
    EV --> JUDGE[Judge]
    EV --> DB
```

The evaluator uses the transaction session supplied by the API. There is no separate worker queue in this version. BackgroundTasks runs work in the API process.

## 3. The loop, with a refund example

A captured answer says 60 days; the policy says 30 days. Capture the trace and create a test. The evaluator later diagnoses the failure, confirms it with replay, proposes a new configuration version, and verifies baseline against candidate. These are evaluator operations, not API decisions.

| Step | Request | Response |
| --- | --- | --- |
| Project | POST `/v1/projects` with `{"name":"Acme Support"}` |201 project `{id,name,created_at}` |
| Baseline | POST `/v1/projects/{id}/versions` with `{"config":{"system_prompt":"Answer from policy"}}` |201 version `{id,project_id,parent_version_id,config,created_at}` |
| Hugging Face tests | GET `/v1/huggingface/datasets?dataset=owner/name`, POST `/v1/projects/{id}/test-cases/huggingface` |200 preview of columns and rows; 201 `{imported,skipped}`. Public datasets only, up to 200 rows per import |
| Credential | POST `/v1/credentials` with `{provider,model,api_key}` |201 metadata with credential_id; no key |
| Default credential | GET `/v1/credentials/default`, POST `/v1/credentials/default` |200 `{available,base_url,model}`; 201 metadata with credential_id, or 404 `default_credential_unavailable` |
| Captured trace | POST `/v1/traces` with `{id,app_version_id,input,output,spans}` |201 trace; repeated IDs do not duplicate spans |
| Test | POST `/v1/projects/{id}/test-cases/from-trace/{trace_id}` with `{}` |201 test containing original input |
| Assertion | POST `/v1/test-cases/{id}/assertions` with `{"kind":"deterministic","spec":{"type":"contains","value":"30 days"}}` |201 stored specification; not executed |
| Run | POST `/v1/projects/{id}/runs` with `{version_id,credential_id}` |202 job, or 503 without evaluator |
| Diagnose | POST `/v1/run-results/{id}/diagnose` with `{credential_id}` |Stored diagnosis from evaluator |
| Confirm | POST `/v1/diagnoses/{id}/replay` with `{credential_id,changed_variable,new_value}` |201 stored replay |
| Repair | POST `/v1/diagnoses/{id}/repairs` with `{credential_id}` |201 repair referencing a new version |
| Verify | POST `/v1/repairs/{id}/verify` with `{credential_id}` |202 job; evaluator supplies verification_id and scorecard |
| Scorecard | GET `/v1/verifications/{id}` |Stored verdict plus baseline/candidate summaries |

Run, verify and improve are asynchronous. Poll GET `/v1/jobs/{job_id}` until completed or failed. The backend must persist referenced records before returning. A job is completed only after the transaction commits. Failure rolls it back.

## 4. Folder map

| File | Purpose / edit when |
| --- | --- |
| app/main.py | Application, CORS, middleware, errors and router registration |
| app/config.py | Environment settings and Neon URL adaptation |
| app/db.py | Async engine, session, lookup, serialization and pagination |
| app/security.py | Fernet, redaction, URL validation and rate limits |
| app/errors.py | API error codes and envelope |
| app/store.py | CredentialStore and JobStore; memory only |
| app/models.py | Exact 14 table ORM mapping; do not add schema here |
| app/schemas.py | Stable public request and response models |
| app/routers/credentials.py | Credential create/read/revoke |
| app/routers/projects.py | Project CRUD, immutable versions and connection-check entry |
| app/routers/traces.py | Trace ingest, spans, trees and timelines |
| app/routers/tests.py | Test/assertion CRUD, split, seed entry |
| app/routers/runs.py | Run entry and stored result views |
| app/routers/fixloop.py | Diagnose/replay/repair/verify/improve entries and reads |
| app/routers/jobs.py | Background job transaction and status/SSE transport |
| app/routers/dashboard.py | Dashboard HTTP routes |
| app/routers/chat.py | Chat SSE HTTP entry |
| app/services/evaluator.py | Prepare inputs, scoped credentials, invoke backend, validate references |
| app/services/results.py | Format existing run results; no assertion execution |
| app/services/dashboard.py | Aggregate stored scores and verdicts |
| app/services/chat.py | Validate, redact and serialize evaluator events |
| tests/conftest.py | Isolated disposable Postgres schema per test |
| tests/test_config.py | URL conversion and health |
| tests/test_security.py | TTL, encryption, URL safety, log format and redaction |
| tests/test_models.py | SQL reflection, schema types/constraints and immutability |
| tests/test_projects.py | Project/version/credential HTTP flow |
| tests/test_traces.py | Concurrent ingestion, trees, chunks and filters |
| tests/test_cases.py | Cases/assertions/source traces/stable split |
| tests/test_runs.py | Evaluator boundary, scoped keys, invalid-reference rollback |
| tests/test_endpoint_contract.py | Error formats, rate limits, deletion safety and job rollback |
| tests/test_views.py | Chat forwarding and stored verdict preservation |
| tests/test_seed.py | Canned setup reset preserves unrelated projects |
| scripts/seed_demo.py | Canned setup data only; no AI-generated evaluation |
| insomnia/agentlab.insomnia.json | Native v4 requests with direct response chaining |
| requirements.txt | Pinned dependencies installed with pip |
| pytest.ini | Async test configuration |
| fastapi-readme.md | This guide |

## 5. Glossary

| Term | Meaning |
| --- | --- |
| Trace | One recorded agent interaction |
| Span | One recorded LLM, retrieval, tool or validation step |
| Retrieved chunk | Context recorded by a retrieval span |
| Test case | Input plus supplied expectations |
| Assertion | Stored deterministic/judge specification owned by evaluator |
| Judge | Model evaluating outputs; absent from API implementation |
| Diagnosis | Persisted explanation with evidence |
| Replay | An evaluator experiment changing one variable |
| Repair | Proposed configuration change linked to a new version |
| Version | Immutable configuration snapshot |
| Verification | Stored baseline/candidate decision |
| Regression | Evaluator identifies a previously passing case now failing |
| Holdout | Cases reserved for verification rather than repair generation |

## 6. How a request flows

Create project: the router validates ProjectCreate, inserts through the transaction session, flushes to obtain UUID/timestamp, serializes the row, and commits. A constraint failure becomes 409. For a run, the API instead loads project/version/tests/assertions, validates relationships, checks credential IDs, and supplies that data to the evaluator inside a job transaction.

## 7. Database ownership

| Table | Stores |
| --- | --- |
| projects | Project identity |
| app_versions | Immutable configurations and parent versions |
| traces | Recorded input/output/status |
| spans | Trace steps and tree links |
| retrieved_chunks | Recorded context per span |
| test_cases | Inputs, expectations, origin and split |
| assertions | Opaque check specifications |
| runs | Evaluator execution references and parameters |
| run_results | Persisted per-case verdicts |
| assertion_results | Persisted assertion verdicts/evidence |
| diagnoses | Explanations and evidence |
| replays | Original/replayed traces and outcome flag |
| repairs | Patch and candidate-version references |
| verifications | Baseline/candidate runs and acceptance decision |

The API owns CRUD and ingestion. The evaluator owns AI-produced records and all decisions. No startup DDL, new table or migration is performed. The SQL in the root db directory is authoritative. Keeping db outside server is intentional shared-schema organization.

## 8. API-key handling

POST credentials encrypts keys using Fernet and stores ciphertext in memory with TTL 7200 seconds, maximum 86400 seconds. GET exposes metadata and only the last four key characters. DELETE revokes immediately. Credentials are never stored in Postgres. Only explicitly supplied credential IDs can be accessed by an evaluator operation. Production requires HTTPS and a stable APP_ENCRYPTION_KEY. There is no user authentication/login. Protect deployment behind an authenticated gateway before sharing it publicly. RSA client-side encryption is a possible later feature, not implemented.

Registered keys, Bearer values and key query fields are redacted. User URLs must pass HTTPS and public-DNS checks. The AI-owned outbound client must validate again at connection time, pin safe DNS addresses, reject unsafe redirects, and enforce its own timeout/size limits. The API bridge contains no outbound model client.

## 9. Configuration

| Variable | Default / missing behavior |
| --- | --- |
| DATABASE_URL | Localhost fallback `agentlab`; must explicitly select intended Neon branch. Existing local installs that used the old `testlab` default must run `ALTER DATABASE testlab RENAME TO agentlab;` once before upgrading |
| APP_ENV | development; production enforces encryption key and HTTPS credential/chat entry |
| APP_ENCRYPTION_KEY | Generated in development with restart warning; required in production |
| CORS_ORIGINS | http://localhost:3000; comma-separated explicit origins, no wildcard |
| AGENTLAB_ALLOW_INSECURE_LOCAL | false; development-only localhost HTTP URL exception |
| CREDENTIAL_RATE_LIMIT |20 per peer IP per minute |
| CHAT_RATE_LIMIT |30 per peer IP per minute |
| HUGGINGFACE_RATE_LIMIT |10 Hugging Face previews and imports per peer IP per minute |
| TEST_DATABASE_URL | Tests only; disposable database name must contain test |
| AGENTLAB_API_KEY | Unset leaves /v1 open for local development; when set, every /v1 route requires a matching X-Api-Key header |
| META_API_KEY, META_BASE_URL, META_MODEL | Optional default Meta key. When all three are set, POST `/v1/credentials/default` stores a normal temporary credential from them; the key is never returned. With AGENTLAB_API_KEY, anyone holding that key can use it. Without it, set AGENTLAB_UNAUTHENTICATED_DEFAULT_KEY=true (development only) to allow direct localhost callers. A request is local only when both the client address and the Host header are loopback and no forwarding header is present |

No Redis, AGENTLAB_FAKE_LLM, provider referer/title settings or model execution options are used by this API. Provider settings belong to the separately supplied evaluator. The server does not automatically read .env: use Uvicorn --env-file. Existing shell variables take precedence over dotenv loading. Clear an obsolete shell DATABASE_URL before switching to a .env connection.

## 10. Run, tests, seed

From the repository root, use your Python 3.11+ environment:

```powershell
python -m pip install -r server/requirements.txt
python -m uvicorn app.main:app --app-dir server --env-file .env --reload
```

Put DATABASE_URL and optional settings in a local ignored .env. Do not commit a real URL or key. A placeholder is `DATABASE_URL=postgresql://REPLACE_ME:REPLACE_ME@HOST/DBNAME?sslmode=require`. SQLAlchemy uses asyncpg, removes libpq channel_binding, supplies verified TLS, and disables both statement caches for pooled hostnames. Connection timeout 20 seconds; pool_pre_ping enabled.

For tests, set TEST_DATABASE_URL to a dedicated disposable database containing test in its name:

```powershell
python -m pytest server/tests -q
ruff check server
ruff format --check server
```

CI (.github/workflows/ci.yml) runs these three commands on every push and pull request against a disposable Postgres service database, so results reproduce without a Neon account.

Fixtures create an isolated schema, apply the unchanged SQL, and remove only that schema afterward. Do not point tests at a shared/main Neon database.

The seed script reads the shell's DATABASE_URL only; it does not load .env. Run it with DATABASE_URL explicitly configured:

```powershell
python server/scripts/seed_demo.py
python server/scripts/seed_demo.py --reset
```

--reset deletes only projects named Demo - Refund Bot and their children. The seed trace and tests are canned examples. They are not evidence of real model evaluation.

## 11. Insomnia and Neon

Use a dedicated Neon branch for manual testing. Initialize the SQL only if that database is empty. Never reapply schema blindly to existing data. A health 200 does not prove database connectivity. POST a project, then query the same ID in Neon's SQL editor to prove persistence.

Import server/insomnia/agentlab.insomnia.json using File import. This is native Insomnia v4 JSON, not Postman scripts. Base URL defaults to http://localhost:8000. Send Create project, Create initial version, Create credential, Create test, then assertions and traces. Direct response tags read the latest source responses automatically. Missing setup history reports a tag error. No empty ID variables need to be updated manually. Create a fresh credential after restart. Trace IDs remain stable for duplicate-ingest checks; change trace_id for a different captured trace.

DELETE requests are in 99 Cleanup and must run last. Do not run the entire collection blindly. AI POST requests return 503 until the evaluator is connected. Read-only result routes need real evaluator records or explicit canned fixtures. Verification job polling needs the job_id returned by verify, not the earlier run's job_id. The automatic run comparison defaults to the same run; select two actual run IDs to compare different versions.

Alternative: import http://localhost:8000/openapi.json directly. OpenAPI imports define requests but do not capture resource IDs automatically. /docs provides interactive schemas.

## 12. Evaluator connection and external agents

The AI team supplies an object with:

```python
async def execute(payload: dict, context: dict) -> dict:
    ...

def stream_chat(payload: dict, context: dict):
    # Return an async iterator of {event, data} objects.
    ...
```

Application assembly calls create_app(evaluator=backend). The team's own entry module can expose that assembled app to Uvicorn. There is no implicit fake engine or evaluator factory environment setting.

Payload fields are operation, project_id, version, test_cases (with assertions), target (with trace/spans/chunks), and options. Query/config/expectation strings are preserved except secret redaction. Run/improve/verify include selected test data. Verify includes baseline and candidate versions and diagnosis. The API does not interpret assertion specs or assign acceptance criteria.

Context supplies session, session_factory(), credentials.metadata(id), credentials.decrypt_for_call(id), http_client, and report_progress(fraction). Credentials helpers are synchronous; report_progress is async when supplied for jobs. Flush records through session but do not commit, close, or replace it. The API commits successful jobs and rolls back failures. Evaluators should commit per result or per batch through short sessions from session_factory() so dashboards update before the job ends; references returned from those sessions are validated by the API afterwards. A referenced parent record (for example the run row) must be committed before result rows that point at it are written through a separate session_factory() session, otherwise the foreign key is invisible to that transaction. http_client is a pre-built httpx client that re-validates each destination IP at call time; it is closed when the operation finishes. The evaluator is trusted server-side code, not an untrusted sandbox.

| Operation | Required returned references |
| --- | --- |
| run / improve | run_id referencing a stored run in this project |
| diagnose | id referencing a stored diagnosis |
| replay | id referencing a stored replay |
| repair | id referencing a stored repair |
| verify | verification_id referencing this repair's stored verification; optional scorecard |
| seed | items containing persisted test-case records in this project |
| test_connection | ok boolean, optional latency_ms/model/error |
| suggest_policies | name and policies (title, policy, test), checked by the response model; nothing is stored |

Hosted vs external execution, replay support, confirmation, repair templates, holdout isolation and verification acceptance are implemented by the evaluator team. External agents commonly expose only prompt/params/tools controls; retrieval changes must not be claimed if their API does not support them.

OpenRouter credential example is provider=openrouter, model=REPLACE_ME, api_key=REPLACE_ME. External agent config can specify type=openai_compatible and base_url=https://openrouter.ai/api/v1. Gemini can use provider=custom and Google's OpenAI-compatible base URL. Storing either credential does not contact a provider; the integration must implement test_connection.

## 13. Frontend contract

| Screen | Routes |
| --- | --- |
| Project setup | projects, versions, credentials, agent/test-connection, policy-suggestions |
| Trace explorer | traces, spans, timeline |
| Test editor | test-cases, assertions, split |
| Run results | runs, results, summary, compare |
| Failure / fix | diagnoses, replay, repairs, verification |
| Dashboard | dashboard and dashboard/series |
| Chat | projects/{id}/chat |

Errors are {error:{code,message,details}}. Lists are {items,next_cursor}; send limit/cursor. Cursors are opaque chronological (created_at, id) markers. Deleting a test case or assertion archives it: reads and future runs exclude it, but past run results and scorecards are preserved. Job statuses are queued/running/completed/failed; polling results carry evaluator IDs. Job SSE emits progress and terminal done.

Chat names are fixed: message.delta {text}, step.started/step.finished step metadata, tool.result {type,data}, error {error:{code,message,details}}, done {}. Card types: run_summary, diagnosis, replay, repair, scorecard. Client sends history; there are no chat tables. API forwards these events, never classifies intent. Adjacent text deltas are buffered until a non-text event or completion to redact keys split across chunks.

Keep contract changes additive. OpenAPI includes 56 operations. No PATCH/PUT version endpoint exists.

## 14. How to extend

- Endpoint: add schema, thin route, appropriate tag/summary, validation and HTTP test; register router.
- Assertion kind: evaluator team implements it; API stores and forwards its opaque spec.
- Diagnosis rule: evaluator team implements it using existing SQL rule enum; schema changes require separate agreement.
- Patch type: use an existing SQL enum and new-version records. New enum values require separate schema agreement.

## 15. Limits and next steps

No user accounts (set AGENTLAB_API_KEY to require a shared X-Api-Key header on /v1 routes), durable queue, distributed store, Redis, RSA or model fingerprinting. Behind a TLS-terminating proxy, run uvicorn with --proxy-headers --forwarded-allow-ips so the HTTPS check and per-IP rate limits see the real client; without them every credential POST returns 400 and the rate limit turns global. Use a single worker; credentials and jobs are lost on restart. TTL cleanup is lazy on lookup. SSE output buffers adjacent text for redaction. Production load, shared-store operation and real AI integration have not been tested. URL safety here does not replace the evaluator's outbound transport protections. No automatic repair deployment occurs.

## 16. Team questions

Why rules before judges? That is an evaluator policy for cheap, explainable checks; API makes no choice. Why no replay versions? Temporary experiments and persisted candidate versions are different records; evaluator handles this. Why immutable versions? Before/after comparisons need stable configurations. Why memory jobs? Small local MVP with an explicit future shared-store boundary. Why Fernet? Authenticated symmetric encryption for short-lived keys. Why no chat tables? History comes from the client. How are regressions computed? Evaluator owns that calculation; API formats stored verdicts. What if a judge is wrong? Retain evidence and require independent validation; API cannot establish judge correctness. How does it scale? Shared credential/job stores, a durable queue and authorization must be designed before multiple workers.

## 17. Ten-minute walkthrough

1. Minute 0–1: distinguish API-owned validation/transport from evaluator-owned AI decisions.
2. Minute 1–2: start server; inspect /health, /v1/meta and /docs.
3. Minute 2–4: create project/version/test/assertion; show matching Neon row IDs.
4. Minute 4–5: ingest a canned trace twice; show one span tree.
5. Minute 5–6: create/revoke a dummy credential; explain TTL and redaction.
6. Minute 6–7: call run without evaluator and show explicit 503.
7. Minute 7–8: walk payload/context integration and transaction ownership.
8. Minute 8–9: explain job polling/SSE and stored dashboard verdicts.
9. Minute 9–10: list remaining AI integration and frontend end-to-end checks.

## 18. MVP runs endpoints

`/v1/mvp/runs` records simplified evaluation runs in the shared `runs` table, keyed by client-supplied `run_id` instead of app versions. `POST /v1/mvp/runs` validates the RunResult model (verdict and failure_type literals plus the 0–1 grounding range mirror the table CHECKs, so violations return 422 instead of 409; passing runs with a failure_type and text containing NUL also return 422), requires an existing parent when `parent_run_id` is set (404), and returns 201; duplicate `run_id` values return 409. `GET /v1/mvp/runs/{run_id}` reads one run. `GET /v1/mvp/scorecard` returns `{total, by_verdict, by_failure_type}` with zero-filled groups. `GET /v1/mvp/runs/{run_id}/diff` returns the run, its parent, and whether the verdict changed; runs without a parent return 404. MVP queries only touch rows with `run_id` set, so legacy evaluator rows (NULL `run_id`) are excluded; conversely, legacy run reads now include the MVP columns as nulls.
