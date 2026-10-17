"""Command line interface for evidence chain management (D-EV-1..8, 11, 13)."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import sys

try:
    from .chain import GENESIS_PREV_HASH, ChainIntegrityError, ChainWriter, read_chain
    from .chain_verify import process_entry_kind, verify_entry_integrity
    from .checkpoint import append_checkpoint, build_checkpoint, read_checkpoints
    from .signing import OpensslSigner, OpensslVerifier, keygen, priv_matches_pub
    from .verify import verify
except ImportError:
    from chain import GENESIS_PREV_HASH, ChainIntegrityError, ChainWriter, read_chain
    from chain_verify import process_entry_kind, verify_entry_integrity
    from checkpoint import append_checkpoint, build_checkpoint, read_checkpoints
    from signing import OpensslSigner, OpensslVerifier, keygen, priv_matches_pub
    from verify import verify




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
    # D-EV-24, 31: read from locked fd, check st_ino/st_dev, verify and sign under LOCK_SH
    chain_fd = os.open(str(chain_p), os.O_RDONLY)
    try:
        fcntl.flock(chain_fd, fcntl.LOCK_SH)
        try:
            st_fd = os.fstat(chain_fd)
            try:
                st_p = os.stat(chain_p)
            except OSError as e:
                print(f"ERROR: cannot stat chain file: {e}", file=sys.stderr)
                return 1
            if st_fd.st_ino != st_p.st_ino or st_fd.st_dev != st_p.st_dev:
                print("ERROR: file replacement detected (rename swap) (D-EV-31)", file=sys.stderr)
                return 1

            entries = read_chain(chain_fd)
            if entries:
                ev = OpensslVerifier(args.entry_pub)
                prev_h = GENESIS_PREV_HASH
                last_ep = -1
                consumed = set()
                intents, results = {}, {}
                for i, entry in enumerate(entries):
                    prev_h, last_ep = verify_entry_integrity(entry, i, entries[0]["instance_id"], last_ep, prev_h, ev)
                    process_entry_kind(entry, i, consumed, intents, results)
                inst = entries[0]["instance_id"]
                ep = entries[-1]["epoch"]
                count = len(entries)
                head_hash = entries[-1]["entry_hash"]
            else:
                inst = args.instance_id
                ep = args.epoch
                count, head_hash = 0, GENESIS_PREV_HASH

            cps = []
            cp_file_p = Path(args.cp_file)
            if cp_file_p.exists() and cp_file_p.stat().st_size > 0:
                try:
                    cps = read_checkpoints(args.cp_file)
                except Exception as e:
                    print(f"ERROR: cannot read checkpoints: {e}", file=sys.stderr)
                    return 1

            # D-EV-13: new head must extend previous checkpoint's head
            if cps:
                prev_cp = cps[-1]
                prev_count = prev_cp.get("count", 0)
                prev_head = prev_cp.get("head_hash")
                if count < prev_count:
                    print(f"ERROR: new count {count} < prev checkpoint count {prev_count}", file=sys.stderr)
                    return 1
                if prev_count > 0:
                    if count < prev_count or entries[prev_count - 1]["entry_hash"] != prev_head:
                        print("ERROR: new head does not extend previous checkpoint head", file=sys.stderr)
                        return 1
                prev_cp_hash = prev_cp["cp_hash"]
                cp_seq = len(cps)
            else:
                prev_cp_hash = GENESIS_PREV_HASH
                cp_seq = 0

            cp = build_checkpoint(cp_seq, inst, ep, count, head_hash, prev_cp_hash, signer)
        finally:
            try:
                fcntl.flock(chain_fd, fcntl.LOCK_UN)
            except OSError:
                pass
    except Exception as e:
        print(f"ERROR: chain verification failed: {e}", file=sys.stderr)
        return 1
    finally:
        os.close(chain_fd)


    append_checkpoint(args.cp_file, cp)
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
    parser = argparse.ArgumentParser(description="Narwhal agent execution evidence chain CLI")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_keygen = sub.add_parser("keygen", help="Generate Ed25519 keypair")
    p_keygen.add_argument("--key", required=True, help="Private key output path")
    p_keygen.add_argument("--pub", required=True, help="Public key output path")

    p_intent = sub.add_parser("intent", help="Append intent record")
    p_intent.add_argument("--chain", required=True, help="Chain file path")
    p_intent.add_argument("--key", required=True, help="Signer private key path")
    p_intent.add_argument("--pub", required=True, help="Entry public key path")
    p_intent.add_argument("--instance-id", required=True, help="Instance ID")
    p_intent.add_argument("--epoch", type=int, default=0, help="Epoch number")
    p_intent.add_argument("--grant-id", required=True, help="Grant ID")
    p_intent.add_argument("--invocation-hash", required=True, help="Invocation hash")

    p_result = sub.add_parser("result", help="Append result record")
    p_result.add_argument("--chain", required=True, help="Chain file path")
    p_result.add_argument("--key", required=True, help="Signer private key path")
    p_result.add_argument("--pub", required=True, help="Entry public key path")
    p_result.add_argument("--instance-id", required=True, help="Instance ID")
    p_result.add_argument("--epoch", type=int, default=0, help="Epoch number")
    p_result.add_argument("--intent-seq", type=int, required=True, help="Intent seq")
    p_result.add_argument("--outcome", choices=["ok", "error", "aborted"], required=True)

    p_cp = sub.add_parser("checkpoint", help="Append checkpoint record")
    p_cp.add_argument("--chain", required=True, help="Chain file path")
    p_cp.add_argument("--cp-file", required=True, help="Checkpoint export file path")
    p_cp.add_argument("--key", required=True, help="Checkpoint signer private key path")
    p_cp.add_argument("--entry-pub", required=True, help="Entry public key path (D-EV-13)")
    p_cp.add_argument("--instance-id", default="inst-1", help="Instance ID")
    p_cp.add_argument("--epoch", type=int, default=0, help="Epoch number")

    p_ver = sub.add_parser("verify", help="Verify evidence chain and checkpoints")
    p_ver.add_argument("--chain", required=True, help="Chain file path")
    p_ver.add_argument("--entry-pub", required=True, help="Entry public key path")
    p_ver.add_argument("--checkpoints", default=None, help="Checkpoints file path")
    p_ver.add_argument("--no-checkpoints", action="store_true", help="Allow verifying without checkpoints (D-EV-11)")
    p_ver.add_argument("--cp-pub", required=True, help="Checkpoint public key path")
    p_ver.add_argument("--fail-on-unresolved", action="store_true", help="Exit 2 if intents unresolved")

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
