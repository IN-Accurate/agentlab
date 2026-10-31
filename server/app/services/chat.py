import json

from app.errors import AppError, error_body
from app.security import safe_http_client
from app.services.evaluator import require_backend, context

EVENTS = {
    "message.delta",
    "step.started",
    "step.finished",
    "tool.result",
    "error",
    "done",
}
CARDS = {"run_summary", "diagnosis", "replay", "repair", "scorecard"}


async def events(state, db, prepared):
    payload, allowed = prepared
    backend = require_backend(state)
    function = getattr(backend, "stream_chat", None)
    if not callable(function):
        raise AppError(
            "chat_not_configured", "Evaluator does not implement stream_chat", 503
        )
    text = ""
    size = 0
    client = safe_http_client(state.settings)
    try:
        async for item in function(payload, context(db, state, allowed, None, client)):
            if hasattr(item, "model_dump"):
                item = item.model_dump()
            if (
                not isinstance(item, dict)
                or item.get("event") not in EVENTS
                or not isinstance(item.get("data"), dict)
            ):
                raise AppError("invalid_evaluator_event", "Unsupported chat event", 502)
            name, data = item["event"], item["data"]
            size += len(json.dumps(item))
            if size > 2_000_000:
                raise AppError("chat_too_large", "Chat response exceeded limit", 502)
            if name == "message.delta":
                if not isinstance(data.get("text"), str):
                    raise AppError(
                        "invalid_evaluator_event", "Message text must be a string", 502
                    )
                text += data["text"]
                continue
            if text:
                yield {
                    "event": "message.delta",
                    "data": json.dumps({"text": state.redactor.text(text)}),
                }
                text = ""
            if name == "tool.result" and (
                data.get("type") not in CARDS or not isinstance(data.get("data"), dict)
            ):
                raise AppError("invalid_evaluator_event", "Invalid result card", 502)
            if name == "done":
                break
            yield {"event": name, "data": json.dumps(state.redactor.scrub(data))}
        if text:
            yield {
                "event": "message.delta",
                "data": json.dumps({"text": state.redactor.text(text)}),
            }
        await db.flush()
    except Exception as exc:
        await db.rollback()
        body = (
            error_body(exc.code, exc.message, exc.details)
            if isinstance(exc, AppError)
            else error_body("chat_failed", "Chat operation failed")
        )
        yield {"event": "error", "data": json.dumps(state.redactor.scrub(body))}
    finally:
        await client.aclose()
    yield {"event": "done", "data": "{}"}
