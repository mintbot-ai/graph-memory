from __future__ import annotations

import concurrent.futures
import json
from types import SimpleNamespace


def _ready_provider(plugin_module):
    provider = plugin_module.GraphMemoryProvider()
    provider._ready = True
    provider._accept_writes = True
    provider._writes = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    return provider


def test_background_turn_writes_are_serialized_and_drained(plugin_module):
    provider = _ready_provider(plugin_module)
    seen = []

    def store(body, source, operation_id):
        seen.append((body, source, operation_id))
        return SimpleNamespace(nodes=[], edges=[])

    provider._store_episode = store
    provider.sync_turn("first", "answer one")
    provider.sync_turn("second", "answer two")
    provider.shutdown()

    assert [item[0] for item in seen] == [
        "User: first\nAssistant: answer one",
        "User: second\nAssistant: answer two",
    ]


def test_explicit_write_waits_for_confirmed_storage(plugin_module):
    provider = _ready_provider(plugin_module)

    def store(body, source, operation_id):
        assert body == "Graph Memory implements AXP."
        assert source == "explicit-memory-write"
        return SimpleNamespace(nodes=[1, 2], edges=[1])

    provider._store_episode = store
    result = json.loads(
        provider.handle_tool_call(
            "memory_write",
            {"content": "Graph Memory implements AXP."},
        )
    )
    provider.shutdown()

    assert result["success"] is True
    assert result["entities_created"] == 2
    assert result["relationships_created"] == 1
    assert result["operation_id"]


def test_explicit_write_reports_not_ready(plugin_module):
    provider = plugin_module.GraphMemoryProvider()
    result = json.loads(provider.handle_tool_call("memory_write", {"content": "x"}))
    assert result["success"] is False
    assert "not ready" in result["error"]


def test_non_primary_context_does_not_ingest(plugin_module):
    provider = _ready_provider(plugin_module)
    provider._primary_context = False
    seen = []
    provider._store_episode = lambda *args: seen.append(args)

    provider.sync_turn("cron prompt", "cron answer")
    provider.on_memory_write("add", "memory", "should not land")
    provider.on_delegation("task", "result")
    provider.shutdown()

    assert seen == []


def test_replace_and_remove_become_temporal_events(plugin_module):
    provider = _ready_provider(plugin_module)
    seen = []

    def store(body, source, operation_id):
        seen.append((body, source))
        return SimpleNamespace(nodes=[], edges=[])

    provider._store_episode = store
    provider.on_memory_write(
        "replace",
        "memory",
        "Kuzu is selected.",
        {"old_text": "FalkorDB is selected."},
    )
    provider.on_memory_write(
        "remove",
        "memory",
        "",
        {"old_text": "The old deployment rule."},
    )
    provider.shutdown()

    assert "superseded" in seen[0][0]
    assert "FalkorDB" in seen[0][0]
    assert "Kuzu" in seen[0][0]
    assert "no longer valid" in seen[1][0]
    assert "old deployment rule" in seen[1][0]


def test_graphiti_calls_never_use_custom_group_ids(plugin_module):
    provider = plugin_module.GraphMemoryProvider()
    captured = {}

    class FakeGraphiti:
        async def add_episode(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(nodes=[], edges=[])

    provider._graphiti = FakeGraphiti()
    provider._async = plugin_module._AsyncLoop()
    try:
        provider._store_episode("fact", "test", "op")
    finally:
        provider._async.close()

    assert captured["group_id"] is None
