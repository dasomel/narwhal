#!/usr/bin/env python3
"""Freshness/staleness gate for the security-db manifest fetch-security-db.sh writes
(narwhal#48).

WHY 7 DAYS: ghcr.io/aquasecurity/trivy-db re-publishes roughly every 6 hours upstream,
so this is a generous outer bound, not an attempt to match upstream cadence — this
repo's own scan cadence (trivy-operator.yaml scanJobTTL: 12h, compliance cron every
6h) already assumes sub-day freshness elsewhere, and Trivy's own CLI defaults to
refusing to scan with a DB older than 3 days (`--skip-db-update` aside). 7 days is
"still useful for most known CVEs, but past due for a promotion refresh" — a WARNING
line in an air-gapped operator's daily routine, not a five-minute-old outage.

WHY THIS CHECKS A FILE, NOT A LIVE TRIVY INVOCATION: there is no live cluster to ask
"how old is the DB you're actually using" (see docs/common/lessons-log.md / the
2026-08-23/24/25 triage on this issue) — this checks the PROMOTION-SIDE record
instead: given a security-db manifest.json (fetch-security-db.sh's or
promote-security-db.sh's output), is every artifact's fetched_at within the SLO. A
missing manifest, a missing fetched_at field, or an unparseable timestamp all fail
CLOSED (exit 1) — narwhal#48's AC explicitly asks for "DB 부재/만료 시 명확한 FAIL
또는 WARNING 정책", and a checker that returns 0 because it could not find anything to
check would be the exact failure mode that AC is about.

USAGE:
  check-security-db-freshness.py [manifest.json] [--slo-days N]

Default manifest path: <repo>/security-db/promoted/manifest.json relative to CWD is
NOT assumed — the caller (a promotion script, or this repo's regression check) always
passes an explicit path, because "which bundle" is never implicit in an air-gap
context where more than one arch/bundle can exist side by side (see 00-config.sh).
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys

DEFAULT_SLO_DAYS = 7


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest", help="path to a security-db manifest.json")
    p.add_argument("--slo-days", type=float, default=DEFAULT_SLO_DAYS)
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    try:
        with open(args.manifest, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        print(f"FAIL: manifest not found: {args.manifest} (no security DB has ever been fetched/promoted)", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"FAIL: manifest is not valid JSON: {args.manifest} ({e})", file=sys.stderr)
        return 1

    artifacts = doc.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        print(f"FAIL: manifest has no artifacts: {args.manifest}", file=sys.stderr)
        return 1

    now = datetime.datetime.now(datetime.timezone.utc)
    slo = datetime.timedelta(days=args.slo_days)
    violations = []

    for a in artifacts:
        name = a.get("name", "<unnamed>")
        raw_ts = a.get("fetched_at")
        if not raw_ts:
            violations.append(f"{name}: no fetched_at field")
            continue
        try:
            fetched_at = datetime.datetime.strptime(raw_ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            violations.append(f"{name}: unparseable fetched_at '{raw_ts}'")
            continue
        age = now - fetched_at
        if age > slo:
            violations.append(
                f"{name}: fetched {age.days}d ago (SLO {args.slo_days:g}d) — fetched_at={raw_ts}"
            )
        if not a.get("digest"):
            violations.append(f"{name}: no recorded digest — cannot serve as scan evidence")

    if violations:
        print(f"STALE/INVALID ({args.manifest}):", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print(f"OK: {len(artifacts)} artifact(s) within {args.slo_days:g}-day freshness SLO ({args.manifest})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
