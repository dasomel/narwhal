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


def read_checkpoints(path: Union[str, Path]) -> List[dict]:
    """Read checkpoints from an export store JSON lines file (D-EV-11, D-EV-17)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"checkpoint file not found: {path}")
    if not p.is_file():
        from .chain import ChainIntegrityError
        raise ChainIntegrityError(f"checkpoint path is not a regular file: {path}")

    cps: List[dict] = []
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
