#!/usr/bin/env python3
"""R380 (D-SECDB-NO-DOWNGRADE, #658): push-security-db.sh never downgrades a fresher mirror DB.

Invariants:
  1. push-security-db.sh compares bundle vs mirror OCI `annotations["org.opencontainers.image.created"]`
     (full RFC 3339: arbitrary fractional seconds and Z/[+-]HH:MM offsets) and raw manifest sha256
     for every artifact in promoted/manifest.json before pushing any artifact.
  2. Refuses (nonzero exit, clear message, 0 skopeo copies) when any bundle artifact is older than
     the mirror (including trivy-java-db when trivy-db is newer).
  3. Skips pushing (exit 0, 0 skopeo copies) ONLY when both `created` AND raw manifest digest match;
     equal `created` with a different manifest digest is not a downgrade and is pushed.
  4. Pushes when mirror is empty / repository or manifest missing (Harbor "repository ... not found"
     or OCI "manifest unknown").
  5. Fails closed (nonzero exit, 0 skopeo copies, never guesses empty) when mirror is unreachable or
     returns a present manifest lacking valid RFC 3339 `org.opencontainers.image.created`.
  6. Re-inspects each queued mirror artifact immediately before its `skopeo copy` and refuses/aborts
     if the mirror moved ahead of the bundle between preflight and copy (shrinking TOCTOU window).
  7. Allows intentional downgrade only with explicit `--allow-downgrade` or
     `AIRGAP_SECURITY_DB_ALLOW_DOWNGRADE=1` (documented with D-SECDB-NO-DOWNGRADE + residual TOCTOU risk).
  8. scripts/airgap/README.md and gitops/resources/prometheus-alerts.yaml (TrivyServerDbRedownloading)
     name pushing an older bundle over a fresher mirror as a cause, document the guard and TOCTOU
     accepted residual risk.

--mutation-verify proves every invariant fails when mutated on isolated temp copies.
"""
from __future__ import annotations

import concurrent.futures
import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
PUSH_REL = "scripts/airgap/lib/push-security-db.sh"
CONFIG_REL = "scripts/airgap/00-config.sh"
FRESH_REL = "scripts/airgap/lib/check-security-db-freshness.py"
README_REL = "scripts/airgap/README.md"
ALERTS_REL = "gitops/resources/prometheus-alerts.yaml"

FILES = (PUSH_REL, CONFIG_REL, FRESH_REL, README_REL, ALERTS_REL)

FAKE_SKOPEO = """#!/usr/bin/env python3
import json, os, sys
log_path = os.environ["FAKE_SKOPEO_LOG"]
state_path = os.environ["FAKE_SKOPEO_STATE"]
cfg = json.loads(open(os.environ["FAKE_SKOPEO_CFG"], encoding="utf-8").read())
args = sys.argv[1:]
with open(log_path, "a", encoding="utf-8") as f:
    f.write(" ".join(args) + "\\n")

cmd = args[0] if args else ""
if cmd == "login":
    sys.exit(0)
if cmd == "inspect":
    if "--authfile" not in args:
        sys.stderr.write("missing --authfile\\n")
        sys.exit(2)
    target = args[-1]
    entry = cfg.get("inspect", {}).get(target)
    if entry is None:
        sys.stderr.write(f"unconfigured fake target: {target}\\n")
        sys.exit(99)
    if isinstance(entry, list):
        counts = {}
        if os.path.exists(state_path):
            counts = json.loads(open(state_path, encoding="utf-8").read())
        idx = counts.get(target, 0)
        counts[target] = idx + 1
        with open(state_path, "w", encoding="utf-8") as sf:
            sf.write(json.dumps(counts))
        entry = entry[min(idx, len(entry) - 1)]
    rc = entry.get("rc", 0)
    if rc != 0:
        sys.stderr.write(entry.get("stderr", "error") + "\\n")
        sys.exit(rc)
    sys.stdout.write(json.dumps(entry["manifest"]))
    sys.exit(0)
if cmd == "copy":
    if "--authfile" not in args:
        sys.stderr.write("missing --authfile\\n")
        sys.exit(2)
    src, dst = args[-2], args[-1]
    with open(os.environ["FAKE_SKOPEO_COPIES"], "a", encoding="utf-8") as f:
        f.write(f"{src} -> {dst}\\n")
    sys.exit(0)
sys.stderr.write(f"unexpected skopeo subcommand: {args}\\n")
sys.exit(3)
"""


def manifest_doc(created: str | None, config_digest: str = "sha256:44136fa") -> dict:
    doc: dict = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"mediaType": "application/vnd.oci.empty.v1+json", "digest": config_digest, "size": 2},
        "layers": [],
    }
    if created is not None:
        doc["annotations"] = {"org.opencontainers.image.created": created}
    return doc


def check_static(root: Path) -> list[str]:
    errs: list[str] = []
    push_text = (root / PUSH_REL).read_text(encoding="utf-8")
    for needle in (
        "D-SECDB-NO-DOWNGRADE",
        "--allow-downgrade",
        "AIRGAP_SECURITY_DB_ALLOW_DOWNGRADE",
        "org.opencontainers.image.created",
        "is_mirror_absent_err",
        "Accepted residual risk:",
        "compare-and-swap",
        "pre-copy",
    ):
        if needle not in push_text:
            errs.append(f"{PUSH_REL}: missing {needle}")

    readme = (root / README_REL).read_text(encoding="utf-8")
    heading = "### 보안 DB 갱신 절차와 낡은 미러 경보 (D-SECDB-STALE)"
    if heading not in readme:
        errs.append(f"{README_REL}: missing heading {heading}")
    else:
        sec = readme.split(heading, 1)[1].split("\n### ", 1)[0]
        if (
            "옛 로컬 에어갭 번들" not in sec
            or "D-SECDB-NO-DOWNGRADE" not in sec
            or "--allow-downgrade" not in sec
            or "TOCTOU" not in sec
            or "다이제스트" not in sec
        ):
            errs.append(f"{README_REL}: D-SECDB-STALE section must explain older-bundle overwrite cause, digest equality, TOCTOU residual risk, and --allow-downgrade guard")

    alerts = yaml.safe_load((root / ALERTS_REL).read_text(encoding="utf-8"))
    found = [r for g in alerts["spec"]["groups"] for r in g["rules"] if r.get("alert") == "TrivyServerDbRedownloading"]
    if len(found) != 1:
        errs.append(f"{ALERTS_REL}: TrivyServerDbRedownloading missing")
    else:
        desc = found[0].get("annotations", {}).get("description", "")
        if "옛 번들을 push" not in desc or "--allow-downgrade" not in desc:
            errs.append(f"{ALERTS_REL}: TrivyServerDbRedownloading description must mention older bundle push and --allow-downgrade")

    return errs


def run_behavioral(root: Path) -> list[str]:
    errs: list[str] = []
    push_script = root / PUSH_REL
    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        fake_bin = tdp / "bin"
        fake_bin.mkdir()
        skopeo_bin = fake_bin / "skopeo"
        skopeo_bin.write_text(FAKE_SKOPEO, encoding="utf-8")
        skopeo_bin.chmod(skopeo_bin.stat().st_mode | stat.S_IXUSR)

        bundle = tdp / "bundle"
        promoted = bundle / "security-db" / "promoted"
        promoted.mkdir(parents=True)
        artifacts = [
            {"name": "trivy-db", "source_ref": "ghcr.io/aquasecurity/trivy-db:2", "tag": "2", "digest": "sha256:aaa", "fetched_at": now_iso, "size_bytes": 100},
            {"name": "trivy-java-db", "source_ref": "ghcr.io/aquasecurity/trivy-java-db:1", "tag": "1", "digest": "sha256:bbb", "fetched_at": now_iso, "size_bytes": 100},
            {"name": "trivy-checks", "source_ref": "ghcr.io/aquasecurity/trivy-checks:1", "tag": "1", "digest": "sha256:ccc", "fetched_at": now_iso, "size_bytes": 100},
        ]
        for a in artifacts:
            (promoted / a["name"]).mkdir()
        (promoted / "manifest.json").write_text(json.dumps({"artifacts": artifacts}), encoding="utf-8")

        def run_case(
            label: str,
            inspect_map: dict,
            extra_args: list[str] | None = None,
            extra_env: dict[str, str] | None = None,
        ) -> tuple[int, str, str, list[str]]:
            cfg_file = tdp / f"{label}-cfg.json"
            state_file = tdp / f"{label}-state.json"
            log_file = tdp / f"{label}-log.txt"
            copies_file = tdp / f"{label}-copies.txt"
            tmp_dir = tdp / f"{label}-tmp"
            tmp_dir.mkdir()
            for p in (log_file, copies_file):
                p.write_text("", encoding="utf-8")
            cfg_file.write_text(json.dumps({"inspect": inspect_map}), encoding="utf-8")

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            env["TMPDIR"] = str(tmp_dir)
            env["HARBOR_ADMIN_PASSWORD"] = "fake-pw"
            env["FAKE_SKOPEO_CFG"] = str(cfg_file)
            env["FAKE_SKOPEO_STATE"] = str(state_file)
            env["FAKE_SKOPEO_LOG"] = str(log_file)
            env["FAKE_SKOPEO_COPIES"] = str(copies_file)
            env.pop("AIRGAP_SECURITY_DB_ALLOW_DOWNGRADE", None)
            if extra_env:
                env.update(extra_env)

            cmd = ["bash", str(push_script), "--bundle", str(bundle), "--registry", "harbor.test.internal", *(extra_args or [])]
            res = subprocess.run(cmd, env=env, capture_output=True, text=True)
            copies = [line for line in copies_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            if list(tmp_dir.iterdir()):
                errs.append(f"{label}: leaked temp files in TMPDIR: {[p.name for p in tmp_dir.iterdir()]}")
            return res.returncode, res.stdout, res.stderr, copies

        def base_bundle(t_db: str, t_java: str, t_checks: str) -> dict:
            return {
                f"oci:{promoted / 'trivy-db'}:2": {"rc": 0, "manifest": manifest_doc(t_db)},
                f"oci:{promoted / 'trivy-java-db'}:1": {"rc": 0, "manifest": manifest_doc(t_java)},
                f"oci:{promoted / 'trivy-checks'}:1": {"rc": 0, "manifest": manifest_doc(t_checks)},
            }

        dst_db = "docker://harbor.test.internal/trivy-db/aquasecurity/trivy-db:2"
        dst_java = "docker://harbor.test.internal/trivy-db/aquasecurity/trivy-java-db:1"
        dst_checks = "docker://harbor.test.internal/trivy-db/aquasecurity/trivy-checks:1"

        # 1. Newer bundle -> pushes all 3
        imap = {
            **base_bundle("2026-11-19T12:00:00Z", "2026-11-19T12:00:00Z", "2026-11-19T12:00:00Z"),
            dst_db: {"rc": 0, "manifest": manifest_doc("2026-11-18T12:00:00Z")},
            dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-18T12:00:00Z")},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2026-11-18T12:00:00Z")},
        }
        rc, out, err, copies = run_case("newer", imap)
        if rc != 0 or len(copies) != 3:
            errs.append(f"newer: expected rc=0 and 3 copies, got rc={rc}, copies={copies}, err={err!r}")

        # 2. Older trivy-db -> refuses, 0 copies
        imap = {
            **base_bundle("2026-11-17T00:00:00Z", "2026-11-19T12:00:00Z", "2026-11-19T12:00:00Z"),
            dst_db: {"rc": 0, "manifest": manifest_doc("2026-11-19T01:00:00Z")},
            dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-18T00:00:00Z")},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2026-11-18T00:00:00Z")},
        }
        rc, out, err, copies = run_case("older-db", imap)
        if rc == 0 or copies or "refusing to downgrade" not in err or "--allow-downgrade" not in err:
            errs.append(f"older-db: expected refusal with 0 copies, got rc={rc}, copies={copies}, err={err!r}")

        # 3. Newer trivy-db, older trivy-java-db -> refuses atomically with 0 copies
        imap = {
            **base_bundle("2026-11-19T12:00:00Z", "2026-11-17T00:00:00Z", "2026-11-19T12:00:00Z"),
            dst_db: {"rc": 0, "manifest": manifest_doc("2026-11-18T12:00:00Z")},
            dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-19T01:00:00Z")},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2026-11-19T12:00:00Z")},
        }
        rc, out, err, copies = run_case("older-java", imap)
        if rc == 0 or copies or "trivy-java-db" not in err:
            errs.append(f"older-java: expected atomic refusal with 0 copies, got rc={rc}, copies={copies}, err={err!r}")

        # 4. Equal timestamps -> skips all, exit 0, 0 copies
        imap = {
            **base_bundle("2026-11-19T01:14:01Z", "2026-11-19T00:59:45Z", "2025-10-09T10:38:47Z"),
            dst_db: {"rc": 0, "manifest": manifest_doc("2026-11-19T01:14:01Z")},
            dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-19T00:59:45Z")},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2025-10-09T10:38:47Z")},
        }
        rc, out, err, copies = run_case("equal", imap)
        if rc != 0 or copies or "equal freshness" not in out:
            errs.append(f"equal: expected rc=0, 0 copies, skip message, got rc={rc}, copies={copies}, out={out!r}, err={err!r}")

        # 5. Empty mirror / missing repo (Harbor "repository ... not found" and OCI "manifest unknown") + 1 equal
        imap = {
            **base_bundle("2026-11-19T01:14:01Z", "2026-11-19T00:59:45Z", "2025-10-09T10:38:47Z"),
            dst_db: {"rc": 1, "stderr": "Error: reading manifest 2 in harbor.test.internal/trivy-db/aquasecurity/trivy-db: unknown: repository trivy-db/aquasecurity/trivy-db not found"},
            dst_java: {"rc": 1, "stderr": "Error: reading manifest 1 in harbor.test.internal/trivy-db/aquasecurity/trivy-java-db: manifest unknown"},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2025-10-09T10:38:47Z")},
        }
        rc, out, err, copies = run_case("empty", imap)
        if rc != 0 or len(copies) != 2 or any("trivy-checks" in c for c in copies):
            errs.append(f"empty: expected rc=0 and 2 copies (db+java only), got rc={rc}, copies={copies}, err={err!r}")

        # 6. Unreachable mirror -> fail closed, 0 copies
        imap = {
            **base_bundle("2026-11-19T12:00:00Z", "2026-11-19T12:00:00Z", "2025-10-09T10:38:47Z"),
            dst_db: {"rc": 1, "stderr": 'Get "https://harbor.test.internal/v2/": dial tcp 192.168.56.200:443: connect: connection refused'},
            dst_java: {"rc": 1, "stderr": "received unexpected HTTP status: 503 Service Unavailable"},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2025-10-09T10:38:47Z")},
        }
        rc, out, err, copies = run_case("unreachable", imap)
        if rc == 0 or copies or "refusing to guess" not in err:
            errs.append(f"unreachable: expected fail-closed with 0 copies, got rc={rc}, copies={copies}, err={err!r}")

        # 7. Present mirror manifest missing org.opencontainers.image.created -> fail closed, 0 copies
        imap = {
            **base_bundle("2026-11-19T12:00:00Z", "2026-11-19T12:00:00Z", "2025-10-09T10:38:47Z"),
            dst_db: {"rc": 0, "manifest": manifest_doc(None)},
            dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-19T00:59:45Z")},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2025-10-09T10:38:47Z")},
        }
        rc, out, err, copies = run_case("missing-ann", imap)
        if rc == 0 or copies or "lacks a valid org.opencontainers.image.created" not in err:
            errs.append(f"missing-ann: expected fail-closed with 0 copies, got rc={rc}, copies={copies}, err={err!r}")

        # 8. Override via --allow-downgrade flag -> pushes older db+java, skips equal checks
        imap = {
            **base_bundle("2026-11-17T00:00:00Z", "2026-11-17T00:00:00Z", "2025-10-09T10:38:47Z"),
            dst_db: {"rc": 0, "manifest": manifest_doc("2026-11-19T01:14:01Z")},
            dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-19T00:59:45Z")},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2025-10-09T10:38:47Z")},
        }
        rc, out, err, copies = run_case("override-flag", imap, extra_args=["--allow-downgrade"])
        if rc != 0 or len(copies) != 2 or "[warn]" not in err:
            errs.append(f"override-flag: expected rc=0, 2 copies, warn, got rc={rc}, copies={copies}, err={err!r}")

        # 9. Override via AIRGAP_SECURITY_DB_ALLOW_DOWNGRADE=1 env -> pushes older db
        rc, out, err, copies = run_case("override-env", imap, extra_env={"AIRGAP_SECURITY_DB_ALLOW_DOWNGRADE": "1"})
        if rc != 0 or len(copies) != 2 or "[warn]" not in err:
            errs.append(f"override-env: expected rc=0, 2 copies, warn, got rc={rc}, copies={copies}, err={err!r}")

        # 10. Equal `created` timestamp but DIFFERENT manifest digest -> not a downgrade, must push
        imap = {
            **base_bundle("2026-11-19T01:14:01Z", "2026-11-19T00:59:45Z", "2025-10-09T10:38:47Z"),
            dst_db: {"rc": 0, "manifest": manifest_doc("2026-11-19T01:14:01Z", config_digest="sha256:different999")},
            dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-19T00:59:45Z")},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2025-10-09T10:38:47Z")},
        }
        rc, out, err, copies = run_case("equal-diff-digest", imap)
        if rc != 0 or len(copies) != 1 or "trivy-db" not in copies[0]:
            errs.append(f"equal-diff-digest: expected rc=0 and 1 copy (trivy-db only), got rc={rc}, copies={copies}, err={err!r}")

        # 11a. RFC 3339 arbitrary fractional seconds & numeric offsets (newer) -> accepted and pushed
        imap = {
            **base_bundle("2026-11-19T01:14:01.123456789Z", "2026-11-19T10:14:01.2+09:00", "2025-10-09T05:08:47.500-05:30"),
            dst_db: {"rc": 0, "manifest": manifest_doc("2026-11-19T01:14:01.123456788Z")},
            dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-19T01:14:01.1Z")},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2025-10-09T10:38:47Z")},
        }
        rc, out, err, copies = run_case("rfc3339-frac-offset-newer", imap)
        if rc != 0 or len(copies) != 3:
            errs.append(f"rfc3339-frac-offset-newer: expected rc=0 and 3 copies, got rc={rc}, copies={copies}, err={err!r}")

        # 11b. RFC 3339 fractional seconds & numeric offsets (older) -> refused with 0 copies
        imap = {
            **base_bundle("2026-11-19T10:14:01.1+09:00", "2026-11-19T01:14:01.123456789Z", "2025-10-09T10:38:47Z"),
            dst_db: {"rc": 0, "manifest": manifest_doc("2026-11-19T01:14:01.123456789Z")},
            dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-19T01:14:01.123456789Z")},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2025-10-09T10:38:47Z")},
        }
        rc, out, err, copies = run_case("rfc3339-frac-offset-older", imap)
        if rc == 0 or copies or "refusing to downgrade" not in err:
            errs.append(f"rfc3339-frac-offset-older: expected refusal with 0 copies, got rc={rc}, copies={copies}, err={err!r}")

        # 11c. Invalid RFC 3339 shapes (trailing dot with no digits, naive, out-of-range offset) -> fail closed
        for bad_label, bad_ts in (
            ("empty-frac", "2026-11-19T01:14:01.Z"),
            ("naive", "2026-11-19T01:14:01"),
            ("bad-offset", "2026-11-19T01:14:01+25:00"),
        ):
            imap = {
                **base_bundle(bad_ts, "2026-11-19T00:59:45Z", "2025-10-09T10:38:47Z"),
                dst_db: {"rc": 0, "manifest": manifest_doc("2026-11-18T00:00:00Z")},
                dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-18T00:00:00Z")},
                dst_checks: {"rc": 0, "manifest": manifest_doc("2025-10-09T10:38:47Z")},
            }
            rc, out, err, copies = run_case(f"rfc3339-bad-{bad_label}", imap)
            if rc == 0 or copies or "lacks a valid org.opencontainers.image.created" not in err:
                errs.append(f"rfc3339-bad-{bad_label}: expected fail-closed, got rc={rc}, copies={copies}, err={err!r}")

        # 12. TOCTOU: mirror moves to newer between preflight and 1st copy -> refuses with 0 copies
        imap = {
            **base_bundle("2026-11-19T12:00:00Z", "2026-11-19T12:00:00Z", "2026-11-19T12:00:00Z"),
            dst_db: [
                {"rc": 0, "manifest": manifest_doc("2026-11-18T12:00:00Z")},
                {"rc": 0, "manifest": manifest_doc("2026-11-19T13:00:00Z")},
            ],
            dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-18T12:00:00Z")},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2026-11-18T12:00:00Z")},
        }
        rc, out, err, copies = run_case("toctou-first", imap)
        if rc == 0 or copies or "refusing to downgrade" not in err or "pre-copy" not in err:
            errs.append(f"toctou-first: expected pre-copy refusal with 0 copies, got rc={rc}, copies={copies}, err={err!r}")

        # 13. TOCTOU: mirror moves to newer between preflight and 2nd copy -> copies 1st, refuses 2nd & aborts 3rd
        imap = {
            **base_bundle("2026-11-19T12:00:00Z", "2026-11-19T12:00:00Z", "2026-11-19T12:00:00Z"),
            dst_db: {"rc": 0, "manifest": manifest_doc("2026-11-18T12:00:00Z")},
            dst_java: [
                {"rc": 0, "manifest": manifest_doc("2026-11-18T12:00:00Z")},
                {"rc": 0, "manifest": manifest_doc("2026-11-19T13:00:00Z")},
            ],
            dst_checks: {"rc": 0, "manifest": manifest_doc("2026-11-18T12:00:00Z")},
        }
        rc, out, err, copies = run_case("toctou-second", imap)
        if rc == 0 or len(copies) != 1 or "trivy-db" not in copies[0] or "trivy-java-db" not in err:
            errs.append(f"toctou-second: expected rc!=0 and only trivy-db copied, got rc={rc}, copies={copies}, err={err!r}")

        # 14. TOCTOU: mirror converges to equal created + equal digest before copy -> skips cleanly (exit 0)
        imap = {
            **base_bundle("2026-11-19T12:00:00Z", "2026-11-19T00:59:45Z", "2025-10-09T10:38:47Z"),
            dst_db: [
                {"rc": 0, "manifest": manifest_doc("2026-11-18T12:00:00Z")},
                {"rc": 0, "manifest": manifest_doc("2026-11-19T12:00:00Z")},
            ],
            dst_java: {"rc": 0, "manifest": manifest_doc("2026-11-19T00:59:45Z")},
            dst_checks: {"rc": 0, "manifest": manifest_doc("2025-10-09T10:38:47Z")},
        }
        rc, out, err, copies = run_case("toctou-equal-converged", imap)
        if rc != 0 or copies:
            errs.append(f"toctou-equal-converged: expected rc=0 and 0 copies, got rc={rc}, copies={copies}, err={err!r}")

    return errs


MUTATIONS = [
    (
        "allow older downgrade without override",
        PUSH_REL,
        '        echo "[FAIL] ${name}: refusing to downgrade',
        '        echo "[skip-check] allowing downgrade"\n        false && echo "[FAIL] ${name}: refusing to downgrade',
    ),
    (
        "do not abort on older guard failure",
        PUSH_REL,
        'if [[ "${guard_fail}" -gt 0 ]]; then',
        'if false && [[ "${guard_fail}" -gt 0 ]]; then',
    ),
    (
        "treat unreachable mirror as absent",
        PUSH_REL,
        'if is_mirror_absent_err "${INSPECT_ERR}"; then',
        "if true; then",
    ),
    (
        "push instead of skipping on equal",
        PUSH_REL,
        '        echo "[skip] ${name}: mirror ${dst} already at ${bundle_created} (${bundle_digest}, equal freshness)"\n        MIRROR_ACTION="skip"',
        '        echo "[push-equal]"\n        MIRROR_ACTION="push"',
    ),
    (
        "skip equal created even when manifest digest differs",
        PUSH_REL,
        '      if [[ "${bundle_digest}" == "${mirror_digest}" ]]; then',
        "      if true; then",
    ),
    (
        "reject arbitrary-length fractional seconds in RFC 3339",
        PUSH_REL,
        r"(?:\.(\d+))?",
        r"(?:\.(\d{6}))?",
    ),
    (
        "skip pre-copy TOCTOU re-inspect",
        PUSH_REL,
        '  check_mirror_freshness "${name}" "${dst}" "${bundle_created}" "${bundle_digest}" "pre-copy"',
        '  MIRROR_ACTION="push"',
    ),
    (
        "drop --allow-downgrade flag",
        PUSH_REL,
        "    --allow-downgrade) ALLOW_DOWNGRADE=1; shift ;;",
        "    --allow-downgrade) ALLOW_DOWNGRADE=0; shift ;;",
    ),
    (
        "drop AIRGAP_SECURITY_DB_ALLOW_DOWNGRADE env",
        PUSH_REL,
        'ALLOW_DOWNGRADE="${AIRGAP_SECURITY_DB_ALLOW_DOWNGRADE:-0}"',
        'ALLOW_DOWNGRADE="0"',
    ),
    (
        "skip trivy-java-db in freshness check",
        PUSH_REL,
        '  if [[ "${mirror_rc}" -ne 0 ]]; then',
        '  if [[ "${name}" == "trivy-java-db" ]]; then echo "[bypass] ${name}"; MIRROR_ACTION="push"; return 0; elif [[ "${mirror_rc}" -ne 0 ]]; then',
    ),
    (
        "drop D-SECDB-NO-DOWNGRADE comment",
        PUSH_REL,
        "# D-SECDB-NO-DOWNGRADE (#658):",
        "# removed-decision-tag:",
    ),
    (
        "drop TOCTOU residual risk comment",
        PUSH_REL,
        "Accepted residual risk:",
        "removed-residual-risk:",
    ),
    (
        "drop README downgrade cause/guard text",
        README_REL,
        "D-SECDB-NO-DOWNGRADE",
        "D-REMOVED",
    ),
    (
        "drop alert downgrade description text",
        ALERTS_REL,
        "옛 번들을 push해",
        "옛 번들 언급 제거",
    ),
]


def mutation_verify(root: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        for rel in FILES:
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / rel, base / rel)

        clean_errs = check_static(base) + run_behavioral(base)
        if clean_errs:
            print("clean control failed:", clean_errs, file=sys.stderr)
            return 1

        def check_one(idx_mut: tuple[int, tuple[str, str, str, str]]) -> str | None:
            idx, (name, rel, old, new) = idx_mut
            case = Path(tmp) / f"case-{idx}"
            for r in FILES:
                (case / r).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / r, case / r)

            target = case / rel
            text = target.read_text(encoding="utf-8")
            assert old in text, f"mutation anchor missing in {rel}: {old!r}"
            target.write_text(text.replace(old, new, 1), encoding="utf-8")

            mut_errs = check_static(case) + run_behavioral(case)
            if not mut_errs:
                return f"MUTATION SURVIVED: {name}"
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for failure in pool.map(check_one, enumerate(MUTATIONS)):
                if failure:
                    print(failure, file=sys.stderr)
                    return 1

    print(f"PASS: R380 push-security-db downgrade guard ({len(MUTATIONS)} mutations rejected)")
    return 0


def main() -> int:
    if "--mutation-verify" in sys.argv:
        return mutation_verify(ROOT)
    errs = check_static(ROOT) + run_behavioral(ROOT)
    for e in errs:
        print("ERROR:", e, file=sys.stderr)
    if errs:
        return 1
    print("PASS: R380 push-security-db downgrade guard")
    return 0


if __name__ == "__main__":
    sys.exit(main())
