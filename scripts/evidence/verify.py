"""Evidence chain strict verification (D-EV-1, D-EV-6, 11, 12, 17, 20, 23, 25)."""

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import List, Optional, Union

try:
    from .canonical import CanonicalError, canonical
    from .chain import ENTRY_PREFIX, GENESIS_PREV_HASH, read_chain
    from .checkpoint import CHECKPOINT_PREFIX, read_checkpoints
    from .shapes import validate_checkpoint_shape, validate_entry_shape
    from .signing import OpensslVerifier, keys_are_identical
except ImportError:
    from canonical import CanonicalError, canonical
    from chain import ENTRY_PREFIX, GENESIS_PREV_HASH, read_chain
    from checkpoint import CHECKPOINT_PREFIX, read_checkpoints
    from shapes import validate_checkpoint_shape, validate_entry_shape
    from signing import OpensslVerifier, keys_are_identical


@dataclass
class Report:
    """Verification results report."""

    errors: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    anchored_count: int = 0
    unanchored_tail: int = 0

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def verify(
    entries: Union[List[dict], str, Path],
    entry_pub: Union[str, Path, OpensslVerifier],
    checkpoints: Optional[Union[List[dict], str, Path]] = None,
    cp_pub: Optional[Union[str, Path, OpensslVerifier]] = None,
    allow_no_checkpoints: bool = False,
    no_checkpoints: Optional[bool] = None,
) -> Report:
    """Strictly verify evidence chain against entry and checkpoint keys (D-EV-6, 11, 12, 17, 20, 23, 25)."""
    if entry_pub is None:
        raise ValueError("entry_pub is mandatory; cannot verify without entry public key")
    if cp_pub is None:
        raise ValueError("cp_pub is mandatory; cannot verify without checkpoint public key")

    if no_checkpoints is not None:
        allow_no_checkpoints = allow_no_checkpoints or no_checkpoints

    report = Report()

    # D-EV-20: Enforce checkpoints XOR allow_no_checkpoints
    has_checkpoints = checkpoints is not None
    if has_checkpoints and allow_no_checkpoints:
        report.errors.append("cannot specify both checkpoints and allow_no_checkpoints (D-EV-20)")
        return report
    if not has_checkpoints and not allow_no_checkpoints:
        report.errors.append("must specify either checkpoints or allow_no_checkpoints=True (D-EV-20)")
        return report

    # D-EV-25: Fail-closed key separation check
    try:
        if keys_are_identical(entry_pub, cp_pub):
            report.errors.append("key separation failure: entry_pub and cp_pub are identical")
    except Exception as e:
        report.errors.append(f"key separation check error: {e}")

    try:
        entry_v = entry_pub if isinstance(entry_pub, OpensslVerifier) else OpensslVerifier(entry_pub)
        cp_v = cp_pub if isinstance(cp_pub, OpensslVerifier) else OpensslVerifier(cp_pub)
    except Exception as e:
        report.errors.append(f"failed to load public key: {e}")
        return report

    if isinstance(entries, (str, Path)):
        try:
            entry_list = read_chain(entries)
        except Exception as e:
            report.errors.append(f"failed to read chain: {e}")
            return report
    elif isinstance(entries, list):
        entry_list = entries
    else:
        report.errors.append(f"entries must be a list or path, got {type(entries)}")
        return report

    cp_list: List[dict] = []
    if has_checkpoints:
        if isinstance(checkpoints, (str, Path)):
            try:
                cp_list = read_checkpoints(checkpoints)
            except Exception as e:
                report.errors.append(f"failed to read checkpoints: {e}")
                return report
        elif isinstance(checkpoints, list):
            cp_list = checkpoints
        else:
            report.errors.append(f"checkpoints must be a list or path, got {type(checkpoints)}")
            return report

        # D-EV-20: Treat explicitly supplied but empty checkpoint list/file as error
        if len(cp_list) == 0:
            report.errors.append("checkpoint list is empty: empty checkpoints not permitted without allow_no_checkpoints (D-EV-20)")
            return report

    expected_instance_id = None
    prev_h = GENESIS_PREV_HASH
    last_epoch = -1
    intents_by_seq: dict[int, dict] = {}
    intents_by_grant: dict[str, int] = {}
    results_by_intent: dict[int, int] = {}

    for i, entry in enumerate(entry_list):
        shape_errors = validate_entry_shape(entry, i)
        if shape_errors:
            report.errors.extend(shape_errors)
            continue

        if expected_instance_id is None:
            expected_instance_id = entry.get("instance_id")
        elif entry.get("instance_id") != expected_instance_id:
            report.errors.append(f"entry {i} instance_id mismatch: {entry.get('instance_id')} != {expected_instance_id}")

        if entry.get("seq") != i:
            report.errors.append(f"entry {i} seq error: expected {i}, got {entry.get('seq')}")

        ep = entry.get("epoch", 0)
        if ep < last_epoch:
            report.errors.append(f"entry {i} epoch decreased from {last_epoch} to {ep}")
        last_epoch = ep

        if entry.get("prev_hash") != prev_h:
            report.errors.append(f"entry {i} prev_hash mismatch: expected {prev_h}, got {entry.get('prev_hash')}")

        body = {k: v for k, v in entry.items() if k not in ("entry_hash", "sig")}
        calc_hash = None
        try:
            calc_hash = hashlib.sha256(ENTRY_PREFIX + canonical(body)).hexdigest()
        except Exception as e:
            report.errors.append(f"entry {i} canonical format error: {e}")

        if calc_hash is not None:
            if calc_hash != entry.get("entry_hash"):
                report.errors.append(f"entry {i} entry_hash mismatch: expected {calc_hash}, got {entry.get('entry_hash')}")
            try:
                sig_bytes = bytes.fromhex(entry.get("sig", ""))
                msg = ENTRY_PREFIX + calc_hash.encode("ascii")
                if not entry_v.verify(msg, sig_bytes):
                    report.errors.append(f"entry {i} signature verification failed")
            except Exception as e:
                report.errors.append(f"entry {i} invalid signature format: {e}")
            prev_h = entry.get("entry_hash")

        kind = entry.get("kind")
        if kind == "intent":
            gid = entry.get("grant_id")
            if gid in intents_by_grant:
                report.errors.append(f"double spend: grant_id '{gid}' reused at seq {i}")
            else:
                intents_by_grant[gid] = i
            intents_by_seq[i] = entry
        elif kind == "result":
            iseq = entry.get("intent_seq")
            if iseq is not None and isinstance(iseq, int):
                if iseq >= i or iseq < 0:
                    report.errors.append(f"result at seq {i} points to forward/invalid intent_seq {iseq}")
                elif iseq not in intents_by_seq:
                    report.errors.append(f"result at seq {i} references non-intent record {iseq}")
                else:
                    intent = intents_by_seq[iseq]
                    if intent.get("grant_id") != entry.get("grant_id"):
                        report.errors.append(f"result at seq {i} grant_id mismatch with intent {iseq}")
                    if intent.get("invocation_hash") != entry.get("invocation_hash"):
                        report.errors.append(f"result at seq {i} invocation_hash mismatch with intent {iseq}")
                if iseq in results_by_intent:
                    report.errors.append(f"duplicate result for intent_seq {iseq} at seq {i}")
                else:
                    results_by_intent[iseq] = i

    if has_checkpoints and cp_list:
        prev_cp_h = GENESIS_PREV_HASH
        last_cp_epoch = -1
        last_cp_count = -1
        for j, cp in enumerate(cp_list):
            shape_errors = validate_checkpoint_shape(cp, j)
            if shape_errors:
                report.errors.extend(shape_errors)
                continue

            if cp.get("cp_seq") != j:
                report.errors.append(f"checkpoint {j} cp_seq error: expected {j}, got {cp.get('cp_seq')}")

            cp_inst = cp.get("instance_id")
            if expected_instance_id and cp_inst != expected_instance_id:
                report.errors.append(f"checkpoint {j} instance_id mismatch: {cp_inst} != {expected_instance_id}")

            c_epoch = cp.get("epoch")
            if type(c_epoch) is int and c_epoch >= 0:
                if c_epoch < last_cp_epoch:
                    report.errors.append(f"checkpoint {j} epoch decreased: {c_epoch} < {last_cp_epoch}")
                last_cp_epoch = c_epoch

            c_count = cp.get("count")
            if type(c_count) is int and c_count >= 0:
                if c_count < last_cp_count:
                    report.errors.append(f"checkpoint {j} count decreased: {c_count} < {last_cp_count}")
                last_cp_count = c_count

            if cp.get("prev_cp_hash") != prev_cp_h:
                report.errors.append(f"checkpoint {j} prev_cp_hash mismatch: expected {prev_cp_h}, got {cp.get('prev_cp_hash')}")

            cp_body = {k: v for k, v in cp.items() if k not in ("cp_hash", "sig")}
            calc_cp_hash = None
            try:
                calc_cp_hash = hashlib.sha256(CHECKPOINT_PREFIX + canonical(cp_body)).hexdigest()
            except Exception as e:
                report.errors.append(f"checkpoint {j} canonical format error: {e}")

            if calc_cp_hash is not None:
                if calc_cp_hash != cp.get("cp_hash"):
                    report.errors.append(f"checkpoint {j} cp_hash mismatch")
                try:
                    cp_sig_bytes = bytes.fromhex(cp.get("sig", ""))
                    msg = CHECKPOINT_PREFIX + calc_cp_hash.encode("ascii")
                    if not cp_v.verify(msg, cp_sig_bytes):
                        report.errors.append(f"checkpoint {j} signature verification failed")
                except Exception as e:
                    report.errors.append(f"checkpoint {j} invalid signature format: {e}")
                prev_cp_h = cp.get("cp_hash")

            # D-EV-12, D-EV-41: check EVERY checkpoint's head against the chain
            if type(c_count) is int and c_count >= 0:
                if c_count == 0:
                    if cp.get("head_hash") != GENESIS_PREV_HASH:
                        report.errors.append(f"checkpoint {j} count=0 must have head_hash == GENESIS: got {cp.get('head_hash')}")
                elif c_count > len(entry_list):
                    report.errors.append(f"checkpoint {j} count {c_count} > entries length {len(entry_list)} (tail truncation/rollback)")
                else:
                    target_entry = entry_list[c_count - 1]
                    if isinstance(target_entry, dict):
                        target_hash = target_entry.get("entry_hash")
                        if target_hash != cp.get("head_hash"):
                            report.errors.append(f"checkpoint {j} head_hash mismatch: expected {cp.get('head_hash')}, got {target_hash}")
                        if cp_inst and target_entry.get("instance_id") != cp_inst:
                            report.errors.append(f"checkpoint {j} instance_id mismatch with entry: {cp_inst} != {target_entry.get('instance_id')}")
                        if target_entry.get("epoch") != c_epoch:
                            report.errors.append(f"checkpoint {j} epoch mismatch with entry: {c_epoch} != {target_entry.get('epoch')}")

        valid_cps = [c for c in cp_list if isinstance(c, dict) and type(c.get("count")) is int and c.get("count", 0) >= 0]
        cp_count = valid_cps[-1].get("count", 0) if valid_cps else 0
        report.anchored_count = cp_count
        report.unanchored_tail = max(0, len(entry_list) - cp_count)
    else:
        report.anchored_count = 0
        report.unanchored_tail = len(entry_list)


    for seq, intent in intents_by_seq.items():
        if seq not in results_by_intent:
            report.findings.append(f"intent seq={seq} grant_id={intent.get('grant_id')} consumed, outcome unknown")

    return report
