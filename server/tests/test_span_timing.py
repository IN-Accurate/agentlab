"""Timing regression coverage without a database or real waits/model calls."""

from datetime import UTC, datetime, timedelta

import pytest

import evaluator.agent as agent
import evaluator.team as team
from evaluator.llm import Completion, ModelError


class Clock:
    def __init__(self):
        self.tick = 0.0
        self.wall = datetime(2026, 1, 1, tzinfo=UTC)

    def advance(self, seconds, wall_seconds=None):
        self.tick += seconds
        self.wall += timedelta(
            seconds=seconds if wall_seconds is None else wall_seconds
        )


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()

    class WallClock:
        @staticmethod
        def now(tz):
            assert tz is UTC
            return clock.wall

    monkeypatch.setattr(agent, "datetime", WallClock)
    monkeypatch.setattr(agent, "monotonic", lambda: clock.tick)
    return clock


def end(item):
    return item["started_at"] + timedelta(milliseconds=item["latency_ms"])


def test_completed_span_uses_operation_start_even_when_monotonic_starts_at_zero(clock):
    before = clock.wall
    started = agent.start_span()
    clock.advance(2.5)
    item = agent.span("llm", started, "error", {"error": {"kind": "provider"}})
    assert item["started_at"] == before
    assert item["latency_ms"] == 2500
    assert end(item) == clock.wall
    assert item["status"] == "error"
    assert item["payload"]["error"]["kind"] == "provider"


def test_span_keeps_captured_start_and_monotonic_duration_when_wall_clock_jumps(clock):
    before = clock.wall
    started = agent.start_span()
    clock.advance(2, wall_seconds=3602)
    item = agent.span("tool", started)
    assert item["started_at"] == before
    assert item["latency_ms"] == 2000


def test_independent_timers_preserve_overlaps_and_real_idle_gaps(clock):
    first_started = agent.start_span()
    clock.advance(1)
    second_started = agent.start_span()
    clock.advance(1)
    first = agent.span("llm", first_started)
    clock.advance(1)
    second = agent.span("tool", second_started)
    assert second["started_at"] < end(first) < end(second)
    assert first["latency_ms"] == second["latency_ms"] == 2000
    clock.advance(0.75)
    third_started = agent.start_span()
    clock.advance(0.25)
    third = agent.span("validation", third_started)
    assert (third["started_at"] - end(second)).total_seconds() == 0.75
    assert third["latency_ms"] == 250


def test_instantaneous_events_have_no_invented_duration(clock):
    item = agent.limit_span("step_limit")
    assert item["started_at"] == clock.wall
    assert item["latency_ms"] == 0


async def test_single_agent_records_retrieval_model_and_tool_in_real_execution_order(
    clock, monkeypatch
):
    def retrieve(*_):
        clock.advance(0.25)
        return []

    def run_tool(*_):
        clock.advance(0.5)
        return {"value": 7}, "ok", {"name": "lookup"}

    class Model:
        model = "test-model"
        turns = 0

        async def complete(self, *_):
            self.turns += 1
            if self.turns == 1:
                clock.advance(1.5)
                return Completion(
                    "", tool_calls=[{"id": "call", "function": {"name": "lookup"}}]
                )
            clock.advance(0.75)
            return Completion("Done")

    before = clock.wall
    monkeypatch.setattr(agent, "retrieve", retrieve)
    monkeypatch.setattr(agent, "run_tool", run_tool)
    result = await agent.run_agent(Model(), {}, "Look this up")
    assert [item["type"] for item in result.spans] == [
        "retrieval",
        "llm",
        "tool",
        "llm",
    ]
    assert [item["latency_ms"] for item in result.spans] == [250, 1500, 500, 750]
    assert result.spans[0]["started_at"] == before
    for previous, following in zip(result.spans, result.spans[1:]):
        assert end(previous) == following["started_at"]
    assert (end(result.spans[-1]) - before).total_seconds() == 3


async def test_model_error_span_covers_the_failed_call(clock):
    class Model:
        model = "test-model"

        async def complete(self, *_):
            clock.advance(2)
            raise ModelError("provider", "Unavailable")

    before = clock.wall
    result = await agent.run_agent(Model(), {"agent": {"type": "endpoint"}}, "Hello")
    assert result.status == "error"
    assert len(result.spans) == 1
    assert result.spans[0]["started_at"] == before
    assert result.spans[0]["latency_ms"] == 2000
    assert end(result.spans[0]) == clock.wall


async def test_team_parent_envelopes_cover_children_without_counting_routing_twice(
    clock, monkeypatch
):
    original_route = team.route

    def route(*args):
        clock.advance(0.25)
        return original_route(*args)

    def retrieve(*_):
        clock.advance(0.125)
        return []

    class Coordinator:
        model = "coordinator"
        turns = 0

        async def complete(self, *_):
            self.turns += 1
            if self.turns == 1:
                clock.advance(1)
                return Completion(
                    "",
                    tool_calls=[
                        {
                            "id": "call",
                            "function": {
                                "name": "ask_helper",
                                "arguments": {"task": "Help"},
                            },
                        }
                    ],
                )
            clock.advance(0.5)
            return Completion("Combined answer")

    class Helper:
        model = "helper"

        async def complete(self, *_):
            clock.advance(2)
            return Completion("Member answer")

    monkeypatch.setattr(team, "route", route)
    monkeypatch.setattr(agent, "retrieve", retrieve)
    config = {"team": {"router": {"rules": [], "fallback": "helper"}}}
    member = team.Member("helper", "", "project", "version", {}, Helper())
    runner = team.Team(config, Coordinator(), [member])
    before = clock.wall
    result = await runner.run("Help")
    router, coordinator = result.spans[:2]
    assert router["started_at"] == before
    assert router["latency_ms"] == 250
    assert coordinator["started_at"] == end(router)
    assert coordinator["latency_ms"] == 3625
    assert end(coordinator) == clock.wall
    member_span = next(
        item for item in result.spans if item["payload"].get("role") == "member"
    )
    assert member_span["latency_ms"] == 2125
    parents = {item["sdk_span_id"]: item for item in result.spans}
    for item in result.spans:
        if item.get("parent"):
            parent = parents[item["parent"]]
            assert parent["started_at"] <= item["started_at"]
            assert end(item) <= end(parent)
    assert (end(coordinator) - before).total_seconds() == 3.875


def test_validation_records_time_spent_in_its_check(clock, monkeypatch):
    def check(*_):
        clock.advance(0.125)
        return True

    monkeypatch.setattr(agent, "check", check)
    before = clock.wall
    item = agent.validate(
        {"validators": [{"type": "contains", "value": "yes"}]}, "yes"
    )[0]
    assert item["started_at"] == before
    assert item["latency_ms"] == 125
    assert end(item) == clock.wall
