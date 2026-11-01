"""OpenAI-compatible /embeddings client, built the same way as evaluator.llm.ModelClient."""

import json

from app.errors import AppError
from evaluator.llm import (
    PROVIDER_URLS,
    REQUEST_TIMEOUT,
    ModelError,
    failure,
    read_capped,
)

BATCH_SIZE = 64


class EmbeddingClient:
    def __init__(self, http, api_key, base_url, model):
        self.http = http
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/embeddings"
        self.model = model

    @classmethod
    def from_context(cls, context, credential_id, options=None):
        """`embedding_model` / `embedding_base_url` come from options, then credential metadata."""
        credentials = context["credentials"]
        meta = credentials["metadata"](credential_id)
        options = options or {}
        base_url = (
            options.get("embedding_base_url")
            or meta.get("embedding_base_url")
            or meta.get("base_url")
            or PROVIDER_URLS.get(meta["provider"])
        )
        model = options.get("embedding_model") or meta.get("embedding_model")
        if not base_url or not model:
            raise AppError(
                "embeddings_not_configured",
                "Set embedding_model (and base_url) on the credential or options",
                422,
            )
        return cls(
            context["http_client"], credentials["decrypt_for_call"](credential_id), base_url, model
        )

    async def embed(self, texts):
        """Returns one vector per input text, in order."""
        vectors = []
        for start in range(0, len(texts), BATCH_SIZE):
            vectors += await self.embed_batch(texts[start : start + BATCH_SIZE])
        return vectors

    async def embed_batch(self, batch):
        async with self.http.stream(
            "POST",
            self.url,
            json={"model": self.model, "input": batch},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=REQUEST_TIMEOUT,
        ) as response:
            raw = await read_capped(response)
        if response.status_code != 200:
            raise failure(response.status_code, raw)
        try:
            rows = sorted(json.loads(raw)["data"], key=lambda row: row["index"])
            vectors = [[float(x) for x in row["embedding"]] for row in rows]
        except (ValueError, KeyError, TypeError) as exc:
            raise ModelError("provider", "Malformed embeddings response") from exc
        if len(vectors) != len(batch):
            raise ModelError("provider", "Embeddings count did not match inputs")
        return vectors
