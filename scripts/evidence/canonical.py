"""Canonical JSON serialization and strict line parsing (D-EV-3, D-EV-17)."""

import json
from typing import Any, Dict, List, Tuple


class CanonicalError(ValueError):
    """Raised when data violates canonical serialization or strict formatting."""


def validate_canonical_types(obj: Any) -> None:
    """Recursively reject floats, NaN, and non-string dict keys (D-EV-3)."""
    if isinstance(obj, float):
        raise CanonicalError(f"floats/NaN are not permitted in canonical JSON: {obj}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise CanonicalError(f"dict keys must be str, got {type(k)}")
            validate_canonical_types(v)
    elif isinstance(obj, list):
        for item in obj:
            validate_canonical_types(item)
    elif isinstance(obj, (str, int, bool)) or obj is None:
        pass
    else:
        raise CanonicalError(f"unsupported type in canonical JSON: {type(obj)}")


def canonical(obj: Any) -> bytes:
    """Canonical JSON encoding: sorted keys, compact separators, UTF-8 (D-EV-3)."""
    validate_canonical_types(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: List[Tuple[Any, Any]]) -> Dict[str, Any]:
    """object_pairs_hook that rejects duplicate dictionary keys (D-EV-17)."""
    d: Dict[str, Any] = {}
    for k, v in pairs:
        if not isinstance(k, str):
            raise CanonicalError(f"dict keys must be str, got {type(k)}")
        if k in d:
            raise CanonicalError(f"duplicate key in JSON object: {k}")
        d[k] = v
    return d


def parse_strict_json_line(raw_line: bytes, line_no: int) -> dict:
    """Strictly parse a single JSON line against canonical rules (D-EV-17).

    Enforces:
    1. Line must end with exactly '\\n'.
    2. JSON parsing rejects duplicate keys.
    3. Root element must be a dict.
    4. Canonical types must be respected (no floats/NaN).
    5. Raw line bytes must exactly equal canonical(rec) + b'\\n'.
    """
    if not raw_line.endswith(b"\n"):
        raise CanonicalError(f"line {line_no} does not end with newline")
    try:
        text = raw_line.decode("utf-8")
    except UnicodeDecodeError as e:
        raise CanonicalError(f"line {line_no} is not valid UTF-8: {e}")

    # Reject empty or blank lines
    if text.strip() == "":
        raise CanonicalError(f"line {line_no} is empty/blank; not canonical")

    try:
        rec = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except Exception as e:
        raise CanonicalError(f"line {line_no} unparseable JSON: {e}")

    if not isinstance(rec, dict):
        raise CanonicalError(f"line {line_no} record is not a dict: {type(rec)}")

    validate_canonical_types(rec)

    expected = canonical(rec) + b"\n"
    if raw_line != expected:
        raise CanonicalError(
            f"line {line_no} raw bytes do not match canonical representation exactly"
        )
    return rec
