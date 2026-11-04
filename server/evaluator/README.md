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
| policies.py | `suggest_policies` proposes policies from an agent's name and prompt |
| repair.py | `repair` proposes a prompt change; `verify` compares both versions |
| team.py | Rules router, one coordinator handing tasks to member agents, step and token limits |
| diagnosis.py | Ordered rule engine with judge fallback |
| backend.py | `Evaluator.execute`, connection test and diagnosis persistence |
| asgi.py | Application entry point with the evaluator attached |

## Operations

`test_connection`, `diagnose`, `run`, `repair`, `verify`, `suggest_policies` and `stream_chat` are implemented. `improve`, `replay` and `seed` return 503 `evaluator_operation_unavailable`.

`run` saves the run first, then commits each result with its trace as it finishes. A result passes when the agent returns a non-empty answer and every check holds: failed validators, `expected.reference`, `expected.behavior` (graded by the judge model) and the case's assertions. A case with no checks passes on a non-empty answer alone. A grader that fails or gives an unusable reply counts as a failure.

`repair` asks the judge model for a new system prompt, or applies a supplied `patch`, and saves it as a child version. It never sees holdout expectations, and rejects a holdout target. `verify` runs the baseline and candidate on every test at temperature 0. A candidate is accepted only when the target test passes, no test regresses, and the project has holdout tests that did not get worse. Holdout passing needs at least one holdout test.

`stream_chat` answers with the project's latest version. A message starting `Run test: ` treats the rest as expected behaviour: it writes a test prompt, saves or reuses a test case and returns a `run_summary` card. The whole answer is sent at once because the model client does not stream.

`suggest_policies` (`POST /v1/projects/{id}/policy-suggestions`, body `credential_id` and optional `count` 1 to 8, default 5) sends the agent's name and latest system prompt to the judge model as untrusted data. It returns `name` and `policies`, each with `title`, `policy` (the rule to follow), `test` (the behaviour a test should expect) and `input` (a customer message that tests it). Suggestions with a missing field or a repeated title are dropped. If none are usable, or the model call fails, it returns 502 `policy_suggestions_failed`. Nothing is saved. Suggestions are drafts: a business edits any field (its own limits, currency, approvers, wording), then saves the result with the existing test-case API as `input` plus `expected` `{"behavior": test, "context": policy, "policy": title}`, and can change it later with `PATCH /v1/test-cases/{id}`. The judge grades against that policy.

A test's `expected.context` is its business policy, and the agent under test receives it. In every run (dashboard runs, chat "Run test", and teams, where both the coordinator and each member get it) the policy is added to the agent's system prompt under "Business policy and context", followed by a rule: answer only from the instructions, this policy and any other context or tool results, and say so plainly when they do not cover the request. The judge then checks that the reply adds no facts, numbers, limits or promises beyond the policy and tool results. If it does, the test fails with "Went beyond the given policy". Tests without a context are unchanged. If the agent fails, `repair` treats `expected.context` as the business's own rule and writes it into the new prompt, and `verify` decides whether to accept it.

## Simulated tools

Each entry in a version's `tools` has a name, description and JSON Schema `parameters`. Two kinds really run:

- `{"builtin": "search"}` adds a `search` tool over Wikipedia's public API (no key). It returns the top three articles with their title, URL and a plain-text extract.
- An entry with `"endpoint": "https://..."` is a connected tool. AgentLab POSTs the checked arguments as JSON and gives the agent the JSON reply (or `{"text": ...}`). Requests go through the evaluator's safe client: HTTPS only, public addresses only, 20 seconds and 200 KB at most. A refused URL, an HTTP error or a timeout becomes a tool error (`tool_blocked`, `tool_http`, `tool_timeout`), not a failed run. Endpoints take no credentials yet.

Every other tool answers in this order:

1. `results`: a list of `{"when": {...}, "result": ...}`, used when every `when` value equals the call's argument.
2. `result`: one canned answer.
3. `simulate`: `true`, or `{"instructions": "scenario notes"}`. A model plays the tool from its schema, the arguments and the notes, at temperature 0, and must reply with JSON.

With none of these, the call fails with `no_executor`. Arguments are checked against the schema before anything answers, so a bad call never reaches the simulator. During `run` and `verify` the judge model plays the tool, so the agent under test never answers its own calls. In chat and inside teams, the agent's own model plays it. A tool span records `arguments`, `result` or `error`, and `simulated: true` when a model answered.

A test can check tool use:

```json
{
  "tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "KE-4521"}}],
  "forbidden_tools": ["issue_refund"],
  "behavior": "Tells the customer the courier the tool returned"
}
```

`"grounded": true` catches agents that have tools but still guess. The test fails if no tool returned a result, or if the judge finds a claim the tool results do not support or that contradicts them. A correct answer from memory still fails, because nothing checked it.

Each `tool_calls` entry needs a call with that name whose arguments include the given values. The call counts even if the tool then fails (an endpoint error or timeout), because the agent still chose the right tool; a call with invalid arguments does not count. Calling a `forbidden_tools` entry fails the test. When a test has either key, any call to an unknown tool or with invalid arguments also fails it. The judge sees every tool call and its result when grading `behavior`.

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
- `limits` are shared by the coordinator and all members in one request. `max_steps` (default 12) counts model calls. `max_tokens` counts `usage.total_tokens`, or about four characters per token when the provider omits usage. Each model call is checked before it starts. Once `max_steps` calls have run, the next one is blocked. Token use is only known after a call returns, so a finished call can take usage past `max_tokens`, and the next call is blocked. A blocked call adds an `llm` error span of kind `step_limit` or `spend_limit`. A request that finishes without needing another call ends normally, even if its usage passed `max_tokens`.
- A team trace has a top-level `router` span (`type`, `choice`, `confidence`, `reason`, `rule`), then an `agent` span for the coordinator (with `steps` and `tokens`). The coordinator's `llm` spans and each member's `agent` span sit under it, and each member's own spans sit under the member.
- Chat sends a `step.finished` event with `route` before the answer.
- `examples/support_team.json` holds a labelled routing set, and `tests/test_team.py` checks that the rules pass every case.

Existing databases need the managed team-span migration. With `DATABASE_URL`
exported to the intended direct Postgres connection, run from the repository root:

```sh
python -m alembic -c server/alembic.ini upgrade head
```

The head is `0003_team_span_types`, after the encrypted saved-key migration.
For an existing SQL-built database without Alembic history, first follow the
[guarded adoption procedure](../migrations/README.md) on a restored database copy;
it also supports the fourteen-table team schema that predates saved-key storage.
Do not apply the manual constraint change separately from the migration history.

Team requests send coordinator, judge, and member credential IDs through the same
saved-key validation and hydration path. Configure a stable `APP_ENCRYPTION_KEY`
to persist keys across restarts. The existing credential expiry still applies.

## Providers

`openrouter` works without a `base_url`. `meta` and `custom` need one on the credential or agent config.

## Diagnosis inputs

Rules run in order and the first match wins: `format`, `tool_arg`, `context_overflow`, `budget`, `safety`, `retrieval_miss`, `grounding`, `unsupported_claim`, `instruction_violation`. If none match, a judge suggests a rule. That result is stored as rule `judge`, with `evidence.suggested_rule`.

- `test_case.expected.reference` is the text a correct answer contains.
- `test_case.expected.source` is the source that should have been retrieved.
- A failed assertion or validator can set `category` to `format`, `safety` or `instruction`.
