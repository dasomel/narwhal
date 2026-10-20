#!/usr/bin/env python3
"""OpenBao raft snapshot backup contract (D-rpo, D-auth, D-holder, D-perms, #431)."""

import re
import sys
import tempfile
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[3]
ROOT = REPO
EXCLUDE_KEY = "backup.velero.io/backup-volumes-excludes"


def cron_to_minutes(expr):
    minute, hour = expr.split()[:2]
    return int(hour) * 60 + int(minute)


# --- behavioural tests: the extracted encrypt script is executed against a temp tree -------------
_FIX = {}
_WRAPPER = """#!/bin/sh
# Test double: delegates to the real openssl, with fault injection selected by env vars.
REAL="{real}"
case " $* " in
  *" cms -encrypt "*)
    out=""; prev=""
    for a in "$@"; do [ "$prev" = "-out" ] && out="$a"; prev="$a"; done
    if [ -n "${{FAIL_ENCRYPT:-}}" ]; then   # looks like valid CMS framing, but the command fails
      printf '\\060\\200\\006\\011\\052\\206\\110\\206\\367\\015\\001\\007\\003xxxx\\000\\000' > "$out"; exit 1
    fi
    if [ -n "${{EMPTY_OUT:-}}" ]; then : > "$out"; exit 0; fi
    if [ -n "${{TRUNC_OUT:-}}" ]; then   # right framing at the start, cut off at the end, exit 0
      printf '\\060\\200\\006\\011\\052\\206\\110\\206\\367\\015\\001\\007\\003xxxx' > "$out"; exit 0
    fi
    ;;
  *" x509 "*" -text "*)
    if [ -n "${{BLANK_BITS:-}}" ]; then "$REAL" "$@" | sed '/Public-Key:/d'; exit $?; fi
    ;;
esac
exec "$REAL" "$@"
"""


def _fixtures():
    """Self-signed certificates for the behavioural tests, generated once per process."""
    import atexit
    import shutil
    import subprocess
    import time

    if _FIX:
        return _FIX
    real = shutil.which("openssl")
    if real is None or subprocess.run([real, "cms", "-help"], capture_output=True).returncode != 0:
        _FIX["error"] = "behavioural tests need an openssl with cms support on PATH"
        return _FIX
    tmp = tempfile.TemporaryDirectory()
    atexit.register(tmp.cleanup)
    d = Path(tmp.name)

    def mk(name, bits, days):
        subprocess.run([real, "req", "-x509", "-newkey", f"rsa:{bits}", "-sha256", "-days", str(days), "-nodes",
                        "-subj", "/CN=t", "-keyout", str(d / f"{name}.key"), "-out", str(d / f"{name}.pem")],
                       check=True, capture_output=True)
    mk("good", 3072, 30)
    mk("weak", 2048, 30)
    mk("short", 3072, 1)
    time.sleep(2)  # checkend 86400 must see the 1-day certificate as expiring
    fpr = subprocess.run([real, "x509", "-in", str(d / "good.pem"), "-noout", "-fingerprint", "-sha256"],
                         capture_output=True, text=True, check=True).stdout.strip().split("=", 1)[1]
    _FIX.update(real=real, dir=d, fpr=fpr)
    return _FIX


def _run_encrypt(script, recipient, env=None, prefill=(), plain=b"raft-snapshot-bytes" * 500):
    """Run the extracted encrypt script in a scratch tree; return (rc, output, tree)."""
    import os
    import subprocess

    fx = _fixtures()
    w = Path(tempfile.mkdtemp(prefix="bk-"))
    (w / "bin").mkdir()
    (w / "work").mkdir()
    (w / "snapshots/raft").mkdir(parents=True)
    wrapper = w / "bin/openssl"
    wrapper.write_text(_WRAPPER.format(real=fx["real"]))
    wrapper.chmod(0o755)
    if recipient is not None:
        (w / "recipient.pem").write_bytes(recipient)
    (w / "work/openbao.snap").write_bytes(plain)
    for name in prefill:
        (w / "snapshots/raft" / name).write_bytes(b"old")
    body = script.replace("/recipient/recipient.pem", f"{w}/recipient.pem").replace("/work/", f"{w}/work/") \
        .replace("/snapshots/", f"{w}/snapshots/")
    e = {"PATH": f"{w}/bin:{os.environ['PATH']}", "HOME": str(w), "KEEP": "7"}
    e.update(env or {})
    r = subprocess.run(["/bin/sh", "-c", body], env=e, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr, w


def _published(w):
    return sorted(p.name for p in (w / "snapshots/raft").iterdir() if p.name.endswith(".snap.cms"))


def behaviour(script, problems):
    import shutil
    import subprocess

    fx = _fixtures()
    if "error" in fx:
        problems.append(fx["error"])
        return
    d = fx["dir"]
    good = (d / "good.pem").read_bytes()
    plain = b"raft-snapshot-bytes" * 500

    def expect_refused(label, recipient, env=None):
        rc, out, w = _run_encrypt(script, recipient, env)
        left = sorted(p.name for p in (w / "snapshots/raft").iterdir())
        if rc == 0 or left or (w / "work/openbao.snap").exists():
            problems.append(f"behaviour[{label}]: must exit non-zero, write nothing and remove the plaintext "
                            f"(rc={rc}, files={left}, plaintext_left={(w / 'work/openbao.snap').exists()})")
        shutil.rmtree(w)

    # Good certificate: ciphertext published atomically, readable by Velero, decryptable, plaintext gone.
    rc, out, w = _run_encrypt(script, good)
    pub = _published(w)
    if rc != 0 or len(pub) != 1:
        problems.append(f"behaviour[good]: expected rc=0 and one .snap.cms, got rc={rc} files={pub}: {out[-200:]}")
    else:
        f = w / "snapshots/raft" / pub[0]
        names = sorted(p.name for p in (w / "snapshots/raft").iterdir())
        if names != sorted([pub[0], "last-success"]):
            problems.append(f"behaviour[good]: stray files on the PVC: {names}")
        if f.stat().st_mode & 0o777 != 0o644 or (w / "snapshots/raft").stat().st_mode & 0o777 != 0o755:
            problems.append("behaviour[good]: ciphertext must be 0644 in a 0755 directory")
        if (w / "work/openbao.snap").exists():
            problems.append("behaviour[good]: plaintext must be deleted")
        if pub[0] not in (w / "snapshots/raft/last-success").read_text():
            problems.append("behaviour[good]: last-success must name the new snapshot")
        if plain in f.read_bytes():
            problems.append("behaviour[good]: ciphertext contains the plaintext")
        dec = subprocess.run([fx["real"], "cms", "-decrypt", "-inform", "DER", "-in", str(f), "-recip", str(d / "good.pem"),
                              "-inkey", str(d / "good.key")], capture_output=True)
        if dec.returncode != 0 or dec.stdout != plain:
            problems.append("behaviour[good]: documented `openssl cms -decrypt` does not restore the snapshot")
        if "WARNING: RECIPIENT_FPR is empty" not in out:
            problems.append("behaviour[good]: an unpinned recipient must log a visible WARNING")
    shutil.rmtree(w)

    # Fail closed on every bad recipient; nothing written, plaintext removed.
    expect_refused("weak-2048", (d / "weak.pem").read_bytes())
    expect_refused("expires<24h", (d / "short.pem").read_bytes())
    expect_refused("cert+private key", good + (d / "good.key").read_bytes())
    expect_refused("private key only", (d / "good.key").read_bytes())
    # A valid single certificate that merely mentions a private key is refused too (only the grep catches it).
    expect_refused("PRIVATE KEY text", good + b"# PRIVATE KEY\n")
    expect_refused("empty", b"")
    expect_refused("missing", None)
    expect_refused("malformed", b"not a pem\n")
    expect_refused("two certs", good + good)
    expect_refused("bits unparseable", good, {"BLANK_BITS": "1"})
    expect_refused("wrong fingerprint", good, {"RECIPIENT_FPR": "00:" * 31 + "00"})
    # Failing/empty encryption must never publish, and `set -e` must hold.
    for label, env in (("encrypt fails", {"FAIL_ENCRYPT": "1"}), ("encrypt writes nothing", {"EMPTY_OUT": "1"}),
                       ("truncated ciphertext", {"TRUNC_OUT": "1"})):
        rc, out, w = _run_encrypt(script, good, env)
        left = sorted(p.name for p in (w / "snapshots/raft").iterdir())
        if rc == 0 or left or (w / "work/openbao.snap").exists():
            problems.append(f"behaviour[{label}]: must exit non-zero, publish nothing, remove plaintext and temp files (rc={rc}, files={left})")
        shutil.rmtree(w)
    # Pinned fingerprint (any case, with or without colons) is accepted.
    rc, out, w = _run_encrypt(script, good, {"RECIPIENT_FPR": fx["fpr"].lower()})
    if rc != 0 or len(_published(w)) != 1 or "WARNING" in out:
        problems.append(f"behaviour[pinned fingerprint]: expected success without WARNING (rc={rc}): {out[-200:]}")
    shutil.rmtree(w)
    # Pruning keeps the NEWEST seven and the marker names a surviving file.
    old = [f"openbao-2026010{i}T000000Z.snap.cms" for i in range(1, 10)]
    rc, out, w = _run_encrypt(script, good, prefill=old)
    pub = _published(w)
    new = [n for n in pub if n not in old]
    if rc != 0 or len(new) != 1 or len(pub) != 7 or pub[:6] != old[3:]:
        problems.append(f"behaviour[prune]: must keep the newest 7 (new + 6 newest old), got rc={rc} {pub}")
    elif new[0] not in (w / "snapshots/raft/last-success").read_text():
        problems.append("behaviour[prune]: marker does not name the new snapshot")
    shutil.rmtree(w)


def check():
    problems = []
    docs = [d for d in yaml.safe_load_all((ROOT / "gitops/resources/openbao-backup.yaml").read_text()) if d]
    by_kind = {d["kind"]: d for d in docs}
    for kind in ("ServiceAccount", "PersistentVolumeClaim", "CronJob", "Deployment"):
        if kind not in by_kind:
            problems.append(f"openbao-backup.yaml: {kind} missing")
    if problems:
        return problems
    cron, holder, pvc = by_kind["CronJob"], by_kind["Deployment"], by_kind["PersistentVolumeClaim"]

    # Schedule is daily and strictly before the Velero run that captures the staging volume.
    velero = yaml.safe_load((ROOT / "gitops/charts/narwhal-apps/templates/velero.yaml").read_text())
    schedules = velero["spec"]["source"]["helm"]["valuesObject"]["schedules"]
    vel_min = cron_to_minutes(schedules["daily-openbao"]["schedule"])
    spec = cron["spec"]
    if re.fullmatch(r"\d+ \d+ \* \* \*", spec["schedule"]) is None:
        problems.append(f"CronJob schedule must be a fixed daily time, got {spec['schedule']!r}")
    elif not 0 < vel_min - cron_to_minutes(spec["schedule"]) <= 90:
        problems.append("CronJob must run 1-90 minutes before Velero daily-openbao")
    if spec.get("timeZone") != "Etc/UTC":
        problems.append("CronJob timeZone must be Etc/UTC (Velero schedules are UTC)")
    if spec.get("concurrencyPolicy") != "Forbid":
        problems.append("CronJob concurrencyPolicy must be Forbid")
    job = spec["jobTemplate"]["spec"]
    for key in ("activeDeadlineSeconds", "backoffLimit"):
        if key not in job:
            problems.append(f"Job {key} missing")
    pod = job["template"]["spec"]
    labels = job["template"]["metadata"]["labels"]
    if labels.get("istio.io/dataplane-mode") != "none" or "app.kubernetes.io/name" not in labels:
        problems.append("job pod needs istio.io/dataplane-mode: none and app.kubernetes.io/name")

    # Hardening on both pods.
    for name, p in (("job", pod), ("holder", holder["spec"]["template"]["spec"])):
        psc = p.get("securityContext", {})
        if not (psc.get("runAsNonRoot") is True and psc.get("runAsUser") and psc.get("seccompProfile", {}).get("type") == "RuntimeDefault"):
            problems.append(f"{name} pod securityContext needs runAsNonRoot, numeric runAsUser, seccomp RuntimeDefault")
        for c in p["containers"] + p.get("initContainers", []):
            sc = c.get("securityContext", {})
            if sc.get("allowPrivilegeEscalation") is not False or sc.get("readOnlyRootFilesystem") is not True \
                    or sc.get("capabilities", {}).get("drop") != ["ALL"]:
                problems.append(f"{name} container {c['name']} securityContext not hardened")
            if not c.get("resources", {}).get("limits") or not c["resources"].get("requests"):
                problems.append(f"{name} container {c['name']} needs resources requests and limits")
            if ":" not in c["image"] or c["image"].endswith(":latest"):
                problems.append(f"{name} container {c['name']} image must be pinned")

    # No root token / unseal key: no env secretKeyRef, and no openbao-init or whole openbao-tls mount.
    text = (ROOT / "gitops/resources/openbao-backup.yaml").read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    for forbidden in ("openbao-init", "root_token", "unseal_keys", "secretKeyRef", "BAO_SKIP_VERIFY", "tls-skip-verify", "bao operator unseal"):
        if forbidden in code:
            problems.append(f"openbao-backup.yaml must not reference {forbidden}")
    for vol in pod["volumes"]:
        sec = vol.get("secret")
        if sec and (sec["secretName"] != "openbao-tls" or [i["key"] for i in sec.get("items", [])] != ["ca.crt"]):
            problems.append("only openbao-tls ca.crt may be mounted (never tls.key)")
    if "BAO_CACERT" not in code:
        problems.append("job must verify TLS with BAO_CACERT")
    tok = next((v for v in pod["volumes"] if v["name"] == "openbao-token"), None)
    if tok is None or tok["projected"]["sources"][0]["serviceAccountToken"].get("audience") != "vault":
        problems.append("job must use a projected SA token with audience vault")

    # Atomic write, marker, bounded retention, Velero-readable permissions.
    script = pod["containers"][0]["command"][-1]
    init = pod.get("initContainers", [])
    init_script = init[0]["command"][-1] if init else ""
    for needle, why in (
        ("set -eu", "strict shell"),
        ("trap ", "cleanup trap"),
        ("umask 022", "0644/0755 ciphertext so the root_squashed Velero data mover can read"),
        ("chmod 0644", "ciphertext file mode"),
        ("chmod 0755", "directory mode"),
        (".snap.cms.tmp", "temp ciphertext file"),
        ('mv "${TMP}" "${DIR}/openbao-${STAMP}.snap.cms"', "atomic rename to the .snap.cms name"),
        ("openssl cms -encrypt -binary -aes-256-cbc -outform DER", "CMS encryption"),
        ('-recip "${CERT}"', "encrypt to the recipient certificate"),
        ('rm -f "${PLAIN}"', "plaintext deletion"),
        ("last-success", "freshness marker"),
        ('mv "${DIR}/.last-success.tmp"', "atomic marker rename"),
        ("NR>keep", "bounded retention"),
        ("command -v openssl", "runtime guard: tool present"),
        ("openssl cms -help", "runtime guard: cms support"),
        ("PRIVATE KEY", "refuse a PEM that contains a private key"),
        ("-checkend", "refuse an expired recipient"),
        ("openssl x509 -in", "parse the recipient certificate"),
        ("-ge 3072", "minimum RSA size"),
        ("rsaEncryption", "RSA recipient"),
        ("[ -s \"${CERT}\" ]", "missing/empty recipient"),
    ):
        if needle not in script:
            problems.append(f"encrypt script lacks {needle!r} ({why})")
    if "snapshot save" not in init_script or "set -eu" not in init_script or "trap " not in init_script:
        problems.append("initContainer must run `bao operator raft snapshot save` with set -eu and a cleanup trap")
    # Order: validate -> encrypt -> rename -> marker. Validation must come before anything is written.
    marks = [script.find(s) for s in ("-checkend", "-ge 3072", "mkdir -p", "openssl cms -encrypt", 'mv "${TMP}"', 'mv "${DIR}/.last-success.tmp"')]
    if -1 in marks or marks != sorted(marks):
        problems.append("encrypt order must be validate -> mkdir -> encrypt -> rename -> marker")
    # Exact guard lines: a validation that is softened (`|| echo`), a lost `set -e`, a neutered trap or a
    # dropped sanity check changes one of these lines. Behaviour is verified below on top of this.
    required = [
        "set -eu", "umask 022",
        """trap 'rm -f "${PLAIN}" "${TMP}" "${DIR}/.last-success.tmp"' EXIT""",
        """command -v openssl >/dev/null 2>&1 || fail "openssl not found in image\"""",
        """|| fail "openssl has no cms support\"""",
        """[ -s "${CERT}" ] || fail "recipient.pem missing or empty (run scripts/ops/openbao-backup-keygen.sh)\"""",
        """if grep -q 'PRIVATE KEY' "${CERT}"; then fail "recipient.pem contains a private key"; fi""",
        """[ "$(grep -c -- '-----BEGIN' "${CERT}")" = 1 ] || fail "recipient.pem must hold exactly one PEM block\"""",
        """grep -q -- '-----BEGIN CERTIFICATE-----' "${CERT}" || fail "recipient.pem is not a certificate\"""",
        """openssl x509 -in "${CERT}" -noout >/dev/null 2>&1 || fail "recipient.pem is malformed\"""",
        """openssl x509 -in "${CERT}" -noout -checkend 86400 >/dev/null 2>&1 || fail "recipient certificate is expired or expires within 24h\"""",
        """printf '%s\\n' "${CERT_TEXT}" | grep -q 'Public Key Algorithm: rsaEncryption' || fail "recipient key must be RSA\"""",
        """[ "${BITS:-0}" -ge 3072 ] || fail "recipient RSA key must be >= 3072 bits (got ${BITS:-?})\"""",
        """[ -n "${FPR}" ] || fail "cannot compute the recipient fingerprint\"""",
        """[ "${FPR}" = "${WANT}" ] || fail "recipient certificate fingerprint ${FPR} does not match RECIPIENT_FPR\"""",
        """echo "WARNING: RECIPIENT_FPR is empty; the recipient certificate (fingerprint ${FPR}) is not pinned" >&2""",
        """[ -s "${PLAIN}" ] || fail "no snapshot in the work volume\"""",
        """chmod 0755 "${DIR}" || echo "WARNING: cannot chmod ${DIR} (owned by another uid?)" >&2""",
        """[ -w "${DIR}" ] || fail "${DIR} is not writable by uid $(id -u); recreate the directory\"""",
        "openssl cms -encrypt -binary -aes-256-cbc -outform DER -stream -in \"${PLAIN}\" -out \"${TMP}\" -recip \"${CERT}\" -keyopt rsa_padding_mode:oaep",
        """[ -s "${TMP}" ] || fail "ciphertext is empty\"""",
        """[ "${HEAD}" = "308006092a864886f70d010703" ] || fail "ciphertext is not CMS EnvelopedData\"""",
        """[ "${TAIL}" = "0000" ] || fail "ciphertext is truncated\"""",
        """ls -1 "${DIR}" | grep -E '^openbao-[0-9]{8}T[0-9]{6}Z\\.snap\\.cms$' | sort -r \\""",
    ]
    have = {l.strip() for l in script.splitlines()}
    for line in required:
        if line not in have:
            problems.append(f"encrypt script lacks the exact guard line {line!r}")
    if "set +e" in script or script.splitlines()[0].strip() != "set -eu":
        problems.append("encrypt script must start with `set -eu` and never `set +e`")
    for env_name, want in (("RECIPIENT_FPR", ""), ("KEEP", "7")):
        envs = {e["name"]: e.get("value") for e in pod["containers"][0].get("env", [])}
        if envs.get(env_name) != want:
            problems.append(f"encrypt container env {env_name} must be {want!r} in Git (RECIPIENT_FPR is set by editing it)")
    cres = pod["containers"][0]["resources"]["limits"]
    if cres.get("memory") not in ("128Mi", "256Mi"):
        problems.append("encrypt memory limit is sized for -stream (128Mi/256Mi); revisit the sizing comment before changing")
    # initContainer guard lines (plaintext cleanup on failure, strict shell).
    init_have = {l.strip() for l in init_script.splitlines()}
    for line in ("set -eu", "umask 077", """trap 'rc=$?; [ "${rc}" -eq 0 ] || rm -f "${PLAIN}"' EXIT""",
                 """[ -s "${PLAIN}" ] || { echo "snapshot is empty" >&2; exit 1; }""", "PLAIN=/work/openbao.snap"):
        if line not in init_have:
            problems.append(f"initContainer script lacks the exact line {line!r}")
    # Mount paths: /work is always the memory volume, /snapshots always the PVC.
    for c in pod["containers"] + init:
        for m in c.get("volumeMounts", []):
            if (m["mountPath"] == "/work") != (m["name"] == "work") or (m["mountPath"] == "/snapshots") != (m["name"] == "snapshots"):
                problems.append(f"container {c['name']}: volume {m['name']} mounted at {m['mountPath']} (/work must be `work`, /snapshots the PVC)")
    # Behaviour is run last, only when the static contract holds (it is the slow part).
    # check_behaviour_only() exercises it in isolation for --mutation-verify.
    if not problems:
        behaviour(script, problems)
    # Fail closed: every validation line ends in fail, no plaintext fallback anywhere in the PVC path.
    if re.search(r"(cp|mv|cat|dd|tee)\b[^\n]*\$\{PLAIN\}", script) or re.search(r"\|\|\s*(cp|mv|cat|true)\b", script):
        problems.append("no plaintext fallback: PLAIN must never be copied/moved/cat'ed and no `|| true/cp` fallbacks")
    if re.search(r'-out "\$\{DIR\}|-out "/snapshots|>\s*"?\$\{DIR\}/openbao', script):
        problems.append("openssl output must go to the temp file, not directly to a final name")
    if re.search(r'mv "\$\{TMP\}" "\$\{DIR\}/openbao-\$\{STAMP\}\.snap"', script) or 'openbao-${STAMP}.snap"' in script:
        problems.append("only the .snap.cms name may be published")
    if "0600" in script or "chmod 0700" in script:
        problems.append("job must not make snapshots owner-only (Velero cannot read them under root_squash)")
    # Plaintext only in a memory emptyDir with a size limit; never touches the PVC.
    vols = {v["name"]: v for v in pod["volumes"]}
    work = vols.get("work", {})
    if work.get("emptyDir", {}).get("medium") != "Memory" or not work.get("emptyDir", {}).get("sizeLimit"):
        problems.append("volume `work` (plaintext) must be a memory-backed emptyDir with a sizeLimit")
    if re.search(r"/work/[^\s\"']*", init_script) is None or "/snapshots" in init_script:
        problems.append("initContainer must write the plaintext under /work and never under /snapshots")
    if re.search(r'PLAIN=/work/', script) is None or re.search(r'PLAIN=/snapshots', script):
        problems.append("encrypt container PLAIN must be under /work")
    for c in init:
        if any(m["name"] == "snapshots" or m["name"] == "recipient" for m in c.get("volumeMounts", [])):
            problems.append("initContainer must not mount the PVC or the recipient certificate")
    for c in pod["containers"]:
        for m in c.get("volumeMounts", []):
            if m["name"] == "work" and m["mountPath"] == "/snapshots":
                problems.append("work volume must not be mounted over the PVC path")
    if vols.get("snapshots", {}).get("persistentVolumeClaim", {}).get("claimName") != "openbao-snapshots":
        problems.append("snapshots volume must be the openbao-snapshots PVC")
    if "persistentVolumeClaim" in work:
        problems.append("plaintext volume must not be a PVC")
    # Recipient certificate comes from the ConfigMap (public only), required.
    rec = vols.get("recipient", {}).get("configMap", {})
    if rec.get("name") != "openbao-backup-recipient" or rec.get("optional") is not False \
            or [i["key"] for i in rec.get("items", [])] != ["recipient.pem"]:
        problems.append("recipient volume must be required ConfigMap openbao-backup-recipient key recipient.pem")
    if any(d["kind"] in ("ConfigMap", "Secret") for d in docs):
        problems.append("openbao-backup.yaml must not ship a ConfigMap/Secret (the operator applies the certificate)")
    # Image: encrypt container is digest-pinned to an image already in the airgap list.
    img = pod["containers"][0]["image"]
    m_img = re.fullmatch(r"(docker\.io/valkey/valkey:8-alpine)@(sha256:[0-9a-f]{64})", img)
    images_txt = (ROOT / "scripts/airgap/images.txt").read_text()
    digests = (ROOT / "scripts/airgap/lib/image-digests.tsv").read_text()
    if not m_img:
        problems.append("encrypt container image must be docker.io/valkey/valkey:8-alpine@sha256:<digest>")
    else:
        if not re.search(rf"^{re.escape(m_img.group(1))}(@sha256:[0-9a-f]{{64}})?$", images_txt, re.M):
            problems.append("encrypt image must already be listed in scripts/airgap/images.txt (no new airgap image)")
        if not re.search(rf"^{re.escape(m_img.group(1))}\t{re.escape(m_img.group(2))}\t", digests, re.M):
            problems.append("encrypt image digest must match scripts/airgap/lib/image-digests.tsv")
    # No private key anywhere in the manifests; helper never ships the key.
    for rel in ("gitops/resources/openbao-backup.yaml", "gitops/charts/narwhal-apps/templates/openbao-backup.yaml"):
        if re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", (ROOT / rel).read_text()):
            problems.append(f"{rel} contains private key material")
    kg = (ROOT / "scripts/ops/openbao-backup-keygen.sh").read_text()
    for needle, why in (("umask 077", "private key file mode"), ("refusing to overwrite", "no overwrite"),
                        ("NODES=()\nPOS=()", "passphrase is the default"), ("--no-passphrase) NODES=(-nodes)", "explicit opt-out only"),
                        ("-fingerprint -sha256", "print the fingerprint to pin"),
                        ("rsa:4096", "RSA-4096"), ("--from-file=recipient.pem=\"${CERT_OUT}\"", "only the certificate goes into the ConfigMap"),
                        ("PRIVATE KEY", "refuse to apply a cert file holding a key"), ("OFFLINE", "loud offline warning"),
                        ("UNRECOVERABLE", "loud loss warning")):
        if needle not in kg:
            problems.append(f"keygen helper lacks {needle!r} ({why})")
    if re.search(r'(cat|echo|printf|base64)[^\n]*"\$\{KEY_OUT\}"', kg) or "from-file" in kg.replace('--from-file=recipient.pem="${CERT_OUT}"', ""):
        problems.append("keygen helper must never print or ship the private key")

    # Policy: sys/storage/raft/snapshot read only, in the 13-2 script, role bound to this SA.
    b = (ROOT / "scripts/cluster/13-2-narwhal-portal-bindings.sh").read_text()
    m = re.search(r"cat > /tmp/openbao-backup\.hcl << [^\n]*\n(.*?)\nPOLICY_EOF", b, re.S)
    if not m:
        problems.append("13-2: openbao-backup policy heredoc missing")
    else:
        paths = re.findall(r'path "([^"]+)" \{\s*capabilities = \[([^\]]*)\]', m.group(1))
        if paths != [("sys/storage/raft/snapshot", '"read"')]:
            problems.append(f"openbao-backup policy must be exactly read on sys/storage/raft/snapshot, got {paths}")
    role = re.search(r"bao write auth/kubernetes/role/openbao-backup(.*?)\n\n", b, re.S)
    if not role or 'policies="openbao-backup"' not in role.group(1) \
            or 'bound_service_account_names="openbao-backup"' not in role.group(1) \
            or 'bound_service_account_namespaces="storage"' not in role.group(1):
        problems.append("13-2: role openbao-backup must bind SA openbao-backup/storage and policy openbao-backup only")
    for needle in ("token_no_default_policy=true", 'token_ttl="10m"', 'token_max_ttl="10m"'):
        if not role or needle not in role.group(1):
            problems.append(f"13-2: role openbao-backup must set {needle} (no default policy, short non-renewed token)")
    pol = re.search(r"/bin/sh -c '\nset -eu\ncat > /tmp/openbao-backup\.hcl", b)
    if not pol or "bao policy read openbao-backup" not in b:
        problems.append("13-2: openbao-backup policy block must run with set -eu and verify the stored policy (bao policy read)")
    for needle in ('"token_no_default_policy\\": true"', '-field=policies "${R}")" = "[openbao-backup]"',
                   '-field=bound_service_account_names "${R}")" = "[openbao-backup]"',
                   '-field=bound_service_account_namespaces "${R}")" = "[storage]"',
                   '-field=audience "${R}")" = "${EXPECT_AUDIENCE}"', 'EXPECT_AUDIENCE="${OPENBAO_K8S_AUTH_AUDIENCE}"'):
        if needle not in b:
            problems.append(f"13-2: stored role verification lacks {needle!r}")
    # One source for the audience: the job's projected token must use 13-2's default.
    aud = re.search(r'OPENBAO_K8S_AUTH_AUDIENCE="\$\{OPENBAO_K8S_AUTH_AUDIENCE:-([^}]+)\}"', b)
    if not aud or aud.group(1) != tok["projected"]["sources"][0]["serviceAccountToken"].get("audience"):
        problems.append("job token audience must equal the OPENBAO_K8S_AUTH_AUDIENCE default in 13-2")
    if pod.get("serviceAccountName") != by_kind["ServiceAccount"]["metadata"]["name"]:
        problems.append("job serviceAccountName must be the openbao-backup ServiceAccount")

    # Volume reaches Velero: holder mounts the PVC, nothing excludes it, Application deploys the file.
    if not any(v.get("persistentVolumeClaim", {}).get("claimName") == pvc["metadata"]["name"] for v in holder["spec"]["template"]["spec"]["volumes"]):
        problems.append("holder pod must mount the staging PVC (Velero fs-backup only reads running pods' volumes)")
    hmounts = [m for c in holder["spec"]["template"]["spec"]["containers"] for m in c.get("volumeMounts", []) if m["name"] == "snapshots"]
    if not hmounts or not all(m.get("mountPath") and m.get("readOnly") is True for m in hmounts):
        problems.append("holder container must volumeMount the snapshots volume read-only")
    # The Job pod (plaintext in `work`) is kept out of Velero; the holder/PVC must stay in.
    jmeta = job["template"]["metadata"]
    if jmeta.get("labels", {}).get("velero.io/exclude-from-backup") != "true":
        problems.append("job pod must carry label velero.io/exclude-from-backup: \"true\" (plaintext in `work`)")
    if jmeta.get("annotations", {}).get(EXCLUDE_KEY) != "work":
        problems.append(f"job pod must carry {EXCLUDE_KEY}: work")
    for where, meta in (("holder pod", holder["spec"]["template"]["metadata"]), ("holder Deployment", holder["metadata"]),
                        ("CronJob", cron["metadata"]), ("PVC", pvc["metadata"])):
        if EXCLUDE_KEY in meta.get("annotations", {}) or meta.get("labels", {}).get("velero.io/exclude-from-backup"):
            problems.append(f"{where} must not be excluded from Velero (the staging volume must reach it)")
    if "ReadWriteMany" not in pvc["spec"]["accessModes"]:
        problems.append("staging PVC must be ReadWriteMany (job and holder mount it together)")
    if "Prune=false" not in pvc["metadata"].get("annotations", {}).get("argocd.argoproj.io/sync-options", ""):
        problems.append("staging PVC must carry Prune=false")
    for path in (ROOT / "gitops").rglob("*.yaml"):
        if "templates" in path.parts or path.parent.name == "resources":
            if EXCLUDE_KEY in path.read_text() and path.name != "openbao-backup.yaml":
                for line in re.findall(rf"{re.escape(EXCLUDE_KEY)}: *\"?([^\"\n]+)", path.read_text()):
                    if "snapshots" in line:
                        problems.append(f"{path.name}: staging volume must not be in {EXCLUDE_KEY}")
    app = yaml.safe_load((ROOT / "gitops/charts/narwhal-apps/templates/openbao-backup.yaml").read_text().replace("{{ .Values.repoURL }}", "x"))
    if app["spec"]["source"]["directory"]["include"] != "openbao-backup.yaml" or app["spec"]["destination"]["namespace"] != "storage":
        problems.append("openbao-backup Application must include openbao-backup.yaml into namespace storage")
    return problems


BACKUP = "gitops/resources/openbao-backup.yaml"
BINDINGS = "scripts/cluster/13-2-narwhal-portal-bindings.sh"
APP = "gitops/charts/narwhal-apps/templates/openbao-backup.yaml"
VELERO = "gitops/charts/narwhal-apps/templates/velero.yaml"
KEYGEN = "scripts/ops/openbao-backup-keygen.sh"
IMAGES = "scripts/airgap/images.txt"
DIGESTS = "scripts/airgap/lib/image-digests.tsv"
MUTATIONS = (
    (BACKUP, "umask 022\n                  CERT=", "umask 077\n                  CERT="),
    (BACKUP, 'schedule: "30 4 * * *"', 'schedule: "30 6 * * *"'),
    (BACKUP, "                - name: BAO_CACERT", '                - name: BAO_SKIP_VERIFY\n                  value: "true"\n                - name: BAO_CACERT'),
    (BACKUP, "concurrencyPolicy: Forbid", "concurrencyPolicy: Allow"),
    (BACKUP, "      activeDeadlineSeconds: 600\n", ""),
    (BACKUP, "          persistentVolumeClaim:\n            claimName: openbao-snapshots", "          persistentVolumeClaim:\n            claimName: other"),
    (BACKUP, "readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false"),
    (BACKUP, "                  - key: ca.crt\n                    path: ca.crt\n", "                  - key: tls.key\n                    path: tls.key\n"),
    (BACKUP, "Prune=false,Delete=false", "Delete=false"),
    (BACKUP, "    spec:\n      automountServiceAccountToken: false\n      securityContext:", "      annotations:\n          backup.velero.io/backup-volumes-excludes: snapshots\n    spec:\n      automountServiceAccountToken: false\n      securityContext:"),
    (BACKUP, "bao operator raft snapshot save", "bao operator raft snapshot save -tls-skip-verify"),
    (BACKUP, "audience: vault", "audience: other"),
    # Velero must never see the plaintext (P1) and must still see the PVC
    (BACKUP, '            velero.io/exclude-from-backup: "true"\n', ""),
    (BACKUP, "            backup.velero.io/backup-volumes-excludes: work\n", "            backup.velero.io/backup-volumes-excludes: other\n"),
    (BACKUP, "      labels:\n        app.kubernetes.io/name: openbao-snapshot-holder\n        app.kubernetes.io/part-of: openbao\n    spec:", '      labels:\n        app.kubernetes.io/name: openbao-snapshot-holder\n        velero.io/exclude-from-backup: "true"\n    spec:'),
    (BACKUP, "              mountPath: /snapshots\n              readOnly: true\n      volumes", "              mountPath: /snapshots\n      volumes"),
    (BACKUP, "          volumeMounts:\n            - name: snapshots\n              mountPath: /snapshots\n              readOnly: true\n", ""),
    # CMS / plaintext / recipient (D-cms, D-plaintext, D-fpr, D-oaep)
    (BACKUP, 'openssl cms -encrypt -binary -aes-256-cbc -outform DER -stream -in "${PLAIN}" -out "${TMP}" -recip "${CERT}" -keyopt rsa_padding_mode:oaep', 'cp "${PLAIN}" "${TMP}"'),
    (BACKUP, " -keyopt rsa_padding_mode:oaep\n", ' -keyopt rsa_padding_mode:oaep || cp "${PLAIN}" "${TMP}"\n'),
    (BACKUP, " -keyopt rsa_padding_mode:oaep\n", " -keyopt rsa_padding_mode:oaep -des3\n"),
    (BACKUP, " -keyopt rsa_padding_mode:oaep\n", "\n"),
    (BACKUP, "-outform DER -stream -in", "-outform DER -in"),
    (BACKUP, "PLAIN=/work/openbao.snap\n                  DIR=", "PLAIN=/snapshots/raft/openbao.snap\n                  DIR="),
    (BACKUP, "PLAIN=/work/openbao.snap\n                  rm -f", "PLAIN=/snapshots/raft/openbao.snap\n                  rm -f"),
    (BACKUP, "            - name: work\n              emptyDir:\n                medium: Memory\n                sizeLimit: 256Mi\n", "            - name: work\n              persistentVolumeClaim:\n                claimName: openbao-snapshots\n"),
    (BACKUP, "            - name: work\n              emptyDir:\n                medium: Memory\n                sizeLimit: 256Mi\n", "            - name: work\n              emptyDir:\n                sizeLimit: 256Mi\n"),
    (BACKUP, "            - name: work\n              emptyDir:\n                medium: Memory\n                sizeLimit: 256Mi\n", "            - name: work\n              nfs:\n                server: 10.0.0.1\n                path: /x\n"),
    (BACKUP, "                - name: work\n                  mountPath: /work\n                - name: recipient", "                - name: snapshots\n                  mountPath: /work\n                - name: recipient"),
    (BACKUP, 'openssl x509 -in "${CERT}" -noout -checkend 86400 >/dev/null 2>&1 || fail "recipient certificate is expired or expires within 24h"', "true"),
    (BACKUP, "if grep -q 'PRIVATE KEY' \"${CERT}\"; then fail \"recipient.pem contains a private key\"; fi", "true"),
    (BACKUP, "if grep -q 'PRIVATE KEY' \"${CERT}\"; then fail", "if grep -q 'PRIVATE KEY' \"${CERT}\"; then echo"),
    (BACKUP, "-ge 3072", "-ge 1024"),
    (BACKUP, '[ "${BITS:-0}" -ge 3072 ]', '[ "${BITS:-4096}" -ge 3072 ]'),
    (BACKUP, '-ge 3072 ] || fail "recipient RSA', '-ge 3072 ] || echo "recipient RSA'),
    (BACKUP, "set -eu\n                  umask 022", "set -eu\n                  set +e\n                  umask 022"),
    (BACKUP, "trap 'rm -f \"${PLAIN}\" \"${TMP}\" \"${DIR}/.last-success.tmp\"' EXIT", "trap '' EXIT"),
    (BACKUP, '[ -s "${TMP}" ] || fail "ciphertext is empty"\n', ""),
    (BACKUP, '[ "${TAIL}" = "0000" ] || fail "ciphertext is truncated"\n', ""),
    (BACKUP, "| sort -r \\", "| sort \\"),
    (BACKUP, "                  trap 'rc=$?; [ \"${rc}\" -eq 0 ] || rm -f \"${PLAIN}\"' EXIT\n", "                  # trap removed\n"),
    (BACKUP, '[ "${FPR}" = "${WANT}" ] || fail', '[ "${FPR}" = "${WANT}" ] || echo'),
    (BACKUP, "                - name: RECIPIENT_FPR\n                  value: \"\"\n", "                - name: RECIPIENT_FPR\n                  value: \"AA\"\n"),
    (BACKUP, 'echo "WARNING: RECIPIENT_FPR is empty;', 'echo "note: RECIPIENT_FPR is empty;'),
    (BACKUP, 'optional: false\n', "optional: true\n"),
    (BACKUP, 'command -v openssl >/dev/null 2>&1 || fail "openssl not found in image"', "true"),
    (BACKUP, 'mv "${TMP}" "${DIR}/openbao-${STAMP}.snap.cms"', 'mv "${TMP}" "${DIR}/openbao-${STAMP}.snap"'),
    (BACKUP, "@sha256:081c2f5cb575efc901aa80ff9cdbd1ec6a301682fd35e1ebb4b0990a4a4a8507", ""),
    (BACKUP, "@sha256:081c2f5cb575efc901aa80ff9cdbd1ec6a301682fd35e1ebb4b0990a4a4a8507", "@sha256:181c2f5cb575efc901aa80ff9cdbd1ec6a301682fd35e1ebb4b0990a4a4a8507"),
    (BACKUP, "docker.io/valkey/valkey:8-alpine@", "docker.io/library/redis:8-alpine@"),
    (KEYGEN, "refusing to overwrite", "overwriting"),
    (KEYGEN, 'echo "!!! PRIVATE KEY: ${KEY_OUT}"', 'cat "${KEY_OUT}"'),
    (KEYGEN, '--from-file=recipient.pem="${CERT_OUT}"', '--from-file=recipient.pem="${KEY_OUT}"'),
    (KEYGEN, "NODES=()\nPOS=()", "NODES=(-nodes)\nPOS=()"),
    (KEYGEN, "umask 077", "umask 022"),
    (BINDINGS, "      token_no_default_policy=true \\\n", ""),
    (BINDINGS, 'token_ttl="10m"', 'token_ttl="30m"'),
    (BINDINGS, 'token_max_ttl="10m"', 'token_max_ttl="768h"'),
    (BINDINGS, "set -eu\ncat > /tmp/openbao-backup.hcl", "cat > /tmp/openbao-backup.hcl"),
    (BINDINGS, 'STORED=$(bao policy read openbao-backup)', 'STORED=$(true)'),
    (BINDINGS, '[ "$(bao read -field=audience "${R}")" = "${EXPECT_AUDIENCE}" ]\n', ""),
    (BINDINGS, '[ "$(bao read -field=bound_service_account_namespaces "${R}")" = "[storage]" ]\n', ""),
    (BINDINGS, 'capabilities = ["read"]\n}\nPOLICY_EOF\nbao policy write openbao-backup', 'capabilities = ["read", "update"]\n}\nPOLICY_EOF\nbao policy write openbao-backup'),
    (BINDINGS, 'bound_service_account_namespaces="storage" \\\n      policies="openbao-backup"', 'bound_service_account_namespaces="storage" \\\n      policies="root"'),
    (BINDINGS, 'OPENBAO_K8S_AUTH_AUDIENCE="${OPENBAO_K8S_AUTH_AUDIENCE:-vault}"', 'OPENBAO_K8S_AUTH_AUDIENCE="${OPENBAO_K8S_AUTH_AUDIENCE:-other}"'),
)


def mutation_verify():
    """Each mutation, applied to a scratch copy, must make check() fail."""
    global ROOT
    survivors = []
    for rel, old, new in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for f in (BACKUP, BINDINGS, APP, VELERO, KEYGEN, IMAGES, DIGESTS):
                (tmp / f).parent.mkdir(parents=True, exist_ok=True)
                (tmp / f).write_text((REPO / f).read_text())
            text = (tmp / rel).read_text()
            if old not in text:
                survivors.append(f"mutation target not found: {old!r}")
                continue
            (tmp / rel).write_text(text.replace(old, new, 1))
            ROOT = tmp
            if not check():
                survivors.append(f"mutation survived: {old!r}")
            elif rel == BACKUP and old in BEHAVIOURAL and not behaviour_only():
                survivors.append(f"mutation not caught by the behavioural tests alone: {old!r}")
    ROOT = REPO
    return survivors


# Mutations of the encrypt script that the behavioural tests must kill on their own, without the
# exact-line static checks (the static checks only pattern-match, the behaviour proves the effect).
BEHAVIOURAL = (
    "set -eu\n                  umask 022", '[ "${BITS:-0}" -ge 3072 ]', '-ge 3072 ] || fail "recipient RSA',
    "if grep -q 'PRIVATE KEY' \"${CERT}\"; then fail", "trap 'rm -f \"${PLAIN}\" \"${TMP}\" \"${DIR}/.last-success.tmp\"' EXIT",
    "| sort -r \\", '[ "${FPR}" = "${WANT}" ] || fail', '[ "${TAIL}" = "0000" ] || fail "ciphertext is truncated"\n',
    'openssl x509 -in "${CERT}" -noout -checkend 86400 >/dev/null 2>&1 || fail "recipient certificate is expired or expires within 24h"',
    "-ge 3072",
)


def behaviour_only():
    """Run just the behavioural tests against ROOT's script; True when they report a problem."""
    docs = [d for d in yaml.safe_load_all((ROOT / BACKUP).read_text()) if d]
    cron = next(d for d in docs if d["kind"] == "CronJob")
    script = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["command"][-1]
    problems = []
    behaviour(script, problems)
    return bool(problems)


if __name__ == "__main__":
    found = check()
    if "--mutation-verify" in sys.argv:
        found += mutation_verify()
    for problem in found:
        print(problem, file=sys.stderr)
    sys.exit(1 if found else 0)
