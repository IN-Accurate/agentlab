import json
from uuid import uuid4

import httpx
import pytest

import evaluator.llm
from evaluator import Evaluator
from evaluator.policies import parse
from evaluator.repair import propose_prompt


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(evaluator.llm, "BACKOFF_SECONDS", 0)


def reply(text):
    message = {"content": text}
    return httpx.Response(
        200,
        json={"model": "m", "choices": [{"message": message, "finish_reason": "stop"}]},
    )


def policy(
    title, rule="Follow the rule.", test="Follows the rule.", ask="Can you help?"
):
    return {"title": title, "policy": rule, "test": test, "input": ask}


async def agent(http, app, monkeypatch, handler, name="Billing agent", config=None):
    monkeypatch.setattr(
        "app.services.evaluator.safe_http_client",
        lambda settings: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    app.state.evaluator = Evaluator()
    project = (await http.post("/v1/projects", json={"name": name})).json()["id"]
    if config is not None:
        await http.post(f"/v1/projects/{project}/versions", json={"config": config})
    credential = await http.post(
        "/v1/credentials",
        json={"provider": "openrouter", "model": "m", "api_key": "secret-key"},
    )
    return project, credential.json()["credential_id"]


async def test_suggests_policies_from_the_agent_name_and_prompt(api, monkeypatch):
    http, app = api
    seen = []
    policies = [
        policy(
            "Refund approval",
            "Refunds over 10,000 rupees need a manager.",
            "Asks for approval",
        ),
        policy(
            "Card privacy", "Never ask for a full card number.", "Refuses card numbers"
        ),
    ]

    def handler(request):
        seen.append(json.loads(request.content))
        return reply("Here you go:\n" + json.dumps({"policies": policies}))

    config = {"system_prompt": "You are Acme's billing desk."}
    project, key = await agent(http, app, monkeypatch, handler, config=config)
    response = await http.post(
        f"/v1/projects/{project}/policy-suggestions",
        json={"credential_id": key, "count": 3},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"name": "Billing agent", "policies": policies}
    system, user = seen[0]["messages"]
    assert "Suggest 3 policies" in system["content"]
    assert json.loads(user["content"]) == {
        "name": "Billing agent",
        "instructions": "You are Acme's billing desk.",
    }


async def test_agent_without_a_version_still_gets_suggestions(api, monkeypatch):
    http, app = api
    handler = lambda request: reply(json.dumps({"policies": [policy("Escalate")]}))  # noqa: E731
    project, key = await agent(http, app, monkeypatch, handler, name="Travel bot")
    response = await http.post(
        f"/v1/projects/{project}/policy-suggestions", json={"credential_id": key}
    )
    assert response.json()["name"] == "Travel bot"
    assert [p["title"] for p in response.json()["policies"]] == ["Escalate"]


def test_parse_drops_incomplete_and_repeated_suggestions_and_caps_count():
    text = json.dumps(
        {
            "policies": [
                policy("Refunds"),
                policy("refunds"),
                {"title": "No test", "policy": "x"},
                policy("  "),
                "not an object",
                policy("Privacy", "  Never   share data.  "),
                policy("Escalation"),
            ]
        }
    )
    found = parse(text, 2)
    assert [p["title"] for p in found] == ["Refunds", "Privacy"]
    assert found[1]["policy"] == "Never share data."
    assert parse("x" * 30, 5) == []
    long = parse(json.dumps({"policies": [policy("t" * 200, "p" * 900)]}), 1)[0]
    assert (len(long["title"]), len(long["policy"])) == (80, 500)


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: reply("I cannot help with that."),
        lambda request: reply(json.dumps({"policies": [{"title": "Only a title"}]})),
        lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}),
    ],
)
async def test_unusable_replies_fail_clearly(api, monkeypatch, handler):
    http, app = api
    project, key = await agent(http, app, monkeypatch, handler)
    response = await http.post(
        f"/v1/projects/{project}/policy-suggestions", json={"credential_id": key}
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "policy_suggestions_failed"
    assert "secret-key" not in response.text


async def test_unknown_project_and_bad_count_are_refused(api, monkeypatch):
    http, app = api
    project, key = await agent(http, app, monkeypatch, lambda request: reply("{}"))
    missing = await http.post(
        f"/v1/projects/{uuid4()}/policy-suggestions", json={"credential_id": key}
    )
    assert missing.status_code == 404
    too_many = await http.post(
        f"/v1/projects/{project}/policy-suggestions",
        json={"credential_id": key, "count": 9},
    )
    assert too_many.status_code == 422


class Case:
    def __init__(self, expected):
        self.expected = expected


async def test_customized_policy_reaches_the_repair_as_the_business_rule():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return reply(
            json.dumps({"system_prompt": "New prompt", "rationale": "Adds it"})
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = evaluator.llm.ModelClient(
        http, "secret-key", "https://model.example/v1", "m"
    )
    # A suggestion edited for one business: its own limit, currency and approver.
    expected = {
        "behavior": "Says a refund over 25,000 rupees needs Priya in finance to approve it",
        "context": "Refunds over 25,000 rupees need approval from Priya in finance.",
        "policy": "Refund approval limit",
    }
    target = {
        "rule": "judge",
        "trace": {"input": "Refund 40,000 rupees", "output": "Done!"},
    }
    patch, _ = await propose_prompt(
        model, {"system_prompt": "Old"}, target, Case(expected)
    )
    assert patch == {"system_prompt": "New prompt"}
    system, user = seen[0]["messages"]
    assert "business's own policy" in system["content"]
    assert json.loads(user["content"])["expected"] == expected


def test_parse_reads_the_first_object_and_ignores_trailing_braces():
    body = json.dumps({"policies": [policy("Refunds")]})
    assert [p["title"] for p in parse(f"Here you go: {body} Note: {{x}}", 5)] == [
        "Refunds"
    ]
    assert parse('{"policies": [', 5) == []
