import json

import httpx
import pytest

import evaluator.llm
from evaluator.agent import run_agent, run_tool, tool_definitions
from evaluator.evaluate import evaluate_case
from evaluator.llm import ModelClient

LOOKUP = {
    "name": "lookup_order",
    "description": "Find an order's delivery status",
    "parameters": {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    },
}


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(evaluator.llm, "BACKOFF_SECONDS", 0)


def completion(text="", tool_calls=None):
    message = {"content": text, "tool_calls": tool_calls}
    return httpx.Response(
        200,
        json={"model": "m", "choices": [{"message": message, "finish_reason": "stop"}]},
    )


def call(name, arguments, identifier="1"):
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": identifier, "function": {"name": name, "arguments": raw}}


class Model:
    """Replays replies in order and keeps every request body."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        self.client = ModelClient(http, "secret-key", "https://model.example/v1", "m")

    def handle(self, request):
        self.seen.append(json.loads(request.content))
        return self.replies.pop(0)


def tool(**extra):
    return {"tools": [{**LOOKUP, **extra}]}


def tool_span(result):
    return next(s for s in result.spans if s["type"] == "tool")


async def test_simulated_tool_answers_from_its_schema_and_scenario():
    agent = Model(
        completion(tool_calls=[call("lookup_order", {"order_id": "A123"})]),
        completion("Your order shipped with Delhivery."),
    )
    simulator = Model(completion('Sure: {"status": "shipped", "courier": "Delhivery"}'))
    config = tool(
        simulate={"instructions": "Order A123 shipped yesterday with Delhivery"}
    )
    result = await run_agent(
        agent.client, config, "Where is order A123?", simulator=simulator.client
    )
    assert result.output == "Your order shipped with Delhivery."
    payload = tool_span(result)["payload"]
    assert payload["simulated"] is True
    assert payload["arguments"] == {"order_id": "A123"}
    assert payload["result"] == {"status": "shipped", "courier": "Delhivery"}
    sent = json.loads(simulator.seen[0]["messages"][1]["content"])
    assert sent == {
        "tool": LOOKUP,
        "arguments": {"order_id": "A123"},
        "scenario": "Order A123 shipped yesterday with Delhivery",
    }
    assert simulator.seen[0]["temperature"] == 0
    # The agent's next turn receives the simulated result.
    tool_message = agent.seen[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert json.loads(tool_message["content"])["courier"] == "Delhivery"


async def test_matched_results_answer_before_the_simulator():
    config = tool(
        results=[{"when": {"order_id": "A1"}, "result": {"status": "lost"}}],
        simulate=True,
    )
    tools = tool_definitions(config)
    content, status, _ = run_tool(tools, call("lookup_order", {"order_id": "A1"}))
    assert (content, status) == ({"status": "lost"}, "ok")
    _, status, _ = run_tool(tools, call("lookup_order", {"order_id": "B2"}))
    assert status == "simulate"
    _, status, payload = run_tool(
        tool_definitions(tool()), call("lookup_order", {"order_id": "B2"})
    )
    assert payload["error"]["kind"] == "no_executor"


async def test_simulator_without_a_usable_result_is_a_tool_error():
    agent = Model(
        completion(tool_calls=[call("lookup_order", {"order_id": "A123"})]),
        completion("I could not check that order."),
    )
    simulator = Model(completion("I am not sure what the tool would say."))
    result = await run_agent(
        agent.client, tool(simulate=True), "Where is A123?", simulator=simulator.client
    )
    span = tool_span(result)
    assert span["status"] == "error"
    assert span["payload"]["error"]["kind"] == "simulation_failed"
    assert result.output == "I could not check that order."


@pytest.mark.parametrize(
    "arguments, message",
    [
        ('{"order": "A123"}', "'order_id' is a required property"),
        ("[1]", "JSON object"),
    ],
)
def test_bad_arguments_never_reach_the_simulator(arguments, message):
    tools = tool_definitions(tool(simulate=True))
    _, status, payload = run_tool(tools, call("lookup_order", arguments))
    assert status == "error" and payload["error"]["kind"] == "tool_arg"
    assert message in payload["error"]["message"]


async def evaluate(agent_calls, expected, grader_verdict=True):
    replies = [completion(tool_calls=[c]) for c in agent_calls] + [completion("Done")]
    agent = Model(*replies)
    simulated = [completion('{"status": "shipped"}') for _ in agent_calls]
    verdict = json.dumps({"passed": grader_verdict, "rationale": "ok"})
    grader = Model(*simulated, completion(verdict))
    config = tool(simulate=True)
    case = {"input": "Where is A123?", "expected": expected}
    outcome = await evaluate_case(agent.client, grader.client, config, case)
    return outcome, grader


async def test_test_passes_when_the_right_call_is_made():
    expected = {
        "tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "A123"}}],
        "forbidden_tools": ["issue_refund"],
        "behavior": "Reports the shipping status the tool returned",
    }
    outcome, grader = await evaluate(
        [call("lookup_order", {"order_id": "A123"})], expected
    )
    assert outcome.passed, outcome.explanation
    graded = json.loads(grader.seen[-1]["messages"][1]["content"])
    assert graded["tool_calls"] == [
        {
            "name": "lookup_order",
            "arguments": {"order_id": "A123"},
            "result": {"status": "shipped"},
        }
    ]


@pytest.mark.parametrize(
    "calls, expected, note",
    [
        (
            [call("lookup_order", {"order_id": "B9"})],
            {
                "tool_calls": [
                    {"name": "lookup_order", "arguments": {"order_id": "A123"}}
                ]
            },
            'Did not call lookup_order with {"order_id": "A123"}',
        ),
        ([], {"tool_calls": [{"name": "lookup_order"}]}, "Did not call lookup_order"),
        (
            [call("lookup_order", {"order_id": "A123"}), call("issue_refund", {}, "2")],
            {"forbidden_tools": ["issue_refund"]},
            "Called forbidden tool issue_refund",
        ),
        (
            [
                call("lookup_order", {"order": "A123"}),
                call("lookup_order", {"order_id": "A123"}, "2"),
            ],
            {"tool_calls": [{"name": "lookup_order"}]},
            "Tool call was invalid: lookup_order",
        ),
    ],
)
async def test_wrong_missing_forbidden_or_invalid_calls_fail(calls, expected, note):
    agent_replies = [completion(tool_calls=[c]) for c in calls] + [completion("Done")]
    agent = Model(*agent_replies)
    valid = [
        c
        for c in calls
        if c["function"]["name"] == "lookup_order"
        and "order_id" in c["function"]["arguments"]
    ]
    grader = Model(*[completion('{"status": "shipped"}') for _ in valid])
    case = {"input": "Where is A123?", "expected": expected}
    outcome = await evaluate_case(
        agent.client, grader.client, tool(simulate=True), case
    )
    assert not outcome.passed
    assert note in outcome.explanation


async def test_tests_without_tool_expectations_are_unchanged():
    outcome, _ = await evaluate([call("lookup_order", {"order": "bad"})], {})
    assert outcome.passed


class Network:
    """One client for the model and the tools, routed by host like the real one."""

    def __init__(self, replies, routes):
        self.replies, self.routes, self.seen = list(replies), routes, []
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        self.client = ModelClient(http, "secret-key", "https://model.example/v1", "m")

    def handle(self, request):
        self.seen.append(request)
        if request.url.host == "model.example":
            return self.replies.pop(0)
        return self.routes[request.url.host](request)


def wikipedia(request):
    params = request.url.params
    if params.get("list") == "search":
        assert params["srsearch"] == "Pune population"
        hits = [{"title": "Pune", "snippet": "<b>Pune</b> is a city"}]
        return httpx.Response(200, json={"query": {"search": hits}})
    pages = [{"title": "Pune", "extract": "Pune is a city in Maharashtra, India."}]
    return httpx.Response(200, json={"query": {"pages": pages}})


async def test_builtin_search_really_runs_and_feeds_the_agent():
    net = Network(
        [
            completion(tool_calls=[call("search", {"query": "Pune population"})]),
            completion("Pune is in Maharashtra."),
        ],
        {"en.wikipedia.org": wikipedia},
    )
    result = await run_agent(
        net.client, {"tools": [{"builtin": "search"}]}, "Where is Pune?"
    )
    span = tool_span(result)
    assert span["payload"]["executed"] is True
    assert span["payload"]["result"]["results"] == [
        {
            "title": "Pune",
            "url": "https://en.wikipedia.org/wiki/Pune",
            "text": "Pune is a city in Maharashtra, India.",
        }
    ]
    wiki = [r for r in net.seen if r.url.host == "en.wikipedia.org"]
    assert all(r.headers["user-agent"].startswith("AgentLab") for r in wiki)
    assert "Maharashtra" in net.seen[-1].content.decode()
    tools = json.loads(net.seen[0].content)["tools"]
    assert tools[0]["function"]["name"] == "search"


async def test_connected_endpoint_gets_the_checked_arguments():
    sent = []

    def crm(request):
        sent.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "delivered", "date": "3 Nov"})

    config = {"tools": [{**LOOKUP, "endpoint": "https://crm.example/orders"}]}
    net = Network(
        [
            completion(tool_calls=[call("lookup_order", {"order_id": "KE-7788"})]),
            completion("Delivered on 3 Nov."),
        ],
        {"crm.example": crm},
    )
    result = await run_agent(net.client, config, "Where is KE-7788?")
    assert sent == [("POST", "/orders", {"order_id": "KE-7788"})]
    assert tool_span(result)["payload"]["result"] == {
        "status": "delivered",
        "date": "3 Nov",
    }


def blocked(request):
    from app.errors import AppError

    raise AppError("unsafe_url", "Outbound URLs require HTTPS", 422)


@pytest.mark.parametrize(
    "route, kind",
    [
        (lambda request: httpx.Response(500, text="boom"), "tool_http"),
        (blocked, "tool_blocked"),
    ],
)
async def test_failing_or_blocked_endpoints_are_tool_errors(route, kind):
    config = {"tools": [{**LOOKUP, "endpoint": "https://crm.example/orders"}]}
    net = Network(
        [
            completion(tool_calls=[call("lookup_order", {"order_id": "KE-1"})]),
            completion("Sorry, I could not check."),
        ],
        {"crm.example": route},
    )
    result = await run_agent(net.client, config, "Where is KE-1?")
    span = tool_span(result)
    assert span["status"] == "error" and span["payload"]["error"]["kind"] == kind
    assert result.output == "Sorry, I could not check."


async def grounded_case(agent_replies, grader_replies):
    agent = Model(*agent_replies)
    grader = Model(*grader_replies)
    case = {"input": "Where is A123?", "expected": {"grounded": True}}
    return await evaluate_case(
        agent.client, grader.client, tool(simulate=True), case
    ), grader


async def test_grounded_test_fails_when_the_agent_skips_its_tools():
    outcome, grader = await grounded_case([completion("It arrives tomorrow.")], [])
    assert not outcome.passed
    assert "Answered without using any tool result" in outcome.explanation
    assert grader.seen == []


@pytest.mark.parametrize("verdict, passed", [(False, False), (True, True)])
async def test_grounded_test_asks_the_judge_about_tool_results(verdict, passed):
    outcome, grader = await grounded_case(
        [
            completion(tool_calls=[call("lookup_order", {"order_id": "A123"})]),
            completion("It was delivered yesterday by Blue Dart."),
        ],
        [
            completion('{"status": "shipped", "courier": "Delhivery"}'),
            completion(json.dumps({"passed": verdict, "rationale": "Courier differs"})),
        ],
    )
    assert outcome.passed is passed
    graded = json.loads(grader.seen[-1]["messages"][1]["content"])
    assert "supported by the results in tool_calls" in graded["criterion"]
    assert graded["tool_calls"][0]["result"]["courier"] == "Delhivery"
    if not passed:
        assert "Not grounded in tool results: Courier differs" in outcome.explanation


def test_a_config_cannot_choose_how_a_tool_runs():
    tools = tool_definitions(
        {"tools": [{**LOOKUP, "runs": "search"}, {**LOOKUP, "name": "x", "runs": 1}]}
    )
    assert [t["runs"] for t in tools.values()] == [None, None]


@pytest.mark.parametrize(
    "config, arguments, route",
    [
        (
            {"tools": [{"builtin": "search", "parameters": {"type": "object"}}]},
            {},
            wikipedia,
        ),
        (
            {"tools": [{"builtin": "search"}]},
            {"query": "Pune"},
            lambda request: httpx.Response(200, json=["not", "an", "object"]),
        ),
    ],
)
async def test_unexpected_tool_shapes_are_tool_errors(config, arguments, route):
    net = Network(
        [
            completion(tool_calls=[call("search", arguments)]),
            completion("Sorry."),
        ],
        {"en.wikipedia.org": route},
    )
    result = await run_agent(net.client, config, "Search something")
    span = tool_span(result)
    assert span["payload"]["error"]["kind"] == "tool_failed"
    assert result.output == "Sorry."


def test_a_call_that_failed_at_run_time_still_counts_as_made():
    from evaluator.evaluate import tool_notes

    expected = {
        "tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "1"}}]
    }
    down = {"kind": "tool_http", "message": "Tool returned HTTP 500"}
    call_made = {"name": "lookup_order", "arguments": {"order_id": "1"}, "error": down}
    assert tool_notes(expected, [call_made]) == []
    bad = {**call_made, "error": {"kind": "tool_arg", "message": "bad"}}
    assert "Did not call lookup_order" in " ".join(tool_notes(expected, [bad]))


async def test_a_slow_drip_endpoint_hits_the_total_deadline(monkeypatch):
    import asyncio

    import evaluator.agent

    monkeypatch.setattr(evaluator.agent, "TOOL_TIMEOUT", 0.2)

    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(20):
                await asyncio.sleep(0.05)
                yield b" "

    config = {"tools": [{**LOOKUP, "endpoint": "https://crm.example/orders"}]}
    net = Network(
        [
            completion(tool_calls=[call("lookup_order", {"order_id": "KE-1"})]),
            completion("Sorry, I could not check."),
        ],
        {"crm.example": lambda request: httpx.Response(200, stream=Drip())},
    )
    result = await run_agent(net.client, config, "Where is KE-1?")
    assert tool_span(result)["payload"]["error"]["kind"] == "tool_timeout"


async def test_a_malformed_endpoint_is_a_tool_error():
    config = {"tools": [{**LOOKUP, "endpoint": "https://[::1"}]}
    net = Network(
        [
            completion(tool_calls=[call("lookup_order", {"order_id": "KE-1"})]),
            completion("Sorry."),
        ],
        {},
    )
    result = await run_agent(net.client, config, "Where is KE-1?")
    assert tool_span(result)["payload"]["error"]["kind"] == "tool_failed"
    assert result.output == "Sorry."
