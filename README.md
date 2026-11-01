# 🧪 AgentLab

> **Failure-to-fix workbench for applications built on Meta's models.**  
> Turn a bad answer into a reproducible test, isolate the root cause, propose a fix, and prove it doesn't break anything else.

---

## 🎯 Target Models

AgentLab interfaces with target models behind a unified adapter:
* **Muse Spark** — Accessible via the Meta Model API or OpenRouter.
* **Llama** — Accessible via any OpenAI-compatible hosting endpoint.

---

## 🔄 The Improvement Loop


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

## 🏗️ Architecture

* **API Engine:** FastAPI Service
* **Task Processing:** Redis-backed worker (`arq` / `RQ`)
* **Storage:** PostgreSQL (leveraging `JSONB` for flexible schema execution)
* **Web Interface:** Dashboard with trace timelines, visual diff views, and comparison scorecards.

---

## 💻 Run Locally

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

## 🗄️ Database Schema Overview (14 Tables)

| Domain | Tables |
| :--- | :--- |
| **App** | `projects`, `app_versions` |
| **Traces** | `traces`, `spans`, `retrieved_chunks` |
| **Tests** | `test_cases`, `assertions` |
| **Runs** | `runs`, `run_results`, `assertion_results` |
| **Fix Loop** | `diagnoses`, `repairs`, `verifications`, `replays` |

---

## 🎬 Demo Scenario

> **Issue:** A customer support bot incorrectly claims refunds are allowed within **60 days**.

1. **Detection:** AgentLab flags the unsupported claim during automated evaluation.
2. **Diagnosis:** AgentLab reveals that the relevant 30-day refund policy chunk was never retrieved, confirming a **retrieval miss** via controlled replay.
3. **Repair:** AgentLab automatically proposes an updated retrieval filter along with an updated grounding rule.
4. **Verification:** AgentLab verifies the repair against all test suites, confirming the issue is resolved with zero regressions.

---

## 🚫 Out of Scope for v1

* Multi-provider model routing
* CI/CD gates & deployment contracts
* Model fingerprinting
* Production live-monitoring
* Adversarial test generation
* Automated prompt optimization loops

---

## 🛠️ Built With & Inspired By

* **Core & Infrastructure:** FastAPI, PostgreSQL, Redis, `arq` / `RQ`, Pydantic, Instructor
* **Ecosystem & Stack:** OGX (formerly Llama Stack), PurpleLlama (Llama Guard, Prompt Guard)
* **Observability & RAG:** OpenLLMetry, Arize Phoenix, Langfuse, LlamaIndex, Chroma
* **Testing & Guardrails:** `promptfoo`, DeepEval, Ragas, Guardrails AI, `prompt-ops`

