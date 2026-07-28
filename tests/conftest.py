from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
HERMES_SOURCE = Path(
    os.environ.get("HERMES_SOURCE", str(Path.home() / ".hermes" / "hermes-agent"))
)
if HERMES_SOURCE.exists():
    sys.path.insert(0, str(HERMES_SOURCE))


@pytest.fixture(scope="session")
def plugin_module():
    if not HERMES_SOURCE.exists():
        pytest.skip("Hermes source is required for MemoryProvider integration tests")
    name = "_graph_memory_test_plugin"
    spec = importlib.util.spec_from_file_location(
        name,
        REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(REPO_ROOT)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
