"""Validation of entry and checkpoint record shapes and types (D-EV-17, D-EV-23)."""

from typing import Any, List

REQUIRED_ENTRY_FIELDS = (
    "v", "seq", "ts_ms", "kind", "instance_id", "epoch", "grant_id",
    "invocation_hash", "prev_hash", "entry_hash", "sig",
)

REQUIRED_CP_FIELDS = (
    "v", "kind", "cp_seq", "instance_id", "epoch", "count",
    "head_hash", "ts_ms", "prev_cp_hash", "cp_hash", "sig",
)


def _is_hex64(val: Any) -> bool:
    return isinstance(val, str) and len(val) == 64 and all(c in "0123456789abcdefABCDEF" for c in val)


def _is_hex_str(val: Any) -> bool:
    return isinstance(val, str) and len(val) > 0 and len(val) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in val)


def validate_entry_shape(entry: Any, i: int) -> List[str]:
    """Validate entry fields and types without throwing uncaught exceptions (D-EV-23, D-EV-30)."""
    if not isinstance(entry, dict):
        return [f"entry {i} is not a dict: {type(entry)}"]

    errors: List[str] = []
    for req in REQUIRED_ENTRY_FIELDS:
        if req not in entry:
            errors.append(f"entry {i} missing required field: {req}")

    if "v" in entry and (type(entry["v"]) is not int or entry["v"] != 1):
        errors.append(f"entry {i} invalid version: {entry.get('v')}")
    if "seq" in entry and (type(entry["seq"]) is not int or entry["seq"] < 0):
        errors.append(f"entry {i} seq is not non-negative int: {entry.get('seq')}")
    if "ts_ms" in entry and (type(entry["ts_ms"]) is not int or entry["ts_ms"] < 0):
        errors.append(f"entry {i} ts_ms is not non-negative int: {entry.get('ts_ms')}")
    if "epoch" in entry and (type(entry["epoch"]) is not int or entry["epoch"] < 0):
        errors.append(f"entry {i} epoch is not non-negative int: {entry.get('epoch')}")

    for str_f in ("instance_id", "grant_id", "invocation_hash"):
        if str_f in entry and (not isinstance(entry[str_f], str) or not entry[str_f]):
            errors.append(f"entry {i} {str_f} is not non-empty str: {entry.get(str_f)}")

    for hash_f in ("prev_hash", "entry_hash"):
        if hash_f in entry and not _is_hex64(entry[hash_f]):
            errors.append(f"entry {i} {hash_f} is not 64-hex str: {entry.get(hash_f)}")

    if "sig" in entry and not _is_hex_str(entry["sig"]):
        errors.append(f"entry {i} sig is not valid hex str: {entry.get('sig')}")

    kind = entry.get("kind")
    if kind not in ("intent", "result"):
        errors.append(f"entry {i} invalid kind: {kind}")
    elif kind == "result":
        if "intent_seq" not in entry or type(entry["intent_seq"]) is not int or entry["intent_seq"] < 0:
            errors.append(f"result at seq {i} has invalid intent_seq: {entry.get('intent_seq')}")
        if entry.get("outcome") not in ("ok", "error", "aborted"):
            errors.append(f"result at seq {i} invalid outcome: {entry.get('outcome')}")

    return errors


def validate_checkpoint_shape(cp: Any, j: int) -> List[str]:
    """Validate checkpoint fields and types without throwing uncaught exceptions (D-EV-23, D-EV-30)."""
    if not isinstance(cp, dict):
        return [f"checkpoint {j} is not a dict: {type(cp)}"]

    errors: List[str] = []
    for req in REQUIRED_CP_FIELDS:
        if req not in cp:
            errors.append(f"checkpoint {j} missing required field: {req}")

    if "v" in cp and (type(cp["v"]) is not int or cp["v"] != 1):
        errors.append(f"checkpoint {j} invalid version: {cp.get('v')}")
    if "kind" in cp and cp["kind"] != "checkpoint":
        errors.append(f"checkpoint {j} invalid kind: {cp.get('kind')}")
    if "cp_seq" in cp and (type(cp["cp_seq"]) is not int or cp["cp_seq"] < 0):
        errors.append(f"checkpoint {j} cp_seq is not non-negative int: {cp.get('cp_seq')}")
    if "epoch" in cp and (type(cp["epoch"]) is not int or cp["epoch"] < 0):
        errors.append(f"checkpoint {j} epoch is not non-negative int: {cp.get('epoch')}")
    if "count" in cp and (type(cp["count"]) is not int or cp["count"] < 0):
        errors.append(f"checkpoint {j} count is not non-negative int: {cp.get('count')}")
    if "ts_ms" in cp and (type(cp["ts_ms"]) is not int or cp["ts_ms"] < 0):
        errors.append(f"checkpoint {j} ts_ms is not non-negative int: {cp.get('ts_ms')}")

    if "instance_id" in cp and (not isinstance(cp["instance_id"], str) or not cp["instance_id"]):
        errors.append(f"checkpoint {j} instance_id is not non-empty str: {cp.get('instance_id')}")

    for hash_f in ("head_hash", "prev_cp_hash", "cp_hash"):
        if hash_f in cp and not _is_hex64(cp[hash_f]):
            errors.append(f"checkpoint {j} {hash_f} is not 64-hex str: {cp.get(hash_f)}")

    if "sig" in cp and not _is_hex_str(cp["sig"]):
        errors.append(f"checkpoint {j} sig is not valid hex str: {cp.get('sig')}")

    return errors

