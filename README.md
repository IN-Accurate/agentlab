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

* **Python 3.12** (the version CI and the Docker image use)
* **Node.js 20+** (Tailwind CSS v4 needs 20 or newer)
* **PostgreSQL 16**, either local or hosted (for example Neon). Use a **direct** connection string, not a pooler, for migrations and tests.
* A **Meta Model API key** for Muse, or any OpenAI-compatible endpoint and key.

### 1. Configure

```sh
cp .env.example .env
```

Then edit `.env`:

| Variable | What to set |
| :--- | :--- |
| `DATABASE_URL`, `DATABASE_URL_DIRECT` | Your Postgres URLs, e.g. `postgresql://USER@localhost:5432/agentlab` locally |
| `APP_ENCRYPTION_KEY` | A Fernet key, so saved API keys survive restarts. Generate once with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and keep it secret and unchanged |
| `META_API_KEY`, `META_BASE_URL`, `META_MODEL` | Your Meta key, `https://api.meta.ai/v1`, and `muse-wonder-1.0` (the "Default Meta key" option in the app) |
| `AGENTLAB_UNAUTHENTICATED_DEFAULT_KEY` | `true` for local development only; it lets localhost use the default key without `AGENTLAB_API_KEY` |

Never commit `.env`; it is git-ignored.

### 2. Backend API and evaluator

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r server/requirements.txt

cd server
set -a; . ../.env; set +a                     # settings are read from the environment
../.venv/bin/python -m alembic upgrade head   # create or update the schema
../.venv/bin/uvicorn evaluator.asgi:app --port 8000
```

`evaluator.asgi:app` is the API with the evaluator attached, which is what chat, test runs, diagnosis and repair need; `app.main:app` alone answers those with `503 evaluator_not_configured`. For a database that already contains AgentLab data, follow the [baseline adoption instructions](server/migrations/README.md) instead of recreating its tables. Credentials keep their expiry (two hours by default, up to one day) and can be revoked through the credential API.

Verify: `curl localhost:8000/health` → `{"status":"ok"}`. Interactive docs at http://localhost:8000/docs (`/` is a 404; the API lives under `/v1`).

### 3. Frontend

```sh
cd client
npm ci
npm run dev
```

Open http://localhost:5173. The dev server proxies `/v1` to the backend on `:8000`, so keep both running. See [client/frontend_README.md](client/frontend_README.md) for details.

### 4. Production build

```sh
# Frontend: static files in client/dist/
cd client
VITE_API_URL=https://your-api.example.com npm run build   # leave VITE_API_URL empty to serve the API from the same origin
npm run preview                                            # optional local check of the build

# Backend: the same image CI builds
docker build -t agentlab-api server
docker run --env-file .env -p 8000:8000 agentlab-api
```

Run the migrations against the production database before starting the container. Use a single API worker: jobs and in-memory credentials are per process.

### 5. Tests and checks

```sh
# Server: lint, format and tests (the database name must contain "test")
.venv/bin/ruff check server && .venv/bin/ruff format --check server
cd server && TEST_DATABASE_URL='postgresql://USER@localhost:5432/agentlab_test' ../.venv/bin/python -m pytest -q

# Client: unit tests (node --test) and UI tests (Vitest), then a build
cd client && npm test && npm run build
```

Each server test builds a fresh schema inside the test database and drops it afterwards. CI (GitHub Actions) runs the server checks on Postgres 16 and builds the Docker image.

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

---

## Contributing and License

Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, workflow and the checks a pull request needs.

AgentLab is released under the [MIT License](LICENSE).
