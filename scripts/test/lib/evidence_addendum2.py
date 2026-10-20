"""Behavioural verification tests for Addendum 2 (D-EV-20..26)."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from scripts.evidence.canonical import CanonicalError, canonical, parse_strict_json_line
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
from scripts.evidence.signing import OpensslSigner, OpensslVerifier, keys_are_identical
from scripts.evidence.verify import verify


def test_writer_open_sig_verify(td, epub, _, esig, __):
    """D-EV-26 item 1: writer open-time signature verification with valid hash but bad sig."""
    chain_p = td / "chain.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv-1")
    lines = chain_p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["sig"] = ("0" * 128)
    chain_p.write_text(canonical(rec).decode("utf-8") + "\n", encoding="utf-8")
    try:
        ChainWriter(chain_p, "inst-1", 1, esig, epub)
        raise AssertionError("Writer should fail on bad signature during open")
    except ChainIntegrityError as e:
        assert "signature verification failed" in str(e)


def test_writer_reload_spend_once(td, epub, _, esig, __):
    """D-EV-26 item 2: writer reload-derived spend-once after reopening."""
    chain_p = td / "chain.jsonl"
    w1 = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w1.append_intent("g-reload", "inv-1")
    w2 = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    try:
        w2.append_intent("g-reload", "inv-2")
        raise AssertionError("Reopened writer must refuse consumed grant_id")
    except DoubleSpendError:
        pass


def test_checkpoint_cross_checks(td, epub, cpub, esig, csig):
    """D-EV-26 item 3: checkpoint epoch/instance vs entry cross-check."""
    chain_p, cp_p = td / "chain.jsonl", td / "cp.jsonl"
    w = ChainWriter(chain_p, "inst-1", 2, esig, epub)
    w.append_intent("g1", "inv1")
    count, head = w.get_head()

    # Instance mismatch
    bad_inst_cp = build_checkpoint(0, "inst-2", 2, count, head, GENESIS_PREV_HASH, csig)
    rep1 = verify(chain_p, epub, [bad_inst_cp], cpub)
    assert not rep1.ok and any("instance_id mismatch" in e for e in rep1.errors)

    # Epoch mismatch
    bad_epoch_cp = build_checkpoint(0, "inst-1", 3, count, head, GENESIS_PREV_HASH, csig)
    rep2 = verify(chain_p, epub, [bad_epoch_cp], cpub)
    assert not rep2.ok and any("epoch mismatch" in e for e in rep2.errors)


def test_writer_epoch_lower_bound(td, epub, _, esig, __):
    """D-EV-26 item 4: writer epoch lower-bound on open."""
    chain_p = td / "chain.jsonl"
    ChainWriter(chain_p, "inst-1", 5, esig, epub).append_intent("g1", "inv1")
    try:
        ChainWriter(chain_p, "inst-1", 4, esig, epub)
        raise AssertionError("Expected epoch lower bound failure on open")
    except ChainIntegrityError as e:
        assert "writer epoch 4 < chain last epoch 5" in str(e)


def test_cli_checkpoint_extension(td, epub, cpub, esig, csig):
    """D-EV-26 item 5: CLI checkpoint extension check (D-EV-13)."""
    chain_p, cp_p = td / "chain.jsonl", td / "cp.jsonl"
    epriv, cpriv = td / "e.key", td / "c.key"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    c1, h1 = w.get_head()
    append_checkpoint(cp_p, build_checkpoint(0, "inst-1", 1, c1, h1, GENESIS_PREV_HASH, csig))

    # Fork chain so new head does not extend previous checkpoint head
    chain_fork = td / "chain_fork.jsonl"
    w_fork = ChainWriter(chain_fork, "inst-1", 1, esig, epub)
    w_fork.append_intent("g-fork", "inv-fork")
    w_fork.append_result(0, "ok")

    rc = cli_main(["checkpoint", "--chain", str(chain_fork), "--cp-file", str(cp_p), "--key", str(cpriv), "--entry-pub", str(epub)])
    assert rc == 1


def test_checkpoints_xor_requirement(td, epub, cpub, esig, _):
    """D-EV-26 item 6: --checkpoints/--no-checkpoints requirement (CLI and API)."""
    chain_p, cp_p = td / "chain.jsonl", td / "empty_cp.jsonl"
    cp_p.write_text("", encoding="utf-8")
    ChainWriter(chain_p, "inst-1", 1, esig, epub).append_intent("g1", "inv1")

    # API XOR and empty checkpoint checks
    rep_both = verify(chain_p, epub, [{}], cpub, allow_no_checkpoints=True)
    assert not rep_both.ok and any("both" in e for e in rep_both.errors)

    rep_neither = verify(chain_p, epub, None, cpub, allow_no_checkpoints=False)
    assert not rep_neither.ok and any("either" in e for e in rep_neither.errors)

    rep_empty_list = verify(chain_p, epub, [], cpub)
    assert not rep_empty_list.ok and any("empty" in e for e in rep_empty_list.errors)

    rep_empty_file = verify(chain_p, epub, cp_p, cpub)
    assert not rep_empty_file.ok and any("empty" in e for e in rep_empty_file.errors)

    # CLI XOR checks
    rc_neither = cli_main(["verify", "--chain", str(chain_p), "--entry-pub", str(epub), "--cp-pub", str(cpub)])
    assert rc_neither == 1

    rc_both = cli_main(["verify", "--chain", str(chain_p), "--entry-pub", str(epub), "--cp-pub", str(cpub), "--checkpoints", str(cp_p), "--no-checkpoints"])
    assert rc_both == 1


def test_duplicate_json_key_independent(td, _, __, ___, ____):
    """D-EV-26 item 7: duplicate-JSON-key rejection independent of canonical bytes check."""
    line = b'{"epoch":1,"grant_id":"g1","instance_id":"inst-1","invocation_hash":"inv1","kind":"intent","prev_hash":"0000","seq":0,"seq":0,"ts_ms":1000}\n'
    with patch("scripts.evidence.canonical.canonical", return_value=line[:-1]):
        try:
            parse_strict_json_line(line, 1)
            raise AssertionError("Expected duplicate JSON key failure")
        except CanonicalError as e:
            assert "duplicate key" in str(e)


def test_directory_fsync_on_creation(td, epub, _, esig, __):
    """D-EV-26 item 8: directory fsync on creation."""
    sub_dir = td / "new_dir"
    chain_p = sub_dir / "chain.jsonl"
    fsynced_inos = []
    real_fsync = os.fsync

    def mock_fsync(fd):
        try:
            fsynced_inos.append(os.fstat(fd).st_ino)
        except OSError:
            pass
        real_fsync(fd)

    with patch("os.fsync", side_effect=mock_fsync):
        ChainWriter(chain_p, "inst-1", 1, esig, epub).append_intent("g1", "inv1")

    assert sub_dir.stat().st_ino in fsynced_inos



def test_symlink_writers_serialise(td, epub, _, esig, __):
    """D-EV-26 item 9: symlink/hardlink writers serialise on same inode."""
    chain_p = td / "chain.jsonl"
    symlink_p = td / "symlink_chain.jsonl"
    w1 = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    os.symlink(chain_p, symlink_p)
    w2 = ChainWriter(symlink_p, "inst-1", 1, esig, epub)

    # While w1 holds lock on chain fd via _lock(), non-blocking lock on symlink fd must fail
    with w1._lock():
        symlink_fd = os.open(str(symlink_p), os.O_RDWR)
        try:
            try:
                fcntl.flock(symlink_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                raise AssertionError("Expected lock contention across symlink on same inode")
            except (BlockingIOError, OSError):
                pass
        finally:
            os.close(symlink_fd)

    w1.append_intent("g-initial", "inv-init")
    try:
        w2.append_intent("g-initial", "inv-conflict")
        raise AssertionError("Symlink writer must detect already consumed grant_id")
    except DoubleSpendError:
        pass


def test_incremental_verification_scale_300(td, epub, _, esig, __):
    """D-EV-22: 300 entries incremental append performance and fresh open tamper detection."""
    chain_p = td / "chain.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    for idx in range(149):
        w.append_intent(f"g-{idx}", f"inv-{idx}")
        w.append_result(idx * 2, "ok")
    w.append_intent("g-149", "inv-149")

    # 300th append: count subprocess.run calls
    call_count = 0
    real_run = subprocess.run

    def mock_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_run(*args, **kwargs)

    with patch("subprocess.run", side_effect=mock_run):
        w.append_result(298, "ok")

    assert call_count <= 3, f"Expected <= 3 subprocess calls, got {call_count}"
    assert w.seq == 300

    # Tamper an earlier line (entry 1) in-place
    lines = chain_p.read_text(encoding="utf-8").splitlines()
    tampered_rec = json.loads(lines[1])
    tampered_rec["outcome"] = "error"
    body = {k: v for k, v in tampered_rec.items() if k not in ("entry_hash", "sig")}
    tampered_rec["entry_hash"] = hashlib.sha256(ENTRY_PREFIX + canonical(body)).hexdigest()
    lines[1] = canonical(tampered_rec).decode("utf-8")
    chain_p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        ChainWriter(chain_p, "inst-1", 1, esig, epub)
        raise AssertionError("Fresh open must catch tampered earlier line")
    except (ChainIntegrityError, ValueError):
        pass


def test_verify_never_raises_malformed(td, epub, cpub, _, __):
    """D-EV-23: verify() never raises on malformed records (reports errors)."""
    # Malformed entry records
    malformed_entries = [
        ["not", "a", "dict"],
        {"v": 1, "seq": 0, "kind": "intent", "grant_id": ["not", "a", "str"]},
        {"v": 1, "seq": 1, "kind": "result", "intent_seq": "not-an-int"},
    ]
    rep1 = verify(malformed_entries, epub, None, cpub, allow_no_checkpoints=True)
    assert not rep1.ok and len(rep1.errors) >= 3

    # Malformed checkpoint records
    malformed_cps = [
        "not-a-dict",
        {"v": 1, "kind": "checkpoint", "cp_seq": 0, "count": "not-an-int"},
        {"v": 1, "kind": "not-a-checkpoint"},
    ]
    rep2 = verify([], epub, malformed_cps, cpub)
    assert not rep2.ok and len(rep2.errors) >= 3


def test_keys_are_identical_fail_closed(td, epub, cpub, _, __):
    """D-EV-25: keys_are_identical fails closed on unparseable input."""
    try:
        keys_are_identical("bad-key-data", "bad-key-data-2")
        raise AssertionError("Expected keys_are_identical to raise on unparseable keys")
    except (ValueError, FileNotFoundError):
        pass


ADDENDUM2_TESTS = [
    ("D-EV-26: writer open-time signature verification", test_writer_open_sig_verify),
    ("D-EV-26: writer reload-derived spend-once", test_writer_reload_spend_once),
    ("D-EV-26: checkpoint epoch/instance cross-checks", test_checkpoint_cross_checks),
    ("D-EV-26: writer epoch lower-bound on open", test_writer_epoch_lower_bound),
    ("D-EV-26: CLI checkpoint extension check", test_cli_checkpoint_extension),
    ("D-EV-26: checkpoints XOR no-checkpoints", test_checkpoints_xor_requirement),
    ("D-EV-26: duplicate JSON key independent", test_duplicate_json_key_independent),
    ("D-EV-26: directory fsync on creation", test_directory_fsync_on_creation),
    ("D-EV-26: symlink writers serialise", test_symlink_writers_serialise),
    ("D-EV-22: incremental 300 appends and fresh open tamper", test_incremental_verification_scale_300),
    ("D-EV-23: verify() never raises on malformed records", test_verify_never_raises_malformed),
    ("D-EV-25: keys_are_identical fail-closed", test_keys_are_identical_fail_closed),
]
