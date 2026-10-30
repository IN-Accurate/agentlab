import re
from copy import deepcopy


def retrieve(config, query):
    config = config.model_dump() if hasattr(config, "model_dump") else config
    settings = config.get("retrieval", {})
    top_k = settings.get("top_k", 3)
    filters = settings.get("filters", {})
    words = set(re.findall(r"[a-z0-9]+", query.lower()))
    ranked = []
    for chunk in config.get("knowledge_base", []):
        if any(chunk.get(key) != value for key, value in filters.items()):
            continue
        tokens = set(re.findall(r"[a-z0-9]+", chunk["text"].lower()))
        score = len(words & tokens) / max(1, len(words))
        if score < settings.get("min_score", 0):
            continue
        ranked.append({**deepcopy(chunk), "score": score})
    ranked.sort(key=lambda item: (-item["score"], item["id"]))
    return [{**chunk, "rank": rank} for rank, chunk in enumerate(ranked[:top_k], 1)]
