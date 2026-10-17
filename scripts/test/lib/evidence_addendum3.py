"""Behavioural verification tests for Addendum 3 (D-EV-28..34)."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

from scripts.evidence.canonical import canonical
from scripts.evidence.chain import (
    ENTRY_PREFIX,
    GENESIS_PREV_HASH,
    ChainIntegrityError,
    ChainWriter,
    DoubleSpendError,
    append_checkpoint,
    build_checkpoint,
    read_chain,
)
from scripts.evidence.cli import main as cli_main
from scripts.evidence.signing import OpensslVerifier
from scripts.evidence.verify import verify


def test_d_ev_28_writer_poison_on_shrink(td, epub, cpub, esig, csig):
    """D-EV-28: Open writer poisons itself on file shrink/tamper; no silent recovery."""
    chain_p, cp_p = td / "chain_shrink.jsonl", td / "cp_shrink.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g0", "inv0")
    w.append_result(0, "ok")
    w.append_intent("g1", "inv1")
    w.append_result(2, "ok")
    w.append_intent("g2", "inv2")
    w.append_result(4, "ok")
    w.append_intent("g3", "inv3")
    w.append_result(6, "ok")
    count, head = w.get_head()
    append_checkpoint(cp_p, build_checkpoint(0, "inst-1", 1, count, head, GENESIS_PREV_HASH, csig))

    # Truncate file from outside to 2 entries
    lines = chain_p.read_text(encoding="utf-8").splitlines()[:2]
    chain_p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    size_after_trunc = chain_p.stat().st_size

    # The SAME writer's append of g3 must raise and write nothing
    try:
        w.append_intent("g3", "inv-new")
        raise AssertionError("Same writer must raise on shrink")
    except ChainIntegrityError as e:
        assert "shrunk" in str(e) or "poisoned" in str(e)
    assert w._poisoned is True

    # Subsequent append must also fail closed
    try:
        w.append_intent("g4", "inv4")
        raise AssertionError("Poisoned writer must refuse subsequent append")
    except ChainIntegrityError:
        pass
    assert chain_p.stat().st_size == size_after_trunc

    # A fresh open sees the truncated chain
    w_fresh = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    assert w_fresh.seq == 2
    # Verify against old checkpoint (count=8) must FAIL
    rep = verify(chain_p, epub, cp_p, cpub)
    assert not rep.ok and any("tail truncation" in err.lower() for err in rep.errors)


def test_d_ev_29_writer_input_validation(td, epub, _, esig, __):
    """D-EV-29: Validate input shape before signing/writing; write nothing on ValueError."""
    chain_p = td / "chain_inputs.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g0", "inv0")
    bytes_before = chain_p.read_bytes()

    for bad_grant in ["", ["list"], None, 123]:
        try:
            w.append_intent(bad_grant, "inv")
            raise AssertionError(f"Expected ValueError for bad grant: {bad_grant}")
        except ValueError:
            pass

    for bad_inv in ["", ["list"], None, 456]:
        try:
            w.append_intent("g-valid", bad_inv)
            raise AssertionError(f"Expected ValueError for bad invocation: {bad_inv}")
        except ValueError:
            pass

    w.epoch = -1
    try:
        w.append_intent("g-epoch", "inv")
        raise AssertionError("Expected ValueError for negative epoch")
    except ValueError:
        pass
    w.epoch = 1

    for bad_outcome in ["x", "invalid", "", None]:
        try:
            w.append_result(0, bad_outcome)
            raise AssertionError(f"Expected ValueError for bad outcome: {bad_outcome}")
        except ValueError:
            pass

    for bad_seq in [-1, "0", None]:
        try:
            w.append_result(bad_seq, "ok")
            raise AssertionError(f"Expected ValueError for bad intent_seq: {bad_seq}")
        except ValueError:
            pass

    assert chain_p.read_bytes() == bytes_before
    w_reopen = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    assert w_reopen.seq == 1


def test_d_ev_30_checkpoint_shape_and_types(td, epub, cpub, _, __):
    """D-EV-30: verify() never raises on malformed checkpoint records."""
    valid_cp = {
        "v": 1, "kind": "checkpoint", "cp_seq": 0, "instance_id": "inst-1",
        "epoch": 0, "count": 0, "head_hash": "0" * 64, "ts_ms": 1000,
        "prev_cp_hash": "0" * 64, "cp_hash": "0" * 64, "sig": "0" * 128,
    }
    for bad_count in ["3", True, 1.0, -1]:
        cp = dict(valid_cp)
        cp["count"] = bad_count
        rep = verify([], epub, [cp], cpub)
        assert not rep.ok and any("count is not non-negative int" in e for e in rep.errors)

    rep_list = verify([], epub, [["not", "a", "dict"]], cpub)
    assert not rep_list.ok and any("not a dict" in e for e in rep_list.errors)

    cp_bad_hash = dict(valid_cp)
    cp_bad_hash["head_hash"] = "short-hash"
    rep_hash = verify([], epub, [cp_bad_hash], cpub)
    assert not rep_hash.ok and any("head_hash is not 64-hex str" in e for e in rep_hash.errors)


def test_d_ev_31_writer_detects_rename_swap(td, epub, _, esig, __):
    """D-EV-31: Detect file replacement under held flock by comparing locked fd with stat(path)."""
    chain_p = td / "chain_swap.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g0", "inv0")

    swap_file = td / "swapped.jsonl"
    swap_file.write_text('{"v":1}\n', encoding="utf-8")
    os.replace(swap_file, chain_p)

    try:
        w.append_intent("g1", "inv1")
        raise AssertionError("Writer must detect rename swap and raise")
    except ChainIntegrityError as e:
        assert "replacement" in str(e) or "swap" in str(e)
    assert w._poisoned is True


def test_d_ev_32_short_writes_and_poison(td, epub, _, esig, __):
    """D-EV-32: Loop until all bytes are written before fsync; poison on any write error."""
    chain_p = td / "chain_short.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)

    real_write = os.write
    def one_byte_write(fd, data):
        return real_write(fd, data[:1])

    with patch("os.write", side_effect=one_byte_write):
        rec = w.append_intent("g-short", "inv-short")
    assert rec["grant_id"] == "g-short"
    assert len(read_chain(chain_p)) == 1

    with patch("os.write", side_effect=OSError("simulated disk failure")):
        try:
            w.append_intent("g-fail", "inv-fail")
            raise AssertionError("Expected OSError on write failure")
        except OSError:
            pass
    assert w._poisoned is True


def test_d_ev_33_cli_checkpoint_validation(td, epub, _, esig, csig):
    """D-EV-33: CLI checkpoint rejects double spend and identical keys."""
    chain_p, cp_p = td / "chain_cp_val.jsonl", td / "cp_export.jsonl"
    epriv, cpriv = td / "e.key", td / "c.key"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g-dup", "inv1")

    # Manually append second intent for same grant
    rec2 = {
        "v": 1, "seq": 1, "ts_ms": 1000, "kind": "intent",
        "instance_id": "inst-1", "epoch": 1, "grant_id": "g-dup",
        "invocation_hash": "inv2", "prev_hash": w.last_hash,
    }
    rec2["entry_hash"] = hashlib.sha256(ENTRY_PREFIX + canonical(rec2)).hexdigest()
    rec2["sig"] = esig.sign(ENTRY_PREFIX + rec2["entry_hash"].encode("ascii")).hex()
    with open(chain_p, "ab") as f:
        f.write(canonical(rec2) + b"\n")

    # CLI checkpoint must reject double spend
    rc_dup = cli_main(["checkpoint", "--chain", str(chain_p), "--cp-file", str(cp_p), "--key", str(cpriv), "--entry-pub", str(epub)])
    assert rc_dup == 1

    # CLI checkpoint must reject signing when entry pub key == checkpoint key
    chain_ok = td / "chain_ok.jsonl"
    ChainWriter(chain_ok, "inst-1", 1, esig, epub).append_intent("g-ok", "inv-ok")
    rc_key = cli_main(["checkpoint", "--chain", str(chain_ok), "--cp-file", str(cp_p), "--key", str(epriv), "--entry-pub", str(epub)])
    assert rc_key == 1


def test_d_ev_34_mutation_survivors(td, epub, cpub, esig, csig):
    """D-EV-34: Mutation-survivor tests for Addendum 3."""
    chain_p, cp_p = td / "chain_surv.jsonl", td / "cp_surv.jsonl"
    epriv, cpriv = td / "e.key", td / "c.key"

    # 1) checkpoint instance_id vs entry cross-check (separate from epoch)
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g0", "inv0")
    count, head = w.get_head()
    bad_inst_cp = build_checkpoint(0, "inst-different", 1, count, head, GENESIS_PREV_HASH, csig)
    rep_inst = verify(chain_p, epub, [bad_inst_cp], cpub)
    assert not rep_inst.ok and any("instance_id mismatch with entry" in e for e in rep_inst.errors)

    # 2) CLI --checkpoints/--no-checkpoints required (must reject before invoking verify())
    with patch("scripts.evidence.cli.verify") as mock_verify:
        rc_neither = cli_main(["verify", "--chain", str(chain_p), "--entry-pub", str(epub), "--cp-pub", str(cpub)])
        assert rc_neither == 1
        assert not mock_verify.called, "verify() must not be called when neither flag is passed"

        rc_both = cli_main(["verify", "--chain", str(chain_p), "--entry-pub", str(epub), "--cp-pub", str(cpub), "--checkpoints", str(cp_p), "--no-checkpoints"])
        assert rc_both == 1
        assert not mock_verify.called, "verify() must not be called when both flags are passed"



    # 3) writer reload spend-once when the CHAIN ITSELF contains a duplicate grant
    chain_dup = td / "chain_dup_open.jsonl"
    w_dup = ChainWriter(chain_dup, "inst-1", 1, esig, epub)
    w_dup.append_intent("g-dup2", "inv1")
    rec_dup = {
        "v": 1, "seq": 1, "ts_ms": 1000, "kind": "intent",
        "instance_id": "inst-1", "epoch": 1, "grant_id": "g-dup2",
        "invocation_hash": "inv2", "prev_hash": w_dup.last_hash,
    }
    rec_dup["entry_hash"] = hashlib.sha256(ENTRY_PREFIX + canonical(rec_dup)).hexdigest()
    rec_dup["sig"] = esig.sign(ENTRY_PREFIX + rec_dup["entry_hash"].encode("ascii")).hex()
    with open(chain_dup, "ab") as f:
        f.write(canonical(rec_dup) + b"\n")

    try:
        ChainWriter(chain_dup, "inst-1", 1, esig, epub)
        raise AssertionError("Reopened writer must reject chain containing duplicate grant")
    except DoubleSpendError:
        pass

    # 4) LOCK_SH in checkpoint (assert via monkeypatched fcntl.flock call recording LOCK_SH)
    recorded_flocks = []
    real_flock = fcntl.flock
    def mock_flock(fd, op):
        recorded_flocks.append(op)
        return real_flock(fd, op)

    with patch("fcntl.flock", side_effect=mock_flock):
        rc_cp = cli_main(["checkpoint", "--chain", str(chain_p), "--cp-file", str(cp_p), "--key", str(cpriv), "--entry-pub", str(epub)])
        assert rc_cp == 0
    assert any(op & fcntl.LOCK_SH for op in recorded_flocks)

    # 5) full-verify path on fresh open after truncation
    chain_trunc = td / "chain_trunc.jsonl"
    w_tr = ChainWriter(chain_trunc, "inst-1", 1, esig, epub)
    w_tr.append_intent("gt0", "inv0"); w_tr.append_result(0, "ok")
    w_tr.append_intent("gt1", "inv1"); w_tr.append_result(2, "ok")
    cnt, hd = w_tr.get_head()
    cp_trunc_p = td / "cp_trunc.jsonl"
    append_checkpoint(cp_trunc_p, build_checkpoint(0, "inst-1", 1, cnt, hd, GENESIS_PREV_HASH, csig))
    # Truncate to 2 entries
    lines_tr = chain_trunc.read_text(encoding="utf-8").splitlines()[:2]
    chain_trunc.write_text("\n".join(lines_tr) + "\n", encoding="utf-8")
    w_fresh = ChainWriter(chain_trunc, "inst-1", 1, esig, epub)
    assert w_fresh.seq == 2
    rep_tr = verify(chain_trunc, epub, cp_trunc_p, cpub)
    assert not rep_tr.ok and any("tail truncation" in e.lower() for e in rep_tr.errors)


ADDENDUM3_TESTS = [
    ("D-EV-28: poison writer on shrink", test_d_ev_28_writer_poison_on_shrink),
    ("D-EV-29: writer input validation", test_d_ev_29_writer_input_validation),
    ("D-EV-30: checkpoint shape and types", test_d_ev_30_checkpoint_shape_and_types),
    ("D-EV-31: writer detects rename swap", test_d_ev_31_writer_detects_rename_swap),
    ("D-EV-32: short writes and poison", test_d_ev_32_short_writes_and_poison),
    ("D-EV-33: CLI checkpoint validation", test_d_ev_33_cli_checkpoint_validation),
    ("D-EV-34: mutation survivors", test_d_ev_34_mutation_survivors),
]
