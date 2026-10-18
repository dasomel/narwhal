"""Behavioural verification tests for Addendum 4 (D-EV-36..41)."""

import hashlib
import multiprocessing
import os
from pathlib import Path
import sys

from scripts.evidence.canonical import canonical
from scripts.evidence.chain import (
    ENTRY_PREFIX, GENESIS_PREV_HASH, ChainIntegrityError, ChainWriter,
    append_checkpoint, build_checkpoint, read_chain,
)
from scripts.evidence.checkpoint import read_checkpoints
from scripts.evidence.cli import main as cli_main
from scripts.evidence.shapes import validate_checkpoint_shape, validate_entry_shape
from scripts.evidence.signing import OpensslVerifier
from scripts.evidence.verify import verify


def _run_cmd_checkpoint_worker(args):
    root = str(Path(__file__).resolve().parents[3])
    if root not in sys.path:
        sys.path.insert(0, root)
    chain_p, cp_p, cpriv, epub = args
    from scripts.evidence.cli import main as cmain
    try:
        return cmain(["checkpoint", "--chain", str(chain_p), "--cp-file", str(cp_p), "--key", str(cpriv), "--entry-pub", str(epub)])
    except SystemExit as e:
        return e.code
    except Exception:
        return 1


def test_d_ev_28_truncate_to_zero(td, epub, cpub, esig, csig):
    """The st_size > 0 guard used to skip the shrink check, so a 0-byte truncate went unnoticed."""
    for op in ("append_intent", "get_head"):
        chain_p = td / f"chain_zero_{op}.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        for i in range(4):
            w.append_intent(f"g{i}", f"inv{i}")
        os.truncate(chain_p, 0)
        try:
            if op == "append_intent":
                w.append_intent("g-new", "inv-new")
            else:
                w.get_head()
            raise AssertionError(f"{op}: open writer must raise after truncate(0)")
        except ChainIntegrityError:
            pass
        assert w._poisoned is True
        assert chain_p.stat().st_size == 0, "poisoned writer must write nothing"
        try:
            w.append_intent("g-other", "inv-other")
            raise AssertionError("poisoned writer must refuse every later append")
        except ChainIntegrityError:
            pass


def test_d_ev_36_countersigner_atomicity(td, epub, cpub, esig, csig):
    """D-EV-36: 6 concurrent processes countersigning at once, repeated 5 trials."""
    cpriv = td / "c.key"
    ctx = multiprocessing.get_context("spawn")
    for trial in range(5):
        chain_p = td / f"chain_atom_{trial}.jsonl"
        cp_p = td / f"cp_atom_{trial}.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        for i in range(5):
            w.append_intent(f"g_{trial}_{i}", f"inv_{trial}_{i}")

        with ctx.Pool(processes=6) as pool:
            args_list = [(chain_p, cp_p, cpriv, epub)] * 6
            results = pool.map(_run_cmd_checkpoint_worker, args_list)

        cps = read_checkpoints(cp_p)
        successes = sum(1 for r in results if r == 0)
        assert len(cps) == successes, f"trial {trial}: cps len {len(cps)} != successes {successes}"
        assert len(cps) == 1, f"trial {trial}: exactly 1 winner expected, got {len(cps)}"
        assert successes == 1, f"trial {trial}: exactly 1 success expected, got {successes}"
        assert cps[0]["cp_seq"] == 0, f"trial {trial}: cp_seq mismatch: {cps[0]['cp_seq']}"
        rep = verify(chain_p, epub, cp_p, cpub)
        assert rep.ok, f"trial {trial}: verify failed: {rep.errors}"


def test_d_ev_37_countersigner_verifies_checkpoints(td, epub, cpub, esig, csig):
    """D-EV-37: Countersigner verifies existing checkpoint chain before extending."""
    chain_p, cp_p = td / "chain_c37.jsonl", td / "cp_c37.jsonl"
    cpriv = td / "c.key"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    count, head = w.get_head()

    # Forged cp0 signed with entry key instead of checkpoint key
    forged_cp = build_checkpoint(0, "inst-1", 1, count, head, GENESIS_PREV_HASH, esig)
    append_checkpoint(cp_p, forged_cp)

    w.append_result(0, "ok")
    rc = cli_main(["checkpoint", "--chain", str(chain_p), "--cp-file", str(cp_p), "--key", str(cpriv), "--entry-pub", str(epub)])
    assert rc == 1, "countersigner must refuse extending forged checkpoint chain"
    assert len(read_checkpoints(cp_p)) == 1, "checkpoint file must not be extended"


def test_d_ev_38_argparse_exit_codes(td, epub, cpub, esig, csig):
    """D-EV-38: Argparse usage errors exit 64; exit 2 is exclusively unresolved intents."""
    for bad_argv in [
        [],
        ["unknown-command"],
        ["intent"],
        ["checkpoint", "--chain", str(td / "c.jsonl")],
        ["verify", "--chain", str(td / "c.jsonl"), "--unknown-flag"],
    ]:
        try:
            cli_main(bad_argv)
            raise AssertionError(f"expected SystemExit 64 for {bad_argv}")
        except SystemExit as e:
            assert e.code == 64, f"expected exit code 64, got {e.code} for {bad_argv}"

    chain_p = td / "chain_unres.jsonl"
    ChainWriter(chain_p, "inst-1", 1, esig, epub).append_intent("g-pending", "inv-pending")
    rc = cli_main(["verify", "--chain", str(chain_p), "--entry-pub", str(epub), "--cp-pub", str(cpub), "--no-checkpoints", "--fail-on-unresolved"])
    assert rc == 2, f"expected exit code 2, got {rc}"


def test_d_ev_39_shapes_lowercase_hex(td, epub, cpub, esig, csig):
    """D-EV-39: Every hash is 64 lowercase [0-9a-f]; sig is lowercase hex; uppercase => integrity error."""
    e_bad_hash = {
        "v": 1, "seq": 0, "ts_ms": 1000, "kind": "intent", "instance_id": "inst-1", "epoch": 1,
        "grant_id": "g1", "invocation_hash": "inv1", "prev_hash": "0" * 64,
        "entry_hash": "A" * 64, "sig": "ab" * 32
    }
    assert any("entry_hash is not 64-hex str" in e for e in validate_entry_shape(e_bad_hash, 0)), "uppercase entry_hash must be rejected"

    e_bad_sig = {
        "v": 1, "seq": 0, "ts_ms": 1000, "kind": "intent", "instance_id": "inst-1", "epoch": 1,
        "grant_id": "g1", "invocation_hash": "inv1", "prev_hash": "0" * 64,
        "entry_hash": "a" * 64, "sig": "AB" * 32
    }
    assert any("sig is not valid hex str" in e for e in validate_entry_shape(e_bad_sig, 0))

    cp_bad_head = {
        "v": 1, "kind": "checkpoint", "cp_seq": 0, "instance_id": "inst-1", "epoch": 1, "count": 1,
        "head_hash": "F" * 64, "ts_ms": 1000, "prev_cp_hash": "0" * 64, "cp_hash": "a" * 64, "sig": "ab" * 32
    }
    assert any("head_hash is not 64-hex str" in e for e in validate_checkpoint_shape(cp_bad_head, 0))

    chain_p = td / "chain_upper.jsonl"
    chain_p.write_text(canonical(e_bad_hash).decode() + "\n")
    rep = verify(chain_p, epub, None, cpub, no_checkpoints=True)
    assert not rep.ok and any("entry_hash is not 64-hex str" in e for e in rep.errors)


def test_d_ev_40_cached_head_hash_mismatch(td, epub, cpub, esig, csig):
    """D-EV-40: Head-hash comparison at chain.py ~159 fails when tampered in-place."""
    chain_p = td / "chain_m10.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g0", "inv0")
    w.append_result(0, "ok")

    lines = chain_p.read_bytes().splitlines(keepends=True)
    assert len(lines) == 2
    tampered_last_line = lines[1].replace(b'"outcome":"ok"', b'"outcome":"er"')
    assert len(tampered_last_line) == len(lines[1])
    chain_p.write_bytes(lines[0] + tampered_last_line)

    try:
        w.get_head()
        raise AssertionError("w.get_head() must raise ChainIntegrityError on head mismatch")
    except ChainIntegrityError as e:
        assert "cached head hash mismatch" in str(e)
    assert w._poisoned is True


def test_d_ev_41_zero_count_head_and_verifier_rejects_private_key(td, epub, cpub, esig, csig):
    """D-EV-41: Checkpoint with count=0 requires head_hash == GENESIS; OpensslVerifier rejects private keys."""
    epriv = td / "e.key"
    try:
        OpensslVerifier(epriv)
        raise AssertionError("OpensslVerifier must reject private key")
    except ValueError as e:
        assert "private key cannot be used as public key" in str(e) or "invalid public key" in str(e)

    chain_p = td / "chain_empty.jsonl"
    chain_p.touch()
    cp_bad_p = td / "cp_zero_bad.jsonl"
    append_checkpoint(cp_bad_p, build_checkpoint(0, "inst-1", 1, 0, "1" * 64, GENESIS_PREV_HASH, csig))
    rep_bad = verify(chain_p, epub, cp_bad_p, cpub)
    assert not rep_bad.ok, "checkpoint with count=0 and non-genesis head must fail verify"
    assert any("count=0 must have head_hash == GENESIS" in e for e in rep_bad.errors)


    cp_good_p = td / "cp_zero_good.jsonl"
    append_checkpoint(cp_good_p, build_checkpoint(0, "inst-1", 1, 0, GENESIS_PREV_HASH, GENESIS_PREV_HASH, csig))
    rep_good = verify(chain_p, epub, cp_good_p, cpub)
    assert rep_good.ok, f"expected ok, got errors: {rep_good.errors}"


ADDENDUM4_TESTS = [
    ("D-EV-28: truncate to zero poisons open writer", test_d_ev_28_truncate_to_zero),
    ("D-EV-36: countersigner atomicity and lock order", test_d_ev_36_countersigner_atomicity),
    ("D-EV-37: countersigner verifies existing checkpoints", test_d_ev_37_countersigner_verifies_checkpoints),
    ("D-EV-38: argparse exit codes 64 and exclusive 2", test_d_ev_38_argparse_exit_codes),
    ("D-EV-39: shapes lowercase hex enforcement", test_d_ev_39_shapes_lowercase_hex),
    ("D-EV-40: cached head hash mismatch detection", test_d_ev_40_cached_head_hash_mismatch),
    ("D-EV-41: zero count head hash and verifier private key rejection", test_d_ev_41_zero_count_head_and_verifier_rejects_private_key),
]

try:
    from .evidence_addendum5 import ADDENDUM5_TESTS
except ImportError:
    from evidence_addendum5 import ADDENDUM5_TESTS

ADDENDUM4_TESTS.extend(ADDENDUM5_TESTS)

