"""Graphiti compatibility driver backed by maintained LadybugDB.

LadybugDB is the actively maintained continuation of Kuzu and intentionally
preserves its embedded Python API. Graphiti 0.29.3 still names its operation
set ``kuzu``. This module imports that operation layer through a short-lived
compatibility alias, removes the alias immediately, and initializes the driver
against Ladybug directly. The archived Kuzu wheel is not a runtime dependency.
"""

from __future__ import annotations

import importlib
import re
import sys
from typing import Any

# Never mix two native storage engines in one process. Legacy export happens in
# a separate subprocess before the provider imports this module.
_existing_kuzu = sys.modules.get("kuzu")
if _existing_kuzu is not None and getattr(_existing_kuzu, "__name__", "") != "ladybug":
    raise RuntimeError(
        "the archived kuzu native module is already loaded; restart Hermes before "
        "activating the Ladybug graph-memory provider"
    )

_existing_driver = sys.modules.get("graphiti_core.driver.kuzu_driver")
if _existing_driver is not None:
    bound_engine = getattr(_existing_driver, "kuzu", None)
    if bound_engine is not None and getattr(bound_engine, "__name__", "") != "ladybug":
        raise RuntimeError(
            "Graphiti's embedded driver was already bound to archived Kuzu; restart "
            "Hermes before activating the Ladybug graph-memory provider"
        )

import ladybug

_alias_was_absent = "kuzu" not in sys.modules
if _alias_was_absent:
    sys.modules["kuzu"] = ladybug
try:
    _graphiti_kuzu_driver = importlib.import_module("graphiti_core.driver.kuzu_driver")
finally:
    if _alias_was_absent:
        sys.modules.pop("kuzu", None)


class LadybugDriver(_graphiti_kuzu_driver.KuzuDriver):
    """Graphiti's Kuzu-shaped operations running on LadybugDB."""

    engine_name = "ladybug"

    def __init__(self, db: str = ":memory:", max_concurrent_queries: int = 1):
        # Do not call KuzuDriver.__init__: it emits a deprecation warning and
        # resolves its engine through a module-global `kuzu` binding. Build the
        # same operation registry explicitly against Ladybug instead.
        _graphiti_kuzu_driver.GraphDriver.__init__(self)
        self.db = ladybug.Database(db)
        self.setup_schema()
        self.client = ladybug.AsyncConnection(
            self.db,
            max_concurrent_queries=max_concurrent_queries,
        )
        self._entity_node_ops = _graphiti_kuzu_driver.KuzuEntityNodeOperations()
        self._episode_node_ops = _graphiti_kuzu_driver.KuzuEpisodeNodeOperations()
        self._community_node_ops = _graphiti_kuzu_driver.KuzuCommunityNodeOperations()
        self._saga_node_ops = _graphiti_kuzu_driver.KuzuSagaNodeOperations()
        self._entity_edge_ops = _graphiti_kuzu_driver.KuzuEntityEdgeOperations()
        self._episodic_edge_ops = _graphiti_kuzu_driver.KuzuEpisodicEdgeOperations()
        self._community_edge_ops = _graphiti_kuzu_driver.KuzuCommunityEdgeOperations()
        self._has_episode_edge_ops = _graphiti_kuzu_driver.KuzuHasEpisodeEdgeOperations()
        self._next_episode_edge_ops = _graphiti_kuzu_driver.KuzuNextEpisodeEdgeOperations()
        self._search_ops = _graphiti_kuzu_driver.KuzuSearchOperations()
        self._graph_ops = _graphiti_kuzu_driver.KuzuGraphMaintenanceOperations()

    def setup_schema(self) -> None:
        connection = ladybug.Connection(self.db)
        connection.execute(_graphiti_kuzu_driver.SCHEMA_QUERIES)
        connection.close()

    async def execute_query(
        self,
        cypher_query_: str,
        **kwargs: Any,
    ) -> tuple[Any, None, None]:
        """Execute Graphiti Cypher with Ladybug's strict parameter binding.

        Graphiti omits keys whose values are ``None``. Archived Kuzu accepted
        missing optional parameters, while Ladybug requires every ``$parameter``
        referenced by a prepared statement. Bind omitted values explicitly as
        null, then preserve Graphiti's expected result shape.
        """
        params = dict(kwargs)
        params.pop("database_", None)
        params.pop("routing_", None)
        for name in re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", cypher_query_):
            params.setdefault(name, None)

        results = await self.client.execute(cypher_query_, parameters=params)
        if not results:
            return [], None, None
        if isinstance(results, list):
            rows = [list(result.rows_as_dict()) for result in results]
        else:
            rows = list(results.rows_as_dict())
        return rows, None, None
