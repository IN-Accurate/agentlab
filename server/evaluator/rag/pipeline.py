"""Query -> retrieve -> answer -> grounding check -> verdict + failure type.

Produces exactly the fields of the `runs` table / POST /v1/mvp/runs (RunResult), so a
result can be saved directly or posted to the API.
"""

from evaluator.rag.grounding import check_grounding
from evaluator.rag.retriever import retrieve as default_retrieve

GROUNDING_RULE = (
    "Answer using only the context below. Cite the chunks you used like [1]. "
    "If the context does not contain the answer, say you don't know. "
    "Never state a number, date or policy that is not in the context."
)
PASS_AT = 0.8


def build_messages(config, query, chunks):
    """Same layout as agent.system_message, with a default grounding rule if none is set."""
    context = "\n".join(f"[{c['rank']}] ({c['source']}) {c['text']}" for c in chunks)
    parts = [
        config.get("system_prompt"),
        config.get("grounding_rule") or GROUNDING_RULE,
        f"Context:\n{context}" if chunks else "Context: (nothing was retrieved)",
    ]
    system = "\n\n".join(p for p in parts if p)
    return [{"role": "system", "content": system}, {"role": "user", "content": query}]


def classify(report, chunks, answer, expected, pass_at=PASS_AT):
    """Returns (verdict, failure_type, diagnosis). Order matters: root cause first.

    retrieval_miss  the evidence the answer needed never reached the model
    hallucination   evidence was there, but the answer states things it does not support
    prompt_issue    evidence was there and nothing unsupported was said, yet the answer
                    is wrong/missing (ignored context, abstained, wrong focus)
    """
    reference = (expected or {}).get("reference")
    source = (expected or {}).get("source")
    joined = "\n".join(c["text"] for c in chunks).lower()
    got = [c["source"] for c in chunks]
    if source:
        retrieved = source in got
    elif reference:
        retrieved = reference.lower() in joined
    else:
        retrieved = bool(chunks)
    if not retrieved:
        want = f"source {source!r}" if source else f"text {reference!r}" if reference else "any context"
        return "fail", "retrieval_miss", f"Expected {want} was not retrieved (got: {got or 'nothing'})."
    if report.unsupported:
        first = report.unsupported[0]
        why = (
            f"numbers not in context: {', '.join(first.missing_numbers)}"
            if first.missing_numbers
            else f"best chunk covers only {first.coverage:.0%} of the claim"
        )
        return "fail", "hallucination", (
            f"{len(report.unsupported)} unsupported claim(s); first: {first.text!r} ({why})."
        )
    if reference and reference.lower() not in answer.lower():
        how = "abstained" if report.abstained else "did not state it"
        return "fail", "prompt_issue", (
            f"Retrieval found {reference!r} but the answer {how}; the prompt or grounding rule is not using the context."
        )
    if report.score < pass_at:
        return "fail", "hallucination", f"Grounding score {report.score:.2f} is below {pass_at}."
    return "pass", None, None


async def evaluate_query(
    model,
    config,
    query,
    *,
    run_id,
    expected=None,
    parent_run_id=None,
    judge_model=None,
    retriever=default_retrieve,
    retrieval_context=None,
    params=None,
    pass_at=PASS_AT,
):
    """Run one query end to end. `retrieval_context` = kwargs for the retriever (db, embedder, project_id)."""
    chunks = await retriever(config, query, **(retrieval_context or {}))
    reply = await model.complete(
        build_messages(config, query, chunks),
        params={**(config.get("params") or {}), **(params or {})},
    )
    report = await check_grounding(reply.text, chunks, judge_model)
    verdict, failure_type, diagnosis = classify(report, chunks, reply.text, expected, pass_at)
    return {
        "run_id": run_id,
        "parent_run_id": parent_run_id,
        "query": query,
        "retrieved_chunks": chunks,
        "answer": reply.text,
        "grounding_score": report.score,
        "verdict": verdict,
        "failure_type": failure_type,
        "diagnosis": diagnosis,
        "model": reply.model or model.model,
    }


async def save_run(db, result):
    """Flush one result into `runs`. The parent run (if any) must already be stored."""
    from app.models import Run  # lazy: the pure parts of this module need no SQLAlchemy

    row = Run(**result)
    db.add(row)
    await db.flush()
    return row


def compare(baseline, candidate, target_ids, holdout_ids=()):
    """Verification gate (README step 6). Both args map case id -> RunResult dict.

    Accepted only if every target case now passes, nothing that passed before fails now,
    and every holdout case that passed at baseline still passes.
    Returns the column values of a `verifications` row (minus ids).
    """
    def ok(results, case):
        return results.get(case, {}).get("verdict") == "pass"

    target_passed = all(ok(candidate, c) for c in target_ids)
    regressions = [c for c in baseline if ok(baseline, c) and not ok(candidate, c)]
    holdout_passed = all(ok(candidate, c) for c in holdout_ids if ok(baseline, c))
    return {
        "target_passed": target_passed,
        "regressions_count": len(regressions),
        "regressed_cases": regressions,  # not a column: for the scorecard
        "holdout_passed": holdout_passed,
        "accepted": target_passed and not regressions and holdout_passed,
    }
