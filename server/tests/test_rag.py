import json
from types import SimpleNamespace

import pytest

from evaluator.rag.bm25 import bm25_retrieve
from evaluator.rag.chunking import chunk_text
from evaluator.rag.grounding import check_grounding, split_claims
from evaluator.rag.pipeline import classify, compare, evaluate_query
from evaluator.rag.retriever import retrieve
from evaluator.rag.store import build_filters, keyword_query, rrf, vector_literal

POLICY = {"text": "Refunds are available within 30 days with proof of purchase.", "source": "refund-policy"}
SHIPPING = {"text": "Shipping takes 5 to 7 business days.", "source": "shipping"}
CONFIG = {
    "system_prompt": "You are a support bot.",
    "knowledge_base": [POLICY, SHIPPING],
    "retrieval": {"top_k": 2},
}
CHUNK = {"rank": 1, "text": POLICY["text"], "source": "refund-policy", "score": 1.0}


class FakeModel:
    model = "fake"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def complete(self, messages, tools=None, params=None):
        self.calls.append(messages)
        return SimpleNamespace(text=self.replies.pop(0), model="fake-1")


# --- chunking ---------------------------------------------------------------


def test_chunks_respect_max_and_carry_overlap():
    text = " ".join(f"Sentence number {i} says something useful." for i in range(30))
    chunks = chunk_text(text, max_chars=200, overlap=60)
    assert len(chunks) > 3
    assert all(len(c.text) <= 200 for c in chunks)
    assert [c.position for c in chunks] == list(range(len(chunks)))
    assert chunks[0].text.split(". ")[-1] in chunks[1].text  # last sentence repeats


def test_chunks_track_markdown_headings_and_drop_empty():
    chunks = chunk_text("# Refunds\n\nRefunds take 30 days.\n\n## Shipping\n\nShips fast.", 60, 0)
    assert [c.heading for c in chunks] == ["Refunds", "Shipping"]
    assert chunk_text("   \n\n  ") == []


def test_chunk_rejects_bad_sizes_and_cuts_unpunctuated_text():
    with pytest.raises(ValueError):
        chunk_text("x", max_chars=100, overlap=100)
    assert all(len(c.text) <= 60 for c in chunk_text("word " * 100, 60, 10))


# --- retrieval --------------------------------------------------------------


def test_bm25_ranks_relevant_chunk_first_with_trace_compatible_keys():
    found = bm25_retrieve(CONFIG, "what is the refund policy?")
    assert found[0]["source"] == "refund-policy"
    assert set(found[0]) == {"rank", "text", "source", "score"}  # save_trace unpacks these
    assert bm25_retrieve(CONFIG, "the of a") == []


def test_bm25_filters_top_k_and_min_score():
    filtered = {**CONFIG, "retrieval": {"filters": {"source": ["shipping"]}}}
    assert [c["source"] for c in bm25_retrieve(filtered, "refund shipping")] == ["shipping"]
    strict = {**CONFIG, "retrieval": {"min_score": 0.9}}
    assert bm25_retrieve(strict, "refund policy for shipping returns") == []
    assert len(bm25_retrieve({**CONFIG, "retrieval": {"top_k": 1}}, "refund shipping")) == 1


async def test_retrieve_dispatch_and_hybrid_requirements():
    assert (await retrieve(CONFIG, "refund"))[0]["source"] == "refund-policy"
    with pytest.raises(ValueError):
        await retrieve({"retrieval": {"mode": "hybrid"}}, "refund")
    with pytest.raises(ValueError):
        await retrieve({"retrieval": {"mode": "nope"}}, "refund")


def test_rrf_rewards_agreement_between_retrievers():
    fused = rrf([["a", "b", "c"], ["b", "a", "d"]])
    assert fused["a"] == fused["b"] > fused["c"] > 0
    assert fused["d"] < fused["a"]


def test_filters_bind_keys_and_values_never_interpolate():
    sql, params = build_filters({"source": "x", "team'; DROP TABLE x;--": ["a", 2]})
    assert "DROP" not in sql and "d.source = ANY(:fv0)" in sql
    assert params["fv1"] == ["a", "2"] and params["fk1"].startswith("team")
    assert build_filters(None) == ("", {})


def test_keyword_query_and_vector_literal_are_safe_and_stable():
    assert keyword_query("What's the refund policy?! ' | & !") == "refund | policy"
    assert vector_literal([0.5, -1, 2e-9]) == "[0.5,-1,2e-09]"


# --- grounding --------------------------------------------------------------


def test_split_claims_skips_questions_bullets_and_citations():
    answer = "- Refunds take 30 days [1]. Anything else?\n- Ok."
    assert split_claims(answer) == ["Refunds take 30 days."]


async def test_correct_answer_is_grounded():
    report = await check_grounding("Refunds are available within 30 days [1].", [CHUNK])
    assert report.score == 1.0 and not report.unsupported and report.bad_citations == []


async def test_wrong_number_is_caught_even_when_every_word_overlaps():
    report = await check_grounding("Refunds are available within 60 days.", [CHUNK])
    claim = report.unsupported[0]
    assert report.score == 0.0 and claim.method == "numbers" and claim.missing_numbers == ["60"]


async def test_abstention_is_grounded_and_empty_answer_is_not():
    assert (await check_grounding("I don't know based on the context.", [CHUNK])).score == 1.0
    assert (await check_grounding("", [CHUNK])).score == 0.0


async def test_negation_flips_are_not_called_supported():
    chunks = [{"rank": 1, "text": "Refunds are available within 30 days. Digital goods are not refundable.", "source": "p"}]
    flipped = await check_grounding("Refunds are not available within 30 days.", chunks)
    assert flipped.score == 0.0 and flipped.claims[0].method == "negation"
    # a negation elsewhere in the chunk must not taint an unrelated, correct claim
    assert (await check_grounding("Refunds are available within 30 days.", chunks)).score == 1.0
    # and the judge can overturn a polarity flag that was only phrasing
    overturned = await check_grounding(
        "Refunds are not unavailable within 30 days.", chunks, FakeModel(json.dumps({"verdicts": [True]}))
    )
    assert overturned.claims[0].method == "judge" and overturned.score == 1.0


async def test_bad_citation_is_reported():
    report = await check_grounding("Refunds are available within 30 days [4].", [CHUNK])
    assert report.bad_citations == [4]


async def test_judge_settles_borderline_claims_and_failure_keeps_lexical_verdict():
    claim = "Customers can get money back whenever they show their receipt."  # partial overlap
    chunk = {"rank": 1, "text": "Money back is possible with a receipt within 30 days.", "source": "s"}
    plain = await check_grounding(claim, [chunk])
    assert plain.claims[0].method == "fallback"
    judged = await check_grounding(claim, [chunk], FakeModel(json.dumps({"verdicts": [False]})))
    assert judged.claims[0].method == "judge" and judged.score == 0.0
    broken = await check_grounding(claim, [chunk], FakeModel("not json"))
    assert broken.claims[0].method == "fallback"


# --- pipeline: the README demo, one test per failure type -------------------


async def run(config, reply, expected, query="What is the refund policy?", **kw):
    return await evaluate_query(FakeModel(reply), config, query, run_id="r1", expected=expected, **kw)


EXPECTED = {"reference": "30 days", "source": "refund-policy"}


async def test_pass():
    result = await run(CONFIG, "Refunds are available within 30 days [1].", EXPECTED)
    assert (result["verdict"], result["failure_type"], result["grounding_score"]) == ("pass", None, 1.0)


async def test_retrieval_miss_is_the_root_cause_even_when_the_answer_also_hallucinates():
    blind = {**CONFIG, "retrieval": {"filters": {"source": ["shipping"]}}}  # policy filtered out
    result = await run(blind, "Refunds are allowed within 60 days.", EXPECTED)
    assert result["failure_type"] == "retrieval_miss" and result["retrieved_chunks"] == []
    assert "refund-policy" in result["diagnosis"] and "nothing" in result["diagnosis"]


async def test_retrieval_miss_names_the_wrong_chunk_that_was_retrieved():
    blind = {**CONFIG, "retrieval": {"filters": {"source": ["shipping"]}}}
    result = await run(blind, "Shipping takes a week.", EXPECTED, query="How long does shipping take?")
    assert result["failure_type"] == "retrieval_miss"
    assert "['shipping']" in result["diagnosis"]


async def test_hallucination_when_evidence_was_retrieved():
    result = await run(CONFIG, "Refunds are available within 60 days.", EXPECTED)
    assert result["failure_type"] == "hallucination"
    assert "60" in result["diagnosis"]


async def test_prompt_issue_when_model_ignores_good_context():
    result = await run(CONFIG, "I don't know.", EXPECTED)
    assert result["failure_type"] == "prompt_issue" and result["grounding_score"] == 1.0


async def test_result_matches_runs_table_contract_and_prompt_has_context():
    model = FakeModel("Refunds are available within 30 days [1].")
    result = await evaluate_query(model, CONFIG, "refund policy?", run_id="r2", parent_run_id="r1", expected=EXPECTED)
    assert set(result) == {
        "run_id", "parent_run_id", "query", "retrieved_chunks", "answer",
        "grounding_score", "verdict", "failure_type", "diagnosis", "model",
    }
    assert result["model"] == "fake-1" and 0 <= result["grounding_score"] <= 1
    system = model.calls[0][0]["content"]
    assert "[1] (refund-policy)" in system and "only the context" in system
    json.dumps(result)  # JSONB-safe


def test_classify_without_expectations_only_flags_empty_retrieval():
    class R:  # minimal report
        unsupported, abstained, score = [], False, 1.0

    assert classify(R, [], "x", None)[1] == "retrieval_miss"
    assert classify(R, [CHUNK], "x", None)[0] == "pass"


# --- verification gate --------------------------------------------------------


def test_compare_accepts_only_fix_without_regressions():
    p, f = {"verdict": "pass"}, {"verdict": "fail"}
    base = {"t": f, "a": p, "h": p}
    assert compare(base, {"t": p, "a": p, "h": p}, ["t"], ["h"])["accepted"] is True
    broke = compare(base, {"t": p, "a": f, "h": p}, ["t"], ["h"])
    assert broke["accepted"] is False and broke["regressed_cases"] == ["a"]
    assert compare(base, {"t": p, "a": p, "h": f}, ["t"], ["h"])["holdout_passed"] is False
    assert compare(base, {"t": f, "a": p, "h": p}, ["t"], ["h"])["target_passed"] is False


# --- embeddings (duck-typed HTTP client: no network, no httpx needed) ---------


class FakeHTTP:
    def __init__(self, status, body):
        self.status, self.body, self.sent = status, body, []

    def stream(self, method, url, json=None, headers=None, timeout=None):
        self.sent.append((url, json, headers))
        outer = self

        class Response:
            status_code = outer.status
            headers = {}

            async def aiter_bytes(self):
                yield outer.body

        class Ctx:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, *a):
                return False

        return Ctx()


async def test_embeddings_reorder_by_index_and_batch():
    from evaluator.rag.embeddings import BATCH_SIZE, EmbeddingClient

    body = json.dumps({"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}).encode()
    http = FakeHTTP(200, body)
    client = EmbeddingClient(http, "k", "https://x/v1/", "emb-1")
    assert await client.embed(["a", "b"]) == [[1.0], [2.0]]
    assert http.sent[0][0] == "https://x/v1/embeddings" and http.sent[0][1]["model"] == "emb-1"
    assert http.sent[0][2]["Authorization"] == "Bearer k"
    assert BATCH_SIZE >= 1


async def test_embeddings_surface_provider_errors_and_count_mismatch():
    from evaluator.rag.embeddings import EmbeddingClient
    from evaluator.llm import ModelError

    err = json.dumps({"error": {"message": "insufficient credit"}}).encode()
    with pytest.raises(ModelError) as info:
        await EmbeddingClient(FakeHTTP(402, err), "k", "https://x", "m").embed(["a"])
    assert info.value.kind == "budget"
    short = json.dumps({"data": [{"index": 0, "embedding": [1.0]}]}).encode()
    with pytest.raises(ModelError):
        await EmbeddingClient(FakeHTTP(200, short), "k", "https://x", "m").embed(["a", "b"])
