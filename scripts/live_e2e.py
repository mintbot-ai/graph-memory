#!/usr/bin/env python3
"""Live store→extract→search E2E using the active Hermes provider.

This consumes the active provider's quota/credit. It writes only to a temporary
Kuzu database and removes it afterward; the user's real graph is untouched.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERMES_SOURCE = Path.home() / ".hermes" / "hermes-agent"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERMES_SOURCE))

from graphiti_core import Graphiti
from graphiti_core.driver.driver import GraphProvider
from graphiti_core.driver.kuzu_driver import KuzuDriver
from graphiti_core.graph_queries import get_fulltext_indices
from graphiti_core.nodes import EpisodeType

from hermes_llm import HermesAgentLLMClient
from local_embeddings import LocalCosineReranker, LocalFastEmbedder


async def ensure_kuzu_indexes(driver: KuzuDriver) -> None:
    """Create the FTS indexes Graphiti's Kuzu search path requires.

    Graphiti 0.29.3 leaves ``KuzuDriver.build_indices_and_constraints`` as a
    no-op, although its search queries assume these indexes already exist.
    """
    for query in get_fulltext_indices(GraphProvider.KUZU):
        try:
            await driver.execute_query(query)
        except RuntimeError as exc:
            if "already exists" not in str(exc).lower():
                raise


async def main() -> None:
    temp_dir = Path(tempfile.mkdtemp(prefix="graph-memory-live-e2e-"))
    driver = KuzuDriver(db=str(temp_dir / "graph.kuzu"))
    graph = Graphiti(
        graph_driver=driver,
        llm_client=HermesAgentLLMClient(),
        embedder=LocalFastEmbedder(),
        cross_encoder=LocalCosineReranker(),
        max_coroutines=1,
    )
    try:
        await ensure_kuzu_indexes(driver)
        await graph.add_episode(
            name="database-decision",
            episode_body=(
                "Mart selected embedded Kuzu as the database for the Graph Memory "
                "project. Graph Memory is the first reference implementation of AXP."
            ),
            source_description="live-e2e",
            reference_time=datetime.now(timezone.utc),
            source=EpisodeType.message,
            group_id=None,
        )
        results = await graph.search(
            "Which database does the AXP graph-memory reference implementation use?",
            group_ids=None,
            num_results=8,
        )
        facts = [str(getattr(edge, "fact", "") or "") for edge in results]
        print(f"facts_returned={len(facts)}")
        for fact in facts:
            print(f"- {fact}")
        if not any("kuzu" in fact.lower() for fact in facts):
            raise AssertionError("semantic recall did not return the Kuzu fact")
        print("LIVE_E2E_OK")
    finally:
        await graph.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
