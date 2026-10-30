from uuid import UUID

from sqlalchemy import select

from app.models import AppVersion, RetrievedChunk, Span, Trace


async def setup_agent(client, config, key="REPLACE_ME"):
    project = await client.post("/v1/projects", json={"name": "Acme Support"})
    assert project.status_code == 201, project.text
    version = await client.post(
        f"/v1/projects/{project.json()['id']}/versions", json={"config": config}
    )
    assert version.status_code == 201, version.text
    credential = await client.post(
        "/v1/credentials",
        json={"provider": "custom", "model": "example-model", "api_key": key},
    )
    assert credential.status_code == 201, credential.text
    return UUID(version.json()["id"]), credential.json()["credential_id"]


async def test_hosted_agent_persists_retrieval_tree(api):
    from app.services.agent import run_agent

    client, app = api
    version_id, credential_id = await setup_agent(
        client,
        {
            "system_prompt": "Answer using retrieved context.",
            "knowledge_base": [
                {
                    "id": "refund-policy",
                    "text": "Refunds are available within 30 days.",
                    "source": "policy",
                }
            ],
            "retrieval": {"top_k": 1},
        },
    )
    async with app.state.sessions() as db, db.begin():
        version = await db.get(AppVersion, version_id)
        trace = await run_agent(
            db, app.state, version, "What is the refund policy?", credential_id
        )
        trace_id = trace.id
    async with app.state.sessions() as db:
        trace = await db.get(Trace, trace_id)
        spans = list(
            (await db.scalars(select(Span).where(Span.trace_id == trace_id))).all()
        )
        assert trace.status == "ok"
        assert trace.output
        by_type = {span.type: span for span in spans}
        assert {"retrieval", "llm", "validation"} <= by_type.keys()
        assert by_type["llm"].parent_span_id == by_type["retrieval"].id
        chunks = list(
            (
                await db.scalars(
                    select(RetrievedChunk).where(
                        RetrievedChunk.span_id == by_type["retrieval"].id
                    )
                )
            ).all()
        )
        assert len(chunks) == 1
        assert chunks[0].source == "policy"
        assert "30 days" in chunks[0].text


async def test_external_fake_agent_records_usage_without_invented_retrieval(api):
    from app.services.agent import run_agent

    client, app = api
    version_id, credential_id = await setup_agent(
        client,
        {"system_prompt": "Answer briefly.", "agent": {"type": "openai_compatible"}},
    )
    async with app.state.sessions() as db, db.begin():
        trace = await run_agent(
            db, app.state, await db.get(AppVersion, version_id), "Hello", credential_id
        )
        trace_id = trace.id
    async with app.state.sessions() as db:
        trace = await db.get(Trace, trace_id)
        spans = list(
            (await db.scalars(select(Span).where(Span.trace_id == trace_id))).all()
        )
        llm = next(span for span in spans if span.type == "llm")
        assert trace.status == "ok"
        assert trace.output
        assert llm.status == "ok"
        assert llm.latency_ms is not None and llm.latency_ms >= 0
        assert isinstance(llm.payload["usage"], dict)
        assert "total_tokens" in llm.payload["usage"]
        assert not any(span.type == "retrieval" for span in spans)


async def test_agent_redacts_registered_secret_before_persistence(api, monkeypatch):
    from app.services import llm
    from app.services.agent import run_agent

    client, app = api
    secret = "REPLACE_ME_PRIVATE_VALUE"
    version_id, credential_id = await setup_agent(
        client, {"system_prompt": "Answer briefly."}, secret
    )

    async def provider(*args, **kwargs):
        return {
            "content": "Unexpected echo " + secret,
            "usage": {"total_tokens": 10},
            "finish_reason": secret,
            "tool_calls": [],
        }

    monkeypatch.setattr(llm, "complete", provider)
    async with app.state.sessions() as db, db.begin():
        trace = await run_agent(
            db,
            app.state,
            await db.get(AppVersion, version_id),
            "Input " + secret,
            credential_id,
        )
        trace_id = trace.id
    async with app.state.sessions() as db:
        trace = await db.get(Trace, trace_id)
        payloads = list(
            (
                await db.scalars(select(Span.payload).where(Span.trace_id == trace_id))
            ).all()
        )
        assert secret not in trace.input
        assert secret not in trace.output
        assert secret not in repr(payloads)
        assert "[REDACTED]" in trace.input
        assert "[REDACTED]" in trace.output
