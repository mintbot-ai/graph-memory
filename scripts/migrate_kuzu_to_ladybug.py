#!/usr/bin/env python3
"""Migrate an archived Kuzu database to maintained LadybugDB.

The public entry point runs export and import in separate processes because both
packages contain native extensions. If the archived ``kuzu`` package is absent,
the exporter uses an ephemeral ``uv --with kuzu==0.11.3`` environment; it is
never added to graph-memory's runtime dependencies.
"""

from __future__ import annotations

import importlib.util
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


def _safe_cypher_path(path: Path) -> str:
    resolved = str(path.resolve())
    if "'" in resolved:
        raise ValueError("database migration paths must not contain apostrophes")
    return resolved


def _export(legacy: Path, export_dir: Path) -> None:
    import kuzu  # pyright: ignore[reportMissingImports]

    database = kuzu.Database(str(legacy))
    connection = kuzu.Connection(database)
    connection.execute(f"EXPORT DATABASE '{_safe_cypher_path(export_dir)}'")


def _import(export_dir: Path, target: Path) -> None:
    import ladybug

    database = ladybug.Database(str(target))
    connection = ladybug.Connection(database)
    connection.execute(f"IMPORT DATABASE '{_safe_cypher_path(export_dir)}'")


def _run(command: list[str], label: str, timeout_seconds: float = 600) -> None:
    popen_kwargs: dict = {}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **popen_kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
        elif os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
            stdout, stderr = process.communicate()
        else:
            process.kill()
            stdout, stderr = process.communicate()
        detail = (stderr or stdout or "no output").strip()
        raise RuntimeError(f"{label} timed out; process tree terminated: {detail}") from exc

    if process.returncode != 0:
        detail = (stderr or stdout or "unknown failure").strip()
        raise RuntimeError(f"{label} failed: {detail}")


def migrate(legacy: Path, target: Path) -> None:
    legacy = legacy.resolve()
    target = target.resolve()
    if not legacy.exists():
        raise FileNotFoundError(f"legacy Kuzu database not found: {legacy}")
    if target.exists():
        raise FileExistsError(f"Ladybug target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="graph-memory-kuzu-export-") as temp:
        export_dir = Path(temp) / "export"
        if importlib.util.find_spec("kuzu") is not None:
            export_command = [sys.executable, str(script), "_export", str(legacy), str(export_dir)]
        else:
            uv = shutil.which("uv")
            if not uv:
                raise RuntimeError(
                    "legacy migration needs uv or a temporary kuzu==0.11.3 installation"
                )
            export_command = [
                uv,
                "run",
                "--quiet",
                "--with",
                "kuzu==0.11.3",
                "python",
                str(script),
                "_export",
                str(legacy),
                str(export_dir),
            ]
        _run(export_command, "Kuzu export")

        staging = target.parent / f".{target.name}.migrating-{uuid.uuid4().hex}"
        try:
            _run(
                [sys.executable, str(script), "_import", str(export_dir), str(staging)],
                "Ladybug import",
            )
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str]) -> None:
    if len(argv) != 4:
        raise SystemExit(
            "usage: migrate_kuzu_to_ladybug.py <migrate|_export|_import> <source> <target>"
        )
    action, source, target = argv[1], Path(argv[2]), Path(argv[3])
    if action == "migrate":
        migrate(source, target)
        print(f"MIGRATION_OK: {source} -> {target}")
    elif action == "_export":
        _export(source, target)
    elif action == "_import":
        _import(source, target)
    else:
        raise SystemExit(f"unknown migration action: {action}")


if __name__ == "__main__":
    main(sys.argv)
