"""Chain entry verification logic for full and incremental passes (D-EV-7, 15, 22)."""

import hashlib
from typing import Set, Tuple

try:
    from .canonical import canonical
    from .shapes import validate_entry_shape
    from .signing import OpensslVerifier
except ImportError:
    from canonical import canonical
    from shapes import validate_entry_shape
    from signing import OpensslVerifier

ENTRY_PREFIX: bytes = b"narwhal-evidence-entry-v1\0"


def verify_entry_integrity(
    entry: dict,
    expected_seq: int,
    expected_instance_id: str,
    last_epoch: int,
    prev_h: str,
    verifier: OpensslVerifier,
) -> Tuple[str, int]:
    """Verify cryptographic, sequential, and structural integrity of an entry.

    Returns (entry_hash, epoch). Raises ChainIntegrityError on any violation.
    """
    try:
        from .chain import ChainIntegrityError
    except ImportError:
        from chain import ChainIntegrityError

    shape_errs = validate_entry_shape(entry, expected_seq)
    if shape_errs:
        raise ChainIntegrityError(f"shape error at {expected_seq}: {shape_errs[0]}")
    if entry.get("seq") != expected_seq:
        raise ChainIntegrityError(f"seq mismatch at {expected_seq}: {entry.get('seq')}")
    if entry.get("instance_id") != expected_instance_id:
        raise ChainIntegrityError(f"instance_id mismatch at {expected_seq}")
    ep = entry.get("epoch", 0)
    if ep < last_epoch:
        raise ChainIntegrityError(f"epoch decreased at {expected_seq}")
    if entry.get("prev_hash") != prev_h:
        raise ChainIntegrityError(f"prev_hash mismatch at {expected_seq}")
    body = {k: v for k, v in entry.items() if k not in ("entry_hash", "sig")}
    calc_hash = hashlib.sha256(ENTRY_PREFIX + canonical(body)).hexdigest()
    if calc_hash != entry.get("entry_hash"):
        raise ChainIntegrityError(f"entry_hash mismatch at {expected_seq}")
    try:
        sig_bytes = bytes.fromhex(entry.get("sig", ""))
    except ValueError:
        raise ChainIntegrityError(f"invalid sig hex at {expected_seq}")
    if not verifier.verify(ENTRY_PREFIX + calc_hash.encode("ascii"), sig_bytes):
        raise ChainIntegrityError(f"signature verification failed at {expected_seq}")

    return calc_hash, ep


def process_entry_kind(
    entry: dict,
    seq: int,
    consumed_grants: Set[str],
    intents_by_seq: dict,
    results_by_intent: dict,
) -> None:
    """Validate and record intent/result kind semantics. Raises on double-spend or invalid result."""
    try:
        from .chain import ChainIntegrityError, DoubleSpendError
    except ImportError:
        from chain import ChainIntegrityError, DoubleSpendError

    kind = entry.get("kind")
    if kind == "intent":
        gid = entry.get("grant_id")
        if gid in consumed_grants:
            raise DoubleSpendError(f"double spend in chain: {gid}")
        consumed_grants.add(gid)
        intents_by_seq[seq] = entry
    elif kind == "result":
        iseq = entry.get("intent_seq")
        if iseq not in intents_by_seq or iseq in results_by_intent:
            raise ChainIntegrityError(f"invalid result intent_seq at {seq}")
        intent = intents_by_seq[iseq]
        if intent.get("grant_id") != entry.get("grant_id"):
            raise ChainIntegrityError(f"result at seq {seq} grant_id mismatch with intent {iseq}")
        if intent.get("invocation_hash") != entry.get("invocation_hash"):
            raise ChainIntegrityError(f"result at seq {seq} invocation_hash mismatch with intent {iseq}")
        results_by_intent[iseq] = entry

