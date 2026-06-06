"""Crash-safe JSON-with-comments loader.

Replaces ``commentjson==0.9.0`` (lark-based) for parsing the Xray config.
commentjson recurses on parse-tree depth (its ``_remove_trailing_commas``
walk and lark's ``Reconstructor``) and, on some Python/lark builds, while
backtracking over very long string terminals — both raise ``RecursionError``
and take the panel down on the boot path (``_initialize`` -> ``XRayConfig``)
and on the config PUT handler.

This module is immune by construction: the fast path is stdlib ``json``
(C scanner, iterative over string length), and the only fallback is a
single-pass, string-literal-aware comment/trailing-comma stripper followed by
another stdlib ``json.loads``. No recursive descent over the input.

Compatibility contract — every input ``commentjson.loads`` accepted must parse
to the *same* value here:
  * ``//`` and ``#`` line comments       (commentjson-supported)
  * trailing commas in objects and arrays (commentjson-supported)
We additionally tolerate ``/* ... */`` block comments — commentjson rejects
those, so accepting them only widens the accepted set (it never changes the
result of a config that already loaded) and matches Xray's own Go config
loader. Comment/comma syntax inside string values is preserved verbatim.
"""

import json

__all__ = ["load_commented_json"]


def _strip_comments(text: str) -> str:
    """Remove ``//``, ``#`` line comments and ``/* */`` block comments.

    String literals (and their backslash escapes) are preserved verbatim, so
    a ``//`` / ``#`` / ``/*`` appearing inside a JSON string value is never
    treated as a comment.
    """
    out = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]

        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                # Escape sequence: copy the escaped char untouched.
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        # `//` or `#` line comment -> drop through end of line (keep newline).
        if (ch == "/" and i + 1 < n and text[i + 1] == "/") or ch == "#":
            i += 2 if ch == "/" else 1
            while i < n and text[i] != "\n":
                i += 1
            continue

        # `/* ... */` block comment -> drop through the closing `*/`.
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2  # consume the closing */
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """Drop a ``,`` that is followed only by whitespace before a ``}``/``]``.

    Run after :func:`_strip_comments`, so "whitespace before the closer" has
    already had any interleaved comments removed. String-literal aware.
    """
    out = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]

        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # trailing comma: drop it
                continue

        out.append(ch)
        i += 1

    return "".join(out)


def load_commented_json(text: str):
    """Parse JSON text that may contain comments and trailing commas.

    Mirrors ``commentjson.loads`` for every input commentjson accepted, but
    cannot raise ``RecursionError``. Raises ``json.JSONDecodeError`` on input
    that is invalid even after stripping (callers in ``app/xray/config.py``
    rely on this to distinguish a JSON string from a file path, exactly as the
    old ``commentjson.loads`` did).
    """
    try:
        # Fast path: byte-identical for every comment/comma-free input, which
        # is 100% of panel-written files (json.dumps) and the shipped config.
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = _strip_trailing_commas(_strip_comments(text))
        return json.loads(cleaned)
