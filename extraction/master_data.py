"""Decode Safco's JSON-string-valued ``window.masterData`` assignment."""

from __future__ import annotations

import json
import re
from typing import Any


_ASSIGNMENT = re.compile(r"\bwindow\.masterData\s*=", re.ASCII)


class MasterDataDecodeError(ValueError):
    """The assignment existed but was not the expected twice-encoded mapping."""


def decode_master_data(html: str) -> dict[str, dict[str, Any]] | None:
    """Perform exactly two JSON decodes after the JavaScript assignment.

    Safco emits an outer JSON string literal whose decoded value is the inner
    JSON object. ``raw_decode`` is used so trailing JavaScript cannot be consumed
    accidentally.

    Three outcomes are distinguished, because callers must treat them
    differently:

    * ``None`` - no assignment on the page at all, so nothing can be concluded.
    * ``{}`` - the page states this family has no child items. PHP serializes an
      empty associative array as ``[]`` rather than ``{}``, so both encodings map
      here. This is a complete answer, not a failure.
    * a populated mapping - the child items keyed by SKU.

    Anything else raises, so a genuinely unreadable payload stays visible instead
    of being silently downgraded to "no variants".
    """

    match = _ASSIGNMENT.search(html)
    if not match:
        return None
    source = html[match.end() :].lstrip()
    try:
        encoded, _ = json.JSONDecoder().raw_decode(source)
    except json.JSONDecodeError as exc:
        raise MasterDataDecodeError(f"invalid outer masterData JSON string: {exc.msg}") from exc
    if not isinstance(encoded, str):
        raise MasterDataDecodeError("masterData outer value must be a JSON string")
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise MasterDataDecodeError(f"invalid inner masterData JSON object: {exc.msg}") from exc
    if isinstance(decoded, list):
        if decoded:
            raise MasterDataDecodeError(
                "masterData inner value must be an object or an empty array"
            )
        return {}
    if not isinstance(decoded, dict):
        raise MasterDataDecodeError("masterData inner value must be an object")
    invalid = [key for key, value in decoded.items() if not isinstance(key, str) or not isinstance(value, dict)]
    if invalid:
        raise MasterDataDecodeError("masterData variants must be keyed objects")
    return decoded
