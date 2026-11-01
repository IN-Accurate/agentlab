"""Okapi BM25 over the inline `knowledge_base` of an app version.

Drop-in for agent.retrieve(): same filters, same output keys (rank/text/source/score),
because save_trace unpacks each chunk straight into RetrievedChunk columns.
`min_score` keeps its existing meaning (fraction of query terms matched), so a
config written for the old retriever behaves the same way.
"""

import math
from collections import Counter

from evaluator.rag.text import terms, tokens


class BM25:
    def __init__(self, documents, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(tokens(doc)) for doc in documents]
        self.lengths = [sum(tf.values()) for tf in self.tf]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        df = Counter(term for tf in self.tf for term in tf)
        n = len(documents)
        # +1 inside the log keeps idf positive even for terms in most documents.
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def score(self, query_terms, index):
        tf, length = self.tf[index], self.lengths[index]
        total = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if not freq:
                continue
            norm = 1 - self.b + self.b * (length / self.avg if self.avg else 1)
            total += self.idf[term] * freq * (self.k1 + 1) / (freq + self.k1 * norm)
        return total


def passes(chunk, filters):
    return all(
        chunk.get(key) in (value if isinstance(value, list) else [value])
        for key, value in filters.items()
    )


def bm25_retrieve(config, query):
    settings = config.get("retrieval") or {}
    wanted = terms(query)
    if not wanted:
        return []
    pool = [
        c
        for c in config.get("knowledge_base") or []
        if passes(c, settings.get("filters") or {})
    ]
    index = BM25([f"{c['text']} {c.get('source', '')}" for c in pool])
    scored = []
    for i, chunk in enumerate(pool):
        coverage = len(wanted & set(index.tf[i])) / len(wanted)
        score = index.score(wanted, i)
        if score > 0 and coverage >= settings.get("min_score", 0):
            scored.append((score, chunk))
    scored.sort(key=lambda item: -item[0])
    return [
        {"rank": rank, "text": c["text"], "source": c.get("source"), "score": score}
        for rank, (score, c) in enumerate(scored[: settings.get("top_k", 3)], start=1)
    ]
