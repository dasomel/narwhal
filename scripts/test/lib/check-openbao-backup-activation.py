#!/usr/bin/env python3
"""R384 (D-OPENBAO-BACKUP-ACTIVATION, D-OPENBAO-BACKUP-ALERT, #757):
OpenBao encrypted snapshot backup activation detection at install/verify time and runtime Prometheus alert.

Verifies:
1. Static invariants on lib-openbao-backup.sh, Phase 2 (06-phase2-start.sh / provision-kakao.sh),
   verify-cluster.sh, verify-backup.sh, prometheus-alerts.yaml, and the runbook.
2. Behavioral tests of lib-openbao-backup.sh, the real CLI callers (verify-backup.sh across UTC+9/-8
   for 20h-fresh/28h-stale/never-succeeded/parse-fail, verify-cluster.sh), and the Phase-2 snippet
   executed from /tmp/.../vagrant-shell against a fake kubectl + real openssl x509 certificates,
   including secret/cert non-leak checks.
3. Behavioral promtool tests of OpenBaoSnapshotBackupStale (with namespace/cronjob label preservation),
   including never-succeeded, last_successful_time=0, stale >26h, healthy, fresh grace, suspended,
   absent, kube-state-metrics down resolution, 2-pod rollout, and manual-job recovery.
4. --mutation-verify rejects mutations across code, alerts, callers, docs, and R384 wiring.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]

LIB = "scripts/common/lib-openbao-backup.sh"
PHASE2 = "scripts/cluster/06-phase2-start.sh"
KAKAO = "scripts/cloud/provision-kakao.sh"
VERIFY_CLUSTER = "scripts/test/verify-cluster.sh"
VERIFY_BACKUP = "scripts/test/verify-backup.sh"
ALERTS = "gitops/resources/prometheus-alerts.yaml"
RUNBOOK = "docs/runbooks/openbao-snapshot-backup.md"
REG = "scripts/test/regression-check-kakao.sh"

ALL_FILES = (LIB, PHASE2, KAKAO, VERIFY_CLUSTER, VERIFY_BACKUP, ALERTS, RUNBOOK, REG)
SEL = 'namespace="storage",cronjob="openbao-snapshot"'


def slug(heading):
    text = heading.lstrip("#").strip().lower()
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def static(root):
    lib = (root / LIB).read_text()
    for needle in (
        "D-OPENBAO-BACKUP-ACTIVATION",
        "openbao_backup_check_activation()",
        "PRIVATE KEY",
        "-checkend 86400",
        "Public Key Algorithm: rsaEncryption",
        "3072",
        "-fingerprint -sha256",
        'spec.get("suspend")',
    ):
        assert needle in lib, f"{LIB} missing {needle!r}"

    p2 = (root / PHASE2).read_text()
    for needle in (
        "PHASE2_DEGRADED=",
        "/home/vagrant/scripts/common/lib-openbao-backup.sh",
        "openbao_backup_check_activation",
        "Phase 2 DEGRADED CONDITIONS",
        'rm -f "${PHASE2_MARKER}" "${PHASE2_DEGRADED}"',
    ):
        assert needle in p2, f"{PHASE2} missing {needle!r}"
    assert p2.rfind('date -u \'+%Y-%m-%dT%H:%M:%SZ\' > "${PHASE2_MARKER}"') > p2.rfind("done\n"), \
        f"{PHASE2} must still write PHASE2_MARKER after the script loop"

    kakao = (root / KAKAO).read_text()
    assert "/var/lib/narwhal/phase2-degraded" in kakao and "phase2 completed with degraded item(s):" in kakao, \
        f"{KAKAO} must surface phase2-degraded summary"

    vc = (root / VERIFY_CLUSTER).read_text()
    assert "openbao_backup_check_activation" in vc and "OpenBao snapshot backup DEGRADED" in vc, \
        f"{VERIFY_CLUSTER} must surface degraded OpenBao backup"

    vb = (root / VERIFY_BACKUP).read_text()
    assert "openbao_backup_check_activation" in vb and 'should_run "openbao"' in vb \
        and "OpenBao snapshot backup DEGRADED" in vb and 'python3 - "${OPENBAO_BACKUP_LAST_SUCCESS}"' in vb, \
        f"{VERIFY_BACKUP} must check OpenBao snapshot backup activation with portable python3 UTC parsing"
    ob_sec = vb[vb.index('if should_run "openbao"; then'):]
    assert "date -j" not in ob_sec and "date -d" not in ob_sec, \
        f"{VERIFY_BACKUP} openbao section must not rely on BSD/GNU date dialects"

    runbook = (root / RUNBOOK).read_text()
    for needle in ("D-OPENBAO-BACKUP-ACTIVATION", "### 3.7 클러스터 재구축(rebuild) 후 재활성화", "## 4. 정기 점검"):
        assert needle in runbook, f"{RUNBOOK} missing {needle!r}"
    for forbidden in (
        "신선도 알림이 없다",
        "신선도 알림은 아직 없다",
        "코드가 강제하는 값이 아니다",
        "OpenBaoSnapshotBackupFailed",
        "show-credentials.sh",
    ):
        assert forbidden not in runbook, f"{RUNBOOK} contains stale/contradictory text {forbidden!r}"
    sec37 = runbook[runbook.index("### 3.7 클러스터 재구축(rebuild) 후 재활성화"):runbook.index("## 4. 정기 점검")]
    assert sec37.index("3.5") < sec37.index("bash scripts/test/verify-backup.sh --section=openbao"), \
        f"{RUNBOOK} 3.7 must run 3.5 manual job before bash scripts/test/verify-backup.sh --section=openbao"

    alerts_text = (root / ALERTS).read_text()
    assert "D-OPENBAO-BACKUP-ALERT" in alerts_text, f"{ALERTS} missing D-OPENBAO-BACKUP-ALERT comment"
    assert "manual jobs do not update" not in alerts_text and "emits no job metrics" not in alerts_text, \
        f"{ALERTS} contains inaccurate comment about manual jobs or job metrics"
    rules_doc = yaml.safe_load(alerts_text)
    group = next((g for g in rules_doc["spec"]["groups"] if g["name"] == "openbao-backup"), None)
    assert group is not None, f"{ALERTS} missing openbao-backup group"
    by_name = {r["alert"]: r for r in group["rules"]}
    assert set(by_name) == {"OpenBaoSnapshotBackupStale"}, set(by_name)

    stale = by_name["OpenBaoSnapshotBackupStale"]
    assert stale["for"] == "15m" and stale["labels"]["severity"] == "critical"
    s_expr = " ".join(stale["expr"].split())
    for needle in (
        SEL,
        "93600",
        "kube_cronjob_created",
        "kube_cronjob_status_last_successful_time",
        "kube_cronjob_spec_suspend",
        "max by (namespace, cronjob)",
        "on (namespace, cronjob)",
    ):
        assert needle in s_expr, f"OpenBaoSnapshotBackupStale missing {needle!r}: {s_expr}"

    expected_anchor = "/docs/runbooks/openbao-snapshot-backup.md#" + slug("## 4. 정기 점검")
    assert stale["annotations"]["runbook_url"].endswith(expected_anchor), stale["annotations"]["runbook_url"]

    reg = (root / REG).read_text()
    assert "check R384 " in reg and "check-openbao-backup-activation.py --mutation-verify" in reg, \
        f"{REG} must wire R384 with --mutation-verify"
    return [stale]


_CERTS = {}


def _certs():
    if _CERTS:
        return _CERTS
    real = shutil.which("openssl")
    assert real, "openssl required for R384"
    tmp = tempfile.TemporaryDirectory()
    d = Path(tmp.name)

    def mk(name, key_args, days):
        subprocess.run(
            [real, "req", "-x509", *key_args, "-sha256", "-days", str(days), "-nodes",
             "-subj", f"/CN=r384-{name}", "-keyout", str(d / f"{name}.key"), "-out", str(d / f"{name}.pem")],
            check=True, capture_output=True,
        )
        fpr = subprocess.run(
            [real, "x509", "-in", str(d / f"{name}.pem"), "-noout", "-fingerprint", "-sha256"],
            check=True, capture_output=True, text=True,
        ).stdout.strip().split("=", 1)[1]
        return (d / f"{name}.pem").read_text(), (d / f"{name}.key").read_text(), fpr

    g_pem, g_key, g_fpr = mk("good", ["-newkey", "rsa:3072"], 30)
    o_pem, _, _ = mk("other", ["-newkey", "rsa:3072"], 30)
    s_pem, _, _ = mk("short", ["-newkey", "rsa:3072"], 1)
    r2048_pem, _, r2048_fpr = mk("rsa2048", ["-newkey", "rsa:2048"], 30)
    ec_pem, _, ec_fpr = mk("ecdsa", ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1"], 30)
    time.sleep(2)  # -checkend 86400 treats 1-day cert as expiring within 24h

    def body_slice(pem_text):
        lines = [ln.strip() for ln in pem_text.splitlines() if ln.strip() and not ln.startswith("-----")]
        return lines[0][:32]

    _CERTS.update(
        _tmp=tmp,
        good_pem=g_pem,
        good_key=g_key,
        good_fpr=g_fpr,
        other_pem=o_pem,
        short_pem=s_pem,
        rsa2048_pem=r2048_pem,
        rsa2048_fpr=r2048_fpr,
        ec_pem=ec_pem,
        ec_fpr=ec_fpr,
        cert_body=body_slice(g_pem),
        key_body=body_slice(g_key),
    )
    return _CERTS


FAKE_KUBECTL = """#!/bin/bash
set -eu
if [ "${FAKE_KUBECTL_FAIL:-0}" = "1" ]; then
  echo "Unable to connect to the server: dial tcp 127.0.0.1:6443: connect: connection refused" >&2
  exit 1
fi
args=" $* "
case "${args}" in
  *" get --raw /readyz "*) exit 0 ;;
  *" get clusterissuer/narwhal-ca-issuer "*) exit 0 ;;
  *" get cronjob openbao-snapshot "*)
    if [ -n "${FAKE_CJ_JSON:-}" ]; then
      printf '%s' "${FAKE_CJ_JSON}"
      exit 0
    fi
    case "${args}" in
      *" --ignore-not-found "*) exit 0 ;;
      *) exit 1 ;;
    esac ;;
  *" get configmap openbao-backup-recipient "*)
    if [ -n "${FAKE_CM_JSON:-}" ]; then
      printf '%s' "${FAKE_CM_JSON}"
      exit 0
    fi
    case "${args}" in
      *" --ignore-not-found "*) exit 0 ;;
      *) exit 1 ;;
    esac ;;
  *" get pods "*) echo "storage-pod 1/1 Running 0 1d"; exit 0 ;;
  *" get deployment "*) echo "1"; exit 0 ;;
  *" get backupstoragelocation "*) echo "Available"; exit 0 ;;
  *" get nodes "*) echo "10.0.0.10"; exit 0 ;;
esac
exit 0
"""


def _cj_json(fpr, suspend=False, last_success="", last_schedule=""):
    status = {}
    if last_success:
        status["lastSuccessfulTime"] = last_success
    if last_schedule:
        status["lastScheduleTime"] = last_schedule
    return json.dumps({
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": "openbao-snapshot", "namespace": "storage"},
        "spec": {
            "suspend": suspend,
            "jobTemplate": {"spec": {"template": {"spec": {
                "containers": [{"name": "encrypt", "env": [{"name": "RECIPIENT_FPR", "value": fpr}]}]
            }}}},
        },
        "status": status,
    })


def _cm_json(pem_text, include_key=True):
    data = {"recipient.pem": pem_text} if include_key else {}
    return json.dumps({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "openbao-backup-recipient", "namespace": "storage"},
        "data": data,
    })


def _assert_no_leak(stdout, stderr, c):
    combined = stdout + "\n" + stderr
    for forbidden in (
        "-----BEGIN CERTIFICATE-----",
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----",
        c["cert_body"],
        c["key_body"],
    ):
        assert forbidden not in combined, f"certificate/key material leaked in output: {forbidden!r}"


def _build_no_openssl_bin(t, fake_kubectl):
    no_ssl = t / "no-openssl-bin"
    no_ssl.mkdir()
    shutil.copy2(fake_kubectl, no_ssl / "kubectl")
    for tool in ("bash", "python3", "grep", "sed", "tr", "awk", "cat", "dirname", "mkdir", "rm", "date", "sleep"):
        p = shutil.which(tool)
        if p:
            (no_ssl / tool).symlink_to(p)
    return no_ssl


def _extract_phase2_snippet(root):
    p2 = (root / PHASE2).read_text()
    start_marker = "# Check OpenBao encrypted snapshot backup activation"
    end_marker = 'echo "Phase 2: Platform Apps Installation Complete"'
    assert start_marker in p2 and end_marker in p2, f"{PHASE2} missing expected section markers"
    return p2[p2.index(start_marker):p2.index(end_marker)]


def behavior_scripts(root):
    c = _certs()
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh_20h_iso = (now - timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale_28h_iso = (now - timedelta(hours=28)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lib = root / LIB

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        kc = t / "kubectl"
        kc.write_text(FAKE_KUBECTL)
        kc.chmod(kc.stat().st_mode | stat.S_IXUSR)
        no_ssl = _build_no_openssl_bin(t, kc)

        def run_lib(cj, cm, fail_kubectl=False, drop_openssl=False):
            path_val = str(no_ssl) if drop_openssl else f"{t}:{os.environ['PATH']}"
            env = dict(
                os.environ,
                PATH=path_val,
                FAKE_CJ_JSON=cj,
                FAKE_CM_JSON=cm,
                FAKE_KUBECTL_FAIL="1" if fail_kubectl else "0",
            )
            cmd = (
                f'set -euo pipefail; source "{lib}"; rc=0; '
                'openbao_backup_check_activation || rc=$?; '
                'printf "%s|%s|%s\\n" "$rc" "$OPENBAO_BACKUP_STATE" "$OPENBAO_BACKUP_MSG"'
            )
            p = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, check=True)
            _assert_no_leak(p.stdout, p.stderr, c)
            rc_s, state, msg = p.stdout.strip().split("|", 2)
            return int(rc_s), state, msg

        # Core lib-openbao-backup.sh matrix
        assert run_lib("", "")[:2] == (0, "absent")
        assert run_lib(_cj_json(c["good_fpr"], suspend=True), "")[:2] == (0, "suspended")
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), "")
        assert (rc, st) == (1, "degraded") and "missing" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), _cm_json(""))
        assert (rc, st) == (1, "degraded") and "missing or empty" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), _cm_json("", include_key=False))
        assert (rc, st) == (1, "degraded") and "missing or empty" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), _cm_json(c["good_pem"] + c["good_key"]))
        assert (rc, st) == (1, "degraded") and "PRIVATE KEY" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), _cm_json(c["good_pem"] + c["other_pem"]))
        assert (rc, st) == (1, "degraded") and "single PEM" in msg, (rc, st, msg)
        rc, st, msg = run_lib(
            _cj_json(c["good_fpr"]),
            _cm_json("-----BEGIN CERTIFICATE-----\nNOT_VALID_BASE64\n-----END CERTIFICATE-----\n"),
        )
        assert (rc, st) == (1, "degraded") and "malformed" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), _cm_json(c["short_pem"]))
        assert (rc, st) == (1, "degraded") and "expired" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["ec_fpr"]), _cm_json(c["ec_pem"]))
        assert (rc, st) == (1, "degraded") and "RSA" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["rsa2048_fpr"]), _cm_json(c["rsa2048_pem"]))
        assert (rc, st) == (1, "degraded") and "3072" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), _cm_json(c["other_pem"]))
        assert (rc, st) == (1, "degraded") and "differs" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(""), _cm_json(c["good_pem"]))
        assert (rc, st) == (1, "degraded") and "empty" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"].lower()), _cm_json(c["good_pem"]))
        assert (rc, st) == (0, "ok"), (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), _cm_json(c["good_pem"]), fail_kubectl=True)
        assert (rc, st) == (2, "unknown") and "unable to query" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), _cm_json(c["good_pem"]), drop_openssl=True)
        assert (rc, st) == (2, "unknown") and "openssl not found" in msg, (rc, st, msg)

        # Verify real CLI caller: verify-backup.sh --section=openbao
        def run_vb(cj, cm, tz="UTC"):
            env = dict(os.environ, PATH=f"{t}:{os.environ['PATH']}", TZ=tz, FAKE_CJ_JSON=cj, FAKE_CM_JSON=cm)
            p = subprocess.run(["bash", str(root / VERIFY_BACKUP), "--section=openbao"],
                               env=env, capture_output=True, text=True)
            _assert_no_leak(p.stdout, p.stderr, c)
            return p

        p = run_vb(_cj_json(c["good_fpr"], last_schedule=now_iso), "")
        assert p.returncode == 1 and "[FAIL] OpenBao snapshot backup DEGRADED" in p.stdout, p.stdout

        # Timezone-independence: 20h fresh PASS and 28h stale FAIL in both UTC+9 and UTC-8
        for tz in ("Asia/Seoul", "America/Los_Angeles"):
            p_fresh = run_vb(_cj_json(c["good_fpr"], last_success=fresh_20h_iso, last_schedule=fresh_20h_iso),
                             _cm_json(c["good_pem"]), tz=tz)
            assert p_fresh.returncode == 0 and "[PASS] OpenBao snapshot lastSuccessfulTime:" in p_fresh.stdout \
                and "(20h ago, within 26h)" in p_fresh.stdout, (tz, p_fresh.returncode, p_fresh.stdout)

            p_stale = run_vb(_cj_json(c["good_fpr"], last_success=stale_28h_iso, last_schedule=stale_28h_iso),
                             _cm_json(c["good_pem"]), tz=tz)
            assert p_stale.returncode == 1 and "[FAIL] OpenBao snapshot lastSuccessfulTime is stale:" in p_stale.stdout \
                and "(28h ago > 26h)" in p_stale.stdout, (tz, p_stale.returncode, p_stale.stdout)

        # Scheduled but never succeeded -> FAIL
        p_never = run_vb(_cj_json(c["good_fpr"], last_schedule=now_iso), _cm_json(c["good_pem"]))
        assert p_never.returncode == 1 and "never succeeded" in p_never.stdout and "[FAIL]" in p_never.stdout, \
            (p_never.returncode, p_never.stdout)

        # Unparseable timestamp -> WARN, never PASS
        p_badts = run_vb(_cj_json(c["good_fpr"], last_success="not-a-timestamp"), _cm_json(c["good_pem"]))
        assert p_badts.returncode == 0 and "[WARN] OpenBao snapshot lastSuccessfulTime could not be parsed" in p_badts.stdout \
            and "[PASS] OpenBao snapshot lastSuccessfulTime" not in p_badts.stdout, (p_badts.returncode, p_badts.stdout)

        # Verify real CLI caller: verify-cluster.sh --section=platform
        env_miss = dict(os.environ, PATH=f"{t}:{os.environ['PATH']}",
                        FAKE_CJ_JSON=_cj_json(c["good_fpr"], last_schedule=now_iso), FAKE_CM_JSON="")
        env_ok = dict(os.environ, PATH=f"{t}:{os.environ['PATH']}",
                      FAKE_CJ_JSON=_cj_json(c["good_fpr"], last_success=now_iso, last_schedule=now_iso),
                      FAKE_CM_JSON=_cm_json(c["good_pem"]))

        p_vc_miss = subprocess.run(["bash", str(root / VERIFY_CLUSTER), "--section=platform"],
                                   env=env_miss, capture_output=True, text=True)
        _assert_no_leak(p_vc_miss.stdout, p_vc_miss.stderr, c)
        assert p_vc_miss.returncode == 0 and "WARN  OpenBao snapshot backup DEGRADED" in p_vc_miss.stdout, p_vc_miss.stdout

        p_vc_ok = subprocess.run(["bash", str(root / VERIFY_CLUSTER), "--section=platform"],
                                 env=env_ok, capture_output=True, text=True)
        _assert_no_leak(p_vc_ok.stdout, p_vc_ok.stderr, c)
        assert p_vc_ok.returncode == 0 and "PASS  OpenBao snapshot backup:" in p_vc_ok.stdout, p_vc_ok.stdout

        # Verify Phase-2 snippet executed from /tmp/.../vagrant-shell (Vagrant provisioner model)
        guest_home = t / "guest-home"
        guest_lib = guest_home / "scripts/common/lib-openbao-backup.sh"
        guest_lib.parent.mkdir(parents=True)
        shutil.copy2(lib, guest_lib)
        p2_degraded = t / "phase2-degraded"
        # Place runner directly in temp root (no sibling scripts/common/) so BASH_SOURCE[0] cannot find lib
        vagrant_shell = t / "vagrant-shell"
        snippet = _extract_phase2_snippet(root).replace("/home/vagrant", str(guest_home))
        vagrant_shell.write_text(
            "#!/bin/bash\nset -euo pipefail\n"
            f'PHASE2_DEGRADED="{p2_degraded}"\n'
            'rm -f "${PHASE2_DEGRADED}"\n'
            "DEGRADED_REASONS=()\n"
            + snippet
        )
        vagrant_shell.chmod(vagrant_shell.stat().st_mode | stat.S_IXUSR)

        def run_p2(cj, cm, fail_kubectl=False):
            env = dict(
                os.environ,
                PATH=f"{t}:{os.environ['PATH']}",
                FAKE_CJ_JSON=cj,
                FAKE_CM_JSON=cm,
                FAKE_KUBECTL_FAIL="1" if fail_kubectl else "0",
                OPENBAO_BACKUP_WAIT_ATTEMPTS="1",
                OPENBAO_BACKUP_WAIT_INTERVAL="0",
            )
            env.pop("OPENBAO_BK_LIB", None)
            p = subprocess.run(["bash", str(vagrant_shell)], env=env, capture_output=True, text=True, check=True)
            _assert_no_leak(p.stdout, p.stderr, c)
            degraded_text = p2_degraded.read_text() if p2_degraded.exists() else ""
            return p, degraded_text

        # Missing ConfigMap via /tmp/.../vagrant-shell -> non-fatal, recorded in PHASE2_DEGRADED
        p, deg = run_p2(_cj_json(c["good_fpr"]), "")
        assert "openbao-backup-recipient is missing" in deg and "Phase 2 DEGRADED CONDITIONS" in p.stdout, (deg, p.stdout)

        # Absent CronJob after wait -> NOT silent pass, recorded in PHASE2_DEGRADED
        p, deg = run_p2("", "")
        assert "not present" in deg and "openbao-backup" in deg, (deg, p.stdout)

        # kubectl unreachable (rc=2) -> recorded in PHASE2_DEGRADED
        p, deg = run_p2("", "", fail_kubectl=True)
        assert "inconclusive (rc=2)" in deg, (deg, p.stdout)

        # Healthy -> no PHASE2_DEGRADED file
        p, deg = run_p2(_cj_json(c["good_fpr"]), _cm_json(c["good_pem"]))
        assert deg == "" and not p2_degraded.exists(), (deg, p.stdout)

        # Missing lib -> loud WARN + recorded in PHASE2_DEGRADED (never silent skip)
        guest_lib.unlink()
        p, deg = run_p2(_cj_json(c["good_fpr"]), "")
        assert "not readable" in deg and "WARN:" in p.stderr, (deg, p.stderr)


def behavior_promtool(rules):
    if not shutil.which("promtool"):
        print("SKIP: promtool unavailable; R384 alert cases not run")
        return

    step, n = 600, 6 * 24 * 3  # 10m samples over 72h

    def s(name, fn, extra_labels=""):
        labels = f"{SEL},{extra_labels}" if extra_labels else SEL
        vals = [fn(k * step) for k in range(n)]
        return {"series": f"{name}{{{labels}}}", "values": " ".join("_" if v is None else str(v) for v in vals)}

    exp_labels = {"severity": "critical", "namespace": "storage", "cronjob": "openbao-snapshot"}

    def case(name, inputs, evals):
        return {
            "name": name,
            "interval": "10m",
            "input_series": inputs,
            "alert_rule_test": [
                {
                    "eval_time": at,
                    "alertname": "OpenBaoSnapshotBackupStale",
                    "exp_alerts": [{"exp_labels": exp_labels}] if firing else [],
                }
                for at, firing in evals
            ],
        }

    created = s("kube_cronjob_created", lambda t: 0)
    unsuspended = s("kube_cronjob_spec_suspend", lambda t: 0)
    suspended = s("kube_cronjob_spec_suspend", lambda t: 1)
    succ_at_31m = s("kube_cronjob_status_last_successful_time", lambda t: 1860 if t >= 1860 else None)
    # Create at t=28h (100800s > 93600s) so time()-0 > 93600 immediately, proving the `> 0`
    # guard is what suppresses false-firing during the 26h creation grace window.
    t_late = 28 * 3600
    created_late = s("kube_cronjob_created", lambda t: t_late if t >= t_late else None)
    unsusp_late = s("kube_cronjob_spec_suspend", lambda t: 0 if t >= t_late else None)
    succ_zero_late = s("kube_cronjob_status_last_successful_time", lambda t: 0 if t >= t_late else None)
    daily_ok = s("kube_cronjob_status_last_successful_time", lambda t: (t // 86400) * 86400 + 1860)
    ksm_down_cutoff = 27 * 3600 + 1800  # 27h30m
    created_ksm_down = s("kube_cronjob_created", lambda t: 0 if t < ksm_down_cutoff else None)
    unsusp_ksm_down = s("kube_cronjob_spec_suspend", lambda t: 0 if t < ksm_down_cutoff else None)
    manual_recovery = s("kube_cronjob_status_last_successful_time", lambda t: 28 * 3600 if t >= 28 * 3600 else None)

    cases = [
        case("stale: never succeeded within 26h grace", [created, unsuspended], [("25h", False)]),
        case("stale: never succeeded past 26h grace + 15m for", [created, unsuspended], [("27h", True)]),
        case("stale: last_successful_time=0 suppressed in 26h grace and fires after",
             [created_late, unsusp_late, succ_zero_late], [("30h", False), ("55h", True)]),
        case("stale: succeeded once, <26h still fresh", [created, unsuspended, succ_at_31m], [("25h", False)]),
        case("stale: succeeded once, >26h stale", [created, unsuspended, succ_at_31m], [("27h", True)]),
        case("healthy: daily success stays fresh at 40h", [created, unsuspended, daily_ok], [("40h", False)]),
        case("suppressed when suspended", [created, suspended], [("27h", False)]),
        case("silent when CronJob absent", [], [("27h", False)]),
        case("resolves when kube-state-metrics goes down",
             [created_ksm_down, unsusp_ksm_down], [("27h", True), ("28h", False)]),
        case("handles 2-pod kube-state-metrics rollout without duplicate/error",
             [
                 s("kube_cronjob_created", lambda t: 0, 'pod="ksm-1"'),
                 s("kube_cronjob_created", lambda t: 0, 'pod="ksm-2"'),
                 s("kube_cronjob_spec_suspend", lambda t: 0, 'pod="ksm-1"'),
                 s("kube_cronjob_spec_suspend", lambda t: 0, 'pod="ksm-2"'),
             ],
             [("27h", True)]),
        case("clears after manual --from=cronjob job updates last_successful_time",
             [created, unsuspended, manual_recovery], [("27h", True), ("28h30m", False)]),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "rules.yaml").write_text(yaml.safe_dump({"groups": [{"name": "g", "rules": [
            {k: v for k, v in r.items() if k != "annotations"} for r in rules]}]}))
        (t / "tests.yaml").write_text(yaml.safe_dump({
            "rule_files": ["rules.yaml"], "evaluation_interval": "10m", "tests": cases}))
        subprocess.run(["promtool", "check", "rules", str(t / "rules.yaml")], check=True, capture_output=True)
        subprocess.run(["promtool", "test", "rules", str(t / "tests.yaml")], check=True, capture_output=True)
    print(f"PASS: {len(cases)} promtool cases")


def check(root, full=True):
    rules = static(root)
    if full:
        behavior_scripts(root)
    behavior_promtool(rules)


MUTATIONS = (
    ("drop PRIVATE KEY check in helper", LIB, "PRIVATE KEY", "NO_SUCH_MARKER"),
    ("drop expiry check in helper", LIB, "-checkend 86400", "-noout"),
    ("drop RSA algorithm check in helper", LIB, "Public Key Algorithm: rsaEncryption", "Public Key Algorithm: "),
    ("drop RSA >= 3072 bit check in helper", LIB, "-ge 3072", "-ge 1024"),
    ("revert Phase 2 to BASH_SOURCE-only lib path", PHASE2,
     'OPENBAO_BK_LIB="${OPENBAO_BK_LIB:-/home/vagrant/scripts/common/lib-openbao-backup.sh}"',
     'OPENBAO_BK_LIB="/nonexistent-path"'),
    ("Phase 2 silently passes absent CronJob", PHASE2,
     '    absent)\n      DEGRADED_REASONS+=("openbao-snapshot backup: ${OPENBAO_BACKUP_MSG} after waiting for ArgoCD openbao-backup sync (check: kubectl get application openbao-backup -n devtools ; runbook: docs/runbooks/openbao-snapshot-backup.md)")',
     '    absent)\n      echo "ok"'),
    ("Phase 2 silently ignores rc=2 lookup failure", PHASE2,
     '    *)\n      DEGRADED_REASONS+=("openbao-snapshot backup: activation check inconclusive (rc=${ob_rc}): ${OPENBAO_BACKUP_MSG}")',
     '    *)\n      echo "warn"'),
    ("drop Kakao degraded summary", KAKAO, "phase2 completed with degraded item(s):", "phase2 done"),
    ("drop verify-cluster backup check", VERIFY_CLUSTER, "OpenBao snapshot backup DEGRADED", "OpenBao snapshot ok"),
    ("drop verify-backup openbao section", VERIFY_BACKUP, 'should_run "openbao"', "false"),
    ("verify-backup treats parse failure as pass", VERIFY_BACKUP,
     'warn "OpenBao snapshot lastSuccessfulTime could not be parsed: ${OPENBAO_BACKUP_LAST_SUCCESS}"',
     'pass "OpenBao snapshot lastSuccessfulTime: ${OPENBAO_BACKUP_LAST_SUCCESS}"'),
    ("stale alert drops by (namespace, cronjob) labels", ALERTS,
     "max by (namespace, cronjob) (", "max("),
    ("stale alert drops 26h threshold", ALERTS, "> 93600", "> 9360000"),
    ("stale alert drops last_successful_time > 0 guard", ALERTS,
     'and on (namespace, cronjob) (max by (namespace, cronjob) (kube_cronjob_status_last_successful_time{namespace="storage",cronjob="openbao-snapshot"}) > 0)',
     ""),
    ("alert does not suppress when suspended", ALERTS,
     'unless on (namespace, cronjob) (max by (namespace, cronjob) (kube_cronjob_spec_suspend{namespace="storage",cronjob="openbao-snapshot"}) == 1)',
     ""),
    ("runbook drops rebuild section", RUNBOOK, "### 3.7 클러스터 재구축(rebuild) 후 재활성화", "### 3.7 기타"),
    ("drop R384 wiring", REG, "check R384 ", "check R9999 "),
)


def mutation_verify():
    for name, rel, old, new in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            for f in ALL_FILES:
                (t / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / f, t / f)
            text = (t / rel).read_text()
            assert old in text, f"mutation target missing ({name}): {old!r}"
            (t / rel).write_text(text.replace(old, new, 1))
            try:
                check(t, full=(rel in (LIB, PHASE2, VERIFY_CLUSTER, VERIFY_BACKUP)))
            except (AssertionError, subprocess.CalledProcessError, KeyError, ValueError):
                continue
            raise SystemExit(f"MUTATION SURVIVED: {name}")
    print(f"PASS: R384 ({len(MUTATIONS)} mutations rejected)")


if __name__ == "__main__":
    check(REPO, full=True)
    print("PASS: R384 OpenBao snapshot backup activation detection & alerts")
    if "--mutation-verify" in sys.argv:
        mutation_verify()
