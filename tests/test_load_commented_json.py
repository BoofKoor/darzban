"""Tests for `app.utils.jsonc.load_commented_json`.

This parser replaced `commentjson==0.9.0`, which raised `RecursionError`
(lark recurses on parse-tree depth, and on some Python/lark builds while
backtracking over very long string terminals) and took the panel down on the
boot path and the config PUT handler.

The contract: every input commentjson accepted must parse to the *same*
value here, and nothing must ever raise `RecursionError`.
"""

import json

import pytest

from app.utils.jsonc import load_commented_json


# --- Primary regression: the production crash class -------------------------

def test_long_string_value_parses():
    """A ~2400-char string value (REALITY mldsa65Verify shape) must parse —
    this is the documented production crash for commentjson/lark."""
    verify = "A" * 2400
    result = load_commented_json('{"mldsa65Verify":"%s"}' % verify)
    assert result["mldsa65Verify"] == verify


def test_deeply_nested_structure_parses():
    """commentjson raised RecursionError at nesting depth ~300; stdlib json
    (and therefore this parser) handles it."""
    depth = 400
    text = '{"a":' * depth + "1" + "}" * depth
    result = load_commented_json(text)
    node = result
    for _ in range(depth):
        node = node["a"]
    assert node == 1


# --- Comments parse identically to the comment-free equivalent --------------

@pytest.mark.parametrize("text,expected", [
    ('{"a":1 // line comment\n,"b":2}', {"a": 1, "b": 2}),
    ('{"a":1 # hash comment\n,"b":2}', {"a": 1, "b": 2}),
    ('{\n  // leading\n  "a":1\n}', {"a": 1}),
    ('{"a":/* block */ 1}', {"a": 1}),
    ('{"a":1 /* multi\nline\nblock */}', {"a": 1}),
    ('[1, 2 /* c */, 3]', [1, 2, 3]),
])
def test_comments_are_stripped(text, expected):
    assert load_commented_json(text) == expected


# --- String-literal safety: comment/comma syntax inside strings preserved ---

@pytest.mark.parametrize("text,expected", [
    ('{"a":"ab//cd"}', {"a": "ab//cd"}),
    ('{"a":"x#y"}', {"a": "x#y"}),
    ('{"u":"https://example.com/p"}', {"u": "https://example.com/p"}),
    ('{"a":"x // y /* z */ #w"}', {"a": "x // y /* z */ #w"}),
    (r'{"a":"x\"//no","b":1 //yes' + "\n}", {"a": 'x"//no', "b": 1}),
    ('{"a":"trailing,comma,inside,"}', {"a": "trailing,comma,inside,"}),
])
def test_string_contents_preserved(text, expected):
    assert load_commented_json(text) == expected


# --- Trailing commas (commentjson supported these) --------------------------

@pytest.mark.parametrize("text,expected", [
    ('{"a":1,}', {"a": 1}),
    ('[1,2,]', [1, 2]),
    ('{"a":[1,2,],"b":{"c":3,},}', {"a": [1, 2], "b": {"c": 3}}),
    ('[1, // c\n]', [1]),
    ('{"a":1,\n}', {"a": 1}),
])
def test_trailing_commas_are_stripped(text, expected):
    assert load_commented_json(text) == expected


# --- Failure mode is a clean JSONDecodeError, never RecursionError ----------

def test_invalid_after_stripping_raises_jsondecodeerror():
    with pytest.raises(json.JSONDecodeError):
        load_commented_json('{"a": }')


def test_filename_like_string_is_not_valid_json():
    """XRayConfig relies on a path string raising so it falls through to
    opening the file. A path with a `//` must NOT silently parse."""
    with pytest.raises(json.JSONDecodeError):
        load_commented_json("./configs//xray_config.json")


# --- Always-run divergence assertions (pin the decided contract; no dep) ----
# These encode the decisions from the one-time parity run so the contract
# keeps being enforced after commentjson is removed from the environment.

def test_divergence_contract():
    # commentjson accepted trailing commas -> we must too (same result).
    assert load_commented_json('{"a":1,}') == {"a": 1}
    assert load_commented_json('[1,2,]') == [1, 2]
    # base64-ish string value with // is data, not a comment.
    assert load_commented_json('{"v":"ab//cd"}') == {"v": "ab//cd"}
    # commentjson rejected these; stdlib accepts -> benign superset.
    assert load_commented_json('{"a":NaN}')["a"] != load_commented_json('{"a":NaN}')["a"]  # NaN
    assert load_commented_json('{"a":Infinity}')["a"] == float("inf")
    assert load_commented_json('{"a":/* c */1}') == {"a": 1}  # block comment
    # commentjson rejected these and so do we (strict JSON otherwise).
    with pytest.raises(json.JSONDecodeError):
        load_commented_json("{'a':1}")  # single-quoted
    with pytest.raises(json.JSONDecodeError):
        load_commented_json("{a:1}")  # unquoted key


# --- Parity cross-check vs the real commentjson (skipped in CI) --------------
# Runs only where commentjson==0.9.0 is installed. For every input commentjson
# accepts, this parser must produce the identical value. Run once pre-merge.

_PARITY_BATTERY = [
    '{"a":1,"b":[1,2,3],"c":"x"}',
    '{"a":1 // c\n,"b":2}',
    '{"a":1 # c\n,"b":2}',
    '{"a":1,}',
    '[1,2,]',
    '{"a":[1,2,],"b":{"c":3,},}',
    '[1, // x\n]',
    '{"a":"ab//cd"}',
    '{"mldsa65Verify":"ab#cd"}',
    '{"a":"x // y /* z */ #w"}',
    r'{"a":"x\"//no","b":1 //yes' + "\n}",
    '{"u":"https://example.com/path"}',
    '{"mldsa65Verify":"%s"}' % ("A" * 2400),
]


def test_parity_with_commentjson():
    commentjson = pytest.importorskip("commentjson")
    for text in _PARITY_BATTERY:
        assert load_commented_json(text) == commentjson.loads(text), text
