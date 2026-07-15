"""Vision-output salvage parsing: small local models emit messy JSON."""

from __future__ import annotations

import json

import pytest

from vision import _as_list, _clean_text, _loads


# --- _loads --------------------------------------------------------------------

def test_loads_plain_json():
    assert _loads('{"description": "a dog"}') == {"description": "a dog"}


def test_loads_fenced_json():
    text = '```json\n{"description": "a dog", "tags": ["dog"]}\n```'
    assert _loads(text)["description"] == "a dog"


def test_loads_json_wrapped_in_prose():
    text = 'Sure! Here is the JSON you asked for: {"description": "a cat"} Hope that helps!'
    assert _loads(text) == {"description": "a cat"}


def test_loads_truncated_output_salvages_description():
    # Model ran past max_tokens mid-object: unparseable, but the description
    # must still be recovered so the photo scans instead of failing.
    text = '{"description": "a sandy beach at golden hour with sand and'
    out = _loads(text)
    assert out["description"].startswith("a sandy beach at golden hour")


def test_loads_runaway_description_is_capped():
    text = '{"description": "' + "very long " * 200
    assert len(_loads(text)["description"]) <= 600


def test_loads_garbage_raises():
    with pytest.raises(json.JSONDecodeError):
        _loads("no json here at all")


# --- _clean_text ------------------------------------------------------------------

def test_clean_text_strips_placeholder_echoes():
    assert _clean_text("[Description] [Location] [Objects]") == ""
    assert _clean_text("A cat sits here. [Tags]") == "A cat sits here"


def test_clean_text_normalizes_whitespace_and_punctuation():
    assert _clean_text("  a   dog ,") == "a dog"


def test_clean_text_leaves_real_text_alone():
    assert _clean_text("A red kayak on a lake") == "A red kayak on a lake"


# --- _as_list ------------------------------------------------------------------

def test_as_list_passes_lists_through():
    assert _as_list(["a", " b ", ""]) == ["a", "b"]


def test_as_list_splits_comma_joined_strings():
    # Small models return "dog, cat" instead of ["dog", "cat"]; without the
    # split a downstream `for x in value` iterates characters.
    assert _as_list("dog, cat") == ["dog", "cat"]


def test_as_list_rejects_non_listish_values():
    assert _as_list(None) == []
    assert _as_list(42) == []
