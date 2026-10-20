"""Signing utilities using OpenSSL Ed25519 subprocesses (D-EV-1, D-EV-10, D-EV-14)."""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Union


def keygen(priv_path: Union[str, Path], pub_path: Union[str, Path]) -> None:
    """Generate an Ed25519 private key and corresponding public key."""
    priv = Path(priv_path)
    pub = Path(pub_path)
    priv.parent.mkdir(parents=True, exist_ok=True)
    pub.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(priv)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(priv), "-pubout", "-out", str(pub)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


generate_keypair = keygen


@contextmanager
def _secure_tempdir():
    """Create a temporary directory with 0700 permissions and ensure cleanup (D-EV-10)."""
    td = tempfile.mkdtemp(prefix="ev-sec-")
    os.chmod(td, 0o700)
    try:
        yield td
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _write_0600_file(td: str, content: bytes, suffix: str = ".tmp") -> str:
    """Write bytes to a 0600 temp file inside the given directory (D-EV-10)."""
    fd, path = tempfile.mkstemp(dir=td, suffix=suffix)
    os.chmod(path, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return path


def _assert_is_public_key_file(p: Path) -> None:
    """Ensure path is a valid public key file and reject private keys in PEM or DER form (D-EV-41, D-FU-1).

    Implementation: a file is acceptable only if `openssl pkey -pubin -in K -noout`
    (and -inform DER for non-PEM) succeeds AND `openssl pkey -in K -noout`
    (private parse, both informs) FAILS.
    """
    if not p.exists():
        raise FileNotFoundError(f"public key not found: {p}")
    try:
        content = p.read_bytes()
    except OSError as e:
        raise ValueError(f"cannot read public key file: {e}")
    if b"PRIVATE KEY" in content:
        raise ValueError(f"private key cannot be used as public key (D-EV-41, D-FU-1): {p}")

    for inform in ([], ["-inform", "DER"]):
        cmd_priv = ["openssl", "pkey"] + inform + ["-in", str(p), "-noout"]
        res_priv = subprocess.run(cmd_priv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res_priv.returncode == 0:
            raise ValueError(f"private key cannot be used as public key (D-EV-41, D-FU-1): {p}")
        if res_priv.returncode < 0:
            # killed by a signal: the probe did not answer, so fail closed instead of treating it as "not private"
            raise ValueError(f"private-key probe crashed (signal {-res_priv.returncode}); refusing {p} (D-FU-1)")

    res_pub = subprocess.run(
        ["openssl", "pkey", "-pubin", "-in", str(p), "-noout"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if res_pub.returncode != 0:
        res_pub_der = subprocess.run(
            ["openssl", "pkey", "-pubin", "-inform", "DER", "-in", str(p), "-noout"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if res_pub_der.returncode != 0:
            raise ValueError(f"invalid public key (D-EV-41, D-FU-1): {res_pub.stderr.decode().strip()}")


def get_canonical_der(pub: Union[str, Path, bytes]) -> bytes:
    """Extract canonical DER representation of an Ed25519 public key (D-EV-14, D-FU-1)."""
    if isinstance(pub, (str, Path)):
        p = Path(pub)
        _assert_is_public_key_file(p)
        cmd = ["openssl", "pkey", "-pubin", "-in", str(p), "-outform", "DER"]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            cmd = ["openssl", "pkey", "-pubin", "-inform", "DER", "-in", str(p), "-outform", "DER"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise ValueError(f"failed to parse public key as DER: {res.stderr.decode().strip()}")
        return res.stdout

    data = pub if isinstance(pub, bytes) else str(pub).encode("utf-8")
    with _secure_tempdir() as td:
        tpath = _write_0600_file(td, data, suffix=".pub")
        _assert_is_public_key_file(Path(tpath))
        cmd = ["openssl", "pkey", "-pubin", "-in", tpath, "-outform", "DER"]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            cmd = ["openssl", "pkey", "-pubin", "-inform", "DER", "-in", tpath, "-outform", "DER"]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise ValueError(f"failed to parse public key as DER: {res.stderr.decode().strip()}")
        return res.stdout


def get_canonical_der_from_priv(priv: Union[str, Path]) -> bytes:
    """Extract canonical DER public key representation from an Ed25519 private key (D-EV-33)."""
    p = Path(priv)
    if not p.exists():
        raise FileNotFoundError(f"private key not found: {p}")
    cmd = ["openssl", "pkey", "-in", str(p), "-pubout", "-outform", "DER"]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        raise ValueError(f"failed to extract public key DER from private key: {res.stderr.decode().strip()}")
    return res.stdout


def priv_matches_pub(priv_path: Union[str, Path], pub_path: Union[str, Path, bytes, "OpensslVerifier"]) -> bool:
    """Check if a private key matches a given public key by canonical DER comparison (D-EV-33)."""
    der_priv = get_canonical_der_from_priv(priv_path)
    if isinstance(pub_path, OpensslVerifier):
        pub_path = pub_path.pub_path
    der_pub = get_canonical_der(pub_path)
    return der_priv == der_pub


def keys_are_identical(
    pub_a: Union[str, Path, bytes, "OpensslVerifier"],
    pub_b: Union[str, Path, bytes, "OpensslVerifier"],
) -> bool:
    """Check if two public keys represent the same key by canonical DER comparison (D-EV-14, D-EV-25).

    FAILS CLOSED: Any parse or conversion error raises an exception; never returns False.
    """
    if pub_a is None or pub_b is None:
        raise ValueError("keys_are_identical: keys must not be None")
    if isinstance(pub_a, OpensslVerifier):
        pub_a = pub_a.pub_path
    if isinstance(pub_b, OpensslVerifier):
        pub_b = pub_b.pub_path
    der_a = get_canonical_der(pub_a)
    der_b = get_canonical_der(pub_b)
    return der_a == der_b




class OpensslSigner:
    """Ed25519 signer via openssl pkeyutl -sign -rawin -inkey KEY (D-EV-1, D-EV-10)."""

    def __init__(self, key_path: Union[str, Path]):
        self.key_path = Path(key_path)
        if not self.key_path.exists():
            raise FileNotFoundError(f"private key not found: {self.key_path}")

    def sign(self, msg: bytes) -> bytes:
        """Sign raw message bytes and return signature bytes.

        NEVER passes message via stdin / /dev/stdin to avoid pipe races / empty signs (D-EV-10).
        """
        with _secure_tempdir() as td:
            in_file = _write_0600_file(td, msg, suffix=".msg")
            cmd = [
                "openssl",
                "pkeyutl",
                "-sign",
                "-rawin",
                "-inkey",
                str(self.key_path),
                "-in",
                in_file,
            ]
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            return res.stdout


def extract_pubkey_from_priv(priv_path: Union[str, Path], pub_path: Union[str, Path]) -> None:
    """Extract public key PEM from Ed25519 private key (D-EV-36, D-EV-37)."""
    p = Path(priv_path)
    if not p.exists():
        raise FileNotFoundError(f"private key not found: {p}")
    out = Path(pub_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["openssl", "pkey", "-in", str(p), "-pubout", "-out", str(out)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


class OpensslVerifier:
    """Ed25519 verifier via openssl pkeyutl -verify -pubin -inkey PUB -rawin -sigfile F (D-EV-1, D-EV-10, D-FU-1)."""

    def __init__(self, pub_path: Union[str, Path]):
        self.pub_path = Path(pub_path)
        _assert_is_public_key_file(self.pub_path)


    def verify(self, msg: bytes, sig: bytes) -> bool:
        """Verify signature bytes over raw message bytes using temp files (D-EV-10)."""
        with _secure_tempdir() as td:
            in_file = _write_0600_file(td, msg, suffix=".msg")
            sig_file = _write_0600_file(td, sig, suffix=".sig")
            cmd = [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(self.pub_path),
                "-rawin",
                "-sigfile",
                sig_file,
                "-in",
                in_file,
            ]
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if res.returncode != 0:
                cmd_der = [
                    "openssl",
                    "pkeyutl",
                    "-verify",
                    "-pubin",
                    "-keyform",
                    "DER",
                    "-inkey",
                    str(self.pub_path),
                    "-rawin",
                    "-sigfile",
                    sig_file,
                    "-in",
                    in_file,
                ]
                res_der = subprocess.run(cmd_der, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                return res_der.returncode == 0
            return True
