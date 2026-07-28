"""Local, CPU-only embedding and reranking for Graphiti — no external API.

Graphiti needs two things beyond its extraction LLM:

  * an ``EmbedderClient`` that turns text into a semantic vector, and
  * a ``CrossEncoderClient`` that reranks candidate passages for a query.

The agent's own model (an Anthropic-shaped chat endpoint) does the reasoning
(entity/edge extraction), but Anthropic exposes no embeddings endpoint, so
both of the pieces here run **locally** via fastembed (ONNX on CPU). This keeps
the whole memory backend self-contained: zero per-recall API cost, zero
external egress, matching the embedded-Kuzu philosophy.

The reranker deliberately reuses the same embedding model (cosine similarity
between query and passage vectors) instead of pulling in a heavyweight
sentence-transformers cross-encoder. It is a lighter reranker, but it needs no
extra model download and no torch.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding
from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.embedder.client import EmbedderClient

# Default local embedding model: 384-dim, ~90 MB, strong quality/size tradeoff.
DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"


@lru_cache(maxsize=4)
def _load_model(model_name: str) -> TextEmbedding:
    """Load (and cache) a fastembed model. First call downloads the weights
    once into the fastembed cache; subsequent calls in-process are free."""
    return TextEmbedding(model_name=model_name)


def _embed_sync(model_name: str, texts: list[str]) -> list[list[float]]:
    model = _load_model(model_name)
    # fastembed yields one ndarray per input, in order.
    return [vec.tolist() for vec in model.embed(texts)]


class LocalFastEmbedder(EmbedderClient):
    """Graphiti ``EmbedderClient`` backed by a local fastembed model.

    Implements the two-method contract (``create`` / ``create_batch``). The
    fastembed call is synchronous, so it is run in the default executor to
    avoid blocking the event loop.
    """

    def __init__(self, model_name: str = DEFAULT_EMBED_MODEL) -> None:
        self.model_name = model_name

    async def create(self, input_data) -> list[float]:
        # Graphiti calls this to embed a single item. ``input_data`` is usually
        # a str; the base type also allows a list of strings or token ids, which
        # we coerce to one text defensively. Returns one vector.
        if isinstance(input_data, str):
            text = input_data
        elif isinstance(input_data, list) and input_data and isinstance(input_data[0], str):
            text = " ".join(str(x) for x in input_data)
        else:
            text = str(input_data)
        loop = asyncio.get_running_loop()
        vecs = await loop.run_in_executor(None, _embed_sync, self.model_name, [text])
        return vecs[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        if not input_data_list:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _embed_sync, self.model_name, list(input_data_list)
        )


class LocalCosineReranker(CrossEncoderClient):
    """Lightweight local reranker: score passages by cosine similarity to the
    query in the shared local embedding space. Avoids a separate
    sentence-transformers cross-encoder (and torch) at a modest quality cost.
    """

    def __init__(self, model_name: str = DEFAULT_EMBED_MODEL) -> None:
        self.model_name = model_name

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        if not passages:
            return []
        loop = asyncio.get_running_loop()
        vecs = await loop.run_in_executor(
            None, _embed_sync, self.model_name, [query, *passages]
        )
        q = np.asarray(vecs[0], dtype=np.float32)
        q_norm = q / (np.linalg.norm(q) + 1e-8)
        scored: list[tuple[str, float]] = []
        for passage, raw in zip(passages, vecs[1:], strict=False):
            p = np.asarray(raw, dtype=np.float32)
            p_norm = p / (np.linalg.norm(p) + 1e-8)
            scored.append((passage, float(np.dot(q_norm, p_norm))))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
