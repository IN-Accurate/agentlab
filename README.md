# AgentLab

> **Meta Global AI Developer Hackathon submission** · Team Promptimists (Vishal, Deeptika, Sumit, Siddhanth) · Code: https://github.com/IN-Accurate/agentlab
>
> A failure-to-fix workbench for applications built on Meta's models: turn a bad answer into a reproducible test, isolate the root cause, propose a fix, and prove it doesn't break anything else.

---

## Submission Overview

Debugging an AI agent usually means reading one bad answer and guessing. AgentLab replaces guessing with a loop: capture the failure as a trace, promote it to a deterministic test, classify the root cause with a transparent rule engine, confirm the diagnosis by controlled replay, generate a targeted repair, and accept the fix only after side-by-side verification shows zero regressions.

The workbench targets agents powered by Meta's **Muse** models (via the Meta Model API or OpenRouter) and **Llama** (via any OpenAI-compatible endpoint), with guardrail coverage informed by PurpleLlama (Llama Guard, Prompt Guard) and the Llama Stack ecosystem.

**Demo video:** a 3-minute narrated walkthrough (the problem, the tech stack, how we use Meta's model, and the live app) accompanies this submission. Every answer, grade, diagnosis and repair in it was produced live by `muse-wonder-1.0`.

---

## Target Models

AgentLab interfaces with target models behind a unified, OpenAI-compatible adapter:
* **Muse** — `muse-wonder-1.0` by default, and the Muse Spark models, via the Meta Model API (`https://api.meta.ai/v1`) or OpenRouter.
* **Llama** — Accessible via any OpenAI-compatible hosting endpoint.

### How AgentLab uses Meta's model

In the default setup (and the demo), every model call runs on Muse, and one model plays six roles. Each role uses whichever model its key and agent config name, so the same roles can run on a Llama endpoint instead:

| Role | What Muse does |
| :--- | :--- |
| **The agent** | Answers as the agent under test, and as every coordinator and member of an agent team. |
| **The judge** | Grades each answer against the expected behaviour, and checks it used only the business policy it was given. |
| **The tools** | Plays tools an agent does not have yet, so tool use can be tested safely. |
| **The test writer** | Writes realistic test questions, and suggests policies from an agent's name and prompt. |
| **The doctor** | Diagnoses why a test failed when the rule engine cannot tell. |
| **The fixer** | Writes the repaired system prompt that verification then checks. |

A test can use a separate key or `judge_model` (for example a Muse Spark model), so an agent running on `muse-wonder-1.0` is not graded by the same model.

---

## The Improvement Loop


Trace ➔ Test ➔ Diagnose ➔ Confirm ➔ Repair ➔ Verify


1. **Trace**  
   A light, one-line SDK decorator captures each request as a tree of spans containing LLM calls, retrieval context, tool executions, and validation checks.
2. **Test**  
   Runs a test suite comprising both seed and user tests, prioritizing **deterministic assertions** first, followed by **LLM-judge checks**.
3. **Diagnose**  
   An ordered rule engine (9 rules) classifies failures before falling back to LLM judges:
   * Format / Tool argument error
   * Context overflow / Budget limit
   * Safety violation
   * Retrieval miss / Grounding failure
   * Unsupported claim / Instruction violation
4. **Confirm by Replay**  
   Reruns the trace while varying a single variable. If the outcome flips, the diagnosis is confirmed.
5. **Repair**  
   Generates targeted patches across prompts, retrieval configurations, tool schemas, validators, or parameters — creating a new candidate app version.
6. **Verify**  
   Executes baseline and candidate versions side-by-side. A patch is accepted **only** if the target test passes, zero regressions occur, and performance on the holdout set holds up. Displays a clear before-and-after scorecard.

---

## Architecture

* **API Engine:** FastAPI service with async SQLAlchemy; replies and job progress stream over Server-Sent Events.
* **Task Processing:** In-process async jobs (memory job store, single worker). Runs evaluate up to four test cases at once.
* **Evaluator:** Python package for agents, agent teams with a rules router, simulated / search / connected tools, diagnosis, repair and verification.
* **Storage:** PostgreSQL (leveraging `JSONB` for flexible schema execution), managed with Alembic migrations.
* **Web Interface:** React 19 + Vite, styled with Tailwind CSS and shadcn/ui: chat, Agent Manager, and a dashboard with trace timelines, visual diff views, and comparison scorecards.

### Highlights

* **Business policy in context** — a test's policy goes into the agent's instructions, and the agent is told to answer only from its instructions, that policy, and any other context or tool results. The judge fails answers that add facts, limits or promises beyond those sources. In chat, attached policy is kept for the whole conversation.
* **Policy suggestions** (API: `POST /v1/projects/{id}/policy-suggestions`) — draft policies and tests from an agent's name and prompt, then customise them for your company.
* **Tool testing** — simulated tools, built-in Wikipedia search and connected HTTPS endpoints; tests can require or forbid tool calls, or demand answers grounded in tool results.
* **Agent teams** — a coordinator, members on their own keys, rules routing with a fallback, and step/token limits, all visible in the trace.
* **Find a fix** — one click from a failed test to a diagnosis, a repaired prompt, and a verification scorecard with held-out tests.

---

## Run Locally

### Prerequisites

* Python 3.11+
* Node 18+ (the `client/` app was built with Node 26 via `nvm`)
* A Postgres database (Neon recommended). Use a **direct** connection string, not the pooler, for tests.

### 1. Backend API

```sh
python3.11 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
DATABASE_URL='<your-postgres-direct-URL>' .venv/bin/python -m uvicorn app.main:app --port 8000 --app-dir server
```

For a new database, install the backend dependencies and run
`DATABASE_URL='<your-postgres-direct-URL>' .venv/bin/python -m alembic -c server/alembic.ini upgrade head`
before starting the API. For a database that already contains AgentLab data, follow the
[baseline adoption instructions](server/migrations/README.md) instead of recreating its tables.
The migration head includes the team `router` and `agent` span types. The adoption
procedure also recognizes the team schema created before saved-key storage existed.

To save BYOK credentials across server restarts, generate a Fernet key once with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
and configure it as `APP_ENCRYPTION_KEY`. Keep the same secret for every worker and
restart; changing or losing it makes existing saved keys unreadable. Credentials keep
their existing expiry (two hours by default, up to one day) and can be revoked through
the existing credential API. Without this setting, development retains temporary
in-memory credentials. Run uvicorn with `--env-file .env` to load local settings.

Verify: `curl localhost:8000/health` → `{"status":"ok"}`. Interactive docs at http://localhost:8000/docs (note: `/` itself is a 404 — the API lives under `/v1`).

### 2. Frontend

```sh
cd client
npm install
npm run dev
```

Open http://localhost:5173. The dev server proxies API calls to the backend on `:8000`, so keep both running. See [client/frontend_README.md](client/frontend_README.md) for build config and details.

### 3. Tests (backend)

```sh
TEST_DATABASE_URL='<your-test-db-URL (name must contain "test")>' .venv/bin/python -m pytest server/tests -q
```

Each test builds a fresh schema from `db/agentlab_schema.sql` inside that database and drops it afterwards. Chat/test-run flows additionally require an evaluator configured on the backend; without one those endpoints return `503` with an explicit error code.

---

## Database Schema Overview (15 Tables)

| Domain | Tables |
| :--- | :--- |
| **App** | `projects`, `app_versions` |
| **Saved keys** | `saved_credentials` (Fernet ciphertext and credential metadata) |
| **Traces** | `traces`, `spans`, `retrieved_chunks` |
| **Tests** | `test_cases`, `assertions` |
| **Runs** | `runs`, `run_results`, `assertion_results` |
| **Fix Loop** | `diagnoses`, `repairs`, `verifications`, `replays` |

---

## Demo Scenario

The demo video follows this flow on the real app, live on `muse-wonder-1.0`:

1. **Connect:** an agent is connected in Agent Manager with the default Meta key, which stays on the server.
2. **Policy in chat:** the company's policy is attached once; follow-up questions keep it, and a question it does not cover ("Do you deliver to Delhi?") gets "the policy only mentions Pune and Mumbai" instead of a guess.
3. **Test:** a plain-language expectation becomes a test; Muse writes the question, the agent answers, and Muse grades it against the policy.
4. **Fix:** a billing agent fails "ends by asking if there is anything else it can help with". Find a fix diagnoses it, adds one sentence to the system prompt as a new version, and verification accepts it: target test passes, pass rate 1/2 → 2/2, held-out test still passes.
5. **Dashboard:** pass rate by version, the team's routing trace on a timeline, and one-click trace diagnosis.

---

## Out of Scope for v1

* Multi-provider model routing
* CI/CD gates & deployment contracts
* Model fingerprinting
* Production live-monitoring
* Adversarial test generation
* Automated prompt optimization loops

---

## Built With

* **Core & Infrastructure:** FastAPI, Uvicorn, Pydantic, PostgreSQL, SQLAlchemy (asyncpg), Alembic, React 19, Vite, Tailwind CSS v4, shadcn/ui (Radix UI), Lucide icons, react-markdown/remark-gfm, and Fernet-encrypted BYOK credentials.
* **Ecosystem & Stack:** Meta's Muse (`muse-wonder-1.0` and Muse Spark) via the Meta Model API; Llama via OpenAI-compatible endpoints; optional OpenRouter model access. Model endpoints and IDs are configurable.
* **Observability & RAG:** AgentLab's own persisted traces and spans for model calls, tools, retrieval, and validation; keyword-ranked knowledge-base retrieval with metadata filters; Wikipedia search via the MediaWiki API; Server-Sent Events for chat and job progress.
* **Testing & Guardrails:** pytest, pytest-asyncio, Node.js's built-in test runner, Vitest, React Testing Library, JSON Schema validation (jsonschema), deterministic assertions, LLM judges, rule-based diagnosis, and baseline/candidate regression verification. PurpleLlama (Llama Guard, Prompt Guard) and Llama Stack are design references, not integrated runtime dependencies.
