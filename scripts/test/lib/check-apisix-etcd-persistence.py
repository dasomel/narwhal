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
   implements --divergence mode using controller-derived crc32 object identities across
   routes, stream_routes, upstreams, and ssls (in-sync no-op, extra-seed + missing-route
   detection, stream route sync/divergence, HTTP 404 empty-prefix handling, non-convergence
   exit 1, 401 fail-closed, zero key leaks).
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
import zlib
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

    net_raw = (root / NET_REL).read_text()
    assert "bounds recovery latency" not in net_raw, (
        "08-1-networking.sh D-ic-resync comment must not claim 5m/15m bounds startup deadlock recovery latency"
    )
    assert "shortens the controller's periodic resync" in net_raw, (
        "08-1-networking.sh D-ic-resync comment must state 5m/15m shortens periodic resync"
    )

    net_active = _active_lines(net_raw)
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
        "for ep in routes stream_routes upstreams ssls; do",
        "zlib.crc32",
        "missing = exp_set - act_set",
        'echo "WARN: APISIX contains',
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


def _gen_id(raw: str) -> str:
    return format(zlib.crc32(raw.encode("utf-8")) & 0xFFFFFFFF, "x")


def _apisix_v3_payload(prefix: str, items: list[dict]) -> str:
    wrapped = [
        {"key": f"/apisix/{prefix}/{item['id']}", "value": item}
        for item in items
    ]
    return json.dumps({"total": len(wrapped), "list": wrapped})


def verify_recovery_behavior(root: Path) -> None:
    """Exercise recover-apisix-resource-sync.sh with fake kubectl/curl and argv leak checks."""
    script = root / RECOVER_REL
    secret_val = "super-secret-admin-key-714"

    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        bin_dir = w / "bin"
        st_dir = w / "st"
        synced_dir = st_dir / "synced"
        bin_dir.mkdir()
        st_dir.mkdir()
        synced_dir.mkdir()

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
      cat "${st}/cr_routes_names.txt"
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
    cp "${st}/synced/"*.resp "${st}/"
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
if [ -f "${st}/auth_fail" ]; then
  printf '{"error_msg":"unauthorized"}\\n401'
  exit 0
fi
cat "${st}/${ep}.resp"
""",
            encoding="utf-8",
        )

        for tool in ("date", "sleep", "base64", "cat", "awk", "sed", "mktemp", "rm", "kill", "cp"):
            real = shutil.which(tool)
            assert real, f"missing required tool {tool}"
            wrapper = bin_dir / tool
            wrapper.write_text(
                f'#!/bin/bash\necho "{tool} $*" >> "${{FAKE_ST}}/calls"\nexec {real} "$@"\n',
                encoding="utf-8",
            )

        for p in bin_dir.iterdir():
            p.chmod(p.stat().st_mode | stat.S_IXUSR)

        base_routes = {
            "items": [
                {
                    "metadata": {"name": "portal", "namespace": "platform-system"},
                    "spec": {"http": [{"name": "p1", "upstreams": [{"name": "portal"}]}]},
                    "status": {"conditions": [{"type": "ResourcesAvailable", "reason": "ResourcesSynced"}]},
                },
                {
                    "metadata": {"name": "keycloak", "namespace": "platform-system"},
                    "spec": {"http": [{"name": "k1", "upstreams": [{"name": "keycloak"}]}]},
                    "status": {"conditions": [{"type": "ResourcesAvailable", "reason": "ResourcesSynced"}]},
                },
            ]
        }
        stream_routes_crs = {
            "items": base_routes["items"] + [
                {
                    "metadata": {"name": "tcp-echo", "namespace": "platform-system"},
                    "spec": {"stream": [{"name": "echo", "backend": {"serviceName": "echo-svc", "servicePort": 9000}}]},
                    "status": {"conditions": [{"type": "ResourcesAvailable", "reason": "ResourcesSynced"}]},
                }
            ]
        }
        cr_tls = {"items": [{"metadata": {"name": "narwhal-wildcard", "namespace": "platform-system"}}]}
        cr_upstreams = {"items": [{"metadata": {"name": "portal"}}, {"metadata": {"name": "keycloak"}}]}
        (st_dir / "cr_tls.json").write_text(json.dumps(cr_tls), encoding="utf-8")
        (st_dir / "cr_upstreams.json").write_text(json.dumps(cr_upstreams), encoding="utf-8")

        route_portal = {
            "id": _gen_id("platform-system_portal_p1"),
            "name": "platform-system_portal_p1",
            "desc": "Created by apisix-ingress-controller, DO NOT modify it manually",
            "labels": {"managed-by": "apisix-ingress-controller"},
        }
        route_keycloak = {
            "id": _gen_id("platform-system_keycloak_k1"),
            "name": "platform-system_keycloak_k1",
            "desc": "Created by apisix-ingress-controller, DO NOT modify it manually",
            "labels": {"managed-by": "apisix-ingress-controller"},
        }
        route_seed = {
            "id": "bootstrap-seed",
            "name": "bootstrap-seed",
            "uri": "/__bootstrap-seed-delete-me__",
        }
        stream_echo = {
            "id": _gen_id("platform-system_tcp-echo_echo_tcp"),
            "desc": "Created by apisix-ingress-controller, DO NOT modify it manually",
            "labels": {"managed-by": "apisix-ingress-controller"},
        }
        ups_portal = {"id": _gen_id("platform-system_portal"), "name": "platform-system_portal"}
        ups_keycloak = {"id": _gen_id("platform-system_keycloak"), "name": "platform-system_keycloak"}
        ups_echo = {"id": _gen_id("platform-system_echo-svc_9000"), "name": "platform-system_echo-svc_9000"}
        ssl_wildcard = {
            "id": _gen_id("platform-system_narwhal-wildcard"),
            "snis": ["*.local.narwhal.internal"],
            "labels": {"managed-by": "apisix-ingress-controller"},
        }

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

        def write_resp(target_dir: Path, ep: str, code: int, items: list[dict] | None = None) -> None:
            if code == 404:
                text = '{"message":"Key not found"}\n404'
            else:
                text = f"{_apisix_v3_payload(ep, items or [])}\n{code}"
            (target_dir / f"{ep}.resp").write_text(text, encoding="utf-8")

        def setup_case(
            *,
            routes_cr: dict,
            initial: dict[str, tuple[int, list[dict]]],
            synced: dict[str, tuple[int, list[dict]]] | None = None,
            converge: bool = False,
            auth_fail: bool = False,
        ) -> None:
            (st_dir / "calls").write_text("", encoding="utf-8")
            (st_dir / "annotated").unlink(missing_ok=True)
            (st_dir / "converge_on_annotate").unlink(missing_ok=True)
            (st_dir / "auth_fail").unlink(missing_ok=True)
            (st_dir / "cr_routes.json").write_text(json.dumps(routes_cr), encoding="utf-8")
            names = [f"apisixroute.apisix.apache.org/{item['metadata']['name']}" for item in routes_cr["items"]]
            (st_dir / "cr_routes_names.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
            for ep, (code, items) in initial.items():
                write_resp(st_dir, ep, code, items)
            if synced is not None:
                for ep, (code, items) in synced.items():
                    write_resp(synced_dir, ep, code, items)
            if converge:
                (st_dir / "converge_on_annotate").write_text("1", encoding="utf-8")
            if auth_fail:
                (st_dir / "auth_fail").write_text("1", encoding="utf-8")

        def assert_no_secret_leak(res: subprocess.CompletedProcess) -> None:
            combined = res.stdout + res.stderr + (st_dir / "calls").read_text(encoding="utf-8")
            assert secret_val not in combined, "admin key leaked to stdout/stderr or argv"
            b64_val = base64.b64encode(secret_val.encode()).decode()
            assert b64_val not in res.stdout + res.stderr, "base64 admin key leaked to output"

        healthy_initial = {
            "routes": (200, [route_portal, route_keycloak]),
            "stream_routes": (404, []),
            "upstreams": (200, [ups_portal, ups_keycloak]),
            "ssls": (200, [ssl_wildcard]),
        }

        # 1. Default aborted mode (no aborted CRs) stays backward-compatible without port-forward.
        setup_case(routes_cr=base_routes, initial=healthy_initial)
        res = subprocess.run(["bash", str(script), "platform-system"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert "port-forward" not in (st_dir / "calls").read_text(encoding="utf-8")

        # 2. Healthy cluster (stream_routes empty/404 when no stream CR rules): in sync, no annotate calls.
        setup_case(routes_cr=base_routes, initial=healthy_initial)
        res = subprocess.run(["bash", str(script), "--divergence", "platform-system"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert not (st_dir / "annotated").exists(), "healthy check must not annotate CRs"
        assert "WARN:" not in res.stderr, res.stderr
        assert_no_secret_leak(res)

        # 3. Partial divergence (even when CRs say ResourcesSynced) triggers resync of all 3 kinds and converges.
        setup_case(
            routes_cr=base_routes,
            initial={
                "routes": (200, [route_portal]),
                "stream_routes": (404, []),
                "upstreams": (200, []),
                "ssls": (404, []),
            },
            synced=healthy_initial,
            converge=True,
        )
        res = subprocess.run(["bash", str(script), "platform-system", "--divergence"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        annotated = (st_dir / "annotated").read_text(encoding="utf-8").splitlines()
        assert annotated == [
            "apisixupstreams.apisix.apache.org",
            "apisixtlses.apisix.apache.org",
            "apisixroutes.apisix.apache.org",
        ], annotated
        assert_no_secret_leak(res)

        # 4. Extra seed object + missing route (total count 2 == 2): divergence detected, resync triggered, WARN emitted.
        setup_case(
            routes_cr=base_routes,
            initial={
                "routes": (200, [route_portal, route_seed]),
                "stream_routes": (404, []),
                "upstreams": (200, [ups_portal, ups_keycloak]),
                "ssls": (200, [ssl_wildcard]),
            },
            synced={
                "routes": (200, [route_portal, route_keycloak, route_seed]),
                "stream_routes": (404, []),
                "upstreams": (200, [ups_portal, ups_keycloak]),
                "ssls": (200, [ssl_wildcard]),
            },
            converge=True,
        )
        res = subprocess.run(["bash", str(script), "--divergence", "platform-system"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert (st_dir / "annotated").exists(), "extra seed must not hide missing route"
        assert "WARN:" in res.stderr and "extra object(s)" in res.stderr, res.stderr
        assert_no_secret_leak(res)

        # 4b. Extra seed object when all CRs ARE present: in sync with WARN, no resync loop.
        setup_case(
            routes_cr=base_routes,
            initial={
                "routes": (200, [route_portal, route_keycloak, route_seed]),
                "stream_routes": (404, []),
                "upstreams": (200, [ups_portal, ups_keycloak]),
                "ssls": (200, [ssl_wildcard]),
            },
        )
        res = subprocess.run(["bash", str(script), "--divergence", "platform-system"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert not (st_dir / "annotated").exists(), "extra non-CR object alone must not trigger resync loop"
        assert "WARN:" in res.stderr and "extra object(s)" in res.stderr, res.stderr
        assert_no_secret_leak(res)

        # 5. Stream rule present and synced -> in sync with no resync (/stream_routes queried separately).
        stream_synced = {
            "routes": (200, [route_portal, route_keycloak]),
            "stream_routes": (200, [stream_echo]),
            "upstreams": (200, [ups_portal, ups_keycloak, ups_echo]),
            "ssls": (200, [ssl_wildcard]),
        }
        setup_case(routes_cr=stream_routes_crs, initial=stream_synced)
        res = subprocess.run(["bash", str(script), "--divergence", "platform-system"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert not (st_dir / "annotated").exists(), "synced stream rule must not trigger resync"
        assert "/apisix/admin/stream_routes" in (st_dir / "calls").read_text(encoding="utf-8")
        assert "WARN:" not in res.stderr, res.stderr
        assert_no_secret_leak(res)

        # 5b. Stream rule missing while HTTP routes match -> divergence detected and resync converges.
        setup_case(
            routes_cr=stream_routes_crs,
            initial={
                "routes": (200, [route_portal, route_keycloak]),
                "stream_routes": (404, []),
                "upstreams": (200, [ups_portal, ups_keycloak]),
                "ssls": (200, [ssl_wildcard]),
            },
            synced=stream_synced,
            converge=True,
        )
        res = subprocess.run(["bash", str(script), "--divergence", "platform-system"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert (st_dir / "annotated").exists(), "missing stream route must trigger resync"
        assert_no_secret_leak(res)

        # 6. Empty etcd HTTP 404 triggers resync and converges.
        empty_404 = {ep: (404, []) for ep in ("routes", "stream_routes", "upstreams", "ssls")}
        setup_case(routes_cr=base_routes, initial=empty_404, synced=healthy_initial, converge=True)
        res = subprocess.run(["bash", str(script), "--check-divergence"], env=env, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        assert (st_dir / "annotated").exists()
        assert_no_secret_leak(res)

        # 7. Non-convergent empty etcd exits non-zero with 404 recovery hint.
        setup_case(routes_cr=base_routes, initial=empty_404, converge=False)
        res = subprocess.run(["bash", str(script), "--divergence"], env=env, capture_output=True, text=True)
        assert res.returncode != 0, "non-convergent divergence must exit non-zero"
        assert "apisix-etcd-recovery.md" in res.stderr
        assert_no_secret_leak(res)

        # 8. Admin API 401 fails closed without leaking the key.
        setup_case(routes_cr=base_routes, initial=healthy_initial, auth_fail=True)
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
        (NET_REL, "shortens the controller's periodic resync", "bounds recovery latency"),
        (RECOVER_REL, '      --divergence|--check-divergence)\n', '      --disabled)\n'),
        (RECOVER_REL, '  elif [ "${code}" = "404" ]; then', '  elif [ "${code}" = "418" ]; then'),
        (RECOVER_REL, 'narwhal.io/resync=${ts}', 'narwhal.io/other=${ts}'),
        (RECOVER_REL, "  for ep in routes stream_routes upstreams ssls; do", "  for ep in routes upstreams ssls; do"),
        (RECOVER_REL, "missing = exp_set - act_set", "missing = set()"),
        (RECOVER_REL, 'echo "WARN: APISIX contains', 'echo "INFO: APISIX contains'),
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
