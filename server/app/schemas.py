import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    create_model,
    model_validator,
)


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def reject_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower().replace("-", "_") in {
                "api_key",
                "authorization",
                "password",
                "access_token",
            }:
                raise ValueError("Secrets belong only in the credential endpoint")
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)
    elif isinstance(value, str) and re.search(r"\bsk-|\bBearer\s+", value, re.I):
        raise ValueError("Secrets belong only in the credential endpoint")


class CredentialCreate(RequestModel):
    provider: Literal["openrouter", "meta", "custom"] = "openrouter"
    base_url: str | None = None
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = Field(min_length=8, max_length=4096, examples=["REPLACE_ME"])
    judge_model: str | None = None
    ttl_seconds: int = Field(default=7200, ge=60, le=86400)


class CredentialResponse(BaseModel):
    credential_id: UUID
    provider: str
    model: str
    key_hint: str
    expires_at: datetime


class Credentials(RequestModel):
    credential_id: UUID
    judge_credential_id: UUID | None = None


class ProjectCreate(RequestModel):
    name: str = Field(min_length=1, max_length=200, examples=["Acme Support"])


class AgentConfig(RequestModel):
    type: Literal["hosted", "openai_compatible"] = "hosted"
    base_url: str | None = None
    model: str | None = None


class AgentParams(RequestModel):
    temperature: float | None = Field(None, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int | None = Field(None, ge=1, le=1000000)
    seed: int | None = None


class RetrievalConfig(RequestModel):
    top_k: int = Field(default=3, ge=0, le=100)
    filters: dict[str, Any] = Field(default_factory=dict)
    min_score: float = Field(default=0, allow_inf_nan=False)


class KnowledgeChunk(RequestModel):
    id: str
    text: str = Field(max_length=32000)
    source: str


class HostedConfig(RequestModel):
    system_prompt: str = Field(default="", max_length=32000)
    knowledge_base: list[KnowledgeChunk] = Field(default_factory=list, max_length=1000)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    params: AgentParams = Field(default_factory=AgentParams)
    grounding_rule: str = Field(default="", max_length=16000)
    validators: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    @model_validator(mode="after")
    def no_secret_fields(self):
        reject_secrets(self.model_dump())
        return self


class VersionCreate(RequestModel):
    config: HostedConfig = Field(default_factory=HostedConfig)


class ConnectionCreate(Credentials):
    agent: AgentConfig


class ChunkCreate(RequestModel):
    rank: int = Field(ge=1)
    text: str = Field(max_length=32000)
    source: str | None = None
    score: float | None = Field(default=None, allow_inf_nan=False)


class SpanCreate(RequestModel):
    sdk_span_id: str = Field(min_length=1, max_length=200)
    parent_sdk_span_id: str | None = None
    type: Literal["llm", "retrieval", "tool", "validation"]
    started_at: AwareDatetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    status: Literal["ok", "error"] = "ok"
    payload: dict[str, Any] = Field(default_factory=dict)
    chunks: list[ChunkCreate] = Field(default_factory=list, max_length=100)


class TraceCreate(RequestModel):
    id: UUID
    app_version_id: UUID
    input: str | None = Field(default=None, max_length=32000)
    output: str | None = Field(default=None, max_length=32000)
    status: Literal["ok", "error"] = "ok"
    spans: list[SpanCreate] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def valid_tree(self):
        parents = {span.sdk_span_id: span.parent_sdk_span_id for span in self.spans}
        if len(parents) != len(self.spans):
            raise ValueError("sdk_span_id must be unique within an ingest request")
        for identifier in parents:
            seen = set()
            current = identifier
            while current is not None:
                if current in seen:
                    raise ValueError("Span parents must not form a cycle")
                if current not in parents:
                    break
                seen.add(current)
                current = parents[current]
        return self


class TestCreate(RequestModel):
    input: str = Field(min_length=1, max_length=32000)
    expected: dict[str, Any] = Field(default_factory=dict)
    origin: Literal["seed", "user", "from_trace"] = "user"
    split: Literal["dev", "holdout"] = "dev"
    source_trace_id: UUID | None = None


class TestUpdate(RequestModel):
    input: str | None = Field(default=None, min_length=1, max_length=32000)
    expected: dict[str, Any] | None = None
    split: Literal["dev", "holdout"] | None = None


class AssertionCreate(RequestModel):
    kind: Literal["deterministic", "judge"]
    spec: dict[str, Any] = Field(default_factory=dict)
    position: int = Field(default=0, ge=0)


class AssertionUpdate(RequestModel):
    kind: Literal["deterministic", "judge"] | None = None
    spec: dict[str, Any] | None = None
    position: int | None = Field(default=None, ge=0)


class RunCreate(Credentials):
    version_id: UUID
    split: Literal["dev", "holdout"] | None = None
    test_case_ids: list[UUID] | None = Field(default=None, max_length=1000)
    params: AgentParams = Field(default_factory=AgentParams)


class DiagnoseRequest(RequestModel):
    credential_id: UUID | None = None
    judge_credential_id: UUID | None = None


class ReplayCreate(Credentials):
    changed_variable: str
    new_value: Any


class RepairCreate(Credentials):
    patch_type: (
        Literal["prompt", "retrieval", "tool_schema", "validator", "params"] | None
    ) = None
    patch: dict[str, Any] | None = None


class SplitCreate(RequestModel):
    pass


class SeedCreate(Credentials):
    version_id: UUID
    count: int = Field(default=5, ge=1, le=30)
    sample_inputs: list[str] = Field(default_factory=list, max_length=30)


class ChatMessage(RequestModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=32000)


class ChatCreate(Credentials):
    message: str = Field(min_length=1, max_length=32000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=100)


class JobResponse(BaseModel):
    job_id: UUID
    status: Literal["queued", "running", "completed", "failed"]
    progress: float = Field(default=0, ge=0, le=1)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class Page(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: UUID | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class MetaResponse(BaseModel):
    evaluator_configured: bool = False
    version: str
    providers: list[str]
    agent_types: list[str]
    sse_events: list[str]


class DetailResponse(BaseModel):
    model_config = ConfigDict(extra="allow")


def entity(model):
    fields = {}
    for column in model.__table__.columns:
        kind = column.type.python_type
        fields[column.name] = (kind | None if column.nullable else kind, ...)
    return create_model(
        model.__name__ + "Response",
        __config__=ConfigDict(from_attributes=True),
        **fields,
    )


class TraceDetail(BaseModel):
    id: UUID
    app_version_id: UUID
    input: str | None
    output: str | None
    status: str
    created_at: datetime
    spans: list[dict[str, Any]]
    span_tree: list[dict[str, Any]]
