"""Test addendum 5: Issue #417 evidence chain follow-ups (D-FU-1..4)."""

import argparse
import fcntl
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

try:
    from scripts.evidence.canonical import canonical
    from scripts.evidence.chain import ChainIntegrityError, ChainWriter, GENESIS_PREV_HASH
    from scripts.evidence.cli import cmd_checkpoint
    from scripts.evidence.signing import (
        OpensslSigner,
        OpensslVerifier,
        get_canonical_der,
        keygen,
        keys_are_identical,
        priv_matches_pub,
    )
except ImportError:
    from canonical import canonical
    from chain import ChainIntegrityError, ChainWriter, GENESIS_PREV_HASH
    from cli import cmd_checkpoint
    from signing import (
        OpensslSigner,
        OpensslVerifier,
        get_canonical_der,
        keygen,
        keys_are_identical,
        priv_matches_pub,
    )


def test_d_fu_1_der_private_key_rejected(td, epub, cpub, esig, csig):
    """D-FU-1: OpensslVerifier and key comparison reject DER/PEM private keys as public keys."""
    priv_pem = td / "fu1_priv.pem"
    priv_pkcs8 = td / "fu1_priv_pkcs8.pem"
    priv_der = td / "fu1_priv.der"
    pub_pem = td / "fu1_pub.pem"
    pub_der = td / "fu1_pub.der"

    keygen(priv_pem, pub_pem)
    subprocess.run(
        ["openssl", "pkcs8", "-topk8", "-nocrypt", "-in", str(priv_pem), "-out", str(priv_pkcs8)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(priv_pem), "-outform", "DER", "-out", str(priv_der)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(priv_pem), "-pubout", "-outform", "DER", "-out", str(pub_der)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # 1. OpensslVerifier must accept both public forms
    v_pem = OpensslVerifier(pub_pem)
    v_der = OpensslVerifier(pub_der)
    test_signer = OpensslSigner(priv_pem)
    sig = test_signer.sign(b"test message")
    assert v_pem.verify(b"test message", sig)
    assert v_der.verify(b"test message", sig)

    # 2. OpensslVerifier must reject all private forms (PEM, PKCS8, DER)
    for bad_priv in (priv_pem, priv_pkcs8, priv_der):
        try:
            OpensslVerifier(bad_priv)
            raise AssertionError(f"OpensslVerifier must reject private key: {bad_priv}")
        except ValueError as e:
            assert "private key cannot be used as public key" in str(e)

    # 3. keys_are_identical: public forms match; private key inputs raise ValueError
    assert keys_are_identical(pub_pem, pub_der)
    assert keys_are_identical(pub_der, pub_pem)
    for bad_priv in (priv_pem, priv_pkcs8, priv_der):
        try:
            keys_are_identical(bad_priv, pub_pem)
            raise AssertionError("keys_are_identical must fail on private key input")
        except ValueError:
            pass
        try:
            keys_are_identical(pub_pem, bad_priv)
            raise AssertionError("keys_are_identical must fail on private key input")
        except ValueError:
            pass

    # 4. priv_matches_pub: private key passed as public parameter raises ValueError
    for bad_priv in (priv_pem, priv_pkcs8, priv_der):
        try:
            priv_matches_pub(priv_pem, bad_priv)
            raise AssertionError("priv_matches_pub must fail when private key is given as public key")
        except ValueError:
            pass


def test_d_fu_2_countersigner_keypair_check(td, epub, cpub, esig, csig):
    """D-FU-2: cmd_checkpoint enforces matching keypair between --key and --cp-pub."""
    chain_p = td / "chain_fu2.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    w.append_result(0, "ok")

    k_a = td / "cp_fu2_a.key"
    pub_a = td / "cp_fu2_a.pub"
    k_b = td / "cp_fu2_b.key"
    pub_b = td / "cp_fu2_b.pub"
    keygen(k_a, pub_a)
    keygen(k_b, pub_b)

    cp_p = td / "cp_fu2.jsonl"

    # Mismatched pair on new file: exits 1 and writes nothing
    args_mismatch = argparse.Namespace(
        chain=str(chain_p),
        cp_file=str(cp_p),
        key=str(k_a),
        entry_pub=str(epub),
        cp_pub=str(pub_b),
        instance_id="inst-1",
        epoch=1,
    )
    rc_mismatch = cmd_checkpoint(args_mismatch)
    assert rc_mismatch == 1
    assert not cp_p.exists() or cp_p.stat().st_size == 0

    # Matching pair: succeeds (exit 0) and writes checkpoint
    args_match = argparse.Namespace(
        chain=str(chain_p),
        cp_file=str(cp_p),
        key=str(k_a),
        entry_pub=str(epub),
        cp_pub=str(pub_a),
        instance_id="inst-1",
        epoch=1,
    )
    rc_match = cmd_checkpoint(args_match)
    assert rc_match == 0
    assert cp_p.exists() and cp_p.stat().st_size > 0
    before_bytes = cp_p.read_bytes()

    # Advance chain
    rec_i2 = w.append_intent("g2", "inv2")
    w.append_result(rec_i2["seq"], "ok")

    # Mismatched pair on existing file: exits 1 and leaves file byte-identical
    rc_mismatch_existing = cmd_checkpoint(args_mismatch)
    assert rc_mismatch_existing == 1
    assert cp_p.read_bytes() == before_bytes


def test_d_fu_3_checkpoint_file_mode_and_rename_swap(td, epub, cpub, esig, csig):
    """D-FU-3: Checkpoint file created with 0600 mode and detects rename swap after lock."""
    chain_p = td / "chain_fu3.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g1", "inv1")
    w.append_result(0, "ok")

    cp_mode_p = td / "cp_mode_0600.jsonl"
    old_umask = os.umask(0)
    try:
        args_mode = argparse.Namespace(
            chain=str(chain_p),
            cp_file=str(cp_mode_p),
            key=str(td / "c.key"),
            entry_pub=str(epub),
            cp_pub=str(cpub),
            instance_id="inst-1",
            epoch=1,
        )
        rc = cmd_checkpoint(args_mode)
        assert rc == 0
        mode = cp_mode_p.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0600 mode with umask 0, got {oct(mode)}"
    finally:
        os.umask(old_umask)

    # Rename-swap test: simulate file replacement between open and flock check
    cp_swap_p = td / "cp_swap.jsonl"
    cp_swap_p.touch()
    swap_away_p = td / "cp_swap_away.jsonl"

    real_flock = fcntl.flock

    def patched_flock(fd, op):
        if op == fcntl.LOCK_EX:
            if cp_swap_p.exists():
                cp_swap_p.rename(swap_away_p)
                cp_swap_p.touch()
        return real_flock(fd, op)

    args_swap = argparse.Namespace(
        chain=str(chain_p),
        cp_file=str(cp_swap_p),
        key=str(td / "c.key"),
        entry_pub=str(epub),
        cp_pub=str(cpub),
        instance_id="inst-1",
        epoch=1,
    )
    with patch("fcntl.flock", side_effect=patched_flock):
        rc_swap = cmd_checkpoint(args_swap)
        assert rc_swap == 1
    assert swap_away_p.stat().st_size == 0
    assert cp_swap_p.stat().st_size == 0


def test_d_fu_4_surviving_mutation_gaps(td, epub, cpub, esig, csig):
    """D-FU-4: Tests that fail when ChainWriter stat poison or CLI rename swap check is disabled."""
    chain_p = td / "chain_fu4.jsonl"
    w = ChainWriter(chain_p, "inst-1", 1, esig, epub)
    w.append_intent("g0", "inv0")
    w.append_result(0, "ok")
    size_before = chain_p.stat().st_size

    # (a) Stat-failure poison in ChainWriter._lock
    real_stat = os.stat

    def bad_stat(path, *args, **kwargs):
        if str(path) == str(chain_p):
            raise OSError("simulated disk error during stat")
        return real_stat(path, *args, **kwargs)

    with patch("os.stat", side_effect=bad_stat):
        try:
            w.append_intent("g1", "inv1")
            raise AssertionError("ChainWriter must raise ChainIntegrityError when stat raises OSError")
        except ChainIntegrityError as e:
            assert "chain file inaccessible" in str(e) or "D-EV-31" in str(e)

    assert w._poisoned is True
    assert chain_p.stat().st_size == size_before

    # Subsequent write fails due to poison
    try:
        w.append_intent("g1", "inv1")
        raise AssertionError("Poisoned ChainWriter must refuse subsequent appends")
    except ChainIntegrityError as e:
        assert "poisoned" in str(e)

    # (b) Checkpoint CLI rename-swap check mutation proof
    cp_p = td / "cp_fu4.jsonl"
    cp_p.touch()

    class MismatchedStat:
        def __init__(self, real):
            self._real = real
            self.st_ino = real.st_ino + 12345
            self.st_dev = real.st_dev

        def __getattr__(self, name):
            return getattr(self._real, name)

    args_swap = argparse.Namespace(
        chain=str(chain_p),
        cp_file=str(cp_p),
        key=str(td / "c.key"),
        entry_pub=str(epub),
        cp_pub=str(cpub),
        instance_id="inst-1",
        epoch=1,
    )

    def stat_with_mismatch(path, *a, **k):
        st = real_stat(path, *a, **k)
        if str(path) == str(cp_p):
            return MismatchedStat(st)
        return st

    with patch("os.stat", side_effect=stat_with_mismatch):
        rc = cmd_checkpoint(args_swap)
        assert rc == 1, "CLI must exit 1 on rename swap mismatch"
    assert cp_p.stat().st_size == 0


ADDENDUM5_TESTS = [
    ("D-FU-1: DER private key rejected as public key", test_d_fu_1_der_private_key_rejected),
    ("D-FU-2: countersigner key pair check", test_d_fu_2_countersigner_keypair_check),
    ("D-FU-3: checkpoint file 0600 mode and rename swap", test_d_fu_3_checkpoint_file_mode_and_rename_swap),
    ("D-FU-4: mutation gaps stat poison and rename swap", test_d_fu_4_surviving_mutation_gaps),
]
