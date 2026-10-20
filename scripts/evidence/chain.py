"""Evidence chain building, durability, concurrency, and poisoning guards (D-EV-2..8, 15..17, 21..23, 28..32)."""

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import time
from typing import List, Optional, Set, Tuple, Union

try:
    from .canonical import canonical, parse_strict_json_line
    from .chain_verify import ENTRY_PREFIX, process_entry_kind, verify_entry_integrity
    from .checkpoint import _fsync_dir, append_checkpoint, build_checkpoint, read_checkpoints
    from .reader import ChainIntegrityError, read_chain
    from .signing import OpensslSigner, OpensslVerifier
except ImportError:
    from canonical import canonical, parse_strict_json_line
    from chain_verify import ENTRY_PREFIX, process_entry_kind, verify_entry_integrity
    from checkpoint import _fsync_dir, append_checkpoint, build_checkpoint, read_checkpoints
    from reader import ChainIntegrityError, read_chain
    from signing import OpensslSigner, OpensslVerifier

GENESIS_PREV_HASH: str = "0" * 64
IntegrityError = ChainIntegrityError


class DoubleSpendError(ValueError):
    """Raised when a grant_id is reused in an intent (D-EV-8)."""


class ChainWriter:
    """Appends intent and result records with spend-once enforcement, flock, and poisoning (D-EV-15,16,21,28..32)."""

    def __init__(
        self, path: Union[str, Path], instance_id: str, epoch: int,
        signer: OpensslSigner, entry_pub: Union[str, Path, OpensslVerifier],
    ):

        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError(f"instance_id must be non-empty str: {instance_id!r}")
        if type(epoch) is not int or epoch < 0:
            raise ValueError(f"epoch must be non-negative int: {epoch!r}")
        self.path = Path(path)
        self.instance_id = instance_id
        self.epoch = epoch
        self.signer = signer
        self.verifier = entry_pub if isinstance(entry_pub, OpensslVerifier) else OpensslVerifier(entry_pub)
        self.consumed_grants: Set[str] = set()
        self.intents_by_seq: dict[int, dict] = {}
        self.results_by_intent: dict[int, dict] = {}
        self.seq: int = 0
        self.last_hash: str = GENESIS_PREV_HASH
        self._poisoned: bool = False
        self._poison_reason: Optional[str] = None

        self._cached_offset: int = 0
        self._cached_count: int = 0
        self._cached_head_hash: str = GENESIS_PREV_HASH
        self._cached_epoch: int = -1
        self._cached_last_entry_offset: int = 0

        self.path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.path.exists()
        self._fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        if is_new:
            _fsync_dir(self.path.parent)
        with self._lock() as fd:
            if os.fstat(fd).st_size > 0:
                self._full_load_and_verify()

    def close(self) -> None:
        if getattr(self, "_fd", None) is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    __del__ = close


    @contextmanager
    def _lock(self):
        """Exclusive advisory flock on chain file's open fd with rename-swap check (D-EV-21, 31)."""
        self._check_poisoned()
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        try:
            st_fd = os.fstat(self._fd)
            try:
                st_p = os.stat(self.path)
            except OSError as e:
                self._poisoned = True
                self._poison_reason = f"chain file inaccessible: {e} (D-EV-31)"
                raise ChainIntegrityError(self._poison_reason)
            if st_fd.st_ino != st_p.st_ino or st_fd.st_dev != st_p.st_dev:
                self._poisoned = True
                self._poison_reason = "file replacement detected (rename swap) (D-EV-31)"
                raise ChainIntegrityError(self._poison_reason)
            yield self._fd
        finally:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass

    def _check_poisoned(self) -> None:
        if self._poisoned:
            raise ChainIntegrityError(
                f"ChainWriter is poisoned due to previous write/fsync failure: {self._poison_reason}"
            )

    def _full_load_and_verify(self) -> None:
        """Full chain load and verification on open (D-EV-7, 22, 28)."""
        entries = read_chain(self._fd)
        prev_h = GENESIS_PREV_HASH
        last_epoch = -1
        consumed: Set[str] = set()
        intents: dict[int, dict] = {}
        results: dict[int, dict] = {}

        last_entry_offset, current_offset = 0, 0
        os.lseek(self._fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(self._fd), "rb") as f:
            for i, line in enumerate(f):
                if i < len(entries):
                    last_entry_offset = current_offset
                    current_offset += len(line)

        for i, entry in enumerate(entries):
            calc_hash, ep = verify_entry_integrity(entry, i, self.instance_id, last_epoch, prev_h, self.verifier)
            process_entry_kind(entry, i, consumed, intents, results)
            last_epoch, prev_h = ep, calc_hash

        if entries and self.epoch < last_epoch:
            raise ChainIntegrityError(f"writer epoch {self.epoch} < chain last epoch {last_epoch} (D-EV-15)")

        self.consumed_grants, self.intents_by_seq, self.results_by_intent = consumed, intents, results
        self.seq, self.last_hash = len(entries), prev_h
        self._cached_count, self._cached_head_hash, self._cached_epoch = len(entries), prev_h, last_epoch
        self._cached_offset, self._cached_last_entry_offset = os.fstat(self._fd).st_size, last_entry_offset

    def _sync_under_lock(self, fd: int) -> None:
        """Incremental verification under held lock with poisoning on shrink/mismatch (D-EV-22, 28)."""
        st = os.fstat(fd)
        current_size = st.st_size

        if current_size < self._cached_offset:
            self._poisoned = True
            self._poison_reason = f"chain shrunk from {self._cached_offset} to {current_size} bytes (D-EV-28)"
            raise ChainIntegrityError(self._poison_reason)

        if self._cached_count > 0:
            os.lseek(fd, self._cached_last_entry_offset, os.SEEK_SET)
            last_bytes = os.read(fd, self._cached_offset - self._cached_last_entry_offset)
            try:
                last_rec = parse_strict_json_line(last_bytes, self._cached_count)
                last_body = {k: v for k, v in last_rec.items() if k not in ("entry_hash", "sig")}
                if hashlib.sha256(ENTRY_PREFIX + canonical(last_body)).hexdigest() != self._cached_head_hash:
                    self._poisoned = True
                    self._poison_reason = "cached head hash mismatch (D-EV-28)"
                    raise ChainIntegrityError(self._poison_reason)
            except Exception as e:
                self._poisoned = True
                self._poison_reason = f"head re-verify failed: {e} (D-EV-28)"
                raise ChainIntegrityError(self._poison_reason)

        if current_size > self._cached_offset:
            os.lseek(fd, self._cached_offset, os.SEEK_SET)
            new_data = os.read(fd, current_size - self._cached_offset)
            offset = self._cached_offset
            for line_idx, raw_line in enumerate(new_data.splitlines(keepends=True), start=self._cached_count + 1):
                try:
                    rec = parse_strict_json_line(raw_line, line_idx)
                except Exception as e:
                    self._poisoned = True
                    self._poison_reason = f"unparseable new line {line_idx}: {e}"
                    raise ChainIntegrityError(self._poison_reason)
                calc_hash, ep = verify_entry_integrity(
                    rec, self.seq, self.instance_id, self._cached_epoch, self.last_hash, self.verifier
                )
                process_entry_kind(rec, self.seq, self.consumed_grants, self.intents_by_seq, self.results_by_intent)
                self._cached_last_entry_offset = offset
                offset += len(raw_line)
                self.seq += 1
                self.last_hash = calc_hash
                self._cached_epoch = ep

            self._cached_offset = current_size
            self._cached_count = self.seq
            self._cached_head_hash = self.last_hash

    def get_head(self) -> Tuple[int, str]:
        """Return (count, head_hash) for countersigning checkpoints (D-EV-5)."""
        self._check_poisoned()
        with self._lock() as fd:
            self._sync_under_lock(fd)
            return self.seq, self.last_hash

    def _sign_and_append(self, fd: int, rec: dict) -> dict:
        entry_hash = hashlib.sha256(ENTRY_PREFIX + canonical(rec)).hexdigest()
        rec["entry_hash"] = entry_hash
        rec["sig"] = self.signer.sign(ENTRY_PREFIX + entry_hash.encode("ascii")).hex()
        line_bytes = canonical(rec) + b"\n"

        is_new = os.fstat(fd).st_size == 0
        try:
            os.lseek(fd, 0, os.SEEK_END)
            written = 0
            while written < len(line_bytes):
                n = os.write(fd, memoryview(line_bytes)[written:])
                if n == 0:
                    raise OSError("os.write returned 0 bytes")
                written += n
            os.fsync(fd)
            if is_new:
                _fsync_dir(self.path.parent)
        except Exception as e:
            self._poisoned = True
            self._poison_reason = str(e)
            raise

        self._cached_last_entry_offset = self._cached_offset
        self._cached_offset += len(line_bytes)
        self._cached_count += 1
        self._cached_head_hash, self._cached_epoch = rec["entry_hash"], rec["epoch"]
        self.seq += 1
        self.last_hash = rec["entry_hash"]
        return rec

    def _validate_writer_state(self) -> None:
        self._check_poisoned()
        if not isinstance(self.instance_id, str) or not self.instance_id:
            raise ValueError(f"instance_id must be non-empty str: {self.instance_id!r}")
        if type(self.epoch) is not int or self.epoch < 0:
            raise ValueError(f"epoch must be non-negative int: {self.epoch!r}")
        if self._cached_epoch >= 0 and self.epoch < self._cached_epoch:
            raise ValueError(f"writer epoch {self.epoch} < chain last epoch {self._cached_epoch} (D-EV-29)")

    def append_intent(self, grant_id: str, invocation_hash: str) -> dict:
        """Append an intent record to the chain under flock (D-EV-2, 7, 8, 15, 16, 21, 28, 29, 32)."""
        self._validate_writer_state()
        if not isinstance(grant_id, str) or not grant_id:
            raise ValueError(f"grant_id must be non-empty str: {grant_id!r}")
        if not isinstance(invocation_hash, str) or not invocation_hash:
            raise ValueError(f"invocation_hash must be non-empty str: {invocation_hash!r}")

        with self._lock() as fd:
            self._sync_under_lock(fd)

            if grant_id in self.consumed_grants:
                raise DoubleSpendError(f"grant_id already consumed: {grant_id}")

            rec = {
                "v": 1, "seq": self.seq, "ts_ms": int(time.time() * 1000), "kind": "intent",
                "instance_id": self.instance_id, "epoch": self.epoch, "grant_id": grant_id,
                "invocation_hash": invocation_hash, "prev_hash": self.last_hash,
            }
            self._sign_and_append(fd, rec)
            self.consumed_grants.add(grant_id)
            self.intents_by_seq[rec["seq"]] = rec
            return rec

    def append_result(self, intent_seq: int, outcome: str, grant_id: Optional[str] = None) -> dict:

        """Append a result record matching an earlier intent under flock (D-EV-2, 15, 16, 21, 28, 29, 32)."""
        self._validate_writer_state()
        if type(intent_seq) is not int or intent_seq < 0:
            raise ValueError(f"intent_seq must be non-negative int: {intent_seq!r}")
        if outcome not in ("ok", "error", "aborted"):
            raise ValueError(f"invalid outcome: {outcome!r}")
        if grant_id is not None and (not isinstance(grant_id, str) or not grant_id):
            raise ValueError(f"grant_id must be non-empty str: {grant_id!r}")

        with self._lock() as fd:
            self._sync_under_lock(fd)

            if intent_seq not in self.intents_by_seq:
                raise ValueError(f"unknown intent_seq: {intent_seq}")
            if intent_seq in self.results_by_intent:
                raise ValueError(f"intent_seq {intent_seq} already has a result")
            intent = self.intents_by_seq[intent_seq]
            if grant_id is not None and grant_id != intent.get("grant_id"):
                raise ValueError(f"result grant_id mismatch: {grant_id} != {intent.get('grant_id')}")

            rec = {
                "v": 1, "seq": self.seq, "ts_ms": int(time.time() * 1000), "kind": "result",
                "instance_id": self.instance_id, "epoch": self.epoch, "grant_id": intent["grant_id"],
                "invocation_hash": intent["invocation_hash"], "intent_seq": intent_seq,
                "outcome": outcome, "prev_hash": self.last_hash,
            }
            self._sign_and_append(fd, rec)
            self.results_by_intent[intent_seq] = rec
            return rec
