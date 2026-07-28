from __future__ import annotations

import json

import pytest

from hermes_llm import _extract_json_object


def test_extract_json_object_accepts_plain_json():
    assert _extract_json_object('{"ok": true}') == {"ok": True}


def test_extract_json_object_accepts_fence_and_short_prefix():
    assert _extract_json_object('Result:\n```json\n{"value": 3}\n```') == {"value": 3}


def test_extract_json_object_rejects_non_json():
    with pytest.raises(json.JSONDecodeError):
        _extract_json_object("there is no object here")
