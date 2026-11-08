#!/usr/bin/env python3
"""R322 (D-SECDB-AUTO, #585): automatic Trivy DB mirror refresh with bounded storage.

Static pins:
  * the CronJob (gitops/resources/trivy-db-refresh.yaml): digest-pinned quay.io skopeo image that matches
    images.txt / image-digests.tsv, non-root, read-only root, no capabilities, no API token, requests and
    limits, Forbid concurrency, a Job deadline, credentials only from Secret trivy-db-refresh, Narwhal CA
    from narwhal-ca-cert, TLS verification never disabled, only the trivy-db and trivy-java-db sources of
    security-db.txt (never trivy-checks) into the paths trivy-operator.yaml pulls;
  * the Application template is gated off for provider kakao and by securityDbAutoRefresh.enabled (helm);
  * 08-6 distributes the CA to security-system;
  * both alerts exist, point at the CronJob and at a runbook heading that exists.
Behavior:
  * the CronJob script runs against a stub skopeo: a current mirror copies nothing, a stale one is copied
    with --preserve-digests, the approver is auto-refresh, a source below the size floor, an unreadable
    source and a digest mismatch after the copy fail without hiding the other artifact, and the password
    never reaches argv or the log;
  * the 08-5 Harbor block runs against a fake Harbor API: robot scoped to project trivy-db with repository
    push+pull only, created once and left alone on a re-run, secret reset when the Secret is missing or invalid,
    retention keeps 2 per repository including untagged, GC created only when none exists and never with
    delete_untagged;
  * the fetch-security-db.sh prune loop removes only directories that left the list;
  * promtool: stale, never-succeeded and failed refreshes fire, healthy and still-running ones do not.
--mutation-verify re-runs everything on copies with one defect each and requires every defect to be rejected.
"""

import json
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CRON = "gitops/resources/trivy-db-refresh.yaml"
APP = "gitops/charts/narwhal-apps/templates/trivy-db-refresh.yaml"
CHART = "gitops/charts/narwhal-apps"
VALUES = CHART + "/values.yaml"
OPERATOR = CHART + "/templates/trivy-operator.yaml"
REG = "scripts/cluster/08-5-registry.sh"
TLS = "scripts/cluster/08-6-tls-routes.sh"
ALERTS = "gitops/resources/prometheus-alerts.yaml"
FETCH = "scripts/airgap/lib/fetch-security-db.sh"
LIST = "scripts/airgap/security-db.txt"
README = "scripts/airgap/README.md"
IMAGES = "scripts/airgap/images.txt"
DIGESTS = "scripts/airgap/lib/image-digests.tsv"
FILES = [CRON, REG, TLS, ALERTS, FETCH, LIST, README, IMAGES, DIGESTS]
CHART_FILES = [CHART + "/Chart.yaml", VALUES]  # templates are copied wholesale for the helm render
RUNBOOK_HEADING = "### 보안 DB 자동 갱신 (D-SECDB-AUTO)"
SEL = 'namespace="security-system",cronjob="trivy-db-refresh"'
PASSWORD = "S3cretRobotPw-Aa1"


def slug(heading):
    text = heading.lstrip("#").strip().lower()
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text))


def cronjob(root):
    docs = list(yaml.safe_load_all((root / CRON).read_text()))
    jobs = [d for d in docs if d and d.get("kind") == "CronJob"]
    sas = [d for d in docs if d and d.get("kind") == "ServiceAccount"]
    assert len(jobs) == 1 and len(sas) == 1, "CronJob + ServiceAccount expected"
    return jobs[0], sas[0]


def script_of(job):
    return job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["command"][2]


# ---------------------------------------------------------------- static
def static(root):
    job, sa = cronjob(root)
    text = (root / CRON).read_text()
    assert sa["automountServiceAccountToken"] is False, "ServiceAccount must not automount a token"
    spec = job["spec"]
    assert spec["concurrencyPolicy"] == "Forbid", "concurrencyPolicy must be Forbid"
    assert spec["schedule"].strip(), "schedule missing"
    jt = spec["jobTemplate"]["spec"]
    assert jt.get("activeDeadlineSeconds"), "Job needs an activeDeadlineSeconds"
    pod = jt["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False, "pod must not automount a token"
    assert pod["securityContext"]["runAsNonRoot"] is True and pod["securityContext"].get("runAsUser"), "pod must run non-root"
    c = pod["containers"][0]
    sc = c["securityContext"]
    assert sc["readOnlyRootFilesystem"] is True and sc["allowPrivilegeEscalation"] is False, sc
    assert sc["capabilities"]["drop"] == ["ALL"], sc
    for kind in ("requests", "limits"):
        assert {"cpu", "memory"} <= set(c["resources"].get(kind, {})), f"resources.{kind} needs cpu and memory"

    m = re.fullmatch(r"(quay\.io/skopeo/stable:(v\d+\.\d+\.\d+))@(sha256:[0-9a-f]{64})", c["image"])
    assert m, f"image must be quay.io/skopeo/stable:<tag>@sha256:<digest>, got {c['image']}"
    tag_ref, digest = m.group(1), m.group(3)
    rows = [l.split("\t") for l in (root / DIGESTS).read_text().splitlines() if l.startswith(tag_ref + "\t")]
    assert len(rows) == 1 and rows[0][1] == digest, f"image-digests.tsv must pin {tag_ref} to {digest}"
    assert tag_ref in (root / IMAGES).read_text().splitlines(), f"{tag_ref} missing from images.txt"

    env = {e["name"]: e for e in c["env"]}
    for name, key in (("HARBOR_REGISTRY", "registry"), ("HARBOR_USERNAME", "username"), ("HARBOR_PASSWORD", "password")):
        ref = env.get(name, {}).get("valueFrom", {}).get("secretKeyRef", {})
        assert ref.get("name") == "trivy-db-refresh" and ref.get("key") == key, f"{name} must come from Secret trivy-db-refresh/{key}"
    assert not any("value" in e and re.search(r"pass|secret|token", e["name"], re.I) for e in c["env"]), "literal credential in env"
    vols = {v["name"]: v for v in pod["volumes"]}
    assert vols["narwhal-ca"]["secret"]["secretName"] == "narwhal-ca-cert", "CA must come from narwhal-ca-cert"
    assert any(m_["mountPath"] == "/etc/narwhal-ca" for m_ in c["volumeMounts"]), "CA not mounted"

    body = script_of(job)
    for bad in ("tls-verify=false", "--insecure", "skip-tls"):
        assert bad not in text, f"TLS verification must stay on ({bad})"
    for need in ("--preserve-digests", "APPROVED_BY=auto-refresh", "skipped-current", "--cert-dir", "--dest-cert-dir"):
        assert need in body, f"script lost {need}"

    # Sources and destinations follow security-db.txt and what trivy-operator pulls.
    listed = dict(l.split("\t") for l in (root / LIST).read_text().splitlines() if l.strip() and not l.startswith("#"))
    op = yaml.safe_load((root / OPERATOR).read_text())["spec"]["source"]["helm"]["valuesObject"]["trivy"]
    calls = re.findall(r'refresh (\S+) (ghcr\.io/\S+) \\\n\s*"\$\{HARBOR_REGISTRY\}/([^"]+)" (\d+)', body)
    assert {n for n, *_ in calls} == {"trivy-db", "trivy-java-db"}, f"refresh set must be trivy-db and trivy-java-db, got {calls}"
    for name, src, dst, floor in calls:
        assert listed[name] == src, f"{name}: {src} != security-db.txt {listed[name]}"
        want_repo = op["dbRepository"] if name == "trivy-db" else op["javaDbRepository"]
        assert dst == f"{want_repo}:{src.rsplit(':', 1)[1]}", f"{name}: destination {dst} != trivy-operator {want_repo}"
        assert int(floor) >= 20000000, f"{name}: size floor {floor} too low"
    assert "trivy-checks" not in body, "trivy-checks is frozen upstream and stays a manual push"

    # Application gating (text), helm render in behavior().
    app = (root / APP).read_text()
    assert app.splitlines()[0] == '{{- if and .Values.securityDbAutoRefresh.enabled (ne .Values.provider "kakao") }}', "gate"
    assert 'include: "trivy-db-refresh.yaml"' in app and "namespace: security-system" in app
    assert yaml.safe_load((root / VALUES).read_text())["securityDbAutoRefresh"]["enabled"] is True

    tls = (root / TLS).read_text()
    assert re.search(r"for ns in [^;]*\bsecurity-system\b[^;]*; do\n\s+kubectl create namespace", tls), "08-6 must distribute narwhal-ca-cert to security-system"

    reg = (root / REG).read_text()
    assert "SECDB_KEEP=2" in reg, "retention keep count"
    for need in ('"registry":base64.b64encode', '"username":base64.b64encode', '"password":base64.b64encode', '"namespace":"security-system"'):
        assert need in reg, f"08-5 Secret lost {need}"
    assert "read_secret_field trivy-db-refresh security-system password" in reg, "08-5 must reuse an existing robot secret"
    assert 'secret = created.get("secret")' in reg, "08-5 must persist Harbor's generated create response secret"
    assert 'kubectl apply -f -' in reg and 'HARBOR_ROBOT_SECRET_VALUE' in reg, "robot Secret must be applied from stdin"
    assert '--dest-cert-dir "${CERT_ROOT}/${HARBOR_REGISTRY}"' in text, "skopeo CA dir must directly contain ca.crt"

    rules = yaml.safe_load((root / ALERTS).read_text())
    found = {r["alert"]: r for g in rules["spec"]["groups"] for r in g["rules"] if r.get("alert", "").startswith("TrivyDbRefresh")}
    assert set(found) == {"TrivyDbRefreshFailed", "TrivyDbRefreshStale"}, found.keys()
    readme = (root / README).read_text()
    assert readme.count(RUNBOOK_HEADING + "\n") == 1, "runbook heading"
    for rule in found.values():
        assert rule["labels"]["severity"] == "warning"
        assert SEL in " ".join(rule["expr"].split()), "alert must select the trivy-db-refresh CronJob"
        assert rule["annotations"]["runbook_url"].endswith("/scripts/airgap/README.md#" + slug(RUNBOOK_HEADING)), "runbook anchor"

    failed = found["TrivyDbRefreshFailed"]
    assert re.search(r"\b1h\b", str(failed.get("for", ""))), f"TrivyDbRefreshFailed for window: {failed.get('for')}"
    failed_expr = " ".join(failed["expr"].split())
    assert re.search(
        rf'max\(kube_cronjob_status_last_schedule_time\{{{re.escape(SEL)}\}}\)\s*>\s*max\(kube_cronjob_status_last_successful_time\{{{re.escape(SEL)}\}}\)',
        failed_expr,
    ), f"TrivyDbRefreshFailed expr comparison: {failed_expr}"

    stale = found["TrivyDbRefreshStale"]
    assert re.search(r"\b30m\b", str(stale.get("for", ""))), f"TrivyDbRefreshStale for window: {stale.get('for')}"
    stale_expr = " ".join(stale["expr"].split())
    assert re.search(r">\s*\b129600\b", stale_expr), f"TrivyDbRefreshStale 36h threshold: {stale_expr}"
    assert re.search(r">\s*\b43200\b", stale_expr), f"TrivyDbRefreshStale 12h threshold: {stale_expr}"
    return job, found


# ---------------------------------------------------------------- behavior: CronJob script
def run_script(root, case):
    job, _ = cronjob(root)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        bindir, state = t / "bin", t / "state"
        for d in (bindir, state / "src", state / "dst"):
            d.mkdir(parents=True)
        (t / "ca.crt").write_text("CA")
        if not shutil.which("sha256sum"):
            (bindir / "sha256sum").write_text('#!/bin/bash\nexec shasum -a 256 "$@"\n')
        (bindir / "skopeo").write_text(STUB)
        for f in bindir.iterdir():
            f.chmod(0o755)
        for kind in ("src", "dst"):
            for ref, content in case.get(kind, {}).items():
                (state / kind / ref.replace("/", "_").replace(":", "_")).write_text(content)
        body = script_of(job)
        body = body.replace("CERT_ROOT=/tmp/certs", f"CERT_ROOT={t}/certs")
        body = body.replace("REGISTRY_AUTH_FILE=/tmp/auth.json", f"REGISTRY_AUTH_FILE={t}/auth.json")
        body = body.replace("/etc/narwhal-ca/ca.crt", f"{t}/ca.crt")
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", STUB_STATE=str(state),
                   HARBOR_REGISTRY="harbor.example.test", HARBOR_USERNAME="robot$trivy-db+trivy-db-refresh",
                   HARBOR_PASSWORD=PASSWORD, **case.get("env", {}))
        p = subprocess.run(["bash", "-c", body], env=env, capture_output=True, text=True, timeout=60)
        calls = (state / "calls").read_text() if (state / "calls").exists() else ""
        return p.returncode, p.stdout, p.stderr, calls


STUB = r'''#!/bin/bash
# stub skopeo: refs map to files under $STUB_STATE/{src,dst}
key() { echo "$1" | sed -e 's#^docker://##' -e 's#/#_#g' -e 's#:#_#g'; }
sub=$1; shift
echo "$sub $*" >> "$STUB_STATE/calls"
case "$sub" in
  login) cat >/dev/null; [ -z "${STUB_LOGIN_FAIL:-}" ] ;;
  inspect)
    ref=""; for a in "$@"; do case "$a" in docker://*) ref=$a ;; esac; done
    k=$(key "$ref")
    case "$ref" in
      docker://ghcr.io/*) f="$STUB_STATE/src/$k" ;;
      *) f="$STUB_STATE/dst/$k" ;;
    esac
    [ -f "$f" ] && cat "$f" || exit 1 ;;
  copy)
    src=""; dst=""
    for a in "$@"; do case "$a" in docker://ghcr.io/*) src=$a ;; docker://*) dst=$a ;; esac; done
    [ -z "${STUB_COPY_FAIL:-}" ] || exit 1
    if [ -n "${STUB_COPY_CORRUPT:-}" ]; then echo corrupted > "$STUB_STATE/dst/$(key "$dst")"
    else cp "$STUB_STATE/src/$(key "$src")" "$STUB_STATE/dst/$(key "$dst")"; fi ;;
esac
'''

DB = "ghcr.io/aquasecurity/trivy-db:2"
JAVA = "ghcr.io/aquasecurity/trivy-java-db:1"
DB_DST = "harbor.example.test/trivy-db/aquasecurity/trivy-db:2"
JAVA_DST = "harbor.example.test/trivy-db/aquasecurity/trivy-java-db:1"


def manifest(size, tag):
    return json.dumps({"schemaVersion": 2, "config": {"size": 2}, "layers": [{"size": size}], "tag": tag})


def script_behavior(root):
    big_db, big_java = manifest(150_000_000, "a"), manifest(990_000_000, "b")
    src = {DB: big_db, JAVA: big_java}

    rc, out, err, calls = run_script(root, {"src": src, "dst": {DB_DST: big_db, JAVA_DST: big_java}})
    assert rc == 0 and out.count("skipped-current") == 2 and "copy " not in calls, ("current mirror must copy nothing", rc, out, err)

    rc, out, err, calls = run_script(root, {"src": src, "dst": {DB_DST: manifest(150_000_000, "old"), JAVA_DST: big_java}})
    assert rc == 0 and calls.count("copy ") == 1, ("stale trivy-db must be copied once", calls)
    copy = [l for l in calls.splitlines() if l.startswith("copy ")][0]
    assert "--preserve-digests" in copy and "--dest-cert-dir" in copy and DB in copy and DB_DST in copy, copy
    promoted = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    assert {p["action"] for p in promoted} == {"promoted", "skipped-current"}, promoted
    assert all(p["approved_by"] == "auto-refresh" for p in promoted), promoted

    rc, out, err, calls = run_script(root, {"src": {DB: manifest(1000, "tiny"), JAVA: big_java}, "dst": {}})
    assert rc == 1 and "rejected-size" in out and calls.count("copy ") == 1 and JAVA in calls, ("size floor", rc, out, calls)

    rc, out, err, calls = run_script(root, {"src": {JAVA: big_java}, "dst": {}})
    assert rc == 1 and "copy " in calls and JAVA_DST in calls and DB_DST not in calls, ("unreadable source must not stop the other", calls)

    rc, out, err, calls = run_script(root, {"src": src, "dst": {}, "env": {"STUB_COPY_CORRUPT": "1"}})
    assert rc == 1 and out.count("rejected-digest") == 2, ("digest mismatch after copy", rc, out)

    rc, out, err, calls = run_script(root, {"src": src, "dst": {}, "env": {"STUB_COPY_FAIL": "1"}})
    assert rc == 1, "failed copy must fail the Job"

    rc, out, err, calls = run_script(root, {"src": src, "dst": {}})
    assert PASSWORD not in calls and PASSWORD not in out + err, "password leaked into argv or output"


# ---------------------------------------------------------------- behavior: 08-5 Harbor block
class FakeHarbor(BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *a):
        pass

    def _send(self, code, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else None

    def _do(self, method):
        s = self.state
        path = urllib.parse.urlsplit(self.path).path
        body = self._body()
        s["calls"].append((method, path, body))
        # D2: token auth is origin-rooted; reject all other unprefixed routes.
        # Cost: explicit route split; escape: revise with Harbor's upstream contract.
        if (method, path) == ("GET", "/service/token"):
            auth = self.headers.get("Authorization", "")
            valid = auth == "Basic " + base64.b64encode((s["robots"][0]["name"] + ":" + s["robot_secret"]).encode()).decode()
            return self._send(200 if valid else 401, {})
        if not path.startswith("/api/v2.0/"):
            return self._send(404, {})
        path = path[len("/api/v2.0"):]
        s["calls"][-1] = (method, path, body)
        if (method, path) == ("GET", "/projects/trivy-db"):
            return self._send(200, {"project_id": 2, "metadata": ({"retention_id": "9"} if s["retention"] else {})})
        if (method, path) == ("GET", "/robots"):
            return self._send(200, s["robots"])
        if (method, path) == ("POST", "/robots"):
            s["robots"].append({"id": 7, "name": "robot$trivy-db+" + body["name"]})
            s["robot_secret"] = "HarborGenerated-Secret9"
            return self._send(201, {"id": 7, "secret": s["robot_secret"]})
        if method == "PATCH" and path == "/robots/7":
            s["robot_secret"] = body["secret"]
            return self._send(200, {"secret": ""})
        if (method, path) == ("POST", "/retentions"):
            s["retention"] = True
            return self._send(201, {})
        if (method, path) == ("PUT", "/retentions/9"):
            return self._send(200, {})
        if (method, path) == ("GET", "/system/gc/schedule"):
            return self._send(200, s["gc"])
        if (method, path) in (("POST", "/system/gc/schedule"), ("PUT", "/system/gc/schedule")):
            s["gc"] = {"schedule": body["schedule"]}
            return self._send(200, {})
        return self._send(404, {})

    do_GET = lambda self: self._do("GET")
    do_POST = lambda self: self._do("POST")
    do_PUT = lambda self: self._do("PUT")
    do_PATCH = lambda self: self._do("PATCH")


def harbor_python(root):
    reg = (root / REG).read_text()
    blocks = [b for b in re.findall(r"python3 - <<'PYEOF'\n(.*?)\nPYEOF\n", reg, re.S) if "ROBOT_USER" in b]
    assert len(blocks) == 1, "08-5 Harbor robot/retention/GC block missing"
    return blocks[0]


def harbor_run(code, state, reset):
    FakeHarbor.state = state
    srv = HTTPServer(("127.0.0.1", 0), FakeHarbor)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        env = dict(os.environ, HARBOR_API=f"http://127.0.0.1:{srv.server_port}/api/v2.0",
                   HARBOR_REGISTRY=f"127.0.0.1:{srv.server_port}", HARBOR_ADMIN_PASS="adminpw",
                   SECDB_KEEP="2", SECDB_ROBOT_PW=PASSWORD, SECDB_ROBOT_HAS_SECRET="1" if not reset else "0")
        p = subprocess.run([sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True, timeout=60)
    finally:
        srv.shutdown()
    return p


def harbor_behavior(root):
    code = harbor_python(root)

    def fresh(**kw):
        return dict({"calls": [], "robots": [], "retention": False, "gc": {}}, **kw)

    st = fresh()
    p = harbor_run(code, st, reset=True)
    assert p.returncode == 0 and "ROBOT_USER=robot$trivy-db+trivy-db-refresh" in p.stdout, (p.stdout, p.stderr)
    assert PASSWORD not in p.stdout + p.stderr, "robot secret printed"
    assert "ROBOT_SECRET=HarborGenerated-Secret9" in p.stdout, "create response secret must be returned for Secret storage"
    post = [c for c in st["calls"] if c[:2] == ("POST", "/robots")]
    assert len(post) == 1, "robot must be created once"
    b = post[0][2]
    assert b["level"] == "project" and b["secret"] == PASSWORD and b["duration"] == -1, b
    assert len(b["permissions"]) == 1 and b["permissions"][0]["kind"] == "project" and b["permissions"][0]["namespace"] == "trivy-db", b
    assert sorted((a["resource"], a["action"]) for a in b["permissions"][0]["access"]) == [("repository", "pull"), ("repository", "push")], \
        "robot must hold repository push+pull only"
    rets = [c for c in st["calls"] if c[1].startswith("/retentions")]
    assert len(rets) == 1 and rets[0][0] == "POST", "retention must be created when the project has none"
    r = rets[0][2]
    rule = r["rules"][0]
    assert rule["template"] == "latestPushedK" and rule["params"] == {"latestPushedK": 2} and not rule["disabled"], rule
    assert json.loads(rule["tag_selectors"][0]["extras"]) == {"untagged": True}, "untagged artifacts must be in scope"
    assert rule["scope_selectors"]["repository"][0]["pattern"] == "**" and r["scope"] == {"level": "project", "ref": 2}, r
    gcs = [c for c in st["calls"] if c[1] == "/system/gc/schedule" and c[0] != "GET"]
    assert len(gcs) == 1 and gcs[0][0] == "POST" and gcs[0][2]["parameters"] == {"delete_untagged": False}, gcs

    # Re-run with the Secret present (reset=0): nothing is created or reset, retention converges by PUT,
    # an existing GC schedule is not overwritten.
    st["calls"].clear()
    st["gc"] = {"schedule": {"type": "Weekly", "cron": "0 0 0 * * 0"}}
    st["robot_secret"] = PASSWORD
    p = harbor_run(code, st, reset=False)
    assert p.returncode == 0, p.stderr
    methods = [(c[0], c[1]) for c in st["calls"]]
    assert ("POST", "/robots") not in methods and not any(m == "PATCH" for m, _ in methods), ("re-run must not touch the robot", methods)
    assert ("PUT", "/retentions/9") in methods and ("POST", "/retentions") not in methods, methods
    assert not any(c[1] == "/system/gc/schedule" and c[0] != "GET" for c in st["calls"]), "existing GC schedule overwritten"
    assert not any(m == "PATCH" for m, _ in methods), "valid stored secret must not rotate"

    # Secret lost, robot still in Harbor: its secret is reset, not duplicated.
    st["calls"].clear()
    st["robot_secret"] = "OldSecret-Pw9"
    p = harbor_run(code, st, reset=True)
    methods = [(c[0], c[1]) for c in st["calls"]]
    assert p.returncode == 0 and ("PATCH", "/robots/7") in methods and ("POST", "/robots") not in methods, methods

    assert ("GET", "/service/token") not in methods, "missing Secret must rotate without authentication"
    assert st["robot_secret"] == PASSWORD and "ROBOT_SECRET=" + PASSWORD in p.stdout, (p.stdout, p.stderr)
    patches = [c[2] for c in st["calls"] if c[:2] == ("PATCH", "/robots/7")]
    assert patches == [{"secret": PASSWORD}], patches

    # Stored Secret no longer matches Harbor: a real 401 must trigger the same refresh.
    st["calls"].clear()
    st["robot_secret"] = "OldSecret-Pw9"
    p = harbor_run(code, st, reset=False)
    methods = [(c[0], c[1]) for c in st["calls"]]
    assert p.returncode == 0 and ("GET", "/service/token") in methods and ("PATCH", "/robots/7") in methods, (methods, p.stderr)
    assert ("POST", "/robots") not in methods, methods
    assert st["robot_secret"] == PASSWORD and "ROBOT_SECRET=" + PASSWORD in p.stdout, (p.stdout, p.stderr)


# ---------------------------------------------------------------- behavior: fetch prune, helm, promtool
def fetch_behavior(root):
    text = (root / FETCH).read_text()
    m = re.search(r"(listed_names=.*?\ndone\n)", text, re.S)
    assert m, "fetch-security-db.sh prune loop missing"
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        lst = t / "list.txt"
        lst.write_text("# c\ntrivy-db\tghcr.io/a/trivy-db:2\ntrivy-java-db\tghcr.io/a/trivy-java-db:1\n")
        staged = t / "staged"
        for n in ("trivy-db", "trivy-java-db", "old-name"):
            (staged / n).mkdir(parents=True)
        (staged / "manifest.json").write_text("{}")
        subprocess.run(["bash", "-c", f'set -euo pipefail; LIST_FILE="{lst}"; STAGED_DIR="{staged}"\n{m.group(1)}'],
                       check=True, capture_output=True)
        left = sorted(p.name for p in staged.iterdir())
        assert left == ["manifest.json", "trivy-db", "trivy-java-db"], left


def helm_behavior(root):
    if not shutil.which("helm"):
        print("SKIP: helm unavailable; R322 render cases not run")
        return
    chart = root / CHART

    def n(*args):
        p = subprocess.run(["helm", "template", "x", str(chart), *args], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        return p.stdout.count("name: trivy-db-refresh\n")

    assert n("--set", "provider=vagrant") == 1, "vagrant must render the Application"
    assert n("--set", "provider=kakao") == 0, "kakao (airgap) must not render it"
    assert n("--set", "securityDbAutoRefresh.enabled=false") == 0, "enabled=false must not render it"


def promtool_behavior(found):
    if not shutil.which("promtool"):
        print("SKIP: promtool unavailable; R322 alert cases not run")
        return
    step, n = 600, 6 * 24 * 2  # 10 min samples over 48h

    def series(name, f):
        vals = [f(k * step) for k in range(n)]
        return {"series": f"{name}{{namespace=\"security-system\",cronjob=\"trivy-db-refresh\"}}",
                "values": " ".join("_" if v is None else str(v) for v in vals)}

    ok_every_6h = lambda t: (t // 21600) * 21600
    created = series("kube_cronjob_created", lambda t: 0)

    def case(name, inputs, alert, at, firing):
        sev = {"severity": "warning"}
        return {"name": name, "interval": "10m", "input_series": inputs,
                "alert_rule_test": [{"eval_time": at, "alertname": alert, "exp_alerts": [{"exp_labels": sev}] if firing else []}]}

    cases = [
        case("stale: no success for 40h", [created, series("kube_cronjob_status_last_successful_time", lambda t: 0)],
             "TrivyDbRefreshStale", "40h", True),
        case("healthy: success every 6h", [created, series("kube_cronjob_status_last_successful_time", ok_every_6h)],
             "TrivyDbRefreshStale", "40h", False),
        case("never succeeded after 14h", [created], "TrivyDbRefreshStale", "14h", True),
        case("young cronjob, first run not due", [created], "TrivyDbRefreshStale", "11h", False),
        case("failed: scheduled at 6h, not succeeded by 8h",
             [series("kube_cronjob_status_last_schedule_time", lambda t: 21600 if t >= 21600 else 0),
              series("kube_cronjob_status_last_successful_time", lambda t: 0)], "TrivyDbRefreshFailed", "8h", True),
        case("running job finished in 10m",
             [series("kube_cronjob_status_last_schedule_time", lambda t: 21600 if t >= 21600 else 0),
              series("kube_cronjob_status_last_successful_time", lambda t: 22200 if t >= 22200 else 0)],
             "TrivyDbRefreshFailed", "8h", False),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "rules.yaml").write_text(yaml.safe_dump({"groups": [{"name": "g", "rules": [
            {k: v for k, v in r.items() if k != "annotations"} for r in found.values()]}]}))
        (t / "tests.yaml").write_text(yaml.safe_dump({"rule_files": ["rules.yaml"], "evaluation_interval": "10m", "tests": cases}))
        subprocess.run(["promtool", "check", "rules", str(t / "rules.yaml")], check=True, capture_output=True)
        subprocess.run(["promtool", "test", "rules", str(t / "tests.yaml")], check=True, capture_output=True)
    print(f"PASS: {len(cases)} promtool cases")


def check(root):
    job, found = static(root)
    script_behavior(root)
    harbor_behavior(root)
    fetch_behavior(root)
    helm_behavior(root)
    promtool_behavior(found)


# ---------------------------------------------------------------- mutations
MUTATIONS = [
    ("copy no longer preserves digests", CRON, " --preserve-digests", ""),
    ("approver is not auto-refresh", CRON, "APPROVED_BY=auto-refresh", "APPROVED_BY=admin"),
    ("current mirror is copied again", CRON, 'if [ "${have}" = "${want}" ]; then', "if false; then"),
    ("TLS verification disabled", CRON, '--dest-cert-dir "${CERT_ROOT}/${HARBOR_REGISTRY}"', "--dest-tls-verify=false"),
    ("runs as root", CRON, "runAsNonRoot: true", "runAsNonRoot: false"),
    ("writable root filesystem", CRON, "readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false"),
    ("image unpinned", CRON, "@sha256:2b7d76dde38c5924e1945348840c58786fc192201f2fd545832e58b53d26f7b8", ""),
    ("concurrent runs allowed", CRON, "concurrencyPolicy: Forbid", "concurrencyPolicy: Allow"),
    ("java-db tag drifts from security-db.txt", CRON, "ghcr.io/aquasecurity/trivy-java-db:1 \\", "ghcr.io/aquasecurity/trivy-java-db:2 \\"),
    ("size floor removed", CRON, "20000000", "0"),
    ("credentials from the admin secret", CRON, "name: trivy-db-refresh\n                      key: password", "name: harbor-secrets\n                      key: password"),
    ("post-copy digest check removed", CRON, 'if [ "${have}" != "${want}" ]; then', "if false; then"),
    ("API token automounted in the pod", CRON, "  automountServiceAccountToken: false\n          securityContext", "  automountServiceAccountToken: true\n          securityContext"),
    ("CA volume from another secret", CRON, "secretName: narwhal-ca-cert", "secretName: other-ca"),
    ("kakao gate dropped", APP, ' (ne .Values.provider "kakao")', ""),
    ("robot gets all actions", REG, '{"resource": "repository", "action": "push"}', '{"resource": "repository", "action": "*"}'),
    ("retention keeps nothing", REG, "SECDB_KEEP=2", "SECDB_KEEP=0"),
    ("retention ignores untagged", REG, 'json.dumps({"untagged": True})', 'json.dumps({"untagged": False})'),
    ("GC overwrites an existing schedule", REG, 'if gtype == "None":', "if True:"),
    ("GC deletes untagged system-wide", REG, '"delete_untagged": False}})', '"delete_untagged": True}})'),
    ("robot secret reset on every run", REG, "elif found and not created_robot:", "elif found:"),
    ("create response secret discarded", REG, 'secret = created.get("secret") or ""', 'secret = PW'),
    ("Harbor REST calls use unprefixed base", REG, 'API + path, method=method,', 'API_BASE + path, method=method,'),
    ("skopeo CA dir points at parent", CRON, '--dest-cert-dir "${CERT_ROOT}/${HARBOR_REGISTRY}"', '--dest-cert-dir "${CERT_ROOT}"'),
    ("robot secret printed", REG, 'print("ROBOT_USER=" + found[0]["name"])', 'print("ROBOT_USER=" + found[0]["name"] + PW)'),
    ("08-6 skips security-system", TLS, "storage security-system; do", "storage; do"),
    ("stale threshold 36h -> 360h", ALERTS, "> 129600)", "> 1296000)"),
    ("never-succeeded branch dropped", ALERTS, "> 43200)", "> 4320000)"),
    ("failed alert compares the wrong way", ALERTS, "max(kube_cronjob_status_last_schedule_time{" + SEL + "})\n              > max(",
     "max(kube_cronjob_status_last_successful_time{" + SEL + "})\n              > max("),
    ("runbook anchor broken", ALERTS, "#보안-db-자동-갱신-d-secdb-auto", "#refresh"),
    ("runbook heading missing", README, RUNBOOK_HEADING, "### 다른 제목"),
    ("fetch prune does not delete", FETCH, 'rm -rf "${stale_dir}" ;;', "true ;;"),
    ("digest table out of step", DIGESTS, "quay.io/skopeo/stable:v1.22.2\tsha256:2b7d", "quay.io/skopeo/stable:v1.22.2\tsha256:0000"),
]


def mutation_verify():
    for name, rel, old, new in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            for f in FILES + CHART_FILES + [OPERATOR, APP]:
                (t / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / f, t / f)
            shutil.copytree(ROOT / CHART / "templates", t / CHART / "templates", dirs_exist_ok=True)
            text = (t / rel).read_text()
            assert text.count(old) >= 1, f"mutation target missing: {name}"
            (t / rel).write_text(text.replace(old, new, 1))
            try:
                check(t)
            except (AssertionError, subprocess.CalledProcessError, KeyError, IndexError, SystemExit):
                print(f"caught: {name}")
                continue
            raise SystemExit(f"MUTATION SURVIVED: {name}")
    print(f"PASS: {len(MUTATIONS)} mutations rejected")


if __name__ == "__main__":
    check(ROOT)
    print("PASS: R322 trivy-db auto refresh, bounded storage, alerts")
    if "--mutation-verify" in sys.argv:
        mutation_verify()
