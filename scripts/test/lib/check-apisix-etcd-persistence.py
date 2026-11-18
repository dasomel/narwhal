#!/usr/bin/env python3
"""R374: apisix-etcd PVC persistence, IC resync intervals, and divergence recovery (#714).

Pins:
1. apisix-infra.yaml: Deployment/apisix-etcd uses strategy.type=Recreate, mounts
   PersistentVolumeClaim apisix-etcd-data (nfs-csi, ReadWriteOnce) at /var/etcd/data
   (no emptyDir), carries backup.velero.io/backup-volumes-excludes: data, and keeps
   its non-root uid/gid/fsGroup 10001 + readOnlyRootFilesystem hardening.
2. 08-1-networking.sh: passes config.apisixResourceSyncInterval=5m and
   config.kubernetes.resyncInterval=15m to apisix-ingress-controller (verified against
   bundled apisix-ingress-controller-0.14.1.tgz when present).
3. recover-apisix-resource-sync.sh: preserves default ResourceSyncAborted mode and
   implements --divergence mode (in-sync no-op, partial divergence resync, HTTP 404
   empty-prefix handling, non-convergence exit 1, 401 fail-closed, zero key leaks).
"""

import argparse
import base64
import glob
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
INFRA_REL = "gitops/charts/narwhal-platform/templates/apisix-infra.yaml"
NET_REL = "scripts/cluster/08-1-networking.sh"
RECOVER_REL = "scripts/cluster/lib/recover-apisix-resource-sync.sh"
ERRORS = (AssertionError, KeyError, TypeError, ValueError, yaml.YAMLError)


def _active_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _render_infra_docs(root: Path) -> list[dict]:
    chart_dir = root / "gitops/charts/narwhal-platform"
    if shutil.which("helm") and (chart_dir / "Chart.yaml").is_file():
        res = subprocess.run(
            [
                "helm",
                "template",
                "narwhal-platform",
                str(chart_dir),
                "--set",
                "baseDomain=local.narwhal.internal",
                "--show-only",
                "templates/apisix-infra.yaml",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return [d for d in yaml.safe_load_all(res.stdout) if isinstance(d, dict)]
    cleaned = re.sub(r"\{\{.*?\}\}", "local.narwhal.internal", (root / INFRA_REL).read_text())
    return [d for d in yaml.safe_load_all(cleaned) if isinstance(d, dict)]


def verify(root: Path) -> None:
    docs = _render_infra_docs(root)

    deps = [
        d for d in docs
        if d.get("kind") == "Deployment"
        and (d.get("metadata") or {}).get("namespace") == "platform-system"
        and (d.get("metadata") or {}).get("name") == "apisix-etcd"
    ]
    assert len(deps) == 1, f"expected 1 Deployment platform-system/apisix-etcd, got {len(deps)}"
    dep = deps[0]
    spec = dep.get("spec") or {}
    assert spec.get("replicas") == 1, f"apisix-etcd replicas must be 1, got {spec.get('replicas')!r}"
    assert (spec.get("strategy") or {}).get("type") == "Recreate", (
        f"apisix-etcd strategy.type must be Recreate, got {spec.get('strategy')!r}"
    )

    tpl = spec.get("template") or {}
    ann = ((tpl.get("metadata") or {}).get("annotations") or {})
    assert ann.get("backup.velero.io/backup-volumes-excludes") == "data", (
        f"apisix-etcd pod must exclude 'data' from Velero fs-backup, got {ann!r}"
    )

    pspec = tpl.get("spec") or {}
    psc = pspec.get("securityContext") or {}
    assert psc.get("runAsNonRoot") is True
    assert psc.get("runAsUser") == 10001 and psc.get("runAsGroup") == 10001 and psc.get("fsGroup") == 10001
    c = next((x for x in (pspec.get("containers") or []) if x.get("name") == "etcd"), None)
    assert c is not None, "apisix-etcd container 'etcd' missing"
    csc = c.get("securityContext") or {}
    assert csc.get("readOnlyRootFilesystem") is True and csc.get("allowPrivilegeEscalation") is False

    vols = {v["name"]: v for v in (pspec.get("volumes") or []) if isinstance(v, dict) and "name" in v}
    assert "data" in vols, "apisix-etcd volume 'data' missing"
    assert "emptyDir" not in vols["data"], "apisix-etcd volume 'data' must not be emptyDir"
    assert (vols["data"].get("persistentVolumeClaim") or {}).get("claimName") == "apisix-etcd-data", (
        f"apisix-etcd data volume must claim apisix-etcd-data, got {vols['data']!r}"
    )

    pvcs = [
        d for d in docs
        if d.get("kind") == "PersistentVolumeClaim"
        and (d.get("metadata") or {}).get("namespace") == "platform-system"
        and (d.get("metadata") or {}).get("name") == "apisix-etcd-data"
    ]
    assert len(pvcs) == 1, f"expected 1 PersistentVolumeClaim platform-system/apisix-etcd-data, got {len(pvcs)}"
    pvc_spec = pvcs[0].get("spec") or {}
    assert pvc_spec.get("storageClassName") == "nfs-csi", (
        f"apisix-etcd-data storageClassName must be nfs-csi, got {pvc_spec.get('storageClassName')!r}"
    )
    assert pvc_spec.get("accessModes") == ["ReadWriteOnce"], (
        f"apisix-etcd-data accessModes must be ['ReadWriteOnce'], got {pvc_spec.get('accessModes')!r}"
    )
    storage_req = ((pvc_spec.get("resources") or {}).get("requests") or {}).get("storage")
    assert storage_req, "apisix-etcd-data resources.requests.storage missing"

    net_active = _active_lines((root / NET_REL).read_text())
    ic_start = net_active.index("helm upgrade --install apisix-ingress-controller")
    ic_end = net_active.index("APISIX_INGRESS_CONTROLLER_OK=true", ic_start)
    ic_cmd = net_active[ic_start:ic_end]
    assert "--set config.apisixResourceSyncInterval=5m" in ic_cmd, (
        "08-1-networking.sh must set config.apisixResourceSyncInterval=5m"
    )
    assert "--set config.kubernetes.resyncInterval=15m" in ic_cmd, (
        "08-1-networking.sh must set config.kubernetes.resyncInterval=15m"
    )

    rec_text = (root / RECOVER_REL).read_text()
    for needle in (
        "--divergence|--check-divergence)",
        "narwhal.io/resync=",
        "apisix-admin-key",
        "port-forward",
        '[ "${code}" = "404" ]',
        "ResourceSyncAborted",
        "MAX_ROUNDS=",
    ):
        assert needle in rec_text, f"{RECOVER_REL} missing {needle!r}"


def verify_helm_ic_render(root: Path) -> bool:
    bundles = sorted(glob.glob(str(root / "narwhal-airgap-bundle-*/charts/apisix-ingress-controller-0.14.1.tgz")))
    if not bundles:
        bundles = sorted(glob.glob(str(root.parents[2] / "narwhal-airgap-bundle-*/charts/apisix-ingress-controller-0.14.1.tgz")))
    if not bundles or not shutil.which("helm"):
        return False
    out = subprocess.run(
        [
            "helm",
            "template",
            "apisix-ingress-controller",
            bundles[0],
            "-n",
            "platform-system",
            "--set",
            "config.apisixResourceSyncInterval=5m",
            "--set",
            "config.kubernetes.resyncInterval=15m",
            "--show-only",
            "templates/configmap.yaml",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    cm = yaml.safe_load(out)
    cfg = yaml.safe_load(cm["data"]["config.yaml"])
    assert cfg["apisix_resource_sync_interval"] == "5m", cfg
    assert cfg["kubernetes"]["resync_interval"] == "15m", cfg
    return True


def verify_recovery_behavior(root: Path) -> None:
    """Exercise recover-apisix-resource-sync.sh with fake kubectl/curl and argv leak checks."""
    script = root / RECOVER_REL
    secret_val = "super-secret-admin-key-714"

    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        bin_dir = w / "bin"
        st_dir = w / "st"
        bin_dir.mkdir()
        st_dir.mkdir()

        kubectl_stub = bin_dir / "kubectl"
        kubectl_stub.write_text(
            """#!/bin/bash
set -euo pipefail
st="${FAKE_ST}"
echo "kubectl $*" >> "${st}/calls"
case "$1 $2" in
  "get secret")
    if [ "$3" = "apisix-admin-key" ]; then
      printf '%s' "${FAKE_ADMIN_KEY}" | base64 | tr -d '\\n'
      exit 0
    fi
    exit 1
    ;;
  "get apisixroutes.apisix.apache.org")
    if [[ "$*" == *" -o name"* ]]; then
      printf 'apisixroute.apisix.apache.org/portal\\napisixroute.apisix.apache.org/keycloak\\n'
    else
      cat "${st}/cr_routes.json"
    fi
    exit 0
    ;;
  "get apisixtlses.apisix.apache.org")
    if [[ "$*" == *" -o name"* ]]; then
      printf 'apisixtls.apisix.apache.org/narwhal-wildcard\\n'
    else
      cat "${st}/cr_tls.json"
    fi
    exit 0
    ;;
  "get apisixupstreams.apisix.apache.org")
    if [[ "$*" == *" -o name"* ]]; then
      printf 'apisixupstream.apisix.apache.org/portal\\napisixupstream.apisix.apache.org/keycloak\\n'
    else
      cat "${st}/cr_upstreams.json"
    fi
    exit 0
    ;;
  "port-forward "*)
    exec sleep 30
    ;;
esac
if [ "$1" = "annotate" ]; then
  kind="$2"
  echo "${kind}" >> "${st}/annotated"
  if [ -f "${st}/converge_on_annotate" ]; then
    printf 'synced' > "${st}/mode"
  fi
  exit 0
fi
exit 0
""",
            encoding="utf-8",
        )

        curl_stub = bin_dir / "curl"
        curl_stub.write_text(
            """#!/bin/bash
set -euo pipefail
st="${FAKE_ST}"
echo "curl $*" >> "${st}/calls"
key=$(sed -n 's/.*X-API-KEY: \\(.*\\)"$/\\1/p')
if [ "${key}" != "${FAKE_ADMIN_KEY}" ]; then
  printf '{"error_msg":"invalid api key"}\\n401'
  exit 0
fi
url="${@: -1}"
ep="${url##*/}"
mode=$(cat "${st}/mode")
if [ "${mode}" = "auth_fail" ]; then
  printf '{"error_msg":"unauthorized"}\\n401'
  exit 0
fi
if [ "${mode}" = "empty_404" ]; then
  printf '{"message":"Key not found"}\\n404'
  exit 0
fi
if [ "${mode}" = "partial" ]; then
  case "${ep}" in
    routes) printf '{"total":1,"list":[{}]}\\n200' ;;
    upstreams) printf '{"total":0,"list":[]}\\n200' ;;
    ssls) printf '{"message":"Key not found"}\\n404' ;;
  esac
  exit 0
fi
case "${ep}" in
  routes) printf '{"total":2,"list":[{},{}]}\\n200' ;;
  upstreams) printf '{"total":2,"list":[{},{}]}\\n200' ;;
  ssls) printf '{"total":1,"list":[{}]}\\n200' ;;
esac
""",
            encoding="utf-8",
        )

        for tool in ("date", "sleep", "base64", "cat", "awk", "sed", "mktemp", "rm", "kill"):
            real = shutil.which(tool)
            assert real, f"missing required tool {tool}"
            wrapper = bin_dir / tool
            wrapper.write_text(
                f'#!/bin/bash\necho "{tool} $*" >> "${{FAKE_ST}}/calls"\nexec {real} "$@"\n',
                encoding="utf-8",
            )

        for p in bin_dir.iterdir():
            p.chmod(p.stat().st_mode | stat.S_IXUSR)

        cr_routes = {
            "items": [
                {
                    "metadata": {"name": "portal"},
                    "spec": {"http": [{"name": "p1", "upstreams": [{"name": "portal"}]}]},
                    "status": {"conditions": [{"type": "ResourcesAvailable", "reason": "ResourcesSynced"}]},
                },
                {
                    "metadata": {"name": "keycloak"},
                    "spec": {"http": [{"name": "k1", "upstreams": [{"name": "keycloak"}]}]},
                    "status": {"conditions": [{"type": "ResourcesAvailable", "reason": "ResourcesSynced"}]},
                },
            ]
        }
        cr_tls = {"items": [{"metadata": {"name": "narwhal-wildcard"}}]}
        cr_upstreams = {"items": [{"metadata": {"name": "portal"}}, {"metadata": {"name": "keycloak"}}]}
        (st_dir / "cr_routes.json").write_text(json.dumps(cr_routes), encoding="utf-8")
        (st_dir / "cr_tls.json").write_text(json.dumps(cr_tls), encoding="utf-8")
        (st_dir / "cr_upstreams.json").write_text(json.dumps(cr_upstreams), encoding="utf-8")

        env = dict(
            os.environ,
            PATH=f"{bin_dir}:{os.environ['PATH']}",
            FAKE_ST=str(st_dir),
            FAKE_ADMIN_KEY=secret_val,
            APISIX_RECOVERY_MAX_ROUNDS="2",
            APISIX_DIVERGENCE_WAIT_SECONDS="0",
            APISIX_DIVERGENCE_POLL_INTERVAL="0",
            APISIX_PF_WAIT_ATTEMPTS="2",
        )

        def reset(mode: str, converge: bool = False) -> None:
            (st_dir / "calls").write_text("", encoding="utf-8")
            (st_dir / "annotated").unlink(missing_ok=True)
            (st_dir / "converge_on_annotate").unlink(missing_ok=True)
            if converge:
                (st_dir / "converge_on_annotate").write_text("1", encoding="utf-8")
            (st_dir / "mode").write_text(mode, encoding="utf-8")

        def assert_no_secret_leak(res: subprocess.CompletedProcess) -> None:
            combined = res.stdout + res.stderr + (st_dir / "calls").read_text(encoding="utf-8")
            assert secret_val not in combined, "admin key leaked to stdout/stderr or argv"
            b64_val = base64.b64encode(secret_val.encode()).decode()
            assert b64_val not in res.stdout + res.stderr, "base64 admin key leaked to output"

        # 1. Default aborted mode (no aborted CRs) stays backward-compatible without port-forward.
        reset("synced")
        res = subprocess.run(["bash", str(script), "platform-system"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert "port-forward" not in (st_dir / "calls").read_text(encoding="utf-8")

        # 2. In-sync divergence check: exits 0 and performs no annotations.
        reset("synced")
        res = subprocess.run(["bash", str(script), "--divergence", "platform-system"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert not (st_dir / "annotated").exists(), "in-sync check must not annotate CRs"
        assert_no_secret_leak(res)

        # 3. Partial divergence (even when CRs say ResourcesSynced) triggers resync of all 3 kinds and converges.
        reset("partial", converge=True)
        res = subprocess.run(["bash", str(script), "platform-system", "--divergence"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        annotated = (st_dir / "annotated").read_text(encoding="utf-8").splitlines()
        assert annotated == [
            "apisixupstreams.apisix.apache.org",
            "apisixtlses.apisix.apache.org",
            "apisixroutes.apisix.apache.org",
        ], annotated
        assert_no_secret_leak(res)

        # 4. Empty etcd HTTP 404 triggers resync and converges.
        reset("empty_404", converge=True)
        res = subprocess.run(["bash", str(script), "--check-divergence"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert (st_dir / "annotated").exists()
        assert_no_secret_leak(res)

        # 5. Non-convergent empty etcd exits non-zero with 404 recovery hint.
        reset("empty_404", converge=False)
        res = subprocess.run(["bash", str(script), "--divergence"], env=env, capture_output=True, text=True)
        assert res.returncode != 0, "non-convergent divergence must exit non-zero"
        assert "apisix-etcd-recovery.md" in res.stderr
        assert_no_secret_leak(res)

        # 6. Admin API 401 fails closed without leaking the key.
        reset("auth_fail")
        res = subprocess.run(["bash", str(script), "--divergence"], env=env, capture_output=True, text=True)
        assert res.returncode != 0, "HTTP 401 must exit non-zero"
        assert_no_secret_leak(res)


def mutation_verify(root: Path) -> int:
    sources = {rel: (root / rel).read_text(encoding="utf-8") for rel in (INFRA_REL, NET_REL, RECOVER_REL)}
    mutations = [
        (INFRA_REL, "  strategy:\n    type: Recreate\n", ""),
        (INFRA_REL, "    type: Recreate", "    type: RollingUpdate"),
        (INFRA_REL, "        backup.velero.io/backup-volumes-excludes: data\n", ""),
        (
            INFRA_REL,
            "          persistentVolumeClaim:\n            claimName: apisix-etcd-data",
            "          emptyDir: {}",
        ),
        (INFRA_REL, "  name: apisix-etcd-data\n", "  name: wrong-etcd-pvc\n"),
        (INFRA_REL, "  storageClassName: nfs-csi", "  storageClassName: standard"),
        (INFRA_REL, "    - ReadWriteOnce", "    - ReadWriteMany"),
        (NET_REL, "    --set config.apisixResourceSyncInterval=5m \\\n", ""),
        (NET_REL, "    --set config.kubernetes.resyncInterval=15m \\\n", ""),
        (RECOVER_REL, '      --divergence|--check-divergence)\n', '      --disabled)\n'),
        (RECOVER_REL, '  elif [ "${code}" = "404" ]; then', '  elif [ "${code}" = "418" ]; then'),
        (RECOVER_REL, 'narwhal.io/resync=${ts}', 'narwhal.io/other=${ts}'),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp)
        shutil.copytree(root / "gitops/charts/narwhal-platform", fixture / "gitops/charts/narwhal-platform")
        for rel in (NET_REL, RECOVER_REL):
            (fixture / rel).parent.mkdir(parents=True, exist_ok=True)
        for rel, old, new in mutations:
            for k, txt in sources.items():
                (fixture / k).write_text(txt, encoding="utf-8")
            assert old in sources[rel], f"mutation anchor missing in {rel}: {old!r}"
            (fixture / rel).write_text(sources[rel].replace(old, new, 1), encoding="utf-8")
            try:
                verify(fixture)
            except ERRORS:
                continue
            raise AssertionError(f"mutation survived in {rel}: {old!r}")
    return len(mutations)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()

    verify(args.root)
    rendered = verify_helm_ic_render(args.root)
    verify_recovery_behavior(args.root)
    ic_note = "offline IC helm render" if rendered else "offline IC helm render SKIPPED (no chart bundle)"
    if args.mutation_verify:
        caught = mutation_verify(args.root)
        print(f"PASS: R374 apisix-etcd PVC persistence + {ic_note} + behavioral recovery ({caught} mutations rejected)")
    else:
        print(f"PASS: R374 apisix-etcd PVC persistence + {ic_note} + behavioral recovery")
    return 0


if __name__ == "__main__":
    sys.exit(main())
