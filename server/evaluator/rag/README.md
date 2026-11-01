# TestLab RAG

| File | Purpose |
| --- | --- |
| text.py | Shared tokeniser (stopwords, plural folding, number normalisation) |
| chunking.py | Sentence-aligned chunks with overlap; markdown headings are hard boundaries |
| bm25.py | In-memory BM25 over a version's inline `knowledge_base` (drop-in for `agent.retrieve`) |
| embeddings.py | OpenAI-compatible `/embeddings` client, same credential flow as `ModelClient` |
| store.py | Neon/pgvector ingest + hybrid search (dense + full-text, fused with RRF) |
| retriever.py | `retrieve()` picks bm25 / hybrid / dense from `config["retrieval"]["mode"]` |
| grounding.py | Per-claim support check -> `grounding_score`; numbers, polarity, coverage, optional judge |
| pipeline.py | `evaluate_query` -> a row for `runs` / `POST /v1/mvp/runs`; `compare` = verification gate |

## Setup (Neon)

```sh
psql "$DATABASE_URL_DIRECT" -f db/rag_schema.sql      # once per branch; needs pgvector
DATABASE_URL=... EMBEDDING_API_KEY=... EMBEDDING_BASE_URL=... EMBEDDING_MODEL=... \
  python server/scripts/ingest_kb.py --project <uuid> --dir ./kb
```

`vector(1536)` is fixed in `db/rag_schema.sql`; change it if your embedding model differs.

## Config knobs (`app_versions.config.retrieval`)

`mode` (`bm25` | `hybrid` | `dense`), `top_k`, `min_score` (bm25: fraction of query terms matched),
`min_dense_score` (cosine floor), `filters` (`{"source": [...], "<metadata key>": ...}`).
A repair patch of type `retrieval` can change any of these without touching code.

## Failure types (`pipeline.classify`)

Checked in this order, so the root cause wins:

1. `retrieval_miss` - `expected.source` (or `expected.reference` text) never reached the model
2. `hallucination` - evidence was retrieved but the answer has unsupported claims
3. `prompt_issue` - evidence retrieved, nothing unsupported, yet the answer omits/abstains
