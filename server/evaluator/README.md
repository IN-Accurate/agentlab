# AgentLab evaluator

Implements the backend described in section 12 of `server/fastapi-readme.md`.

```powershell
python -m uvicorn evaluator.asgi:app --app-dir server --env-file .env --reload
```

| File | Purpose |
| --- | --- |
| llm.py | OpenAI-compatible model client: retries, size limit, failure kinds |
| agent.py | Hosted/external agent, retrieval, tools, validators, trace persistence |
| evaluate.py | `run` operation, pass/fail grading and `stream_chat` |
| repair.py | `repair` proposes a prompt change; `verify` compares both versions |
| diagnosis.py | Ordered rule engine with judge fallback |
| backend.py | `Evaluator.execute`, connection test and diagnosis persistence |
| asgi.py | Application entry point with the evaluator attached |

## Operations

`test_connection`, `diagnose`, `run`, `repair`, `verify` and `stream_chat` are implemented. `improve`, `replay` and `seed` return 503 `evaluator_operation_unavailable`.

`run` saves the run first, then commits each result with its trace as it finishes. A result passes when the agent returns a non-empty answer and every check holds: failed validators, `expected.reference`, `expected.behavior` (graded by the judge model) and the case's assertions. A case with no checks passes on a non-empty answer alone. A grader that fails or gives an unusable reply counts as a failure.

`repair` asks the judge model for a new system prompt, or applies a supplied `patch`, and saves it as a child version. It never sees holdout expectations, and rejects a holdout target. `verify` runs the baseline and candidate on every test at temperature 0. A candidate is accepted only when the target test passes, no test regresses, and the project has holdout tests that did not get worse. Holdout passing needs at least one holdout test.

`stream_chat` answers with the project's latest version. A message starting `Run test: ` treats the rest as expected behaviour: it writes a test prompt, saves or reuses a test case and returns a `run_summary` card. The whole answer is sent at once because the model client does not stream.

## Providers

`openrouter` works without a `base_url`. `meta` and `custom` need one on the credential or agent config.

## Diagnosis inputs

Rules run in order and the first match wins: `format`, `tool_arg`, `context_overflow`, `budget`, `safety`, `retrieval_miss`, `grounding`, `unsupported_claim`, `instruction_violation`. If none match, a judge suggests a rule. That result is stored as rule `judge`, with `evidence.suggested_rule`.

- `test_case.expected.reference` is the text a correct answer contains.
- `test_case.expected.source` is the source that should have been retrieved.
- A failed assertion or validator can set `category` to `format`, `safety` or `instruction`.
