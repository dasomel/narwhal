"""Test addendum 6: the private-key probe fails closed when it crashes (D-FU-1, review of #427)."""

import subprocess
import sys
from unittest.mock import patch

try:
    from scripts.evidence.signing import OpensslVerifier, keygen
except ImportError:
    from signing import OpensslVerifier, keygen


def test_d_fu_1_probe_crash_fails_closed(td, epub, cpub, esig, csig):
    """A DER private key must not be accepted when the private-parse probe is killed by a signal."""
    priv, pub, priv_der = td / "fu1c_priv.pem", td / "fu1c_pub.pem", td / "fu1c_priv.der"
    keygen(priv, pub)
    subprocess.run(["openssl", "pkey", "-in", str(priv), "-outform", "DER", "-out", str(priv_der)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    real_run = subprocess.run

    def crashing(cmd, *a, **kw):
        # the private probe is `openssl pkey [-inform DER] -in K -noout` without -pubin
        if cmd[:2] == ["openssl", "pkey"] and "-pubin" not in cmd and "-noout" in cmd:
            return subprocess.CompletedProcess(cmd, -11, b"", b"Segmentation fault")
        return real_run(cmd, *a, **kw)

    OpensslVerifier(pub)  # sanity: a real public key is still accepted
    with patch("subprocess.run", side_effect=crashing):
        try:
            OpensslVerifier(priv_der)
        except ValueError as e:
            assert "probe crashed" in str(e), e
        else:
            raise AssertionError("a crashed private-key probe must not let a private key through")


ADDENDUM6_TESTS = [
    ("D-FU-1: private-key probe crash fails closed", test_d_fu_1_probe_crash_fails_closed),
]
