from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.errors import AppError


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Project(Record, Base):
    __tablename__ = "projects"
    name: Mapped[str] = mapped_column(Text)


class AppVersion(Record, Base):
    __tablename__ = "app_versions"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    parent_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_versions.id")
    )
    config: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    __table_args__ = (Index("idx_app_versions_project", "project_id"),)


class Trace(Record, Base):
    __tablename__ = "traces"
    app_version_id: Mapped[UUID] = mapped_column(ForeignKey("app_versions.id"))
    input: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="ok", server_default="ok")
    __table_args__ = (Index("idx_traces_version", "app_version_id"),)


class Span(Record, Base):
    __tablename__ = "spans"
    trace_id: Mapped[UUID] = mapped_column(ForeignKey("traces.id", ondelete="CASCADE"))
    parent_span_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("spans.id", ondelete="CASCADE")
    )
    sdk_span_id: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, default="ok", server_default="ok")
    payload: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    __table_args__ = (
        UniqueConstraint("trace_id", "sdk_span_id"),
        CheckConstraint("type IN ('llm', 'retrieval', 'tool', 'validation')"),
        Index("idx_spans_trace", "trace_id"),
        Index("idx_spans_parent", "parent_span_id"),
    )


class RetrievedChunk(Record, Base):
    __tablename__ = "retrieved_chunks"
    span_id: Mapped[UUID] = mapped_column(ForeignKey("spans.id", ondelete="CASCADE"))
    rank: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Double)
    __table_args__ = (Index("idx_chunks_span", "span_id"),)


class TestCase(Record, Base):
    __test__ = False
    __tablename__ = "test_cases"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE")
    )
    source_trace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("traces.id", ondelete="SET NULL")
    )
    origin: Mapped[str] = mapped_column(Text)
    split: Mapped[str] = mapped_column(Text, default="dev", server_default="dev")
    input: Mapped[str] = mapped_column(Text)
    expected: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("origin IN ('seed', 'user', 'from_trace')"),
        CheckConstraint("split IN ('dev', 'holdout')"),
        Index("idx_test_cases_project", "project_id", "split"),
    )


class Assertion(Record, Base):
    __tablename__ = "assertions"
    test_case_id: Mapped[UUID] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("kind IN ('deterministic', 'judge')"),
        Index("idx_assertions_test_case", "test_case_id"),
    )


class Run(Record, Base):
    __tablename__ = "runs"
    app_version_id: Mapped[UUID] = mapped_column(ForeignKey("app_versions.id"))
    params: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    __table_args__ = (Index("idx_runs_version", "app_version_id"),)


class RunResult(Record, Base):
    __tablename__ = "run_results"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    test_case_id: Mapped[UUID] = mapped_column(ForeignKey("test_cases.id"))
    trace_id: Mapped[UUID | None] = mapped_column(ForeignKey("traces.id"))
    passed: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (
        UniqueConstraint("run_id", "test_case_id"),
        Index("idx_run_results_test_case", "test_case_id"),
    )


class AssertionResult(Record, Base):
    __tablename__ = "assertion_results"
    run_result_id: Mapped[UUID] = mapped_column(
        ForeignKey("run_results.id", ondelete="CASCADE")
    )
    assertion_id: Mapped[UUID] = mapped_column(ForeignKey("assertions.id"))
    passed: Mapped[bool] = mapped_column(Boolean)
    evidence: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    __table_args__ = (
        UniqueConstraint("run_result_id", "assertion_id"),
        Index("idx_assertion_results_rr", "run_result_id"),
    )


class Diagnosis(Record, Base):
    __tablename__ = "diagnoses"
    trace_id: Mapped[UUID] = mapped_column(ForeignKey("traces.id", ondelete="CASCADE"))
    rule: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Double)
    evidence: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    __table_args__ = (
        CheckConstraint(
            "rule IN ('format', 'tool_arg', 'context_overflow', 'budget', 'safety', 'retrieval_miss', 'grounding', 'unsupported_claim', 'instruction_violation', 'judge')"
        ),
        Index("idx_diagnoses_trace", "trace_id"),
    )


class Replay(Record, Base):
    __tablename__ = "replays"
    original_trace_id: Mapped[UUID] = mapped_column(
        ForeignKey("traces.id", ondelete="CASCADE")
    )
    replay_trace_id: Mapped[UUID] = mapped_column(
        ForeignKey("traces.id", ondelete="CASCADE")
    )
    changed_variable: Mapped[str] = mapped_column(Text)
    outcome_flipped: Mapped[bool | None] = mapped_column(Boolean)
    __table_args__ = (Index("idx_replays_original", "original_trace_id"),)


class Repair(Record, Base):
    __tablename__ = "repairs"
    diagnosis_id: Mapped[UUID] = mapped_column(
        ForeignKey("diagnoses.id", ondelete="CASCADE")
    )
    new_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app_versions.id"), unique=True
    )
    patch_type: Mapped[str] = mapped_column(Text)
    patch: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    __table_args__ = (
        CheckConstraint(
            "patch_type IN ('prompt', 'retrieval', 'tool_schema', 'validator', 'params')"
        ),
        Index("idx_repairs_diagnosis", "diagnosis_id"),
    )


class Verification(Record, Base):
    __tablename__ = "verifications"
    repair_id: Mapped[UUID] = mapped_column(
        ForeignKey("repairs.id", ondelete="CASCADE")
    )
    baseline_run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"))
    candidate_run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"))
    target_passed: Mapped[bool] = mapped_column(Boolean)
    regressions_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    holdout_passed: Mapped[bool] = mapped_column(Boolean)
    accepted: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (
        CheckConstraint("baseline_run_id <> candidate_run_id"),
        Index("idx_verifications_repair", "repair_id"),
    )


@event.listens_for(AppVersion, "before_update")
def immutable_version(mapper, connection, target):
    raise AppError(
        "immutable_version",
        "Create a new version instead of updating an existing one",
        409,
    )


@event.listens_for(Session, "before_flush")
def redact_persisted_values(session, flush_context, instances):
    redactor = session.info.get("redactor")
    if redactor is None:
        return
    for row in session.new | session.dirty:
        if not isinstance(row, Base):
            continue
        for column in row.__table__.columns:
            value = getattr(row, column.name)
            if isinstance(value, (str, dict, list)):
                setattr(row, column.name, redactor.scrub(value))
