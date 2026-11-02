# AgentLab Web

ChatGPT-style frontend for AgentLab: a conversational workspace (connect an agent, describe expected behavior, run tests) plus one unified dashboard (Overview, Runs, Traces, Improvements). Built against the Final Design Figma frames. React 19 + Vite 6, no component library — hand-rolled SVG for the pass-rate chart and span waterfall.

## Quick start

```sh
cd client
npm install
npm run dev
```

Open http://localhost:5173. The dev server proxies `/v1`, `/health`, and `/openapi.json` to the backend at `http://localhost:8000`, so run the API alongside (see the repo-root README).

## Scripts

| Command           | Purpose                                  |
| ----------------- | ---------------------------------------- |
| `npm run dev`     | Dev server with hot reload (`:5173`)     |
| `npm run build`   | Production build into `client/dist/`     |
| `npm run preview` | Serve the production build locally       |

## Configuration

| Variable       | Default | Purpose                                                    |
| -------------- | ------- | ---------------------------------------------------------- |
| `VITE_API_URL` | `""`    | Backend origin. Empty means same-origin (uses dev proxy).  |

For a production deploy, set `VITE_API_URL` to the API origin (or serve `dist/` behind the same host that serves `/v1`) and run `npm run build`.

## Backend dependency

All data comes from the FastAPI backend — there are no baked-in fixtures, so an empty database shows empty states rather than sample content:

- Dashboard reads: projects, versions, dashboard series, runs/summaries/results, traces, diagnoses, repairs, verifications.
- Chat connect creates a credential → project → version, then checks the connection live.
- Chat messages and test runs stream over `POST /v1/projects/{id}/chat` (SSE) and require an **evaluator** configured on the backend; without one the UI shows an honest error state.

## Layout

```
client/
├── index.html            # Entry HTML (preload for self-hosted Montserrat)
├── vite.config.js        # Dev proxy for /v1 → localhost:8000
├── public/fonts/         # Self-hosted Inter + Montserrat variable woff2, OFL texts
└── src/
    ├── main.jsx          # React root
    ├── App.jsx           # Sidebar shell, chat/dashboard routing, persistence
    ├── LogoMark.jsx      # Inline sidebar logo (mirrors assets/logo-mark.svg)
    ├── assets/           # logo-mark.svg source of truth
    ├── api.js            # fetch wrapper, SSE chat client, job polling
    ├── hooks.js          # useFetch (loading/error/retry)
    ├── ui.jsx            # Badges, empty/error states, fields, modal, formatters
    ├── chat/             # ConnectForm, Composer, TestCard, ChatView
    ├── dashboard/        # DashboardView + Overview/Runs/Traces/Improvements tabs
    └── styles.css        # Design tokens + all component styles
```

Chats (titles, messages, connection metadata — never API keys) persist in `localStorage`. UI location (view, active chat, dashboard tab/project) also persists and restores on reload; drill selections (run/result/trace/review) reset.
