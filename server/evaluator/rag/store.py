"""Neon/pgvector knowledge-base storage and hybrid (dense + keyword) search.

Every statement is parameterised; filter *keys* are bound values too, never
interpolated. Embeddings travel as pgvector text literals ('[0.1,0.2,...]') so no
extra driver package is needed with asyncpg.
"""

import hashlib
import json

from sqlalchemy import text

from evaluator.rag.chunking import chunk_text
from evaluator.rag.text import STOPWORDS, WORD

RRF_K = 60  # standard reciprocal-rank-fusion constant
CANDIDATES = 20  # per-retriever pool fed into fusion


def vector_literal(vector):
    return "[" + ",".join(f"{x:.8g}" for x in vector) + "]"


def build_filters(filters):
    """Config filters -> (sql, params). `source` is a column; other keys read document metadata."""
    clauses, params = [], {}
    for i, (key, value) in enumerate((filters or {}).items()):
        values = [str(v) for v in (value if isinstance(value, list) else [value])]
        params[f"fv{i}"] = values
        if key == "source":
            clauses.append(f"d.source = ANY(:fv{i})")
        else:
            params[f"fk{i}"] = key
            clauses.append(f"(d.metadata ->> CAST(:fk{i} AS text)) = ANY(:fv{i})")
    return "".join(f" AND {c}" for c in clauses), params


def keyword_query(query):
    """OR-joined terms for to_tsquery. Only [a-z0-9] survives, so nothing can inject syntax."""
    words = [w for w in WORD.findall(query.lower()) if w not in STOPWORDS]
    return " | ".join(dict.fromkeys(words))


def rrf(rankings, k=RRF_K):
    """Reciprocal rank fusion: rank-based, so cosine and ts_rank scales never need normalising."""
    fused = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank)
    return fused


async def ingest(db, embedder, project_id, documents, max_chars=800, overlap=120):
    """Upsert documents ({source, text, title?, metadata?}); unchanged ones are skipped.

    Returns {"ingested": [...sources], "skipped": [...sources], "chunks": n}.
    """
    done, skipped, total = [], [], 0
    # Raw SQL bypasses the ORM before_flush redactor, so scrub explicitly -- and do it
    # before embedding so a secret is never sent to the embedding provider either.
    redactor = db.info.get("redactor")
    scrub = redactor.scrub if redactor else (lambda value: value)
    for doc in documents:
        source = doc["source"]
        digest = hashlib.sha256(doc["text"].encode()).hexdigest()  # hash the original
        body = scrub(doc["text"])
        existing = await db.execute(
            text("SELECT id, content_hash FROM kb_documents WHERE project_id=:p AND source=:s"),
            {"p": project_id, "s": source},
        )
        row = existing.first()
        if row and row.content_hash == digest:
            skipped.append(source)
            continue
        chunks = chunk_text(body, max_chars, overlap)
        if not chunks:
            skipped.append(source)
            continue
        vectors = await embedder.embed([c.text for c in chunks])  # embed before deleting anything
        if row:
            await db.execute(text("DELETE FROM kb_documents WHERE id=:i"), {"i": row.id})
        inserted = await db.execute(
            text(
                "INSERT INTO kb_documents (project_id, source, title, content_hash, metadata) "
                "VALUES (:p, :s, :t, :h, CAST(:m AS jsonb)) RETURNING id"
            ),
            {
                "p": project_id,
                "s": source,
                "t": scrub(doc.get("title")) if doc.get("title") else None,
                "h": digest,
                "m": json.dumps(scrub(doc.get("metadata") or {})),
            },
        )
        document_id = inserted.scalar_one()
        await db.execute(
            text(
                "INSERT INTO kb_chunks (document_id, project_id, position, text, heading, "
                "embedding, embedding_model) VALUES (:d, :p, :pos, :t, :h, "
                "CAST(:e AS vector), :m)"
            ),
            [
                {
                    "d": document_id,
                    "p": project_id,
                    "pos": c.position,
                    "t": c.text,
                    "h": c.heading,
                    "e": vector_literal(v),
                    "m": embedder.model,
                }
                for c, v in zip(chunks, vectors, strict=True)
            ],
        )
        done.append(source)
        total += len(chunks)
    return {"ingested": done, "skipped": skipped, "chunks": total}


DENSE_SQL = (
    "SELECT c.id, c.text, d.source, 1 - (c.embedding <=> CAST(:emb AS vector)) AS score "
    "FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id "
    "WHERE c.project_id = :p AND c.embedding IS NOT NULL{filters} "
    "ORDER BY c.embedding <=> CAST(:emb AS vector) LIMIT :n"
)
KEYWORD_SQL = (
    "SELECT c.id, c.text, d.source, ts_rank_cd(c.tsv, q) AS score "
    "FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id, "
    "to_tsquery('english', :q) q "
    "WHERE c.project_id = :p AND c.tsv @@ q{filters} "
    "ORDER BY score DESC LIMIT :n"
)


async def search(db, embedder, project_id, query, settings=None):
    """Hybrid search. Returns chunks shaped {rank, text, source, score} (score = fused RRF)."""
    settings = settings or {}
    top_k = settings.get("top_k", 3)
    clause, params = build_filters(settings.get("filters"))
    base = {"p": project_id, "n": settings.get("candidates", CANDIDATES), **params}
    [vector] = await embedder.embed([query])
    dense = (
        await db.execute(
            text(DENSE_SQL.format(filters=clause)), {**base, "emb": vector_literal(vector)}
        )
    ).all()
    floor = settings.get("min_dense_score")
    if floor is not None:
        dense = [r for r in dense if r.score >= floor]
    keyword_text = keyword_query(query)
    sparse = []
    if keyword_text and settings.get("mode", "hybrid") != "dense":
        sparse = (
            await db.execute(
                text(KEYWORD_SQL.format(filters=clause)), {**base, "q": keyword_text}
            )
        ).all()
    rows = {r.id: r for r in [*dense, *sparse]}
    fused = rrf([[r.id for r in dense], [r.id for r in sparse]])
    ranked = sorted(fused, key=lambda key: -fused[key])[:top_k]
    return [
        {"rank": i, "text": rows[k].text, "source": rows[k].source, "score": fused[k]}
        for i, k in enumerate(ranked, start=1)
    ]
