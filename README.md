# 🧪 TestLab

> **Failure-to-fix workbench for applications built on Meta's models.**  
> Turn a bad answer into a reproducible test, isolate the root cause, propose a fix, and prove it doesn't break anything else.

---

## 🎯 Target Models

TestLab interfaces with target models behind a unified adapter:
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

1. **Detection:** TestLab flags the unsupported claim during automated evaluation.
2. **Diagnosis:** TestLab reveals that the relevant 30-day refund policy chunk was never retrieved, confirming a **retrieval miss** via controlled replay.
3. **Repair:** TestLab automatically proposes an updated retrieval filter along with an updated grounding rule.
4. **Verification:** TestLab verifies the repair against all test suites, confirming the issue is resolved with zero regressions.

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

