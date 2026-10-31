# TestLab evaluator

Implements the backend described in section 12 of `server/fastapi-readme.md`.

```powershell
python -m uvicorn evaluator.asgi:app --app-dir server --env-file .env --reload
```

| File | Purpose |
| --- | --- |
| llm.py | OpenAI-compatible model client: retries, size limit, failure kinds |
| agent.py | Hosted/external agent, retrieval, tools, validators, trace persistence |
| diagnosis.py | Ordered rule engine with judge fallback |
| backend.py | `Evaluator.execute`, connection test and diagnosis persistence |
| asgi.py | Application entry point with the evaluator attached |

## Operations

`test_connection` and `diagnose` are implemented. `run`, `improve`, `replay`, `repair`, `verify` and `seed` return 503 `evaluator_operation_unavailable`. The evaluation code should call `run_agent` and then `save_trace`, and must set `run_results.trace_id` so results can be diagnosed.

## Providers

`openrouter` works without a `base_url`. `meta` and `custom` need one on the credential or agent config.

## Diagnosis inputs

Rules run in order and the first match wins: `format`, `tool_arg`, `context_overflow`, `budget`, `safety`, `retrieval_miss`, `grounding`, `unsupported_claim`, `instruction_violation`. If none match, a judge suggests a rule. That result is stored as rule `judge`, with `evidence.suggested_rule`.

- `test_case.expected.reference` is the text a correct answer contains.
- `test_case.expected.source` is the source that should have been retrieved.
- A failed assertion or validator can set `category` to `format`, `safety` or `instruction`.
