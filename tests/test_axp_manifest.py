from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
AXP_SCHEMA = ROOT.parent / "agent-extension" / "schema" / "agent-extension.schema.json"


def test_axp_manifest_validates_against_protocol_schema():
    if not AXP_SCHEMA.exists():
        raise AssertionError(f"AXP schema not found at {AXP_SCHEMA}")
    schema = json.loads(AXP_SCHEMA.read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "agent-extension.json").read_text(encoding="utf-8"))
    jsonschema.validate(manifest, schema)


def test_every_manifest_reference_exists():
    manifest = json.loads((ROOT / "agent-extension.json").read_text(encoding="utf-8"))
    refs = []
    for tool in manifest["provides"]["tools"]:
        refs.append(tool["input_schema_ref"])
    for memory in manifest["provides"]["memory"]:
        refs.append(memory["schema_ref"])
    refs.append(manifest["provides"]["config"]["schema_ref"])
    for target in manifest["targets"]:
        refs.extend(target["lifecycle"].values())

    missing = [ref for ref in refs if not (ROOT / ref).exists()]
    assert missing == []


def test_ladybug_extension_declares_no_external_database_service():
    manifest = json.loads((ROOT / "agent-extension.json").read_text(encoding="utf-8"))
    assert "services" not in manifest["provides"]
    assert manifest["targets"][0]["component_map"]["memory"]["driver"] == "ladybug"
    assert manifest["targets"][0]["component_map"]["memory"]["compatibility_family"] == "kuzu"


def test_runtime_depends_on_maintained_ladybug_not_archived_kuzu():
    plugin = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    dependencies = plugin["pip_dependencies"]
    assert "ladybug==0.18.3" in dependencies
    assert not any(dependency.startswith("kuzu") for dependency in dependencies)
