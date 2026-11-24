#!/usr/bin/env python3
"""R396: KISA-ETCD-02 Stage 1 (apisix-etcd NetworkPolicy) & KISA-SEC-01 (OpenBao unseal hygiene).

Verifies:
1. gitops/resources/apisix-etcd-ingress-policy.yaml isolates app=apisix-etcd in platform-system:
   - Ingress only, managed-by=narwhal-gitops
   - Allows ONLY platform-system pods matching app.kubernetes.io/name=apisix (no ipBlock,
     no namespaceSelector, no apisix-ingress-controller, no waypoint, no peer-less rule)
   - Allows ONLY TCP 2379 and ambient HBONE TCP 15008 (peer port 2380 stays closed)
2. gitops/charts/narwhal-apps/templates/apisix-etcd-ingress-policy.yaml wires the ArgoCD
   Application (sync-wave 2, destination platform-system, include apisix-etcd-ingress-policy.yaml,
   prune+selfHeal+ServerSideApply) and docs/common/upgrade-waves.json registers it.
3. scripts/cluster/08-1-networking.sh bootstraps apisix-etcd-ingress-policy.yaml loudly
   (existence guard + kubectl apply without `|| true`).
4. gitops/resources/openbao-unseal.yaml defines dedicated ServiceAccount storage/openbao-auto-unseal
   with automountServiceAccountToken: false, wires the CronJob pod to it with
   automountServiceAccountToken: false, removes BAO_SKIP_VERIFY / -tls-skip-verify, verifies TLS
   via BAO_CACERT=/openbao/ca/ca.crt, and mounts ONLY ca.crt from Secret openbao-tls (never tls.key).
5. scripts/cluster/08-4-storage.sh includes the CronJob's dialed BAO_ADDR host in
   Certificate/openbao-tls spec.dnsNames, creates OPENBAO_INIT_DIR with umask 077 and an EXIT
   cleanup trap, creates/replaces openbao-init from files without kubectl apply or --from-literal
   (no last-applied-configuration copy of keys), labels it velero.io/exclude-from-backup=true,
   and strips kubectl.kubernetes.io/last-applied-configuration idempotently.
6. scripts/test/regression-check-kakao.sh wires R396 with --mutation-verify.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "netpol": ROOT / "gitops/resources/apisix-etcd-ingress-policy.yaml",
    "app": ROOT / "gitops/charts/narwhal-apps/templates/apisix-etcd-ingress-policy.yaml",
    "waves": ROOT / "docs/common/upgrade-waves.json",
    "net_sh": ROOT / "scripts/cluster/08-1-networking.sh",
    "unseal": ROOT / "gitops/resources/openbao-unseal.yaml",
    "storage_sh": ROOT / "scripts/cluster/08-4-storage.sh",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

EXPECTED_CALLER = {"app.kubernetes.io/name": "apisix"}
EXPECTED_PORTS = {("TCP", 2379), ("TCP", 15008)}
EXPECTED_BAO_ADDR = "https://openbao-0.openbao-internal:8200"
EXPECTED_BAO_CACERT = "/openbao/ca/ca.crt"


def _strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _check_etcd_netpol(text: str, errors: list[str]) -> None:
    docs = [d for d in yaml.safe_load_all(text) if d]
    if len(docs) != 1:
        errors.append(f"apisix-etcd-ingress-policy.yaml must contain exactly 1 document (got {len(docs)})")
        return
    doc = docs[0]
    if doc.get("apiVersion") != "networking.k8s.io/v1" or doc.get("kind") != "NetworkPolicy":
        errors.append("apisix-etcd-ingress-policy.yaml must declare networking.k8s.io/v1 NetworkPolicy")
        return
    meta = doc.get("metadata") or {}
    if meta.get("name") != "apisix-etcd-ingress-policy" or meta.get("namespace") != "platform-system":
        errors.append("apisix-etcd-ingress-policy metadata must target platform-system/apisix-etcd-ingress-policy")
    if (meta.get("labels") or {}).get("app.kubernetes.io/managed-by") != "narwhal-gitops":
        errors.append("apisix-etcd-ingress-policy must carry label app.kubernetes.io/managed-by=narwhal-gitops")

    spec = doc.get("spec") or {}
    pod_sel = spec.get("podSelector") or {}
    if pod_sel.get("matchLabels") != {"app": "apisix-etcd"} or pod_sel.get("matchExpressions"):
        errors.append(f"apisix-etcd-ingress-policy podSelector must match only app=apisix-etcd (got {pod_sel!r})")
    if spec.get("policyTypes") != ["Ingress"]:
        errors.append(f"apisix-etcd-ingress-policy policyTypes must be ['Ingress'] (got {spec.get('policyTypes')!r})")

    rules = spec.get("ingress")
    if not isinstance(rules, list) or len(rules) != 1:
        errors.append(f"apisix-etcd-ingress-policy must have exactly 1 ingress rule (got {rules!r})")
        return
    rule = rules[0]
    entries = rule.get("from")
    if not isinstance(entries, list) or len(entries) != 1:
        errors.append(f"apisix-etcd-ingress-policy ingress rule must allow exactly 1 caller entry (got {entries!r})")
    else:
        peer = entries[0]
        if not isinstance(peer, dict):
            errors.append("apisix-etcd-ingress-policy peer entry must be a mapping")
        else:
            if set(peer.keys()) != {"podSelector"}:
                errors.append(
                    f"apisix-etcd-ingress-policy peer must use same-namespace podSelector only (got keys {sorted(peer.keys())})"
                )
            ps = peer.get("podSelector") or {}
            if ps.get("matchLabels") != EXPECTED_CALLER or ps.get("matchExpressions"):
                errors.append(f"apisix-etcd-ingress-policy must allow only {EXPECTED_CALLER} (got {ps!r})")

    ports = rule.get("ports")
    if not isinstance(ports, list):
        errors.append("apisix-etcd-ingress-policy ingress rule must declare ports")
    else:
        got_ports = {(p.get("protocol", "TCP"), p.get("port")) for p in ports if isinstance(p, dict)}
        if got_ports != EXPECTED_PORTS or len(ports) != len(EXPECTED_PORTS):
            errors.append(
                f"apisix-etcd-ingress-policy ports must be exactly {sorted(EXPECTED_PORTS)} (got {sorted(got_ports)})"
            )
        if any("endPort" in p for p in ports if isinstance(p, dict)):
            errors.append("apisix-etcd-ingress-policy must not use endPort ranges")


def _check_etcd_app_and_waves(app_text: str, waves_text: str, errors: list[str]) -> None:
    rendered = app_text.replace("{{ .Values.repoURL }}", "http://example.invalid/repo.git")
    doc = yaml.safe_load(rendered)
    if not isinstance(doc, dict) or doc.get("kind") != "Application":
        errors.append("narwhal-apps/templates/apisix-etcd-ingress-policy.yaml must declare an ArgoCD Application")
        return
    meta = doc.get("metadata") or {}
    if meta.get("name") != "apisix-etcd-ingress-policy" or meta.get("namespace") != "devtools":
        errors.append("apisix-etcd-ingress-policy Application must be devtools/apisix-etcd-ingress-policy")
    if (meta.get("annotations") or {}).get("argocd.argoproj.io/sync-wave") != "2":
        errors.append("apisix-etcd-ingress-policy Application must use sync-wave '2'")
    spec = doc.get("spec") or {}
    if spec.get("project") != "platform":
        errors.append("apisix-etcd-ingress-policy Application must use project 'platform'")
    src = spec.get("source") or {}
    if src.get("path") != "resources" or (src.get("directory") or {}).get("include") != "apisix-etcd-ingress-policy.yaml":
        errors.append("apisix-etcd-ingress-policy Application must include resources/apisix-etcd-ingress-policy.yaml")
    if (spec.get("destination") or {}).get("namespace") != "platform-system":
        errors.append("apisix-etcd-ingress-policy Application destination.namespace must be platform-system")
    sync = spec.get("syncPolicy") or {}
    auto = sync.get("automated") or {}
    if auto.get("prune") is not True or auto.get("selfHeal") is not True:
        errors.append("apisix-etcd-ingress-policy Application must enable automated prune and selfHeal")
    if "ServerSideApply=true" not in (sync.get("syncOptions") or []):
        errors.append("apisix-etcd-ingress-policy Application must set ServerSideApply=true")

    waves = json.loads(waves_text)
    if "apisix-etcd-ingress-policy" not in (waves.get("NOT_YET_MODELLED") or {}):
        errors.append("docs/common/upgrade-waves.json must list apisix-etcd-ingress-policy under NOT_YET_MODELLED")


def _check_bootstrap_netpol(net_sh: str, errors: list[str]) -> None:
    code = _strip_comments(net_sh)
    target = "/home/vagrant/configs/gitops/resources/apisix-etcd-ingress-policy.yaml"
    if f'[ ! -f "{target}" ]' not in code:
        errors.append("08-1-networking.sh must guard existence of apisix-etcd-ingress-policy.yaml before apply")
    apply_lines = [line.strip() for line in code.splitlines() if target in line and "kubectl apply" in line]
    if not apply_lines:
        errors.append("08-1-networking.sh must apply apisix-etcd-ingress-policy.yaml during bootstrap")
    elif any("|| true" in line for line in apply_lines):
        errors.append("08-1-networking.sh must not swallow errors with `|| true` when applying apisix-etcd-ingress-policy.yaml")


def _check_openbao_unseal(unseal_text: str, errors: list[str]) -> str | None:
    docs = [d for d in yaml.safe_load_all(unseal_text) if d]
    sa = next((d for d in docs if d.get("kind") == "ServiceAccount"), None)
    cj = next((d for d in docs if d.get("kind") == "CronJob"), None)

    if not sa:
        errors.append("openbao-unseal.yaml must define dedicated ServiceAccount storage/openbao-auto-unseal")
    else:
        meta = sa.get("metadata") or {}
        if meta.get("name") != "openbao-auto-unseal" or meta.get("namespace") != "storage":
            errors.append("openbao-unseal.yaml ServiceAccount must be storage/openbao-auto-unseal")
        if sa.get("automountServiceAccountToken") is not False:
            errors.append("ServiceAccount storage/openbao-auto-unseal must set automountServiceAccountToken: false")

    if not cj:
        errors.append("openbao-unseal.yaml missing CronJob")
        return None

    pod_spec = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    if pod_spec.get("serviceAccountName") != "openbao-auto-unseal":
        errors.append(
            f"openbao-auto-unseal pod must set serviceAccountName: openbao-auto-unseal (got {pod_spec.get('serviceAccountName')!r})"
        )
    if pod_spec.get("automountServiceAccountToken") is not False:
        errors.append("openbao-auto-unseal pod must set automountServiceAccountToken: false")

    containers = pod_spec.get("containers") or []
    if len(containers) != 1:
        errors.append(f"openbao-auto-unseal must have 1 container (got {len(containers)})")
        return None
    c = containers[0]
    envs = {e.get("name"): e for e in (c.get("env") or []) if isinstance(e, dict)}
    if "BAO_SKIP_VERIFY" in envs:
        errors.append("openbao-auto-unseal must not set BAO_SKIP_VERIFY")
    cmd_text = " ".join(str(x) for x in (c.get("command") or []))
    if "tls-skip-verify" in cmd_text or "BAO_SKIP_VERIFY" in cmd_text:
        errors.append("openbao-auto-unseal command must not disable TLS verification")

    bao_addr = (envs.get("BAO_ADDR") or {}).get("value")
    if bao_addr != EXPECTED_BAO_ADDR:
        errors.append(f"openbao-auto-unseal BAO_ADDR must be {EXPECTED_BAO_ADDR!r} (got {bao_addr!r})")
    bao_cacert = (envs.get("BAO_CACERT") or {}).get("value")
    if bao_cacert != EXPECTED_BAO_CACERT:
        errors.append(f"openbao-auto-unseal BAO_CACERT must be {EXPECTED_BAO_CACERT!r} (got {bao_cacert!r})")

    mounts = {m.get("name"): m for m in (c.get("volumeMounts") or []) if isinstance(m, dict)}
    ca_mount = mounts.get("openbao-ca")
    if not ca_mount or ca_mount.get("mountPath") != "/openbao/ca" or ca_mount.get("readOnly") is not True:
        errors.append("openbao-auto-unseal must mount openbao-ca at /openbao/ca readOnly: true")

    vols = {v.get("name"): v for v in (pod_spec.get("volumes") or []) if isinstance(v, dict)}
    ca_vol = vols.get("openbao-ca")
    sec = (ca_vol or {}).get("secret") if isinstance(ca_vol, dict) else None
    if not sec or sec.get("secretName") != "openbao-tls" or sec.get("items") != [{"key": "ca.crt", "path": "ca.crt"}]:
        errors.append("openbao-auto-unseal openbao-ca volume must project ONLY ca.crt from Secret openbao-tls")

    return urlparse(bao_addr).hostname if isinstance(bao_addr, str) else None


def _check_storage_sh(storage_sh: str, dialed_host: str | None, errors: list[str]) -> None:
    try:
        cert_yaml = storage_sh.split("apiVersion: cert-manager.io/v1\n", 1)[1].split("\nEOF", 1)[0]
        cert = yaml.safe_load("apiVersion: cert-manager.io/v1\n" + cert_yaml)
    except (IndexError, yaml.YAMLError) as exc:
        errors.append(f"08-4-storage.sh openbao-tls Certificate parse failed: {exc}")
        cert = None

    if cert:
        dns_names = (cert.get("spec") or {}).get("dnsNames") or []
        if not dialed_host:
            errors.append("openbao-unseal.yaml BAO_ADDR missing hostname to cross-check against openbao-tls dnsNames")
        elif dialed_host not in dns_names:
            errors.append(
                f"08-4-storage.sh Certificate/openbao-tls spec.dnsNames missing BAO_ADDR host {dialed_host!r}"
            )

    code = _strip_comments(storage_sh)
    init_block_match = re.search(
        r"OPENBAO_INIT_DIR=[\s\S]*?(?=kubectl create secret generic openbao-init)",
        code,
    )
    if not init_block_match:
        errors.append("08-4-storage.sh missing OPENBAO_INIT_DIR setup before openbao-init creation")
    else:
        init_block = init_block_match.group(0)
        if not re.search(r"OPENBAO_INIT_DIR=\$\(\s*umask\s+077\s*(?:&&|;)\s*mktemp\s+-d\s*\)", init_block):
            errors.append("08-4-storage.sh must create OPENBAO_INIT_DIR with umask 077")
        if not re.search(
            r"""trap\s+['"]rm\s+-rf\s+"\$\{OPENBAO_INIT_DIR(?::-[^}]*)?\}"['"]\s+EXIT""",
            init_block,
        ):
            errors.append(
                "08-4-storage.sh must register an EXIT trap removing OPENBAO_INIT_DIR before kubectl create/replace"
            )

    # Inspect the openbao-init creation block specifically.
    create_cmds = re.findall(r"kubectl create secret generic openbao-init[\s\S]*?(?=\n\s*(?:fi|else|rm |echo ))", code)
    if not create_cmds:
        errors.append("08-4-storage.sh missing kubectl create secret generic openbao-init")
    for cmd in create_cmds:
        if "kubectl apply" in cmd:
            errors.append("08-4-storage.sh must not use kubectl apply for openbao-init (creates last-applied-configuration copy)")
        if "--from-literal" in cmd:
            errors.append("08-4-storage.sh must not pass --from-literal for openbao-init (use 077-umask --from-file)")

    if "kubectl label secret openbao-init -n storage velero.io/exclude-from-backup=true --overwrite" not in code:
        errors.append("08-4-storage.sh must label openbao-init with velero.io/exclude-from-backup=true")
    if "kubectl annotate secret openbao-init -n storage kubectl.kubernetes.io/last-applied-configuration-" not in code:
        errors.append("08-4-storage.sh must strip kubectl.kubernetes.io/last-applied-configuration from openbao-init")


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    _check_etcd_netpol(texts["netpol"], errors)
    _check_etcd_app_and_waves(texts["app"], texts["waves"], errors)
    _check_bootstrap_netpol(texts["net_sh"], errors)
    dialed_host = _check_openbao_unseal(texts["unseal"], errors)
    _check_storage_sh(texts["storage_sh"], dialed_host, errors)
    if "check R396 " not in texts["reg"] or "check-kisa-etcd-openbao-hardening.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R396 with --mutation-verify")
    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("drop HBONE 15008 from etcd policy", "netpol", "        - protocol: TCP\n          port: 15008\n", ""),
    ("open peer port 2380 on etcd policy", "netpol", "          port: 2379\n", "          port: 2379\n        - protocol: TCP\n          port: 2380\n"),
    (
        "add apisix-ingress-controller to etcd policy",
        "netpol",
        "              app.kubernetes.io/name: apisix\n",
        "              app.kubernetes.io/name: apisix\n        - podSelector:\n            matchLabels:\n              app.kubernetes.io/name: apisix-ingress-controller\n",
    ),
    ("widen etcd policy with namespaceSelector", "netpol", "        - podSelector:\n", "        - namespaceSelector: {}\n          podSelector:\n"),
    ("drop ArgoCD Application include", "app", 'include: "apisix-etcd-ingress-policy.yaml"', 'include: "other.yaml"'),
    ("drop upgrade-waves entry", "waves", '    "apisix-etcd-ingress-policy": "Network policy configuration, not a separately upgraded component.",\n', ""),
    ("swallow bootstrap apply errors with || true", "net_sh", "kubectl apply -f /home/vagrant/configs/gitops/resources/apisix-etcd-ingress-policy.yaml", "kubectl apply -f /home/vagrant/configs/gitops/resources/apisix-etcd-ingress-policy.yaml || true"),
    ("drop bootstrap existence guard", "net_sh", 'if [ ! -f "/home/vagrant/configs/gitops/resources/apisix-etcd-ingress-policy.yaml" ]; then', 'if false; then'),
    ("enable automountServiceAccountToken on SA", "unseal", "automountServiceAccountToken: false\n---", "automountServiceAccountToken: true\n---"),
    ("drop serviceAccountName on CronJob pod", "unseal", "          serviceAccountName: openbao-auto-unseal\n", ""),
    ("enable automountServiceAccountToken on CronJob pod", "unseal", "          serviceAccountName: openbao-auto-unseal\n          automountServiceAccountToken: false\n", "          serviceAccountName: openbao-auto-unseal\n          automountServiceAccountToken: true\n"),
    ("reintroduce BAO_SKIP_VERIFY", "unseal", "                - name: BAO_CACERT\n                  value: /openbao/ca/ca.crt\n", "                - name: BAO_SKIP_VERIFY\n                  value: \"true\"\n"),
    ("dial non-SAN FQDN in openbao-unseal BAO_ADDR", "unseal", 'value: "https://openbao-0.openbao-internal:8200"', 'value: "https://openbao-0.openbao-internal.storage.svc.cluster.local:8200"'),
    ("mount whole openbao-tls secret (exposes tls.key)", "unseal", "                items:\n                  - key: ca.crt\n                    path: ca.crt\n", ""),
    ("drop dialed SAN from openbao-tls Certificate", "storage_sh", "    - openbao-0.openbao-internal\n", ""),
    ("drop umask 077 on OPENBAO_INIT_DIR", "storage_sh", "OPENBAO_INIT_DIR=$(umask 077 && mktemp -d)", "OPENBAO_INIT_DIR=$(mktemp -d)"),
    ("drop EXIT trap for OPENBAO_INIT_DIR", "storage_sh", "    trap 'rm -rf \"${OPENBAO_INIT_DIR:-}\"' EXIT\n", ""),
    ("reintroduce kubectl apply for openbao-init", "storage_sh", "--dry-run=client -o yaml | kubectl replace -f -", "--dry-run=client -o yaml | kubectl apply -f -"),
    ("reintroduce --from-literal for openbao-init", "storage_sh", '--from-file=unseal_keys_b64="${OPENBAO_INIT_DIR}/unseal_keys_b64"', '--from-literal=unseal_keys_b64="${UNSEAL_KEY}"'),
    ("drop Velero exclude label on openbao-init", "storage_sh", "  kubectl label secret openbao-init -n storage velero.io/exclude-from-backup=true --overwrite\n", ""),
    ("drop last-applied-configuration strip on openbao-init", "storage_sh", "  kubectl annotate secret openbao-init -n storage kubectl.kubernetes.io/last-applied-configuration-\n", ""),
    ("drop R396 wiring", "reg", "check R396 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            mutated = copy.deepcopy(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation target not found for {name}", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R396 KISA-ETCD-02 Stage 1 & KISA-SEC-01 hardening ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R396 KISA-ETCD-02 Stage 1 & KISA-SEC-01 hardening")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
