import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

import evaluator.llm
from app.models import Trace
from evaluator import Evaluator
from evaluator.team import route

EXAMPLE = json.loads(
    (Path(__file__).parents[1] / "evaluator/examples/support_team.json").read_text()
)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(evaluator.llm, "BACKOFF_SECONDS", 0)


def reply(text="", tool_calls=None, tokens=None):
    message = {"content": text, "tool_calls": tool_calls}
    body = {"model": "m", "choices": [{"message": message, "finish_reason": "stop"}]}
    if tokens is not None:
        body["usage"] = {"total_tokens": tokens}
    return httpx.Response(200, json=body)


def call(name, arguments, identifier="1"):
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def events(text):
    parsed = []
    for frame in text.replace("\r\n", "\n").strip().split("\n\n"):
        lines = frame.split("\n")
        data = next(x[5:] for x in lines if x.startswith("data:"))
        parsed.append((lines[0][6:].strip(), json.loads(data)))
    return parsed


@pytest.mark.parametrize("case", EXAMPLE["labelled"], ids=lambda c: c["input"])
def test_rules_router_passes_its_labelled_set(case):
    assert route(EXAMPLE["router"], case["input"])["choice"] == case["member"]


def test_unmatched_input_goes_to_fallback_and_says_why():
    decision = route(EXAMPLE["router"], "What are your opening hours?")
    assert decision == {
        "type": "rules",
        "choice": "general",
        "confidence": 0.0,
        "reason": "No rule matched, so the fallback member answers",
        "rule": None,
    }
    matched = route(EXAMPLE["router"], "refund please")
    assert matched["confidence"] == 1.0
    assert matched["reason"] == "Rule 1 matched keyword 'refund'"


class Agents:
    """Model endpoint fake that records which key each model call used."""

    def __init__(self, http, app, monkeypatch, replies):
        self.http, self.app, self.replies, self.calls = http, app, replies, []
        for module in ("evaluator", "chat"):
            monkeypatch.setattr(
                f"app.services.{module}.safe_http_client",
                lambda settings: httpx.AsyncClient(
                    transport=httpx.MockTransport(self.handle)
                ),
            )
        app.state.evaluator = Evaluator()

    def handle(self, request):
        body = json.loads(request.content)
        key = request.headers["authorization"].removeprefix("Bearer ")
        self.calls.append((key, body["model"], body))
        return self.replies[body["model"]](body)

    async def agent(self, name, config=None):
        """A connected agent: its own project, version and key."""
        project = (await self.http.post("/v1/projects", json={"name": name})).json()
        version = await self.http.post(
            f"/v1/projects/{project['id']}/versions", json={"config": config or {}}
        )
        assert version.status_code == 201, version.text
        credential = await self.http.post(
            "/v1/credentials",
            json={
                "provider": "openrouter",
                "model": f"{name}-model",
                "api_key": f"{name}-secret-key",
            },
        )
        return project["id"], credential.json()["credential_id"]

    async def trace(self):
        async with self.app.state.sessions() as db:
            row = await db.scalar(select(Trace).order_by(Trace.created_at.desc()))
        return (await self.http.get(f"/v1/traces/{row.id}")).json()


def team_config(members, limits=None):
    team = {
        "members": [
            {"name": name, "project_id": project, "description": f"Handles {name}"}
            for name, project in members.items()
        ],
        "router": {
            "rules": [{"member": "billing", "keywords": ["refund"]}],
            "fallback": "shipping",
        },
    }
    if limits:
        team["limits"] = limits
    return {"system_prompt": "You run Acme support.", "team": team}


async def test_two_agents_run_side_by_side_on_their_own_keys(api, monkeypatch):
    http, app = api
    replies = {
        "alpha-model": lambda body: reply("alpha answer"),
        "beta-model": lambda body: reply("beta answer"),
    }
    agents = Agents(http, app, monkeypatch, replies)
    alpha, alpha_key = await agents.agent("alpha")
    beta, beta_key = await agents.agent("beta")
    for project, key, answer in (
        (alpha, alpha_key, "alpha answer"),
        (beta, beta_key, "beta answer"),
    ):
        body = {"message": "hi", "credential_id": key}
        response = await http.post(f"/v1/projects/{project}/chat", json=body)
        assert ("message.delta", {"text": answer}) in events(response.text)
        trace = await agents.trace()
        versions = await http.get(f"/v1/projects/{project}/versions")
        assert trace["app_version_id"] in [v["id"] for v in versions.json()["items"]]
    assert [(key, model) for key, model, _ in agents.calls] == [
        ("alpha-secret-key", "alpha-model"),
        ("beta-secret-key", "beta-model"),
    ]


async def test_coordinator_delegates_and_combines_with_nested_trace(api, monkeypatch):
    http, app = api
    turns = iter(
        [
            reply(tool_calls=[call("ask_billing", {"task": "Refund order 7?"})]),
            reply(
                tool_calls=[call("ask_shipping", {"task": "Where is order 7?"}, "2")]
            ),
            reply("Your refund is approved and the parcel is in Pune."),
        ]
    )
    replies = {
        "coordinator-model": lambda body: next(turns),
        "billing-model": lambda body: reply("Refund approved"),
        "shipping-model": lambda body: reply("Parcel is in Pune"),
    }
    agents = Agents(http, app, monkeypatch, replies)
    billing, billing_key = await agents.agent("billing")
    shipping, shipping_key = await agents.agent("shipping")
    team, team_key = await agents.agent(
        "coordinator", team_config({"billing": billing, "shipping": shipping})
    )
    body = {
        "message": "I want a refund for order 7",
        "credential_id": team_key,
        "member_credentials": {billing: billing_key, shipping: shipping_key},
    }
    response = await http.post(f"/v1/projects/{team}/chat", json=body)
    parsed = events(response.text)
    assert parsed[0][0] == "step.finished"
    assert parsed[0][1]["route"]["choice"] == "billing"
    assert (
        "message.delta",
        {"text": "Your refund is approved and the parcel is in Pune."},
    ) in parsed

    # Every model call used the key of the agent it ran as.
    for key, model, _ in agents.calls:
        assert key == model.replace("-model", "-secret-key")
    final = agents.calls[-1][2]["messages"]
    tool_answers = [json.loads(m["content"]) for m in final if m["role"] == "tool"]
    assert tool_answers == [
        {"answer": "Refund approved"},
        {"answer": "Parcel is in Pune"},
    ]

    trace = await agents.trace()
    assert trace["status"] == "ok"
    router, coordinator = trace["span_tree"][:2]
    assert router["type"] == "router"
    assert {k: router["payload"][k] for k in ("type", "choice", "confidence")} == {
        "type": "rules",
        "choice": "billing",
        "confidence": 1.0,
    }
    assert router["payload"]["reason"] == "Rule 1 matched keyword 'refund'"
    assert coordinator["type"] == "agent"
    assert coordinator["payload"]["role"] == "coordinator"
    assert coordinator["payload"]["steps"] == 5
    members = [c for c in coordinator["children"] if c["type"] == "agent"]
    assert [m["payload"]["name"] for m in members] == ["billing", "shipping"]
    assert members[0]["payload"]["project_id"] == billing
    assert [c["type"] for c in members[0]["children"]] == ["retrieval", "llm"]
    llm_under_coordinator = [c for c in coordinator["children"] if c["type"] == "llm"]
    assert len(llm_under_coordinator) == 3


async def test_member_without_its_own_key_is_refused(api, monkeypatch):
    http, app = api
    agents = Agents(http, app, monkeypatch, {})
    billing, billing_key = await agents.agent("billing")
    shipping, _ = await agents.agent("shipping")
    team, team_key = await agents.agent(
        "coordinator", team_config({"billing": billing, "shipping": shipping})
    )
    # Only billing's key is sent, so shipping cannot borrow any other key.
    body = {
        "message": "refund",
        "credential_id": team_key,
        "member_credentials": {billing: billing_key},
    }
    response = await http.post(f"/v1/projects/{team}/chat", json=body)
    error = events(response.text)[0]
    assert error[0] == "error"
    assert error[1]["error"]["code"] == "member_not_connected"
    assert agents.calls == []


async def test_looping_member_stops_at_the_step_limit(api, monkeypatch):
    http, app = api
    lookup = {"name": "lookup", "parameters": {"type": "object"}, "result": {"x": 1}}
    replies = {
        "coordinator-model": lambda body: reply(
            tool_calls=[call("ask_billing", {"task": "check"})]
        ),
        # The member asks for its tool forever.
        "billing-model": lambda body: reply(tool_calls=[call("lookup", {})]),
        "shipping-model": lambda body: reply("unused"),
    }
    agents = Agents(http, app, monkeypatch, replies)
    billing, billing_key = await agents.agent("billing", {"tools": [lookup]})
    shipping, shipping_key = await agents.agent("shipping")
    team, team_key = await agents.agent(
        "coordinator",
        team_config({"billing": billing, "shipping": shipping}, {"max_steps": 6}),
    )
    body = {
        "message": "refund",
        "credential_id": team_key,
        "member_credentials": {billing: billing_key, shipping: shipping_key},
    }
    response = await http.post(f"/v1/projects/{team}/chat", json=body)
    assert ("error", {"error": {"message": "Stopped at the team's step limit"}}) in (
        events(response.text)
    )
    assert len(agents.calls) == 6
    trace = await agents.trace()
    assert trace["status"] == "error"
    kinds = [
        s["payload"]["error"]["kind"]
        for s in trace["spans"]
        if s["payload"].get("error") and s["type"] == "llm"
    ]
    assert kinds[-1] == "step_limit"


async def test_team_stops_at_the_token_limit(api, monkeypatch):
    http, app = api
    replies = {
        "coordinator-model": lambda body: reply(
            tool_calls=[call("ask_billing", {"task": "check"})], tokens=400
        ),
        "billing-model": lambda body: reply("Still checking", tokens=400),
        "shipping-model": lambda body: reply("unused"),
    }
    agents = Agents(http, app, monkeypatch, replies)
    billing, billing_key = await agents.agent("billing")
    shipping, shipping_key = await agents.agent("shipping")
    team, team_key = await agents.agent(
        "coordinator",
        team_config({"billing": billing, "shipping": shipping}, {"max_tokens": 1000}),
    )
    body = {
        "message": "refund",
        "credential_id": team_key,
        "member_credentials": {billing: billing_key, shipping: shipping_key},
    }
    response = await http.post(f"/v1/projects/{team}/chat", json=body)
    assert ("error", {"error": {"message": "Stopped at the team's token limit"}}) in (
        events(response.text)
    )
    # 400 + 400 + 400 tokens: the fourth call would pass 1000, so it never starts.
    assert len(agents.calls) == 3
    coordinator = (await agents.trace())["span_tree"][1]
    assert coordinator["payload"]["tokens"] == 1200
    assert coordinator["status"] == "error"


@pytest.mark.parametrize(
    "router, status",
    [
        (
            {
                "rules": [{"member": "billing", "keywords": ["x"]}],
                "fallback": "billing",
            },
            201,
        ),
        (
            {"rules": [{"member": "nobody", "keywords": ["x"]}], "fallback": "billing"},
            422,
        ),
        ({"rules": [], "fallback": "nobody"}, 422),
        (
            {"rules": [{"member": "billing", "keywords": []}], "fallback": "billing"},
            422,
        ),
    ],
)
async def test_team_config_must_name_real_members(api, router, status):
    http, _ = api
    project = (await http.post("/v1/projects", json={"name": "t"})).json()["id"]
    member = (await http.post("/v1/projects", json={"name": "m"})).json()["id"]
    config = {
        "team": {
            "members": [{"name": "billing", "project_id": member}],
            "router": router,
        }
    }
    response = await http.post(
        f"/v1/projects/{project}/versions", json={"config": config}
    )
    assert response.status_code == status, response.text


async def test_members_keep_their_own_params(api, monkeypatch):
    http, app = api
    turns = iter(
        [
            reply(tool_calls=[call("ask_billing", {"task": "Refund?"})]),
            reply("Done"),
        ]
    )
    replies = {
        "coordinator-model": lambda body: next(turns),
        "billing-model": lambda body: reply("Refund approved"),
    }
    agents = Agents(http, app, monkeypatch, replies)
    billing, billing_key = await agents.agent("billing")
    shipping, shipping_key = await agents.agent("shipping")
    config = team_config({"billing": billing, "shipping": shipping})
    config["params"] = {"temperature": 0.9}
    team, team_key = await agents.agent("coordinator", config)
    body = {
        "message": "refund",
        "credential_id": team_key,
        "member_credentials": {billing: billing_key, shipping: shipping_key},
    }
    await http.post(f"/v1/projects/{team}/chat", json=body)
    sent = {model: body.get("temperature") for _, model, body in agents.calls}
    assert sent == {"coordinator-model": 0.9, "billing-model": None}
