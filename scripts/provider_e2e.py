#!/usr/bin/env python3
"""Live E2E through the actual Hermes MemoryProvider contract.

Consumes the active provider's quota/credit. Uses a temporary HERMES_HOME for
the Kuzu database while resolving LLM credentials/model from the caller's real
active Hermes configuration.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERMES_SOURCE = Path.home() / ".hermes" / "hermes-agent"
sys.path.insert(0, str(HERMES_SOURCE))


def load_provider_module():
    name = "_graph_memory_live_provider"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    if not spec or not spec.loader:
        raise RuntimeError("could not load graph-memory provider module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    module = load_provider_module()
    temp_home = Path(tempfile.mkdtemp(prefix="graph-memory-provider-e2e-"))
    provider = module.GraphMemoryProvider()
    try:
        provider.initialize(
            "live-e2e",
            hermes_home=str(temp_home),
            platform="test",
            agent_context="primary",
            agent_identity="default",
        )
        write_result = json.loads(
            provider.handle_tool_call(
                "memory_write",
                {
                    "content": (
                        "Mart selected embedded Kuzu for the Graph Memory project. "
                        "Graph Memory is the first AXP reference implementation."
                    )
                },
            )
        )
        print("write_result=" + json.dumps(write_result, sort_keys=True))
        if not write_result.get("success"):
            raise AssertionError(f"provider write failed: {write_result}")

        query_result = json.loads(
            provider.handle_tool_call(
                "memory_query",
                {"query": "Which database does the AXP graph-memory implementation use?"},
            )
        )
        print("query_result=" + json.dumps(query_result, sort_keys=True))
        facts = query_result.get("facts") or []
        if not any("kuzu" in str(fact).lower() for fact in facts):
            raise AssertionError("provider recall did not return the Kuzu fact")
        print("PROVIDER_E2E_OK")
    finally:
        provider.shutdown()
        shutil.rmtree(temp_home, ignore_errors=True)


if __name__ == "__main__":
    main()
