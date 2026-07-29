from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_ladybug_driver_does_not_leave_global_kuzu_alias():
    import ladybug_driver  # noqa: F401

    module = sys.modules.get("kuzu")
    assert module is None or getattr(module, "__name__", "") != "ladybug"


def test_ladybug_driver_rejects_preloaded_archived_kuzu_module():
    code = f"""
import sys, types
sys.path.insert(0, {str(ROOT)!r})
sys.modules['kuzu'] = types.ModuleType('kuzu')
try:
    import ladybug_driver
except RuntimeError as exc:
    assert 'archived kuzu native module is already loaded' in str(exc)
else:
    raise AssertionError('driver accepted a preloaded archived Kuzu module')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression test")
def test_migration_timeout_terminates_descendant_process_tree(tmp_path):
    from scripts.migrate_kuzu_to_ladybug import _run

    marker = tmp_path / "orphan-survived"
    child_code = (
        "import pathlib,time; time.sleep(1); "
        f"pathlib.Path({str(marker)!r}).write_text('bad')"
    )
    parent_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(60)"
    )

    with pytest.raises(RuntimeError, match="process tree terminated"):
        _run([sys.executable, "-c", parent_code], "timeout-test", timeout_seconds=0.1)

    time.sleep(1.2)
    assert not marker.exists()
