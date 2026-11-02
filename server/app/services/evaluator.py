import inspect
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from app.db import record, require
from app.errors import AppError
from app.models import (
    AppVersion,
    Assertion,
    Diagnosis,
    Repair,
    Replay,
    Run,
    RunResult,
    TestCase,
    Trace,
    Verification,
)
from app.routers.traces import details
from app.security import (
    credential_record,
    decrypt_credential,
    safe_http_client,
    validate_url,
)


def require_backend(state):
    backend = getattr(state, "evaluator", None)
    if backend is None:
        raise AppError(
            "evaluator_not_configured",
            "Connect the evaluator implementation before using this operation",
            503,
        )
    return backend


async def target_context(db, model, identifier):
    row = await require(db, model, identifier)
    if isinstance(row, RunResult):
        trace = await require(db, Trace, row.trace_id)
    elif isinstance(row, Trace):
        trace = row
    elif isinstance(row, Diagnosis):
        trace = await require(db, Trace, row.trace_id)
    elif isinstance(row, Repair):
        diagnosis = await require(db, Diagnosis, row.diagnosis_id)
        trace = await require(db, Trace, diagnosis.trace_id)
    elif isinstance(row, Replay):
        trace = await require(db, Trace, row.original_trace_id)
    else:
        raise AppError("invalid_target", "Unsupported operation target", 422)
    version = await require(db, AppVersion, trace.app_version_id)
    target = {**record(row), "trace": await details(db, trace.id)}
    if isinstance(row, RunResult):
        target["test_case"] = record(await require(db, TestCase, row.test_case_id))
    if isinstance(row, Repair):
        target["diagnosis"] = record(diagnosis)
        if row.new_version_id:
            candidate = await require(db, AppVersion, row.new_version_id)
            if candidate.project_id != version.project_id:
                raise AppError(
                    "project_mismatch", "Candidate belongs to another project", 422
                )
            target["candidate_version"] = record(candidate)
    return version, target


async def prepare(
    db, state, operation, data, project_id=None, target_model=None, target_id=None
):
    require_backend(state)
    values = (
        data.model_dump(mode="json", exclude_none=True)
        if hasattr(data, "model_dump")
        else dict(data)
    )
    version, target = None, None
    if target_model:
        version, target = await target_context(db, target_model, target_id)
        project_id = version.project_id
    if values.get("version_id"):
        version = await require(db, AppVersion, UUID(values["version_id"]))
        if version.project_id != project_id:
            raise AppError(
                "project_mismatch", "Version belongs to another project", 422
            )
    if version is not None:
        # Stored configs are re-validated on every use: DNS may have changed
        # since the base URL passed its creation-time check.
        agent = (version.config or {}).get("agent") or {}
        if agent.get("base_url"):
            await validate_url(
                agent["base_url"], state.settings.agentlab_allow_insecure_local
            )
    cases = []
    if version and operation in {"run", "improve", "verify"}:
        query = (
            select(TestCase)
            .where(TestCase.project_id == project_id, TestCase.archived_at.is_(None))
            .order_by(TestCase.id)
        )
        if values.get("split"):
            query = query.where(TestCase.split == values["split"])
        if "test_case_ids" in values:
            query = query.where(
                TestCase.id.in_([UUID(x) for x in values["test_case_ids"]])
            )
        rows = list(await db.scalars(query))
        if "test_case_ids" in values and {str(x.id) for x in rows} != set(
            values["test_case_ids"]
        ):
            raise AppError("test_mismatch", "Tests do not match project and split", 422)
        if not rows:
            raise AppError("no_tests", "Add test cases before evaluating", 422)
        by_case = {case.id: [] for case in rows}
        for assertion in await db.scalars(
            select(Assertion)
            .where(
                Assertion.test_case_id.in_([case.id for case in rows]),
                Assertion.archived_at.is_(None),
            )
            .order_by(Assertion.position, Assertion.id)
        ):
            by_case[assertion.test_case_id].append(assertion)
        for case in rows:
            cases.append(
                {**record(case), "assertions": [record(x) for x in by_case[case.id]]}
            )
    allowed = {
        str(values[k])
        for k in ("credential_id", "judge_credential_id")
        if values.get(k)
    }
    for identifier in allowed:
        credential_record(identifier, state.credential_store)
    payload = {
        "operation": operation,
        "project_id": str(project_id),
        "version": record(version) if version else None,
        "test_cases": cases,
        "target": target,
        "options": values,
    }
    return state.redactor.scrub(jsonable_encoder(payload)), allowed


def context(db, state, allowed, progress=None, http_client=None):
    def check(identifier):
        identifier = str(identifier)
        if identifier not in allowed:
            raise AppError(
                "credential_not_authorized",
                "Credential not supplied for this operation",
                403,
            )
        return identifier

    def metadata(identifier):
        value = credential_record(check(identifier), state.credential_store)
        return {
            k: v for k, v in value.items() if k not in {"ciphertext", "key_fingerprint"}
        }

    def decrypt(identifier):
        return decrypt_credential(
            check(identifier), state.credential_store, state.settings
        )

    def session_factory():
        # Independent sessions the evaluator may commit per result or batch,
        # so dashboards show progress before the job ends and a late failure
        # keeps earlier results. Close them (ideally via `async with`).
        child = state.sessions()
        child.info["redactor"] = state.redactor
        return child

    return {
        "session": db,
        "session_factory": session_factory,
        "credentials": {"metadata": metadata, "decrypt_for_call": decrypt},
        "report_progress": progress,
        "http_client": http_client,
    }


async def execute(db, state, prepared, progress=None):
    payload, allowed = prepared
    backend = require_backend(state)
    function = getattr(backend, "execute", None)
    if not callable(function):
        raise AppError(
            "evaluator_operation_unavailable",
            "Evaluator does not implement execute",
            503,
        )
    client = safe_http_client(state.settings)
    try:
        result = function(payload, context(db, state, allowed, progress, client))
        if inspect.isawaitable(result):
            result = await result
    finally:
        await client.aclose()
    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    if not isinstance(result, dict):
        raise AppError(
            "invalid_evaluator_response", "Evaluator must return an object", 502
        )
    await db.flush()
    operation = payload["operation"]
    project_id = UUID(payload["project_id"])
    try:
        if operation in {"run", "improve"}:
            row = await require(db, Run, UUID(str(result["run_id"])))
            version = await require(db, AppVersion, row.app_version_id)
            if version.project_id != project_id:
                raise ValueError()
            expected = payload.get("options", {}).get("version_id")
            if expected is not None and str(row.app_version_id) != str(expected):
                raise ValueError()
        elif operation in {"diagnose", "replay", "repair"}:
            model = {"diagnose": Diagnosis, "replay": Replay, "repair": Repair}[
                operation
            ]
            row = await require(db, model, UUID(str(result["id"])))
            version, _ = await target_context(db, model, row.id)
            if version.project_id != project_id:
                raise ValueError()
            result = record(row)
        elif operation == "verify":
            row = await require(db, Verification, UUID(str(result["verification_id"])))
            if str(row.repair_id) != payload["target"]["id"]:
                raise ValueError()
        elif operation == "seed":
            for item in result["items"]:
                row = await require(db, TestCase, UUID(str(item["id"])))
                if row.project_id != project_id:
                    raise ValueError()
        elif operation == "test_connection" and not isinstance(result.get("ok"), bool):
            raise ValueError()
    except (KeyError, ValueError, TypeError, AppError) as exc:
        raise AppError(
            "invalid_evaluator_response",
            "Evaluator returned invalid or unrelated record references",
            502,
        ) from exc
    return state.redactor.scrub(jsonable_encoder(result))
