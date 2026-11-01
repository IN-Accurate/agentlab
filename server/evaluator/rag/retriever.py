"""One entry point for retrieval. Output is always [{rank, text, source, score}]."""

from evaluator.rag.bm25 import bm25_retrieve


async def retrieve(config, query, *, db=None, embedder=None, project_id=None):
    """Pick a retriever from the version config.

    retrieval.mode:
      "bm25"   (default) in-memory BM25 over config["knowledge_base"]; no network
      "hybrid" Neon pgvector + full-text, fused with RRF  (needs db, embedder, project_id)
      "dense"  vector search only                          (same requirements)
    A version with no inline knowledge_base but a project KB in Neon uses hybrid.
    """
    settings = config.get("retrieval") or {}
    mode = settings.get("mode") or ("bm25" if config.get("knowledge_base") else "hybrid")
    if mode == "bm25":
        return bm25_retrieve(config, query)
    if mode in {"hybrid", "dense"}:
        if db is None or embedder is None or project_id is None:
            raise ValueError(f"retrieval.mode={mode!r} needs db, embedder and project_id")
        from evaluator.rag.store import search  # keeps bm25-only users free of SQLAlchemy

        return await search(db, embedder, project_id, query, {**settings, "mode": mode})
    raise ValueError(f"Unknown retrieval.mode {mode!r}")
