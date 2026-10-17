"""Command line interface for evidence chain management (D-EV-1..8, 11, 13, 36..38)."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

try:
    from .canonical import canonical
    from .chain import GENESIS_PREV_HASH, ChainWriter, read_chain
    from .chain_verify import process_entry_kind, verify_entry_integrity
    from .checkpoint import _fsync_dir, append_checkpoint_fd, build_checkpoint, read_checkpoints, verify_checkpoints_list
    from .signing import (
        OpensslSigner, OpensslVerifier, _secure_tempdir, extract_pubkey_from_priv, keygen, priv_matches_pub
    )
    from .verify import verify
except ImportError:
    from canonical import canonical
    from chain import GENESIS_PREV_HASH, ChainWriter, read_chain
    from chain_verify import process_entry_kind, verify_entry_integrity
    from checkpoint import _fsync_dir, append_checkpoint_fd, build_checkpoint, read_checkpoints, verify_checkpoints_list
    from signing import (
        OpensslSigner, OpensslVerifier, _secure_tempdir, extract_pubkey_from_priv, keygen, priv_matches_pub
    )
    from verify import verify


class EvidenceArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that exits 64 on usage errors (D-EV-38)."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        sys.exit(64)


def cmd_keygen(args: argparse.Namespace) -> int:
    keygen(args.key, args.pub)
    print(f"Generated Ed25519 keypair:\n  Private: {args.key}\n  Public:  {args.pub}")
    return 0


def cmd_intent(args: argparse.Namespace) -> int:
    signer = OpensslSigner(args.key)
    writer = ChainWriter(args.chain, args.instance_id, args.epoch, signer, args.pub)
    rec = writer.append_intent(grant_id=args.grant_id, invocation_hash=args.invocation_hash)
    print(json.dumps(rec, ensure_ascii=False))
    return 0


def cmd_result(args: argparse.Namespace) -> int:
    signer = OpensslSigner(args.key)
    writer = ChainWriter(args.chain, args.instance_id, args.epoch, signer, args.pub)
    rec = writer.append_result(intent_seq=args.intent_seq, outcome=args.outcome)
    print(json.dumps(rec, ensure_ascii=False))
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    chain_p = Path(args.chain)
    if not chain_p.exists():
        print(f"ERROR: chain file not found: {args.chain}", file=sys.stderr)
        return 1

    try:
        if priv_matches_pub(args.key, args.entry_pub):
            print("ERROR: entry public key and checkpoint key are identical (key separation failure)", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"ERROR: key separation check error: {e}", file=sys.stderr)
        return 1

    signer = OpensslSigner(args.key)

    with _secure_tempdir() as td:
        cp_pub_path = getattr(args, "cp_pub", None)
        if not cp_pub_path:
            derived_pub = Path(td) / "cp.pub"
            try:
                extract_pubkey_from_priv(args.key, derived_pub)
                cp_verifier = OpensslVerifier(derived_pub)
            except Exception as e:
                print(f"ERROR: cannot derive checkpoint verifier: {e}", file=sys.stderr)
                return 1
        else:
            try:
                cp_verifier = OpensslVerifier(cp_pub_path)
            except Exception as e:
                print(f"ERROR: cannot load checkpoint verifier: {e}", file=sys.stderr)
                return 1

        # D-EV-36: Lock order is always checkpoint-file first (LOCK_EX), then chain file (LOCK_SH)
        cp_file_p = Path(args.cp_file)
        cp_file_p.parent.mkdir(parents=True, exist_ok=True)
        is_new_cp = not cp_file_p.exists() or cp_file_p.stat().st_size == 0
        cp_fd = os.open(str(cp_file_p), os.O_RDWR | os.O_CREAT, 0o666)
        try:
            fcntl.flock(cp_fd, fcntl.LOCK_EX)
            if is_new_cp:
                _fsync_dir(cp_file_p.parent)

            # 1. Read existing checkpoints from locked fd
            try:
                cps = read_checkpoints(cp_fd)
            except Exception as e:
                print(f"ERROR: cannot read checkpoints: {e}", file=sys.stderr)
                return 1

            # 2. D-EV-37: Verify existing checkpoints before extending
            if cps:
                try:
                    verify_checkpoints_list(cps, cp_verifier)
                except Exception as e:
                    print(f"ERROR: existing checkpoint verification failed: {e}", file=sys.stderr)
                    return 1

            # 3. Read and verify chain under LOCK_SH on chain file (D-EV-24, D-EV-31)
            chain_fd = os.open(str(chain_p), os.O_RDONLY)
            try:
                fcntl.flock(chain_fd, fcntl.LOCK_SH)
                st_fd, st_p = os.fstat(chain_fd), os.stat(chain_p)
                if st_fd.st_ino != st_p.st_ino or st_fd.st_dev != st_p.st_dev:
                    print("ERROR: file replacement detected (rename swap) (D-EV-31)", file=sys.stderr)
                    return 1

                entries = read_chain(chain_fd)
                if entries:
                    ev = OpensslVerifier(args.entry_pub)
                    prev_h, last_ep = GENESIS_PREV_HASH, -1
                    consumed, intents, results = set(), {}, {}
                    for i, entry in enumerate(entries):
                        prev_h, last_ep = verify_entry_integrity(entry, i, entries[0]["instance_id"], last_ep, prev_h, ev)
                        process_entry_kind(entry, i, consumed, intents, results)
                    inst, ep = entries[0]["instance_id"], entries[-1]["epoch"]
                    count, head_hash = len(entries), entries[-1]["entry_hash"]
                else:
                    inst, ep, count, head_hash = args.instance_id, args.epoch, 0, GENESIS_PREV_HASH

                # D-EV-13, D-EV-36: new head must extend previous checkpoint's head
                if cps:
                    prev_cp = cps[-1]
                    prev_count, prev_head = prev_cp.get("count", 0), prev_cp.get("head_hash")
                    if count <= prev_count:
                        print(f"ERROR: new count {count} <= prev checkpoint count {prev_count} (nothing to extend)", file=sys.stderr)
                        return 1
                    if prev_count > 0 and (count < prev_count or entries[prev_count - 1]["entry_hash"] != prev_head):
                        print("ERROR: new head does not extend previous checkpoint head", file=sys.stderr)
                        return 1
                    prev_cp_hash, cp_seq = prev_cp["cp_hash"], len(cps)
                else:
                    prev_cp_hash, cp_seq = GENESIS_PREV_HASH, 0

                cp = build_checkpoint(cp_seq, inst, ep, count, head_hash, prev_cp_hash, signer)
            except Exception as e:
                print(f"ERROR: chain verification failed: {e}", file=sys.stderr)
                return 1
            finally:
                try:
                    fcntl.flock(chain_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(chain_fd)

            # 4. Append to cp_fd and fsync while holding LOCK_EX (D-EV-36)
            append_checkpoint_fd(cp_fd, cp)
        finally:
            try:
                fcntl.flock(cp_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(cp_fd)

    print(json.dumps(cp, ensure_ascii=False))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    if not args.entry_pub or not args.cp_pub:
        print("ERROR: --entry-pub and --cp-pub are required", file=sys.stderr)
        return 1

    # D-EV-11: Validate --checkpoints vs --no-checkpoints
    if args.checkpoints and args.no_checkpoints:
        print("ERROR: cannot specify both --checkpoints and --no-checkpoints", file=sys.stderr)
        return 1
    if not args.checkpoints and not args.no_checkpoints:
        print("ERROR: either --checkpoints or --no-checkpoints must be specified (D-EV-11)", file=sys.stderr)
        return 1

    if not Path(args.chain).exists():
        print(f"ERROR: chain file not found: {args.chain}", file=sys.stderr)
        return 1
    if args.checkpoints and not Path(args.checkpoints).exists():
        print(f"ERROR: checkpoints file not found: {args.checkpoints}", file=sys.stderr)
        return 1

    report = verify(
        entries=args.chain,
        entry_pub=args.entry_pub,
        checkpoints=args.checkpoints,
        cp_pub=args.cp_pub,
        no_checkpoints=args.no_checkpoints,
    )

    if report.errors:
        for err in report.errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1

    if report.findings:
        for f in report.findings:
            print(f"FINDING: {f}")
        if args.fail_on_unresolved:
            return 2

    print(f"OK: anchored={report.anchored_count} unanchored_tail={report.unanchored_tail} findings={len(report.findings)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = EvidenceArgumentParser(description="Narwhal agent execution evidence chain CLI")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_keygen = sub.add_parser("keygen", help="Generate Ed25519 keypair")
    p_keygen.add_argument("--key", required=True)
    p_keygen.add_argument("--pub", required=True)

    def _add_writer_args(p):
        p.add_argument("--chain", required=True)
        p.add_argument("--key", required=True)
        p.add_argument("--pub", required=True)
        p.add_argument("--instance-id", required=True)
        p.add_argument("--epoch", type=int, default=0)

    p_intent = sub.add_parser("intent", help="Append intent record")
    _add_writer_args(p_intent)
    p_intent.add_argument("--grant-id", required=True)
    p_intent.add_argument("--invocation-hash", required=True)

    p_result = sub.add_parser("result", help="Append result record")
    _add_writer_args(p_result)
    p_result.add_argument("--intent-seq", type=int, required=True)
    p_result.add_argument("--outcome", choices=["ok", "error", "aborted"], required=True)

    p_cp = sub.add_parser("checkpoint", help="Append checkpoint record")
    p_cp.add_argument("--chain", required=True)
    p_cp.add_argument("--cp-file", required=True)
    p_cp.add_argument("--key", required=True)
    p_cp.add_argument("--entry-pub", required=True)
    p_cp.add_argument("--cp-pub", default=None)
    p_cp.add_argument("--instance-id", default="inst-1")
    p_cp.add_argument("--epoch", type=int, default=0)

    p_ver = sub.add_parser("verify", help="Verify evidence chain and checkpoints")
    p_ver.add_argument("--chain", required=True)
    p_ver.add_argument("--entry-pub", required=True)
    p_ver.add_argument("--checkpoints", default=None)
    p_ver.add_argument("--no-checkpoints", action="store_true")
    p_ver.add_argument("--cp-pub", required=True)
    p_ver.add_argument("--fail-on-unresolved", action="store_true")

    return parser


def main(argv: list = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "keygen": cmd_keygen,
        "intent": cmd_intent,
        "result": cmd_result,
        "checkpoint": cmd_checkpoint,
        "verify": cmd_verify,
    }
    handler = handlers.get(args.subcommand)
    if not handler:
        parser.print_help()
        return 1
    try:
        return handler(args)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
