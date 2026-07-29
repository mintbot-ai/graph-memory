"""Temporal graph-memory provider for Hermes.

Graphiti extracts entities, relationships, facts, and validity windows with the
model/provider already selected in Hermes. FastEmbed performs semantic vector
work locally, and LadybugDB (the maintained Kuzu continuation) stores the graph
in-process under the active ``HERMES_HOME``. No daemon, second API key, or
Hermes core patch is required.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
_DEFAULT_RECALL_LIMIT = 8
_WRITE_FLUSH_TIMEOUT_SECONDS = 180


class _AsyncLoop:
    """Own one event loop for Graphiti's async API."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self._serve,
            daemon=True,
            name="graph-memory-async",
        )
        self.thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coroutine, timeout: float | None = None):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        if not self.thread.is_alive():
            self.loop.close()


class GraphMemoryProvider(MemoryProvider):
    """Hermes ``MemoryProvider`` backed by Graphiti and embedded LadybugDB.

    LadybugDB supports one writer process per database. Hermes profiles already have
    separate ``HERMES_HOME`` roots; within a profile, run one long-lived Hermes
    process (normally the gateway) while graph memory is active.
    """

    def __init__(self) -> None:
        self._graphiti = None
        self._async: _AsyncLoop | None = None
        self._writes: concurrent.futures.ThreadPoolExecutor | None = None
        self._pending: set[concurrent.futures.Future] = set()
        self._pending_lock = threading.Lock()
        self._accept_writes = False
        self._ready = False
        self._primary_context = True
        self._hermes_home = ""
        self._database_path = ""
        self._embedding_model = DEFAULT_EMBED_MODEL
        self._ingest_turns = "all"
        self._recall_limit = _DEFAULT_RECALL_LIMIT
        self._last_error = ""

    @property
    def name(self) -> str:
        return "graph-memory"

    def is_available(self) -> bool:
        """Dependency-only check; performs no network access."""
        try:
            import fastembed  # noqa: F401
            import graphiti_core  # noqa: F401
            import ladybug  # noqa: F401
            return True
        except Exception as exc:
            logger.warning("graph-memory dependencies unavailable: %s", exc)
            return False

    @staticmethod
    def _provider_config() -> dict[str, Any]:
        try:
            from hermes_cli.config import load_config

            memory = (load_config().get("memory") or {})
            value = memory.get("graph-memory") or {}
            return dict(value) if isinstance(value, dict) else {}
        except Exception:
            return {}

    def initialize(self, session_id: str, **kwargs) -> None:
        del session_id

        if not self.is_available():
            raise RuntimeError(
                "graph-memory dependencies are missing; run `hermes memory setup graph-memory`"
            )

        from graphiti_core import Graphiti
        from .hermes_llm import HermesAgentLLMClient
        from .ladybug_driver import LadybugDriver
        from .local_embeddings import LocalCosineReranker, LocalFastEmbedder

        agent_context = str(kwargs.get("agent_context") or "primary")
        self._primary_context = agent_context == "primary"
        self._hermes_home = str(
            kwargs.get("hermes_home")
            or os.environ.get("HERMES_HOME")
            or (Path.home() / ".hermes")
        )
        provider_config = self._provider_config()
        self._embedding_model = str(
            provider_config.get("embedding_model") or DEFAULT_EMBED_MODEL
        )
        self._ingest_turns = str(provider_config.get("ingest_turns") or "all")
        try:
            self._recall_limit = max(
                1,
                min(50, int(provider_config.get("recall_limit") or _DEFAULT_RECALL_LIMIT)),
            )
        except (TypeError, ValueError):
            self._recall_limit = _DEFAULT_RECALL_LIMIT

        state_dir = Path(self._hermes_home) / "graph-memory"
        state_dir.mkdir(parents=True, exist_ok=True)
        legacy_path = state_dir / "graph.kuzu"
        ladybug_path = state_dir / "graph.lbug"
        if legacy_path.exists() and not ladybug_path.exists():
            self._migrate_legacy_database(legacy_path, ladybug_path)
        self._database_path = str(ladybug_path)

        self._async = _AsyncLoop()
        try:
            embedder = LocalFastEmbedder(self._embedding_model)
            reranker = LocalCosineReranker(self._embedding_model)
            # Ladybug is intentionally single-tenant here. Passing a custom
            # group_id triggers a known Graphiti 0.29.3 KuzuDriver bug; profile
            # isolation is instead provided by the HERMES_HOME-specific DB.
            driver = LadybugDriver(db=self._database_path)
            self._graphiti = Graphiti(
                graph_driver=driver,
                llm_client=HermesAgentLLMClient(),
                embedder=embedder,
                cross_encoder=reranker,
                max_coroutines=1,
            )
            self._async.run(self._ensure_ladybug_indexes(driver), timeout=120)
            vector = self._async.run(embedder.create("graph-memory warmup"), timeout=180)
            if not vector:
                raise RuntimeError("local embedding model returned an empty vector")
        except Exception:
            self._async.close()
            self._async = None
            self._graphiti = None
            raise

        self._writes = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="graph-memory-write",
        )
        self._accept_writes = True
        self._ready = True
        logger.info(
            "graph-memory ready: database=%s embedding_model=%s ingest_turns=%s",
            self._database_path,
            self._embedding_model,
            self._ingest_turns,
        )

    @staticmethod
    def _migrate_legacy_database(legacy_path: Path, ladybug_path: Path) -> None:
        """Export a legacy Kuzu store and import it into Ladybug atomically."""
        script = Path(__file__).parent / "scripts" / "migrate_kuzu_to_ladybug.py"
        if not script.exists():
            raise RuntimeError(f"legacy migration helper is missing: {script}")
        result = subprocess.run(
            [sys.executable, str(script), "migrate", str(legacy_path), str(ladybug_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown failure").strip()
            raise RuntimeError(f"Kuzu to Ladybug migration failed: {detail}")
        logger.info(
            "graph-memory migrated legacy Kuzu database to Ladybug; legacy backup retained at %s",
            legacy_path,
        )

    @staticmethod
    async def _ensure_ladybug_indexes(driver) -> None:
        """Load Ladybug FTS and create Graphiti's indexes idempotently.

        Ladybug ships FTS as an extension instead of Kuzu's implicit autoload.
        Graphiti 0.29.3's embedded-driver index setup is also a no-op even
        though its search path requires these indexes. Keep both compatibility
        steps inside the provider boundary.
        """
        from graphiti_core.driver.driver import GraphProvider
        from graphiti_core.graph_queries import get_fulltext_indices

        # INSTALL is idempotent and ensures the extension is available on a
        # fresh host; LOAD is connection-scoped and required on every startup.
        await driver.execute_query("INSTALL FTS")
        await driver.execute_query("LOAD EXTENSION FTS")
        for query in get_fulltext_indices(GraphProvider.KUZU):
            try:
                await driver.execute_query(query)
            except RuntimeError as exc:
                if "already exists" not in str(exc).lower():
                    raise

    def _store_episode(self, body: str, source_description: str, operation_id: str):
        if self._graphiti is None or self._async is None:
            raise RuntimeError("graph-memory is not initialized")
        from graphiti_core.nodes import EpisodeType

        # group_id MUST remain None for the embedded driver (see initialize()).
        return self._async.run(
            self._graphiti.add_episode(
                name=f"{source_description}:{operation_id}",
                episode_body=body,
                source_description=source_description,
                reference_time=datetime.now(timezone.utc),
                source=EpisodeType.message,
                group_id=None,
            ),
            timeout=_WRITE_FLUSH_TIMEOUT_SECONDS,
        )

    def _track_future(
        self,
        future: concurrent.futures.Future,
        operation_id: str,
    ) -> None:
        with self._pending_lock:
            self._pending.add(future)

        def _done(completed: concurrent.futures.Future) -> None:
            with self._pending_lock:
                self._pending.discard(completed)
            try:
                completed.result()
            except Exception as exc:
                self._last_error = str(exc)
                logger.error(
                    "graph-memory write %s failed: %s",
                    operation_id,
                    exc,
                    exc_info=True,
                )

        future.add_done_callback(_done)

    def _enqueue_episode(
        self,
        body: str,
        source_description: str,
        *,
        wait: bool,
    ) -> tuple[str, Any | None]:
        if not self._ready or not self._accept_writes or self._writes is None:
            raise RuntimeError("graph-memory is not ready for writes")
        if not body or not body.strip():
            raise ValueError("memory content must not be empty")
        operation_id = uuid.uuid4().hex
        future = self._writes.submit(
            self._store_episode,
            body.strip(),
            source_description,
            operation_id,
        )
        self._track_future(future, operation_id)
        if wait:
            return operation_id, future.result(timeout=_WRITE_FLUSH_TIMEOUT_SECONDS)
        return operation_id, None

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages=None,
    ) -> None:
        del session_id, messages
        if not self._primary_context or self._ingest_turns != "all":
            return
        body = f"User: {user_content}\nAssistant: {assistant_content}".strip()
        self._enqueue_episode(body, "conversation-turn", wait=False)

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._primary_context:
            return
        metadata = metadata or {}
        old_text = str(metadata.get("old_text") or "").strip()
        if action == "add":
            event = content
        elif action == "replace":
            event = (
                f"The previous {target} statement is superseded. "
                f"Previous statement: {old_text or '(unspecified)'}. "
                f"Current statement: {content}."
            )
        elif action == "remove":
            event = (
                f"The following {target} statement is no longer valid and was removed: "
                f"{old_text or content}."
            )
        else:
            return
        self._enqueue_episode(event, f"builtin-memory-{action}", wait=False)

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        if not self._primary_context or self._ingest_turns != "all":
            return ""
        transcript = "\n".join(
            f"{message.get('role', '')}: {message.get('content', '')}"
            for message in messages or []
            if isinstance(message, dict) and message.get("content")
        )
        if transcript.strip():
            self._enqueue_episode(
                transcript[:20_000],
                "pre-compression-transcript",
                wait=False,
            )
        return ""

    def on_delegation(
        self,
        task: str,
        result: str,
        *,
        child_session_id: str = "",
        **kwargs,
    ) -> None:
        del child_session_id, kwargs
        if not self._primary_context:
            return
        self._enqueue_episode(
            f"Delegated task: {task}\nDelegation result: {result}",
            "delegation-result",
            wait=False,
        )

    def _search(self, query: str, limit: int) -> list[str]:
        if not self._ready or self._graphiti is None or self._async is None:
            return []
        if not query.strip():
            return []
        try:
            # group_ids=None is required for the embedded driver's default group.
            edges = self._async.run(
                self._graphiti.search(
                    query=query,
                    group_ids=None,
                    num_results=max(1, min(50, limit)),
                ),
                timeout=10,
            )
        except Exception as exc:
            self._last_error = str(exc)
            logger.warning("graph-memory recall failed: %s", exc)
            return []
        facts: list[str] = []
        seen: set[str] = set()
        for edge in edges or []:
            fact = str(getattr(edge, "fact", "") or "").strip()
            if fact and fact not in seen:
                facts.append(fact)
                seen.add(fact)
        return facts

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        del session_id
        facts = self._search(query, self._recall_limit)
        if not facts:
            return ""
        return "## Relevant long-term graph memory\n" + "\n".join(
            f"- {fact}" for fact in facts
        )

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "memory_query",
                "description": (
                    "Search long-term temporal graph memory for facts and relationships "
                    "between projects, people, topics, decisions, and artifacts."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "query": {"type": "string", "minLength": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memory_write",
                "description": (
                    "Store a durable fact, decision, event, or relationship in temporal "
                    "graph memory. Entities and edges are extracted automatically."
                ),
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "content": {"type": "string", "minLength": 1},
                    },
                    "required": ["content"],
                },
            },
        ]

    def handle_tool_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        **kwargs,
    ) -> str:
        del kwargs
        if tool_name == "memory_query":
            limit = int(args.get("limit") or self._recall_limit)
            facts = self._search(str(args.get("query") or ""), limit)
            return json.dumps({"success": True, "facts": facts, "count": len(facts)})
        if tool_name == "memory_write":
            try:
                operation_id, result = self._enqueue_episode(
                    str(args.get("content") or ""),
                    "explicit-memory-write",
                    wait=True,
                )
            except Exception as exc:
                return json.dumps({"success": False, "error": str(exc)})
            return json.dumps(
                {
                    "success": True,
                    "status": "stored",
                    "operation_id": operation_id,
                    "entities_created": len(getattr(result, "nodes", []) or []),
                    "relationships_created": len(getattr(result, "edges", []) or []),
                }
            )
        raise NotImplementedError(f"graph-memory does not handle {tool_name}")

    def system_prompt_block(self) -> str:
        if not self._ready:
            return ""
        return (
            "You have temporal knowledge-graph long-term memory. Relevant facts are "
            "recalled automatically. Use memory_query for explicit relationship/history "
            "questions and memory_write for durable facts and decisions."
        )

    def get_config_schema(self) -> list[dict[str, Any]]:
        return [
            {
                "key": "embedding_model",
                "description": "Local FastEmbed model",
                "required": False,
                "default": DEFAULT_EMBED_MODEL,
            },
            {
                "key": "ingest_turns",
                "description": "Automatically extract every conversation turn",
                "required": False,
                "default": "all",
                "choices": ["all", "explicit-only"],
            },
            {
                "key": "recall_limit",
                "description": "Maximum facts injected per turn",
                "required": False,
                "default": str(_DEFAULT_RECALL_LIMIT),
            },
        ]

    def save_config(self, values: dict[str, Any], hermes_home: str) -> None:
        from hermes_cli.config import load_config, save_config

        del hermes_home  # load/save_config already honor the active HERMES_HOME.
        config = load_config()
        memory = config.setdefault("memory", {})
        memory["graph-memory"] = dict(values)
        save_config(config)

    def get_status_config(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        return {
            **(provider_config or {}),
            "database": self._database_path or "not initialized",
            "ready": self._ready,
            "last_error": self._last_error or None,
        }

    def backup_paths(self) -> list[str]:
        # State is under HERMES_HOME and is already included by `hermes backup`.
        return []

    def shutdown(self) -> None:
        self._accept_writes = False
        if self._writes is not None:
            self._writes.shutdown(wait=True, cancel_futures=False)
            self._writes = None
        if self._graphiti is not None and self._async is not None:
            try:
                self._async.run(self._graphiti.close(), timeout=30)
            except Exception as exc:
                logger.debug("graph-memory close failed: %s", exc)
        if self._async is not None:
            self._async.close()
        self._async = None
        self._graphiti = None
        self._ready = False


def register(ctx) -> None:
    """Hermes user-plugin entry point."""
    ctx.register_memory_provider(GraphMemoryProvider())
