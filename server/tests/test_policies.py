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


class Recorder:
    """A fake model that records each system prompt and replays replies in order."""

    def __init__(self, *replies):
        self.replies, self.systems = list(replies), []
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        self.client = evaluator.llm.ModelClient(http, "k", "https://m.example/v1", "m")

    def handle(self, request):
        body = json.loads(request.content)
        self.systems.append(body["messages"][0]["content"])
        reply = self.replies.pop(0)
        return reply if isinstance(reply, httpx.Response) else reply_text(reply)


def reply_text(text):
    return reply(text) if isinstance(text, str) else httpx.Response(200, json=text)


def verdict(passed, why):
    return json.dumps({"passed": passed, "rationale": why})


REFUND_POLICY = "Refunds up to Rs 25,000 are approved by the agent; larger ones go to Priya in finance."
CASE = {
    "input": "Can I get Rs 30,000 back?",
    "expected": {
        "behavior": "Sends large refunds to finance",
        "context": REFUND_POLICY,
    },
}


async def test_a_run_gives_the_agent_the_policy_and_the_rule_to_use_only_it():
    from evaluator.evaluate import evaluate_case

    agent = Recorder("That is over Rs 25,000, so Priya in finance will approve it.")
    grader = Recorder(
        verdict(True, "uses the policy"), verdict(True, "sends to finance")
    )
    outcome = await evaluate_case(
        agent.client, grader.client, {"system_prompt": "You are Acme billing."}, CASE
    )
    assert outcome.passed
    system = agent.systems[0]
    assert system.startswith("You are Acme billing.")
    assert REFUND_POLICY in system and "Answer only from" in system


async def test_an_answer_beyond_the_policy_fails():
    from evaluator.evaluate import evaluate_case

    agent = Recorder("Approved! You'll also get a free Rs 500 voucher.")
    grader = Recorder(
        verdict(False, "the voucher is not in the policy"), verdict(True, "fine")
    )
    outcome = await evaluate_case(agent.client, grader.client, {}, CASE)
    assert not outcome.passed
    assert "Went beyond the given policy: the voucher is not in the policy" in (
        outcome.explanation
    )


async def test_tests_without_a_policy_are_unchanged():
    from evaluator.evaluate import evaluate_case

    agent = Recorder("Hello")
    grader = Recorder(verdict(True, "greets"))
    case = {"input": "Hi", "expected": {"behavior": "Greets the user"}}
    outcome = await evaluate_case(
        agent.client, grader.client, {"system_prompt": "S"}, case
    )
    assert outcome.passed and agent.systems == ["S"] and len(grader.systems) == 1


async def test_team_coordinator_and_member_both_get_the_policy():
    from evaluator.team import Member, Team

    member = Recorder("Priya in finance approves refunds over Rs 25,000.")
    coordinator = Recorder(
        {
            "model": "m",
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "1",
                                "function": {
                                    "name": "ask_billing",
                                    "arguments": json.dumps(
                                        {"task": "Rs 30,000 refund?"}
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        },
        "Priya in finance will approve it.",
    )
    config = {
        "system_prompt": "You run Acme support.",
        "team": {
            "members": [{"name": "billing", "project_id": "p"}],
            "router": {"rules": [], "fallback": "billing"},
        },
    }
    billing = Member(
        "billing", "Billing", "p", "v", {"system_prompt": "Billing."}, member.client
    )
    team = Team(config, coordinator.client, [billing])
    result = await team.run(CASE["input"], policy=REFUND_POLICY)
    assert result.output == "Priya in finance will approve it."
    for system in (coordinator.systems[0], member.systems[0]):
        assert REFUND_POLICY in system and "Answer only from" in system
