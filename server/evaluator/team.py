import json
import re
from dataclasses import dataclass
from uuid import UUID

from app.errors import AppError
from evaluator.agent import (
    MAX_OUTPUT,
    AgentResult,
    Budget,
    clip,
    limit_span,
    run_agent,
    span,
    start_span,
    validate,
    with_policy,
)
from evaluator.llm import ModelClient, ModelError

MAX_ROUNDS = 6
DEFAULT_MAX_STEPS = 12
COORDINATOR_PROMPT = (
    "You coordinate a team of agents. Hand tasks to members with their ask_ tools, "
    "then combine their answers into one reply for the user. Do not invent facts "
    "a member did not give you."
)
TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "description": "What the member must do"}
    },
    "required": ["task"],
}


def route(router, text):
    """First matching rule wins; anything unmatched goes to the fallback."""
    lowered = text.lower()
    for index, rule in enumerate(router.get("rules") or []):
        for keyword in rule["keywords"]:
            if re.search(rf"(?<!\w){re.escape(keyword.lower())}(?!\w)", lowered):
                return {
                    "type": "rules",
                    "choice": rule["member"],
                    "confidence": 1.0,
                    "reason": f"Rule {index + 1} matched keyword '{keyword}'",
                    "rule": index,
                }
    return {
        "type": "rules",
        "choice": router["fallback"],
        "confidence": 0.0,
        "reason": "No rule matched, so the fallback member answers",
        "rule": None,
    }


@dataclass
class Member:
    name: str
    description: str
    project_id: str
    version_id: str
    config: dict
    model: ModelClient

    @property
    def tool(self):
        return f"ask_{self.name}"


async def load_members(context, options, team, own_project_id):
    """Each member is another connected agent and must run on its own key."""
    from evaluator.evaluate import latest_version

    keys = {
        str(k): str(v) for k, v in (options.get("member_credentials") or {}).items()
    }
    members = []
    async with context["session_factory"]() as db:
        for item in team["members"]:
            project_id = str(item["project_id"])
            if project_id == str(own_project_id):
                raise AppError(
                    "team_config_invalid", "A team cannot list itself as a member", 422
                )
            if project_id not in keys:
                raise AppError(
                    "member_not_connected",
                    f"Connect agent {item['name']} and send its credential",
                    422,
                )
            version = await latest_version(db, project_id)
            config = version.config or {}
            if config.get("team"):
                raise AppError(
                    "team_config_invalid",
                    f"Member {item['name']} is a team; teams cannot be nested",
                    422,
                )
            model = ModelClient.from_context(
                context, keys[project_id], config.get("agent")
            )
            members.append(
                Member(
                    item["name"],
                    item.get("description") or "",
                    project_id,
                    str(version.id),
                    config,
                    model,
                )
            )
    return members


class Team:
    def __init__(self, config, coordinator, members):
        self.config = config
        self.team = config["team"]
        self.coordinator = coordinator
        self.members = {member.tool: member for member in members}

    @classmethod
    async def load(cls, context, options, config, coordinator, project_id):
        members = await load_members(context, options, config["team"], project_id)
        return cls(config, coordinator, members)

    def budget(self):
        limits = self.team.get("limits") or {}
        return Budget(
            limits.get("max_steps", DEFAULT_MAX_STEPS), limits.get("max_tokens")
        )

    def system(self, decision, policy=None):
        roster = "\n".join(
            f"- {m.tool}: {m.name}. {m.description}".rstrip()
            for m in self.members.values()
        )
        note = (
            f"The router chose {decision['choice']} for this request "
            f"({decision['reason']}). Ask it first; consult others only if needed."
        )
        parts = [self.config.get("system_prompt"), COORDINATOR_PROMPT, roster, note]
        prompt = "\n\n".join(part for part in parts if part)
        return with_policy({"system_prompt": prompt}, policy)["system_prompt"]

    async def ask(self, call, parent, params, budget, spans, policy=None):
        function = call.get("function") or {}
        member = self.members.get(function.get("name"))
        try:
            raw = function.get("arguments")
            arguments = raw if isinstance(raw, dict) else json.loads(raw or "{}")
            task = arguments["task"]
            if not isinstance(task, str) or not task.strip():
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            task = None
        if member is None or task is None:
            message = "Unknown member" if member is None else "Send a task string"
            error = {"kind": "tool_arg", "message": message}
            payload = {"name": function.get("name"), "error": error}
            spans.append(
                {**span("tool", status="error", payload=payload), "parent": parent}
            )
            return {"error": message}
        started = start_span()
        node = span(
            "agent",
            started,
            payload={
                "role": "member",
                "name": member.name,
                "project_id": member.project_id,
                "version_id": member.version_id,
                "model": member.model.model,
                "task": clip(task),
            },
        )
        node["parent"] = parent
        spans.append(node)
        result = await run_agent(
            member.model,
            with_policy(member.config, policy),
            task,
            params,
            budget=budget,
        )
        for item in result.spans:
            item.setdefault("parent", node["sdk_span_id"])
        spans += result.spans
        node["latency_ms"] = started.elapsed_ms()
        node["status"] = result.status
        if result.status != "ok":
            errors = [s["payload"].get("error") for s in result.spans]
            error = next((e for e in reversed(errors) if e), {"kind": "member_failed"})
            node["payload"]["error"] = error
            return {"error": error.get("message") or error.get("kind")}
        node["payload"]["output"] = clip(result.output)
        return {"answer": result.output}

    async def run(self, query, params=None, history=None, policy=None):
        result = AgentResult()
        started = start_span()
        decision = route(self.team["router"], query)
        result.spans.append(span("router", started, payload=decision))
        coordinator_started = start_span()
        coordinator = span(
            "agent",
            coordinator_started,
            payload={
                "role": "coordinator",
                "name": "coordinator",
                "model": self.coordinator.model,
                "route": decision["choice"],
            },
        )
        result.spans.append(coordinator)
        parent = coordinator["sdk_span_id"]
        budget = self.budget()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": member.tool,
                    "description": member.description or f"Ask {member.name}",
                    "parameters": TASK_SCHEMA,
                },
            }
            for member in self.members.values()
        ]
        messages = [{"role": "system", "content": self.system(decision, policy)}]
        messages += history or []
        messages.append({"role": "user", "content": query})
        # Members keep their own params; only request overrides reach them.
        merged = {**(self.config.get("params") or {}), **(params or {})}
        try:
            for _ in range(MAX_ROUNDS):
                limit = budget.charge()
                if limit:
                    return self.fail(result, coordinator, limit_span(limit))
                call_started = start_span()
                try:
                    reply = await self.coordinator.complete(messages, tools, merged)
                except ModelError as exc:
                    error = {"kind": exc.kind, "message": exc.message}
                    payload = {"model": self.coordinator.model, "error": error}
                    return self.fail(
                        result, coordinator, span("llm", call_started, "error", payload)
                    )
                budget.record(messages, reply)
                payload = {
                    "model": reply.model or self.coordinator.model,
                    "input": clip(json.dumps(messages, default=str)),
                    "output": clip(reply.text),
                    "finish_reason": reply.finish_reason,
                    "usage": reply.usage,
                }
                result.spans.append(
                    {**span("llm", call_started, payload=payload), "parent": parent}
                )
                if not reply.tool_calls:
                    result.output = clip(reply.text, MAX_OUTPUT)
                    result.spans += validate(self.config, result.output)
                    return result
                messages.append(
                    {
                        "role": "assistant",
                        "content": reply.text,
                        "tool_calls": reply.tool_calls,
                    }
                )
                for call in reply.tool_calls:
                    content = await self.ask(
                        call, parent, params, budget, result.spans, policy
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(content, default=str),
                        }
                    )
            return self.fail(result, coordinator, limit_span("step_limit"))
        finally:
            coordinator["latency_ms"] = coordinator_started.elapsed_ms()
            coordinator["payload"].update(steps=budget.steps, tokens=budget.tokens)

    @staticmethod
    def fail(result, coordinator, item):
        item["parent"] = coordinator["sdk_span_id"]
        result.spans.append(item)
        result.status = coordinator["status"] = "error"
        return result


async def runner(context, options, config, model, project_id):
    """A team for team configs, else None so callers run the single agent."""
    if not config.get("team"):
        return None
    return await Team.load(context, options, config, model, UUID(str(project_id)))
