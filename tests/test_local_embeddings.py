from __future__ import annotations

import asyncio

import numpy as np

from local_embeddings import LocalCosineReranker, LocalFastEmbedder


def test_local_embedder_returns_stable_384_dimensional_vectors():
    embedder = LocalFastEmbedder()
    first, second = asyncio.run(embedder.create_batch(["graph memory", "graph memory"]))

    assert len(first) == 384
    assert len(second) == 384
    assert np.allclose(first, second)


def test_local_reranker_puts_identical_meaning_first():
    reranker = LocalCosineReranker()
    ranked = asyncio.run(
        reranker.rank(
            "Which database stores graph memory?",
            [
                "LadybugDB stores the graph-memory database.",
                "The weather is sunny in Tallinn.",
            ],
        )
    )

    assert ranked[0][0] == "LadybugDB stores the graph-memory database."
    assert ranked[0][1] > ranked[1][1]
