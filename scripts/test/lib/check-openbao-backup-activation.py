#!/usr/bin/env python3
"""R384 (D-OPENBAO-BACKUP-ACTIVATION, D-OPENBAO-BACKUP-ALERT, #757):
OpenBao encrypted snapshot backup activation detection at install/verify time and runtime Prometheus alerts.

Verifies:
1. Static invariants on lib-openbao-backup.sh, Phase 2 (06-phase2-start.sh / up.sh / provision-kakao.sh),
   verify-cluster.sh, verify-backup.sh, show-credentials.sh, prometheus-alerts.yaml, and the runbook.
2. Behavioral tests of lib-openbao-backup.sh and the real CLI callers (verify-backup.sh, verify-cluster.sh,
   show-credentials.sh) against a fake kubectl + real openssl x509 certificates.
3. Behavioral promtool tests of OpenBaoSnapshotBackupFailed and OpenBaoSnapshotBackupStale, including
   the #757 never-succeeded / pod-cannot-start case, stale >26h, healthy, fresh grace, suspended, and absent.
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
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]

LIB = "scripts/common/lib-openbao-backup.sh"
PHASE2 = "scripts/cluster/06-phase2-start.sh"
UP = "scripts/up.sh"
KAKAO = "scripts/cloud/provision-kakao.sh"
VERIFY_CLUSTER = "scripts/test/verify-cluster.sh"
VERIFY_BACKUP = "scripts/test/verify-backup.sh"
SHOW_CREDS = "scripts/test/show-credentials.sh"
ALERTS = "gitops/resources/prometheus-alerts.yaml"
RUNBOOK = "docs/runbooks/openbao-snapshot-backup.md"
REG = "scripts/test/regression-check-kakao.sh"

ALL_FILES = (LIB, PHASE2, UP, KAKAO, VERIFY_CLUSTER, VERIFY_BACKUP, SHOW_CREDS, ALERTS, RUNBOOK, REG)
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
        "-fingerprint -sha256",
        "spec.get(\"suspend\")",
    ):
        assert needle in lib, f"{LIB} missing {needle!r}"

    p2 = (root / PHASE2).read_text()
    for needle in (
        "PHASE2_DEGRADED=",
        "openbao_backup_check_activation",
        "Phase 2 DEGRADED CONDITIONS",
        'rm -f "${PHASE2_MARKER}" "${PHASE2_DEGRADED}"',
    ):
        assert needle in p2, f"{PHASE2} missing {needle!r}"
    assert p2.rfind('date -u \'+%Y-%m-%dT%H:%M:%SZ\' > "${PHASE2_MARKER}"') > p2.rfind("done\n"), \
        f"{PHASE2} must still write PHASE2_MARKER after the script loop"

    up = (root / UP).read_text()
    assert "PHASE2_DEGRADED=" in up and "Phase 2 completed with degraded item(s):" in up, \
        f"{UP} must surface PHASE2_DEGRADED summary"

    kakao = (root / KAKAO).read_text()
    assert "/var/lib/narwhal/phase2-degraded" in kakao and "phase2 completed with degraded item(s):" in kakao, \
        f"{KAKAO} must surface phase2-degraded summary"

    vc = (root / VERIFY_CLUSTER).read_text()
    assert "openbao_backup_check_activation" in vc and "OpenBao snapshot backup DEGRADED" in vc, \
        f"{VERIFY_CLUSTER} must surface degraded OpenBao backup"

    vb = (root / VERIFY_BACKUP).read_text()
    assert "openbao_backup_check_activation" in vb and 'should_run "openbao"' in vb \
        and "OpenBao snapshot backup DEGRADED" in vb, \
        f"{VERIFY_BACKUP} must check OpenBao snapshot backup activation"

    sc = (root / SHOW_CREDS).read_text()
    assert "openbao_backup_check_activation" in sc and 'row "Snapshot backup"' in sc, \
        f"{SHOW_CREDS} must show OpenBao Snapshot backup status"
    assert sc.index("NARWHAL_CLUSTER_MARKER") < sc.index("openbao_backup_check_activation"), \
        f"{SHOW_CREDS} must run NARWHAL_CLUSTER_MARKER guard before checking OpenBao backup"

    runbook = (root / RUNBOOK).read_text()
    for needle in ("D-OPENBAO-BACKUP-ACTIVATION", "### 3.7 클러스터 재구축(rebuild) 후 재활성화", "## 4. 정기 점검"):
        assert needle in runbook, f"{RUNBOOK} missing {needle!r}"
    assert "신선도 알림이 없다" not in runbook and "신선도 알림은 아직 없다" not in runbook, \
        f"{RUNBOOK} still claims there is no freshness alert"

    alerts_text = (root / ALERTS).read_text()
    assert "D-OPENBAO-BACKUP-ALERT" in alerts_text, f"{ALERTS} missing D-OPENBAO-BACKUP-ALERT comment"
    rules_doc = yaml.safe_load(alerts_text)
    group = next((g for g in rules_doc["spec"]["groups"] if g["name"] == "openbao-backup"), None)
    assert group is not None, f"{ALERTS} missing openbao-backup group"
    by_name = {r["alert"]: r for r in group["rules"]}
    assert set(by_name) == {"OpenBaoSnapshotBackupFailed", "OpenBaoSnapshotBackupStale"}, set(by_name)

    failed = by_name["OpenBaoSnapshotBackupFailed"]
    assert failed["for"] == "30m" and failed["labels"]["severity"] == "warning"
    f_expr = " ".join(failed["expr"].split())
    assert SEL in f_expr and "kube_cronjob_status_last_schedule_time" in f_expr \
        and "kube_cronjob_status_last_successful_time" in f_expr \
        and "unless on()" in f_expr and "kube_cronjob_spec_suspend" in f_expr, f_expr

    stale = by_name["OpenBaoSnapshotBackupStale"]
    assert stale["for"] == "15m" and stale["labels"]["severity"] == "critical"
    s_expr = " ".join(stale["expr"].split())
    assert SEL in s_expr and "93600" in s_expr and "kube_cronjob_created" in s_expr \
        and "kube_cronjob_status_last_successful_time" in s_expr \
        and "unless on()" in s_expr and "kube_cronjob_spec_suspend" in s_expr, s_expr

    expected_anchor = "/docs/runbooks/openbao-snapshot-backup.md#" + slug("## 4. 정기 점검")
    for r in (failed, stale):
        assert r["annotations"]["runbook_url"].endswith(expected_anchor), r["annotations"]["runbook_url"]

    reg = (root / REG).read_text()
    assert "check R384 " in reg and "check-openbao-backup-activation.py --mutation-verify" in reg, \
        f"{REG} must wire R384 with --mutation-verify"
    return [failed, stale]


_CERTS = {}


def _certs():
    if _CERTS:
        return _CERTS
    real = shutil.which("openssl")
    assert real, "openssl required for R384"
    tmp = tempfile.TemporaryDirectory()
    d = Path(tmp.name)

    def mk(name, days):
        subprocess.run(
            [real, "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-days", str(days), "-nodes",
             "-subj", "/CN=r384-test", "-keyout", str(d / f"{name}.key"), "-out", str(d / f"{name}.pem")],
            check=True, capture_output=True,
        )
        fpr = subprocess.run(
            [real, "x509", "-in", str(d / f"{name}.pem"), "-noout", "-fingerprint", "-sha256"],
            check=True, capture_output=True, text=True,
        ).stdout.strip().split("=", 1)[1]
        return (d / f"{name}.pem").read_text(), (d / f"{name}.key").read_text(), fpr

    g_pem, g_key, g_fpr = mk("good", 30)
    o_pem, _, _ = mk("other", 30)
    s_pem, _, _ = mk("short", 1)
    time.sleep(2)  # -checkend 86400 treats 1-day cert as expiring within 24h
    _CERTS.update(_tmp=tmp, good_pem=g_pem, good_key=g_key, good_fpr=g_fpr, other_pem=o_pem, short_pem=s_pem)
    return _CERTS


FAKE_KUBECTL = """#!/bin/bash
set -eu
args=" $* "
case "${args}" in
  *" get --raw /readyz "*) exit 0 ;;
  *" get clusterissuer/narwhal-ca-issuer "*) exit 0 ;;
  *" get cronjob openbao-snapshot "*)
    [ -n "${FAKE_CJ_JSON:-}" ] && printf '%s' "${FAKE_CJ_JSON}"
    exit 0 ;;
  *" get configmap openbao-backup-recipient "*)
    [ -n "${FAKE_CM_JSON:-}" ] && printf '%s' "${FAKE_CM_JSON}"
    exit 0 ;;
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


def _cm_json(pem_text):
    return json.dumps({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "openbao-backup-recipient", "namespace": "storage"},
        "data": {"recipient.pem": pem_text},
    })


def behavior_scripts(root):
    c = _certs()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lib = root / LIB

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        kc = t / "kubectl"
        kc.write_text(FAKE_KUBECTL)
        kc.chmod(kc.stat().st_mode | stat.S_IXUSR)

        def run_lib(cj, cm):
            env = dict(os.environ, PATH=f"{t}:{os.environ['PATH']}", FAKE_CJ_JSON=cj, FAKE_CM_JSON=cm)
            cmd = f'set -euo pipefail; source "{lib}"; rc=0; openbao_backup_check_activation || rc=$?; printf "%s|%s|%s\\n" "$rc" "$OPENBAO_BACKUP_STATE" "$OPENBAO_BACKUP_MSG"'
            p = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, check=True)
            rc_s, state, msg = p.stdout.strip().split("|", 2)
            return int(rc_s), state, msg

        assert run_lib("", "")[:2] == (0, "absent")
        assert run_lib(_cj_json(c["good_fpr"], suspend=True), "")[:2] == (0, "suspended")
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), "")
        assert (rc, st) == (1, "degraded") and "missing" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), _cm_json(c["other_pem"]))
        assert (rc, st) == (1, "degraded") and "differs" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), _cm_json(c["good_pem"] + c["good_key"]))
        assert (rc, st) == (1, "degraded") and "PRIVATE KEY" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"]), _cm_json(c["short_pem"]))
        assert (rc, st) == (1, "degraded") and "expired" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(""), _cm_json(c["good_pem"]))
        assert (rc, st) == (1, "degraded") and "empty" in msg, (rc, st, msg)
        rc, st, msg = run_lib(_cj_json(c["good_fpr"].lower()), _cm_json(c["good_pem"]))
        assert (rc, st) == (0, "ok"), (rc, st, msg)

        # Verify real CLI callers against fake kubectl (missing vs healthy)
        env_miss = dict(os.environ, PATH=f"{t}:{os.environ['PATH']}",
                        FAKE_CJ_JSON=_cj_json(c["good_fpr"], last_schedule=now_iso), FAKE_CM_JSON="")
        env_ok = dict(os.environ, PATH=f"{t}:{os.environ['PATH']}",
                      FAKE_CJ_JSON=_cj_json(c["good_fpr"], last_success=now_iso, last_schedule=now_iso),
                      FAKE_CM_JSON=_cm_json(c["good_pem"]))

        p_vb_miss = subprocess.run(["bash", str(root / VERIFY_BACKUP), "--section=openbao"],
                                   env=env_miss, capture_output=True, text=True)
        assert p_vb_miss.returncode == 1 and "[FAIL] OpenBao snapshot backup DEGRADED" in p_vb_miss.stdout, p_vb_miss.stdout

        p_vb_ok = subprocess.run(["bash", str(root / VERIFY_BACKUP), "--section=openbao"],
                                 env=env_ok, capture_output=True, text=True)
        assert p_vb_ok.returncode == 0 and "[PASS] OpenBao snapshot backup:" in p_vb_ok.stdout, p_vb_ok.stdout

        p_vc_miss = subprocess.run(["bash", str(root / VERIFY_CLUSTER), "--section=platform"],
                                   env=env_miss, capture_output=True, text=True)
        assert p_vc_miss.returncode == 0 and "WARN  OpenBao snapshot backup DEGRADED" in p_vc_miss.stdout, p_vc_miss.stdout

        p_sc_miss = subprocess.run(["bash", str(root / SHOW_CREDS)],
                                   env=env_miss, capture_output=True, text=True)
        assert p_sc_miss.returncode == 0 and "Snapshot backup" in p_sc_miss.stdout and "DEGRADED:" in p_sc_miss.stdout, p_sc_miss.stdout

        p_sc_ok = subprocess.run(["bash", str(root / SHOW_CREDS)],
                                 env=env_ok, capture_output=True, text=True)
        assert p_sc_ok.returncode == 0 and "Snapshot backup" in p_sc_ok.stdout and "OK (recipient ConfigMap" in p_sc_ok.stdout, p_sc_ok.stdout


def behavior_promtool(rules):
    if not shutil.which("promtool"):
        print("SKIP: promtool unavailable; R384 alert cases not run")
        return

    step, n = 600, 6 * 24 * 2  # 10m samples over 48h

    def s(name, fn):
        vals = [fn(k * step) for k in range(n)]
        return {"series": f'{name}{{{SEL}}}', "values": " ".join("_" if v is None else str(v) for v in vals)}

    def case(name, inputs, alert, at, firing):
        sev = {"severity": next(r["labels"]["severity"] for r in rules if r["alert"] == alert)}
        return {"name": name, "interval": "10m", "input_series": inputs,
                "alert_rule_test": [{"eval_time": at, "alertname": alert,
                                     "exp_alerts": [{"exp_labels": sev}] if firing else []}]}

    created = s("kube_cronjob_created", lambda t: 0)
    unsuspended = s("kube_cronjob_spec_suspend", lambda t: 0)
    suspended = s("kube_cronjob_spec_suspend", lambda t: 1)
    sched_at_30m = s("kube_cronjob_status_last_schedule_time", lambda t: 1800 if t >= 1800 else None)
    succ_at_31m = s("kube_cronjob_status_last_successful_time", lambda t: 1860 if t >= 1860 else None)
    sched_day2_fail = s("kube_cronjob_status_last_schedule_time", lambda t: 88200 if t >= 88200 else 1800)
    daily_ok = s("kube_cronjob_status_last_successful_time", lambda t: (t // 86400) * 86400 + 1860)

    cases = [
        # OpenBaoSnapshotBackupFailed
        case("failed: #757 never succeeded after first schedule", [created, unsuspended, sched_at_30m],
             "OpenBaoSnapshotBackupFailed", "1h", True),
        case("failed: zero last_successful_time gauge", [created, unsuspended, sched_at_30m,
             s("kube_cronjob_status_last_successful_time", lambda t: 0)],
             "OpenBaoSnapshotBackupFailed", "1h", True),
        case("failed: succeeded day 1, failed day 2", [created, unsuspended, sched_day2_fail, succ_at_31m],
             "OpenBaoSnapshotBackupFailed", "25h10m", True),
        case("healthy: first run succeeded in 1m", [created, unsuspended, sched_at_30m, succ_at_31m],
             "OpenBaoSnapshotBackupFailed", "2h", False),
        case("suppressed when suspended (Failed)", [created, suspended, sched_at_30m],
             "OpenBaoSnapshotBackupFailed", "2h", False),
        case("silent when CronJob absent (Failed)", [], "OpenBaoSnapshotBackupFailed", "2h", False),
        # OpenBaoSnapshotBackupStale
        case("stale: never succeeded within 26h grace", [created, unsuspended],
             "OpenBaoSnapshotBackupStale", "25h", False),
        case("stale: never succeeded past 26h grace + 15m for", [created, unsuspended],
             "OpenBaoSnapshotBackupStale", "27h", True),
        case("stale: succeeded once, <26h still fresh", [created, unsuspended, succ_at_31m],
             "OpenBaoSnapshotBackupStale", "25h", False),
        case("stale: succeeded once, >26h stale", [created, unsuspended, succ_at_31m],
             "OpenBaoSnapshotBackupStale", "27h", True),
        case("healthy: daily success stays fresh at 40h", [created, unsuspended, daily_ok],
             "OpenBaoSnapshotBackupStale", "40h", False),
        case("suppressed when suspended (Stale)", [created, suspended],
             "OpenBaoSnapshotBackupStale", "27h", False),
        case("silent when CronJob absent (Stale)", [], "OpenBaoSnapshotBackupStale", "27h", False),
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
    ("drop Phase 2 degraded check", PHASE2, "openbao_backup_check_activation", "true"),
    ("drop up.sh degraded summary", UP, "Phase 2 completed with degraded item(s):", "Phase 2 done"),
    ("drop Kakao degraded summary", KAKAO, "phase2 completed with degraded item(s):", "phase2 done"),
    ("drop verify-cluster backup check", VERIFY_CLUSTER, "OpenBao snapshot backup DEGRADED", "OpenBao snapshot ok"),
    ("drop verify-backup openbao section", VERIFY_BACKUP, 'should_run "openbao"', "false"),
    ("drop show-credentials backup check", SHOW_CREDS, 'openbao_backup_check_activation || ob_rc=$?', 'OPENBAO_BACKUP_STATE=absent'),
    ("failed alert ignores never-succeeded", ALERTS,
     "unless on() (max(kube_cronjob_status_last_successful_time{namespace=\"storage\",cronjob=\"openbao-snapshot\"}) > 0)",
     "and on() (max(kube_cronjob_status_last_successful_time{namespace=\"storage\",cronjob=\"openbao-snapshot\"}) > 0)"),
    ("stale alert drops 26h threshold", ALERTS, "> 93600", "> 9360000"),
    ("alert does not suppress when suspended", ALERTS,
     "unless on() (max(kube_cronjob_spec_suspend{namespace=\"storage\",cronjob=\"openbao-snapshot\"}) == 1)",
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
                check(t, full=(rel in (LIB, VERIFY_CLUSTER, VERIFY_BACKUP, SHOW_CREDS)))
            except (AssertionError, subprocess.CalledProcessError, KeyError, ValueError):
                continue
            raise SystemExit(f"MUTATION SURVIVED: {name}")
    print(f"PASS: R384 ({len(MUTATIONS)} mutations rejected)")


if __name__ == "__main__":
    check(REPO, full=True)
    print("PASS: R384 OpenBao snapshot backup activation detection & alerts")
    if "--mutation-verify" in sys.argv:
        mutation_verify()
