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
| team.py | Rules router, one coordinator handing tasks to member agents, step and token limits |
| diagnosis.py | Ordered rule engine with judge fallback |
| backend.py | `Evaluator.execute`, connection test and diagnosis persistence |
| asgi.py | Application entry point with the evaluator attached |

## Operations

`test_connection`, `diagnose`, `run`, `repair`, `verify` and `stream_chat` are implemented. `improve`, `replay` and `seed` return 503 `evaluator_operation_unavailable`.

`run` saves the run first, then commits each result with its trace as it finishes. A result passes when the agent returns a non-empty answer and every check holds: failed validators, `expected.reference`, `expected.behavior` (graded by the judge model) and the case's assertions. A case with no checks passes on a non-empty answer alone. A grader that fails or gives an unusable reply counts as a failure.

`repair` asks the judge model for a new system prompt, or applies a supplied `patch`, and saves it as a child version. It never sees holdout expectations, and rejects a holdout target. `verify` runs the baseline and candidate on every test at temperature 0. A candidate is accepted only when the target test passes, no test regresses, and the project has holdout tests that did not get worse. Holdout passing needs at least one holdout test.

`stream_chat` answers with the project's latest version. A message starting `Run test: ` treats the rest as expected behaviour: it writes a test prompt, saves or reuses a test case and returns a `run_summary` card. The whole answer is sent at once because the model client does not stream.

## Teams

A team is a project whose version config has a `team` block. The version's own `system_prompt`, `agent` and `params` are the single coordinator. Members are other connected agents, named by project id, and each answers with its own latest version.

```json
{
  "system_prompt": "You run Acme support.",
  "team": {
    "members": [
      {"name": "billing", "project_id": "<uuid>", "description": "Refunds and invoices"},
      {"name": "general", "project_id": "<uuid>", "description": "Anything else"}
    ],
    "router": {
      "type": "rules",
      "rules": [{"member": "billing", "keywords": ["refund", "invoice"]}],
      "fallback": "general"
    },
    "limits": {"max_steps": 12, "max_tokens": 20000}
  }
}
```

- Member names match `^[a-z0-9_-]{1,40}$` and must be unique. Rules and `fallback` must name members, or the version is rejected with 422. Members cannot be teams.
- The router runs first. The first rule with a keyword in the message, matched as a whole word and ignoring case, picks the member with confidence 1. Unmatched input goes to `fallback` with confidence 0. The coordinator is told the choice and asks that member first.
- The coordinator gets one `ask_<name>` tool per member, taking `{"task": "..."}`, and combines the members' answers.
- Chat and run requests send `member_credentials`: member project id to credential id. Each member runs only on its own credential. A member with no credential stops the request with 422 `member_not_connected`.
- `limits` are shared by the coordinator and all members in one request. `max_steps` (default 12) counts model calls. `max_tokens` counts `usage.total_tokens`, or about four characters per token when the provider omits usage. The call that would pass a limit never starts, and the trace ends with an `llm` error span of kind `step_limit` or `spend_limit`.
- A team trace has a top-level `router` span (`type`, `choice`, `confidence`, `reason`, `rule`), then an `agent` span for the coordinator (with `steps` and `tokens`). The coordinator's `llm` spans and each member's `agent` span sit under it, and each member's own spans sit under the member.
- Chat sends a `step.finished` event with `route` before the answer.
- `examples/support_team.json` holds a labelled routing set, and `tests/test_team.py` checks that the rules pass every case.

Existing databases need the new span types:

```sql
ALTER TABLE spans DROP CONSTRAINT spans_type_check;
ALTER TABLE spans ADD CONSTRAINT spans_type_check CHECK (type IN (
    'llm', 'retrieval', 'tool', 'validation', 'router', 'agent'));
```

## Providers

`openrouter` works without a `base_url`. `meta` and `custom` need one on the credential or agent config.

## Diagnosis inputs

Rules run in order and the first match wins: `format`, `tool_arg`, `context_overflow`, `budget`, `safety`, `retrieval_miss`, `grounding`, `unsupported_claim`, `instruction_violation`. If none match, a judge suggests a rule. That result is stored as rule `judge`, with `evidence.suggested_rule`.

- `test_case.expected.reference` is the text a correct answer contains.
- `test_case.expected.source` is the source that should have been retrieved.
- A failed assertion or validator can set `category` to `format`, `safety` or `instruction`.
