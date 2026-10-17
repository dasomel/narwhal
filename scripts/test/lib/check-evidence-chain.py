#!/usr/bin/env python3
"""Behavioural verification of agent execution evidence chain (spec-evidence.md).

Covers base tamper/attack scenarios + addendum 1 & 2 scenarios (D-EV-18, 20..26).
Exits non-zero on first failure, prints PASS/FAIL lines.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.evidence.canonical import canonical  # noqa: E402
from scripts.evidence.chain import (  # noqa: E402
    ENTRY_PREFIX, GENESIS_PREV_HASH, ChainIntegrityError, ChainWriter,
    append_checkpoint, build_checkpoint, read_chain,
)
from scripts.evidence.cli import main as cli_main  # noqa: E402
from scripts.evidence.signing import OpensslSigner, generate_keypair  # noqa: E402
from scripts.evidence.verify import verify  # noqa: E402
from scripts.test.lib.evidence_attacks import ATTACK_TESTS  # noqa: E402
from scripts.test.lib.evidence_addendum2 import ADDENDUM2_TESTS  # noqa: E402
from scripts.test.lib.evidence_addendum3 import ADDENDUM3_TESTS  # noqa: E402
from scripts.test.lib.evidence_addendum4 import ADDENDUM4_TESTS  # noqa: E402



@contextmanager
def env_ctx():
    td = Path(tempfile.mkdtemp(prefix="ev-test-"))
    epriv, epub, cpriv, cpub = td / "e.key", td / "e.pub", td / "c.key", td / "c.pub"
    generate_keypair(epriv, epub)
    generate_keypair(cpriv, cpub)
    try:
        yield td, epub, cpub, OpensslSigner(epriv), OpensslSigner(cpriv)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def run_test(name: str, fn) -> None:
    try:
        fn()
        print(f"PASS: {name}")
    except Exception as ex:
        print(f"FAIL: {name}: {ex}")
        sys.exit(1)


def test_happy_path() -> None:
    with env_ctx() as (td, epub, cpub, esig, csig):
        chain_p, cp_p = td / "chain.jsonl", td / "cp.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        w.append_intent("g1", "inv-1")
        w.append_result(0, "ok")
        count, head = w.get_head()
        append_checkpoint(cp_p, build_checkpoint(0, "inst-1", 1, count, head, GENESIS_PREV_HASH, csig))
        rep = verify(chain_p, epub, cp_p, cpub)
        assert rep.ok and rep.anchored_count == 2 and rep.unanchored_tail == 0


def test_field_tamper() -> None:
    with env_ctx() as (td, epub, cpub, esig, _):
        chain_p = td / "chain.jsonl"
        ChainWriter(chain_p, "inst-1", 1, esig, epub).append_intent("g1", "inv-1")
        lines = chain_p.read_text(encoding="utf-8").splitlines()
        chain_p.write_text(lines[0].replace('"inv-1"', '"inv-tampered"') + "\n", encoding="utf-8")
        rep = verify(chain_p, epub, None, cpub, allow_no_checkpoints=True)
        assert not rep.ok and any("entry_hash mismatch" in e for e in rep.errors)


def test_entry_removed_gap() -> None:
    with env_ctx() as (td, epub, cpub, esig, _):
        chain_p = td / "chain.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        w.append_intent("g1", "inv-1"); w.append_result(0, "ok"); w.append_intent("g2", "inv-2")
        lines = chain_p.read_text(encoding="utf-8").splitlines()
        chain_p.write_text(lines[0] + "\n" + lines[2] + "\n", encoding="utf-8")
        rep = verify(chain_p, epub, None, cpub, allow_no_checkpoints=True)
        assert not rep.ok and any("seq error" in e or "prev_hash mismatch" in e for e in rep.errors)


def test_reorder() -> None:
    with env_ctx() as (td, epub, cpub, esig, _):
        chain_p = td / "chain.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        w.append_intent("g1", "inv-1"); w.append_result(0, "ok")
        lines = chain_p.read_text(encoding="utf-8").splitlines()
        chain_p.write_text(lines[1] + "\n" + lines[0] + "\n", encoding="utf-8")
        rep = verify(chain_p, epub, None, cpub, allow_no_checkpoints=True)
        assert not rep.ok and any("seq error" in e or "prev_hash mismatch" in e for e in rep.errors)


def test_tail_truncation() -> None:
    with env_ctx() as (td, epub, cpub, esig, csig):
        chain_p, cp_p = td / "chain.jsonl", td / "cp.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        w.append_intent("g1", "inv-1"); w.append_result(0, "ok")
        count, head = w.get_head()
        append_checkpoint(cp_p, build_checkpoint(0, "inst-1", 1, count, head, GENESIS_PREV_HASH, csig))
        chain_p.write_text(chain_p.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
        rep_with = verify(chain_p, epub, cp_p, cpub)
        assert not rep_with.ok and any("tail truncation" in e.lower() for e in rep_with.errors)
        rep_no_cp = verify(chain_p, epub, None, cpub, allow_no_checkpoints=True)
        assert rep_no_cp.ok and rep_no_cp.unanchored_tail == 1 and rep_no_cp.anchored_count == 0


def test_torn_last_line() -> None:

    with env_ctx() as (td, epub, cpub, esig, _):
        chain_p = td / "chain.jsonl"
        ChainWriter(chain_p, "inst-1", 1, esig, epub).append_intent("g1", "inv-1")
        with open(chain_p, "ab") as f:
            f.write(b'{"v":1,"seq":1,"ts_m')
        rep = verify(chain_p, epub, None, cpub, allow_no_checkpoints=True)
        assert not rep.ok and any("failed to read chain" in e or "unparseable" in e for e in rep.errors)
        try:
            ChainWriter(chain_p, "inst-1", 1, esig, epub)
            raise AssertionError("Writer should refuse torn tail")
        except (ChainIntegrityError, ValueError):
            pass


def test_forged_chain_bad_sig() -> None:
    with env_ctx() as (td, epub, cpub, esig, _):
        chain_p = td / "chain.jsonl"
        ChainWriter(chain_p, "inst-1", 1, esig, epub).append_intent("g1", "inv-1")
        entries = read_chain(chain_p)
        entries[0]["grant_id"] = "forged-g"
        body = {k: v for k, v in entries[0].items() if k not in ("entry_hash", "sig")}
        entries[0]["entry_hash"] = hashlib.sha256(ENTRY_PREFIX + canonical(body)).hexdigest()
        rep = verify(entries, epub, None, cpub, allow_no_checkpoints=True)
        assert not rep.ok and any("signature verification failed" in e for e in rep.errors)


def test_verify_without_key() -> None:
    with env_ctx() as (_, epub, cpub, _, _):
        for entry_k, cp_k in [(None, cpub), (epub, None)]:
            try:
                verify([], entry_k, None, cp_k, allow_no_checkpoints=True)  # type: ignore
                raise AssertionError("Expected ValueError for missing public key")
            except (TypeError, ValueError):
                pass


def test_identical_public_keys() -> None:
    with env_ctx() as (td, epub, _, esig, _):
        chain_p = td / "chain.jsonl"
        ChainWriter(chain_p, "inst-1", 1, esig, epub).append_intent("g1", "inv-1")
        rep = verify(chain_p, epub, None, epub, allow_no_checkpoints=True)
        assert not rep.ok and any("key separation" in e for e in rep.errors)


def test_rollback_fork() -> None:
    with env_ctx() as (td, epub, cpub, esig, csig):
        chain_p, cp_p, fork_p = td / "chain.jsonl", td / "cp.jsonl", td / "fork.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        w.append_intent("g1", "inv-1")
        w.append_result(0, "ok")
        count, head = w.get_head()
        append_checkpoint(cp_p, build_checkpoint(0, "inst-1", 1, count, head, GENESIS_PREV_HASH, csig))
        fork_p.write_text(chain_p.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
        ChainWriter(fork_p, "inst-1", 1, esig, epub).append_result(0, "aborted")
        rep = verify(fork_p, epub, cp_p, cpub)
        assert not rep.ok and any("head_hash mismatch" in e for e in rep.errors)


def test_replay_second_intent_same_grant() -> None:
    with env_ctx() as (td, epub, _, esig, _):
        chain_p = td / "chain.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        w.append_intent("g-replay", "inv-1")
        try:
            w.append_intent("g-replay", "inv-2")
            raise AssertionError("Expected spend-once refusal")
        except ValueError:
            pass
        w2 = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        try:
            w2.append_intent("g-replay", "inv-3")
            raise AssertionError("Expected spend-once refusal on reopened writer")
        except ValueError:
            pass


def test_result_wrong_grant_or_unknown_intent() -> None:
    with env_ctx() as (td, epub, cpub, esig, _):
        chain_p = td / "chain.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        w.append_intent("g1", "inv-1")
        try:
            w.append_result(999, "ok")
            raise AssertionError("Expected unknown intent_seq refusal")
        except ValueError:
            pass
        try:
            w.append_result(0, "ok", grant_id="wrong-g")
            raise AssertionError("Expected grant mismatch refusal")
        except ValueError:
            pass
        w.append_result(0, "ok")
        lines = chain_p.read_text(encoding="utf-8").splitlines()
        res_rec = json.loads(lines[1])
        res_rec["grant_id"] = "mismatched-grant"
        body = {k: v for k, v in res_rec.items() if k not in ("entry_hash", "sig")}
        res_rec["entry_hash"] = hashlib.sha256(ENTRY_PREFIX + canonical(body)).hexdigest()
        res_rec["sig"] = esig.sign(ENTRY_PREFIX + res_rec["entry_hash"].encode("ascii")).hex()
        chain_p.write_text(lines[0] + "\n" + canonical(res_rec).decode("utf-8") + "\n", encoding="utf-8")
        rep = verify(chain_p, epub, None, cpub, allow_no_checkpoints=True)
        assert not rep.ok and any("grant_id mismatch" in e for e in rep.errors)


def test_intent_without_result_unresolved() -> None:
    with env_ctx() as (td, epub, cpub, esig, _):
        chain_p = td / "chain.jsonl"
        ChainWriter(chain_p, "inst-1", 1, esig, epub).append_intent("g-pending", "inv-pending")
        rep = verify(chain_p, epub, None, cpub, allow_no_checkpoints=True)
        assert rep.ok and any("consumed, outcome unknown" in f for f in rep.findings)
        rc0 = cli_main(["verify", "--chain", str(chain_p), "--entry-pub", str(epub), "--cp-pub", str(cpub), "--no-checkpoints"])
        rc2 = cli_main(["verify", "--chain", str(chain_p), "--entry-pub", str(epub), "--cp-pub", str(cpub), "--no-checkpoints", "--fail-on-unresolved"])
        assert rc0 == 0 and rc2 == 2


def test_float_rejected() -> None:
    for val in [1.23, float("nan"), float("inf")]:
        try:
            canonical({"val": val})
            raise AssertionError(f"Expected float {val} to be rejected")
        except ValueError:
            pass


def test_checkpoint_signed_by_entry_key() -> None:
    with env_ctx() as (td, epub, cpub, esig, _):
        chain_p, cp_p = td / "chain.jsonl", td / "cp.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        w.append_intent("g1", "inv-1")
        count, head = w.get_head()
        append_checkpoint(cp_p, build_checkpoint(0, "inst-1", 1, count, head, GENESIS_PREV_HASH, esig))
        rep = verify(chain_p, epub, cp_p, cpub)
        assert not rep.ok and any("checkpoint 0 signature verification failed" in e for e in rep.errors)


def test_append_failure_readonly() -> None:
    with env_ctx() as (td, epub, _, esig, _):
        chain_p = td / "chain.jsonl"
        w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
        w.append_intent("g1", "inv-1")
        with patch("os.write", side_effect=OSError("permission denied")):
            try:
                w.append_intent("g-fail", "inv-fail")
                raise AssertionError("Expected OSError on append failure")
            except OSError:
                pass
        assert "g-fail" not in w.consumed_grants
        assert len(read_chain(chain_p)) == 1


def main() -> None:
    base_tests = [
        ("happy path", test_happy_path),
        ("field tamper", test_field_tamper),
        ("entry removed (gap)", test_entry_removed_gap),
        ("reorder", test_reorder),
        ("tail truncation with/without checkpoint", test_tail_truncation),
        ("torn last line", test_torn_last_line),
        ("forged chain bad sig", test_forged_chain_bad_sig),
        ("verify without key", test_verify_without_key),
        ("identical public keys", test_identical_public_keys),
        ("chain rollback/fork", test_rollback_fork),
        ("replay second intent same grant_id", test_replay_second_intent_same_grant),
        ("result wrong grant or unknown intent_seq", test_result_wrong_grant_or_unknown_intent),
        ("intent without result (unresolved)", test_intent_without_result_unresolved),
        ("float in record rejected", test_float_rejected),
        ("checkpoint signed by entry key", test_checkpoint_signed_by_entry_key),
        ("append failure read-only file", test_append_failure_readonly),
    ]
    for name, fn in base_tests:
        run_test(name, fn)

    for suite in (ATTACK_TESTS, ADDENDUM2_TESTS, ADDENDUM3_TESTS, ADDENDUM4_TESTS):
        for name, fn in suite:
            with env_ctx() as (td, epub, cpub, esig, csig):
                run_test(name, lambda f=fn, t=td, ep=epub, cp=cpub, es=esig, cs=csig: f(t, ep, cp, es, cs))

    total = len(base_tests) + len(ATTACK_TESTS) + len(ADDENDUM2_TESTS) + len(ADDENDUM3_TESTS) + len(ADDENDUM4_TESTS)
    print(f"All {total} tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()

