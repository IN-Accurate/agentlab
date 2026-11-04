# Contributing to AgentLab

Thanks for helping improve AgentLab. This guide covers how to set up, how we work,
and what a pull request needs before it can merge.

## Set up

Follow [Run Locally](README.md#run-locally) in the README: Python 3.12, Node.js 20+,
PostgreSQL 16, and a `.env` copied from `.env.example`. Run the backend with
`evaluator.asgi:app` and the frontend with `npm run dev` in `client/`.

## Project layout

| Path | What lives there |
| :--- | :--- |
| `server/app/` | FastAPI API: routes, schemas, credentials, jobs |
| `server/evaluator/` | Model client, agents and teams, tools, runs, diagnosis, repair, verification |
| `server/migrations/` | Alembic migrations (the schema's source of truth) |
| `server/tests/` | pytest suites, run against a real Postgres test database |
| `client/src/` | React app: chat, Agent Manager, dashboard; shadcn/ui components in `components/ui/` |

## Workflow

1. **Branch from `main`.** Name branches by kind: `feature/…`, `fix/…`, `bugfix/…`, `docs/…`.
2. **Keep changes focused.** One concern per pull request is easiest to review. If you spot a bug
   outside your change, open an issue or mention it in the PR rather than folding it in.
3. **Write clear commits.** Use the imperative mood and say what changed and why, e.g.
   `Fix Improvements tab crash: declare hooks before the early returns`.
4. **Open a pull request** against `main`, describing what changed, how you tested it, and anything
   reviewers should look at closely.

## Before you open a pull request

Run the same checks CI runs, plus the client checks:

```sh
# Server
.venv/bin/ruff check server && .venv/bin/ruff format --check server
cd server && TEST_DATABASE_URL='postgresql://USER@localhost:5432/agentlab_test' ../.venv/bin/python -m pytest -q

# Client
cd client && npm test && npm run build
```

### Tests

* **Every behaviour change comes with a test.** Bug fixes get a test that reproduces the bug.
* **Prove the test can fail.** Before you push, revert or break the change and confirm the new test
  goes red, then restore it. A test that passes either way protects nothing.
* **Use real systems where we can.** Server tests run on a real Postgres database (its name must
  contain `test`); model calls are faked with `httpx.MockTransport` so tests stay fast and free.
* **Client tests** query by role and accessible name (`getByRole('button', { name: … })`) rather than
  class names, so restyling does not break them.

### Database changes

* Change the schema only through a new Alembic migration in `server/migrations/versions/`.
* Never run schema changes against a shared database; use your own local or branch database.

### Frontend conventions

* Build UI from the shadcn/ui components in `client/src/components/ui/` and Tailwind utilities.
* `client/src/styles.css` is the older stylesheet, kept in a `legacy` cascade layer under Tailwind's
  utilities. Prefer utilities for new work; remove legacy rules when you replace what they style.
* Declare React hooks before any early `return` in a component.
* Long or rarely used views load lazily; keep heavy imports out of the app shell.

## Code review

* Pull requests are reviewed by CodeRabbit and by a teammate. You can also run the CodeRabbit CLI
  locally before pushing: `coderabbit review --base-commit <main sha>`.
* Treat review comments as suggestions to verify: fix what is valid, and reply with the reason when
  you decline one.
* GitGuardian scans every push for secrets.

## Security

* **Never commit secrets.** API keys live in `.env` (git-ignored) or are saved encrypted through the
  app. Use placeholders such as `REPLACE_ME` in examples and docs.
* Do not add media, build output or other large binaries to the repository.
* Report a security problem privately to a maintainer instead of opening a public issue.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
