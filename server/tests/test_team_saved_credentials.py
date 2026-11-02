"""The merged team evaluator must hydrate every saved key before model calls."""

import json
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import update

from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.models import SavedCredential
from app.schemas import ChatCreate
from app.services.credentials import release
from app.services.evaluator import context, prepare
from evaluator import Evaluator


@pytest.fixture
async def saved_team_apps(api, monkeypatch):
    _, existing = api
    encryption_key = Fernet.generate_key().decode()
    calls = []
    turns = iter(("billing", "shipping", None))

    def handle(request):
        body = json.loads(request.content)
        model = body["model"]
        calls.append((model, request.headers["authorization"]))
        message = {"content": ""}
        if model == "coordinator-model":
            member = next(turns)
            if member is None:
                message["content"] = "Refund approved; parcel is in Pune."
            else:
                message["tool_calls"] = [
                    {
                        "id": member,
                        "type": "function",
                        "function": {
                            "name": f"ask_{member}",
                            "arguments": json.dumps({"task": f"Check {member}"}),
                        },
                    }
                ]
        elif model == "judge-model":
            message["content"] = json.dumps(
                {"passed": True, "rationale": "Both answers combined"}
            )
        elif model == "billing-model":
            message["content"] = "Refund approved"
        elif model == "shipping-model":
            message["content"] = "Parcel is in Pune"
        else:
            pytest.fail(f"Unexpected model {model}")
        return httpx.Response(
            200,
            json={
                "model": model,
                "choices": [{"message": message, "finish_reason": "stop"}],
            },
        )

    for module in ("evaluator", "chat"):
        monkeypatch.setattr(
            f"app.services.{module}.safe_http_client",
            lambda settings: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )

    async with AsyncExitStack() as stack:

        async def start():
            app = create_app(
                Settings(
                    _env_file=None,
                    database_url=existing.state.settings.database_url,
                    app_encryption_key=encryption_key,
                ),
                evaluator=Evaluator(),
            )
            await app.state.engine.dispose()
            app.state.engine = existing.state.engine
            app.state.sessions = existing.state.sessions
            await stack.enter_async_context(app.router.lifespan_context(app))
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                    base_url="http://testserver",
                )
            )
            return client, app

        yield start, calls


async def credential(http, name):
    response = await http.post(
        "/v1/credentials",
        json={
            "provider": "openrouter",
            "model": f"{name}-model",
            "api_key": f"{name}-saved-secret-key",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["credential_id"]


async def agent(http, name, config=None):
    project = await http.post("/v1/projects", json={"name": name})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    version = await http.post(
        f"/v1/projects/{project_id}/versions", json={"config": config or {}}
    )
    assert version.status_code == 201, version.text
    return project_id, version.json()["id"], await credential(http, name)


async def provision(http):
    billing, _, billing_key = await agent(http, "billing")
    shipping, _, shipping_key = await agent(http, "shipping")
    team, version, team_key = await agent(
        http,
        "coordinator",
        {
            "team": {
                "members": [
                    {"name": "billing", "project_id": billing},
                    {"name": "shipping", "project_id": shipping},
                ],
                "router": {
                    "rules": [{"member": "billing", "keywords": ["refund"]}],
                    "fallback": "shipping",
                },
            }
        },
    )
    judge_key = await credential(http, "judge")
    outsider = await credential(http, "outsider")
    case = await http.post(
        f"/v1/projects/{team}/test-cases",
        json={
            "input": "Refund my order and locate the parcel.",
            "expected": {"behavior": "Answer both refund and shipping questions."},
        },
    )
    assert case.status_code == 201, case.text
    options = {
        "version_id": version,
        "credential_id": team_key,
        "judge_credential_id": judge_key,
        "member_credentials": {billing: billing_key, shipping: shipping_key},
    }
    return team, options, outsider


async def test_restarted_team_hydrates_coordinator_judge_and_member_keys(
    saved_team_apps,
):
    start, calls = saved_team_apps
    first, old_app = await start()
    project, options, outsider = await provision(first)
    second, restarted = await start()
    # Drop the new worker's restored cache: the request must fetch the allowed
    # coordinator, judge and member records from Postgres, not the old worker.
    for identifier in (
        options["credential_id"],
        options["judge_credential_id"],
        outsider,
        *options["member_credentials"].values(),
    ):
        old_app.state.credential_store.delete(identifier)
        restarted.state.credential_store.delete(identifier)

    response = await second.post(f"/v1/projects/{project}/runs", json=options)
    assert response.status_code == 202, response.text
    job = (await second.get(f"/v1/jobs/{response.json()['job_id']}")).json()
    assert job["status"] == "completed", job
    assert job["result"]["passed"] == 1
    assert calls == [
        ("coordinator-model", "Bearer coordinator-saved-secret-key"),
        ("billing-model", "Bearer billing-saved-secret-key"),
        ("coordinator-model", "Bearer coordinator-saved-secret-key"),
        ("shipping-model", "Bearer shipping-saved-secret-key"),
        ("coordinator-model", "Bearer coordinator-saved-secret-key"),
        ("judge-model", "Bearer judge-saved-secret-key"),
    ]

    results = (await second.get(f"/v1/runs/{job['result']['run_id']}/results")).json()[
        "items"
    ]
    assert len(results) == 1 and results[0]["passed"] is True
    trace_response = await second.get(f"/v1/traces/{results[0]['trace_id']}")
    trace = trace_response.json()
    assert trace["status"] == "ok"
    router, coordinator = trace["span_tree"][:2]
    assert router["type"] == "router" and router["payload"]["choice"] == "billing"
    assert (
        coordinator["type"] == "agent"
        and coordinator["payload"]["role"] == "coordinator"
    )
    members = [span for span in coordinator["children"] if span["type"] == "agent"]
    assert [member["payload"]["name"] for member in members] == ["billing", "shipping"]
    assert all(
        any(span["type"] == "llm" for span in member["children"]) for member in members
    )
    assert "saved-secret-key" not in trace_response.text

    chat = ChatCreate(
        message="Hello", **{k: v for k, v in options.items() if k != "version_id"}
    )
    async with restarted.state.sessions() as db:
        _, allowed = await prepare(
            db, restarted.state, "chat", chat, project_id=UUID(project)
        )
    try:
        assert set(allowed) == {
            options["credential_id"],
            options["judge_credential_id"],
            *options["member_credentials"].values(),
        }
        credentials = context(None, restarted.state, allowed)["credentials"]
        with pytest.raises(AppError) as unauthorized:
            credentials["decrypt_for_call"](outsider)
        assert unauthorized.value.code == "credential_not_authorized"
        assert restarted.state.credential_store.get(outsider) is None
    finally:
        release(allowed)


@pytest.mark.parametrize("role", ["coordinator", "judge", "member"])
@pytest.mark.parametrize("invalidation", ["revoked", "expired"])
async def test_stale_team_worker_rechecks_all_saved_keys(
    saved_team_apps, role, invalidation
):
    start, calls = saved_team_apps
    first, old_app = await start()
    project, options, _ = await provision(first)
    second, restarted = await start()
    identifier = {
        "coordinator": options["credential_id"],
        "judge": options["judge_credential_id"],
        "member": next(iter(options["member_credentials"].values())),
    }[role]
    assert restarted.state.credential_store.get(identifier) is not None
    if invalidation == "revoked":
        assert (await first.delete(f"/v1/credentials/{identifier}")).status_code == 204
    else:
        async with old_app.state.sessions() as db, db.begin():
            await db.execute(
                update(SavedCredential.__table__)
                .where(SavedCredential.id == UUID(identifier))
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
    response = await second.post(f"/v1/projects/{project}/runs", json=options)
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "credential_expired"
    assert restarted.state.credential_store.get(identifier) is None
    assert calls == []
