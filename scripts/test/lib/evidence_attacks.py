"""Behavioural attack and edge-case tests for evidence chain (D-EV-18)."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing as mp
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
from scripts.evidence.signing import OpensslSigner, OpensslVerifier
from scripts.evidence.verify import verify


def _forge_and_append(chain_p, esig, rec):
    raw = canonical(rec)
    rec["entry_hash"] = hashlib.sha256(ENTRY_PREFIX + raw).hexdigest()
    rec["sig"] = esig.sign(ENTRY_PREFIX + rec["entry_hash"].encode("ascii")).hex()
    with open(chain_p, "a", encoding="utf-8") as f:
        f.write(canonical(rec).decode("utf-8") + "\n")


def test_verify_double_spend(td, epub, cpub, esig, _):
    chain_p = td / "chain.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    _forge_and_append(chain_p, esig, {
        "v": 1, "seq": 1, "ts_ms": 1000, "kind": "intent",
        "instance_id": "inst-1", "epoch": 1, "grant_id": "g1",
        "invocation_hash": "inv2", "prev_hash": w.last_hash,
    })
    rep = verify(chain_p, epub, None, cpub, no_checkpoints=True)
    assert not rep.ok and any("double spend" in e for e in rep.errors)


def test_duplicate_result(td, epub, cpub, esig, _):
    chain_p = td / "chain.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    w.append_result(0, "ok")
    _forge_and_append(chain_p, esig, {
        "v": 1, "seq": 2, "ts_ms": 1000, "kind": "result",
        "instance_id": "inst-1", "epoch": 1, "grant_id": "g1",
        "invocation_hash": "inv1", "intent_seq": 0, "outcome": "error",
        "prev_hash": w.last_hash,
    })
    rep = verify(chain_p, epub, None, cpub, no_checkpoints=True)
    assert not rep.ok and any("duplicate result" in e for e in rep.errors)


def test_result_invocation_hash_mismatch(td, epub, cpub, esig, _):
    chain_p = td / "chain.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    _forge_and_append(chain_p, esig, {
        "v": 1, "seq": 1, "ts_ms": 1000, "kind": "result",
        "instance_id": "inst-1", "epoch": 1, "grant_id": "g1",
        "invocation_hash": "inv-tampered", "intent_seq": 0, "outcome": "ok",
        "prev_hash": w.last_hash,
    })
    rep = verify(chain_p, epub, None, cpub, no_checkpoints=True)
    assert not rep.ok and any("invocation_hash mismatch" in e for e in rep.errors)


def test_forward_unknown_intent_seq(td, epub, cpub, esig, _):
    chain_p = td / "chain.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    _forge_and_append(chain_p, esig, {
        "v": 1, "seq": 1, "ts_ms": 1000, "kind": "result",
        "instance_id": "inst-1", "epoch": 1, "grant_id": "g1",
        "invocation_hash": "inv1", "intent_seq": 99, "outcome": "ok",
        "prev_hash": w.last_hash,
    })
    rep = verify(chain_p, epub, None, cpub, no_checkpoints=True)
    assert not rep.ok and any("forward/invalid intent_seq" in e for e in rep.errors)


def test_epoch_decrease(td, epub, cpub, esig, _):
    chain_p = td / "chain.jsonl"
    w = ChainWriter(chain_p, "inst-1", 2, esig, epub)
    w.append_intent("g1", "inv1")
    _forge_and_append(chain_p, esig, {
        "v": 1, "seq": 1, "ts_ms": 1000, "kind": "result",
        "instance_id": "inst-1", "epoch": 1, "grant_id": "g1",
        "invocation_hash": "inv1", "intent_seq": 0, "outcome": "ok",
        "prev_hash": w.last_hash,
    })
    rep = verify(chain_p, epub, None, cpub, no_checkpoints=True)
    assert not rep.ok and any("epoch decreased" in e for e in rep.errors)


def test_instance_id_mismatch(td, epub, cpub, esig, _):
    chain_p = td / "chain.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    _forge_and_append(chain_p, esig, {
        "v": 1, "seq": 1, "ts_ms": 1000, "kind": "result",
        "instance_id": "inst-2", "epoch": 1, "grant_id": "g1",
        "invocation_hash": "inv1", "intent_seq": 0, "outcome": "ok",
        "prev_hash": w.last_hash,
    })
    rep = verify(chain_p, epub, None, cpub, no_checkpoints=True)
    assert not rep.ok and any("instance_id mismatch" in e for e in rep.errors)


def test_cp_seq_gap(td, epub, cpub, esig, csig):
    chain_p, cp_p = td / "chain.jsonl", td / "cp.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    count, head = w.get_head()
    append_checkpoint(cp_p, build_checkpoint(1, "inst-1", 1, count, head, GENESIS_PREV_HASH, csig))
    rep = verify(chain_p, epub, cp_p, cpub)
    assert not rep.ok and any("cp_seq error" in e for e in rep.errors)


def test_prev_cp_hash_break(td, epub, cpub, esig, csig):
    chain_p, cp_p = td / "chain.jsonl", td / "cp.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    c1, h1 = w.get_head()
    append_checkpoint(cp_p, build_checkpoint(0, "inst-1", 1, c1, h1, GENESIS_PREV_HASH, csig))
    w.append_result(0, "ok")
    c2, h2 = w.get_head()
    append_checkpoint(cp_p, build_checkpoint(1, "inst-1", 1, c2, h2, "1" * 64, csig))
    rep = verify(chain_p, epub, cp_p, cpub)
    assert not rep.ok and any("prev_cp_hash mismatch" in e for e in rep.errors)


def test_cp_hash_tamper(td, epub, cpub, esig, csig):
    chain_p, cp_p = td / "chain.jsonl", td / "cp.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    count, head = w.get_head()
    cp = build_checkpoint(0, "inst-1", 1, count, head, GENESIS_PREV_HASH, csig)
    cp["cp_hash"] = "0" * 64
    append_checkpoint(cp_p, cp)
    rep = verify(chain_p, epub, cp_p, cpub)
    assert not rep.ok and any("cp_hash mismatch" in e for e in rep.errors)


def test_count_non_monotonic(td, epub, cpub, esig, csig):
    chain_p, cp_p = td / "chain.jsonl", td / "cp.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    w.append_result(0, "ok")
    append_checkpoint(cp_p, build_checkpoint(0, "inst-1", 1, 2, w.last_hash, GENESIS_PREV_HASH, csig))
    append_checkpoint(cp_p, build_checkpoint(1, "inst-1", 1, 1, w.last_hash, "0" * 64, csig))
    rep = verify(chain_p, epub, cp_p, cpub)
    assert not rep.ok and any("count decreased" in e for e in rep.errors)


def test_non_latest_checkpoint_head_mismatch(td, epub, cpub, esig, csig):
    chain_p, cp_p = td / "chain.jsonl", td / "cp.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    append_checkpoint(cp_p, build_checkpoint(0, "inst-1", 1, 1, "f" * 64, GENESIS_PREV_HASH, csig))
    w.append_result(0, "ok")
    c2, h2 = w.get_head()
    append_checkpoint(cp_p, build_checkpoint(1, "inst-1", 1, c2, h2, "0" * 64, csig))
    rep = verify(chain_p, epub, cp_p, cpub)
    assert not rep.ok and any("checkpoint 0 head_hash mismatch" in e for e in rep.errors)


def test_writer_open_verification(td, epub, _, esig, __):
    chain_p = td / "chain.jsonl"
    ChainWriter(chain_p, "inst-1", 1, esig, epub).append_intent("g1", "inv1")
    lines = chain_p.read_text(encoding="utf-8").splitlines()
    bad_rec = json.loads(lines[0])
    bad_rec["entry_hash"] = "e" * 64
    chain_p.write_text(canonical(bad_rec).decode("utf-8") + "\n", encoding="utf-8")
    try:
        ChainWriter(chain_p, "inst-1", 1, esig, epub)
        raise AssertionError("Writer open should have failed")
    except (ChainIntegrityError, ValueError):
        pass


def test_missing_files(td, epub, cpub, _, __):
    rep = verify(td / "missing.jsonl", epub, None, cpub, no_checkpoints=True)
    assert not rep.ok and any("failed to read chain" in e for e in rep.errors)
    rc1 = cli_main(["verify", "--chain", str(td / "missing.jsonl"), "--entry-pub", str(epub), "--cp-pub", str(cpub), "--no-checkpoints"])
    assert rc1 == 1


def test_duplicate_json_key(td, epub, cpub, _, __):
    chain_p = td / "chain.jsonl"
    chain_p.write_bytes(b'{"epoch":1,"grant_id":"g1","instance_id":"inst-1","invocation_hash":"inv1","kind":"intent","prev_hash":"' + (b"0" * 64) + b'","seq":0,"seq":0,"ts_ms":1000}\n')
    try:
        read_chain(chain_p)
        raise AssertionError("Expected duplicate JSON key failure")
    except ChainIntegrityError:
        pass


def test_non_canonical_whitespace(td, epub, cpub, esig, _):
    chain_p = td / "chain.jsonl"
    ChainWriter(chain_p, "inst-1", 1, esig, epub).append_intent("g1", "inv1")
    content = chain_p.read_text(encoding="utf-8")
    chain_p.write_text(content.replace(',"epoch":', ',"epoch": '), encoding="utf-8")
    try:
        read_chain(chain_p)
        raise AssertionError("Expected whitespace rejection")
    except ChainIntegrityError:
        pass


def _worker_append(args):
    path, inst, epoch, epriv, epub, grant_id = args
    signer = OpensslSigner(epriv)
    try:
        ChainWriter(path, inst, epoch, signer, epub).append_intent(grant_id, f"inv-{grant_id}")
        return True
    except DoubleSpendError:
        return False


def test_concurrent_writers(td, epub, _, esig, __):
    chain_p = td / "chain.jsonl"
    epriv = td / "e.key"
    args = [(str(chain_p), "inst-1", 1, str(epriv), str(epub), "g-shared")] * 4
    with mp.get_context("spawn").Pool(4) as pool:
        results = pool.map(_worker_append, args)
    assert results.count(True) == 1 and results.count(False) == 3
    assert len(read_chain(chain_p)) == 1


def test_key_separation_der(td, epub, cpub, esig, csig):
    chain_p = td / "chain.jsonl"
    ChainWriter(chain_p, "inst-1", 1, esig, epub).append_intent("g1", "inv1")
    epub2 = td / "e2.pub"
    epub2.write_text("# extra comment\n\n" + epub.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    rep = verify(chain_p, epub, None, epub2, no_checkpoints=True)
    assert not rep.ok and any("key separation" in e for e in rep.errors)


def test_fsync_poisoning(td, epub, _, esig, __):
    chain_p = td / "chain.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    with patch("os.fsync", side_effect=OSError("simulated fsync failure")):
        try:
            w.append_intent("g2", "inv2")
            raise AssertionError("Expected fsync failure")
        except OSError:
            pass
    try:
        w.append_intent("g3", "inv3")
        raise AssertionError("Expected poisoned call to fail")
    except ChainIntegrityError:
        pass
    w2 = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    assert w2.seq in (1, 2)
    w2.append_intent("g-after-recovery", "inv-after")
    assert w2.seq == len(read_chain(chain_p))


def test_concurrent_signing(td, epub, _, esig, __):
    msgs = [f"msg-{i}".encode("utf-8") for i in range(200)]
    verifier = OpensslVerifier(epub)
    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(lambda m: verifier.verify(m, esig.sign(m)), msgs))
    assert all(results) and len(results) == 200


ATTACK_TESTS = [
    ("D-EV-18: double spend in verify", test_verify_double_spend),
    ("D-EV-18: duplicate result", test_duplicate_result),
    ("D-EV-18: invocation_hash mismatch", test_result_invocation_hash_mismatch),
    ("D-EV-18: forward/unknown intent_seq", test_forward_unknown_intent_seq),
    ("D-EV-18: epoch decrease", test_epoch_decrease),
    ("D-EV-18: instance_id mismatch", test_instance_id_mismatch),
    ("D-EV-18: cp_seq gap", test_cp_seq_gap),
    ("D-EV-18: prev_cp_hash break", test_prev_cp_hash_break),
    ("D-EV-18: cp_hash tamper", test_cp_hash_tamper),
    ("D-EV-18: count non-monotonic", test_count_non_monotonic),
    ("D-EV-18: non-latest cp head mismatch", test_non_latest_checkpoint_head_mismatch),
    ("D-EV-18: writer-open sig/hash verify", test_writer_open_verification),
    ("D-EV-18: missing chain/checkpoint file", test_missing_files),
    ("D-EV-18: duplicate JSON key", test_duplicate_json_key),
    ("D-EV-18: non-canonical whitespace", test_non_canonical_whitespace),
    ("D-EV-18: concurrent writers flock", test_concurrent_writers),
    ("D-EV-18: key separation DER", test_key_separation_der),
    ("D-EV-18: fsync failure poisoning", test_fsync_poisoning),
    ("D-EV-18: concurrent signing integrity", test_concurrent_signing),
]
