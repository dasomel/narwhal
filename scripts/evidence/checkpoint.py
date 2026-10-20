"""Checkpoint building, appending, and reading (D-EV-5, D-EV-7, D-EV-11, D-EV-17)."""

import hashlib
import os
from pathlib import Path
import time
from typing import List, Union

try:
    from .canonical import canonical, parse_strict_json_line
    from .signing import OpensslSigner
except ImportError:
    from canonical import canonical, parse_strict_json_line
    from signing import OpensslSigner

CHECKPOINT_PREFIX: bytes = b"narwhal-evidence-checkpoint-v1\0"


def _fsync_dir(dir_path: Path) -> None:
    try:
        fd = os.open(str(dir_path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _append_line_fsync(path: Path, line: str) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
    if is_new:
        _fsync_dir(parent)


def read_checkpoints(path: Union[str, Path, int]) -> List[dict]:
    """Read checkpoints from an export store JSON lines file or open fd (D-EV-11, D-EV-17, D-EV-36)."""
    if isinstance(path, int):
        cps: List[dict] = []
        os.lseek(path, 0, os.SEEK_SET)
        with os.fdopen(os.dup(path), "rb") as f:
            for line_no, raw_line in enumerate(f, start=1):
                try:
                    rec = parse_strict_json_line(raw_line, line_no)
                except Exception as e:
                    try:
                        from .chain import ChainIntegrityError
                    except ImportError:
                        from chain import ChainIntegrityError
                    raise ChainIntegrityError(f"unparseable checkpoint line {line_no}: {e}")
                cps.append(rec)
        return cps

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"checkpoint file not found: {path}")
    if not p.is_file():
        from .chain import ChainIntegrityError
        raise ChainIntegrityError(f"checkpoint path is not a regular file: {path}")

    cps = []
    with open(p, "rb") as f:
        for line_no, raw_line in enumerate(f, start=1):
            try:
                rec = parse_strict_json_line(raw_line, line_no)
            except Exception as e:
                # Import ChainIntegrityError dynamically if needed
                try:
                    from .chain import ChainIntegrityError
                except ImportError:
                    from chain import ChainIntegrityError
                raise ChainIntegrityError(f"unparseable checkpoint line {line_no}: {e}")
            cps.append(rec)
    return cps


def verify_checkpoints_list(cps: List[dict], cp_verifier) -> None:
    """Verify cryptographic, sequential, and structural integrity of checkpoints (D-EV-37, D-EV-41)."""
    try:
        from .chain import ChainIntegrityError, GENESIS_PREV_HASH
        from .shapes import validate_checkpoint_shape
    except ImportError:
        from chain import ChainIntegrityError, GENESIS_PREV_HASH
        from shapes import validate_checkpoint_shape

    prev_cp_h = GENESIS_PREV_HASH
    last_cp_epoch = -1
    last_cp_count = -1
    for j, cp in enumerate(cps):
        shape_errs = validate_checkpoint_shape(cp, j)
        if shape_errs:
            raise ChainIntegrityError(f"checkpoint {j} shape error: {shape_errs[0]}")
        if cp.get("cp_seq") != j:
            raise ChainIntegrityError(f"checkpoint {j} cp_seq error: expected {j}, got {cp.get('cp_seq')}")
        if cp.get("prev_cp_hash") != prev_cp_h:
            raise ChainIntegrityError(f"checkpoint {j} prev_cp_hash mismatch: expected {prev_cp_h}, got {cp.get('prev_cp_hash')}")
        c_count = cp.get("count", 0)
        if c_count < last_cp_count:
            raise ChainIntegrityError(f"checkpoint {j} count decreased: {c_count} < {last_cp_count}")
        last_cp_count = c_count
        c_epoch = cp.get("epoch", 0)
        if c_epoch < last_cp_epoch:
            raise ChainIntegrityError(f"checkpoint {j} epoch decreased: {c_epoch} < {last_cp_epoch}")
        last_cp_epoch = c_epoch
        if c_count == 0 and cp.get("head_hash") != GENESIS_PREV_HASH:
            raise ChainIntegrityError(f"checkpoint {j} count=0 must have head_hash == GENESIS (D-EV-41)")

        cp_body = {k: v for k, v in cp.items() if k not in ("cp_hash", "sig")}
        calc_cp_hash = hashlib.sha256(CHECKPOINT_PREFIX + canonical(cp_body)).hexdigest()
        if calc_cp_hash != cp.get("cp_hash"):
            raise ChainIntegrityError(f"checkpoint {j} cp_hash mismatch")
        try:
            cp_sig_bytes = bytes.fromhex(cp.get("sig", ""))
        except ValueError:
            raise ChainIntegrityError(f"checkpoint {j} invalid sig hex")
        msg = CHECKPOINT_PREFIX + calc_cp_hash.encode("ascii")
        if not cp_verifier.verify(msg, cp_sig_bytes):
            raise ChainIntegrityError(f"checkpoint {j} signature verification failed")
        prev_cp_h = cp.get("cp_hash")



def build_checkpoint(
    cp_seq: int,
    instance_id: str,
    epoch: int,
    count: int,
    head_hash: str,
    prev_cp_hash: str,
    signer: OpensslSigner,
) -> dict:
    """Build and sign a checkpoint record (D-EV-5)."""
    if not instance_id:
        raise ValueError("checkpoint instance_id cannot be empty")
    rec = {
        "v": 1,
        "kind": "checkpoint",
        "cp_seq": cp_seq,
        "instance_id": instance_id,
        "epoch": epoch,
        "count": count,
        "head_hash": head_hash,
        "ts_ms": int(time.time() * 1000),
        "prev_cp_hash": prev_cp_hash,
    }
    raw_body = canonical(rec)
    cp_hash = hashlib.sha256(CHECKPOINT_PREFIX + raw_body).hexdigest()
    rec["cp_hash"] = cp_hash
    rec["sig"] = signer.sign(CHECKPOINT_PREFIX + cp_hash.encode("ascii")).hex()
    return rec


def append_checkpoint(path: Union[str, Path], cp: dict) -> None:
    """Append a checkpoint to the export store with durability (D-EV-5, D-EV-7)."""
    line = canonical(cp).decode("utf-8")
    _append_line_fsync(Path(path), line)


def append_checkpoint_fd(cp_fd: int, cp: dict) -> None:
    """Append canonical checkpoint record to open fd and fsync (D-EV-32, D-EV-36)."""
    line_bytes = canonical(cp) + b"\n"
    os.lseek(cp_fd, 0, os.SEEK_END)
    written = 0
    while written < len(line_bytes):
        n = os.write(cp_fd, memoryview(line_bytes)[written:])
        if n == 0:
            raise OSError("os.write returned 0 bytes")
        written += n
    os.fsync(cp_fd)

