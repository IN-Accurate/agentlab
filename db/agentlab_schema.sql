-- AgentLab data model v0 (Postgres 15+)
-- Run top to bottom: tables are ordered so every foreign key target already exists.
-- gen_random_uuid() is built in from Postgres 13, so no extension is needed.

BEGIN;

-- 1. APP -------------------------------------------------------------------

CREATE TABLE projects (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Immutable: a repair creates a new row, never edits an existing one.
CREATE TABLE app_versions (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id         uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_version_id  uuid REFERENCES app_versions(id),
    config             jsonb NOT NULL DEFAULT '{}'::jsonb,  -- prompt, retrieval, tool schema, params
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- 2. TRACES ----------------------------------------------------------------

CREATE TABLE traces (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    app_version_id  uuid NOT NULL REFERENCES app_versions(id),
    input           text,
    output          text,
    status          text NOT NULL DEFAULT 'ok',
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE spans (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id        uuid NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    parent_span_id  uuid REFERENCES spans(id) ON DELETE CASCADE,
    sdk_span_id     text NOT NULL,   -- id assigned by the SDK, makes ingest idempotent
    type            text NOT NULL CHECK (type IN (
                    'llm', 'retrieval', 'tool', 'validation', 'router', 'agent')),
    started_at      timestamptz,
    latency_ms      integer,
    status          text NOT NULL DEFAULT 'ok',
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (trace_id, sdk_span_id)
);

CREATE TABLE retrieved_chunks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    span_id     uuid NOT NULL REFERENCES spans(id) ON DELETE CASCADE,
    rank        integer NOT NULL,
    text        text NOT NULL,
    source      text,
    score       double precision,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- 3. TESTS -----------------------------------------------------------------

CREATE TABLE test_cases (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_trace_id  uuid REFERENCES traces(id) ON DELETE SET NULL,  -- null for seed tests
    origin           text NOT NULL CHECK (origin IN ('seed', 'user', 'from_trace')),
    split            text NOT NULL DEFAULT 'dev' CHECK (split IN ('dev', 'holdout')),
    input            text NOT NULL,
    expected         jsonb NOT NULL DEFAULT '{}'::jsonb,
    archived_at      timestamptz,  -- soft delete; past run results are preserved
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE assertions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    test_case_id uuid NOT NULL REFERENCES test_cases(id) ON DELETE CASCADE,
    kind         text NOT NULL CHECK (kind IN ('deterministic', 'judge')),
    spec         jsonb NOT NULL DEFAULT '{}'::jsonb,
    position     integer NOT NULL DEFAULT 0,   -- deterministic assertions run first
    archived_at  timestamptz,  -- soft delete; past assertion results are preserved
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- 4. RUNS ------------------------------------------------------------------

CREATE TABLE runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    app_version_id  uuid REFERENCES app_versions(id),
    params          jsonb NOT NULL DEFAULT '{}'::jsonb,  -- model, temperature, seed
    run_id          text CONSTRAINT runs_run_id_unique UNIQUE,
    parent_run_id   text CONSTRAINT runs_parent_run_id_fkey REFERENCES runs(run_id) ON DELETE SET NULL,
    query           text,
    retrieved_chunks jsonb NOT NULL DEFAULT '[]'::jsonb,
    answer          text,
    grounding_score double precision CONSTRAINT runs_grounding_score_check CHECK (grounding_score >= 0 AND grounding_score <= 1),
    verdict         text CONSTRAINT runs_verdict_check CHECK (verdict IN ('pass', 'fail')),
    failure_type    text CONSTRAINT runs_failure_type_check CHECK (failure_type IN ('retrieval_miss', 'hallucination', 'prompt_issue')),
    diagnosis       text,
    model           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE run_results (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    test_case_id  uuid NOT NULL REFERENCES test_cases(id),
    trace_id      uuid REFERENCES traces(id),
    passed        boolean NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, test_case_id)
);

CREATE TABLE assertion_results (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_result_id  uuid NOT NULL REFERENCES run_results(id) ON DELETE CASCADE,
    assertion_id   uuid NOT NULL REFERENCES assertions(id),
    passed         boolean NOT NULL,
    evidence       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_result_id, assertion_id)
);

-- 5. FIX LOOP --------------------------------------------------------------

CREATE TABLE diagnoses (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trace_id    uuid NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    rule        text NOT NULL CHECK (rule IN (
                    'format', 'tool_arg', 'context_overflow', 'budget', 'safety',
                    'retrieval_miss', 'grounding', 'unsupported_claim',
                    'instruction_violation', 'judge')),
    confidence  double precision,
    evidence    jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE replays (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    original_trace_id  uuid NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    replay_trace_id    uuid NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    changed_variable   text NOT NULL,
    outcome_flipped    boolean,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE repairs (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    diagnosis_id   uuid NOT NULL REFERENCES diagnoses(id) ON DELETE CASCADE,
    new_version_id uuid UNIQUE REFERENCES app_versions(id),  -- one repair, one new version
    patch_type     text NOT NULL CHECK (patch_type IN (
                       'prompt', 'retrieval', 'tool_schema', 'validator', 'params')),
    patch          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE verifications (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repair_id          uuid NOT NULL REFERENCES repairs(id) ON DELETE CASCADE,
    baseline_run_id    uuid NOT NULL REFERENCES runs(id),
    candidate_run_id   uuid NOT NULL REFERENCES runs(id),
    target_passed      boolean NOT NULL,
    regressions_count  integer NOT NULL DEFAULT 0,
    holdout_passed     boolean NOT NULL,
    accepted           boolean NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CHECK (baseline_run_id <> candidate_run_id)
);

-- INDEXES (foreign keys are not indexed automatically in Postgres) ---------

CREATE INDEX idx_app_versions_project   ON app_versions(project_id);
CREATE INDEX idx_traces_version         ON traces(app_version_id);
CREATE INDEX idx_spans_trace            ON spans(trace_id);
CREATE INDEX idx_spans_parent           ON spans(parent_span_id);
CREATE INDEX idx_chunks_span            ON retrieved_chunks(span_id);
CREATE INDEX idx_test_cases_project     ON test_cases(project_id, split);
CREATE INDEX idx_assertions_test_case   ON assertions(test_case_id);
CREATE INDEX idx_runs_version           ON runs(app_version_id);
CREATE INDEX idx_run_results_test_case  ON run_results(test_case_id);
CREATE INDEX idx_assertion_results_rr   ON assertion_results(run_result_id);
CREATE INDEX idx_diagnoses_trace        ON diagnoses(trace_id);
CREATE INDEX idx_replays_original       ON replays(original_trace_id);
CREATE INDEX idx_repairs_diagnosis      ON repairs(diagnosis_id);
CREATE INDEX idx_verifications_repair   ON verifications(repair_id);
CREATE INDEX idx_runs_parent_run_id       ON runs(parent_run_id);

-- TRIGGERS -----------------------------------------------------------------

-- Version immutability is also enforced by an ORM event; this trigger closes
-- the bypass for Core UPDATE statements issued by trusted server-side code.
CREATE OR REPLACE FUNCTION reject_app_version_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable_app_version: create a new version instead of updating %', OLD.id;
END;
$$;

CREATE TRIGGER no_app_version_update BEFORE UPDATE ON app_versions
FOR EACH ROW EXECUTE FUNCTION reject_app_version_update();

-- 6. SAVED CREDENTIALS ----------------------------------------------------
-- Only Fernet ciphertext and non-secret metadata are durable. The encryption
-- key is supplied by APP_ENCRYPTION_KEY and must never be stored in Postgres.
CREATE TABLE saved_credentials (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    provider         text NOT NULL,
    base_url         text,
    model            text NOT NULL,
    judge_model      text,
    ciphertext       text NOT NULL,
    key_hint         text NOT NULL,
    key_fingerprint  jsonb NOT NULL,
    expires_at       timestamptz NOT NULL
);

CREATE INDEX idx_saved_credentials_expires_at ON saved_credentials(expires_at);

COMMIT;
