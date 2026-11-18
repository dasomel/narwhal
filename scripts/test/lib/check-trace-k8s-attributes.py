#!/usr/bin/env python3
"""R371: OTLP trace ingestion & k8s.namespace.name enrichment via Alloy (narwhal#691).

Verifies that:
  - gitops/charts/narwhal-apps/templates/k8s-monitoring.yaml enables
    applicationObservability on the existing alloy-logs collector with OTLP
    gRPC (4317) + HTTP (4318) receivers, k8sattributes metadata including
    k8s.namespace.name/k8s.pod.name/k8s.deployment.name (and omitting
    k8s.cronjob.name so no extra batch/jobs ClusterRole rule is needed),
    and an OTLP gRPC destination to tempo.monitoring.svc.cluster.local:4317.
  - scripts/cluster/08-2-monitoring.sh K8SMONVALUES matches GitOps values.
  - gitops/charts/narwhal-apps/templates/prometheus-stack.yaml routes Grafana
    tracing through k8s-monitoring-alloy-logs.monitoring.svc.cluster.local:4317.
  - Offline `helm template` of k8s-monitoring-4.2.0.tgz renders the expected
    Alloy ConfigMap pipeline and Alloy CR extraPorts without weakening
    securityContext.
"""
import argparse
import copy
import glob
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

K8SMON_YAML = "gitops/charts/narwhal-apps/templates/k8s-monitoring.yaml"
PROM_YAML = "gitops/charts/narwhal-apps/templates/prometheus-stack.yaml"
MON_SH = "scripts/cluster/08-2-monitoring.sh"


def _extract_k8smon_heredoc(script_text: str) -> dict:
    m = re.search(r"cat <<'K8SMONVALUES' > /tmp/k8s-monitoring-values\.yaml\n(.*?)\nK8SMONVALUES", script_text, re.S)
    assert m, "08-2-monitoring.sh: missing K8SMONVALUES heredoc"
    return yaml.safe_load(m.group(1))


def _check_values(vals: dict, label: str) -> None:
    dests = vals.get("destinations") or {}
    tempo = dests.get("tempo") or {}
    assert tempo.get("type") == "otlp", f"{label}: destinations.tempo.type must be otlp"
    assert tempo.get("url") == "tempo.monitoring.svc.cluster.local:4317", (
        f"{label}: destinations.tempo.url mismatch"
    )
    assert tempo.get("protocol") == "grpc", f"{label}: destinations.tempo.protocol must be grpc"
    assert (tempo.get("tls") or {}).get("insecure") is True, f"{label}: destinations.tempo.tls.insecure must be true"
    assert (tempo.get("metrics") or {}).get("enabled") is False, f"{label}: destinations.tempo.metrics.enabled must be false"
    assert (tempo.get("logs") or {}).get("enabled") is False, f"{label}: destinations.tempo.logs.enabled must be false"
    assert (tempo.get("traces") or {}).get("enabled") is True, f"{label}: destinations.tempo.traces.enabled must be true"

    ao = vals.get("applicationObservability") or {}
    assert ao.get("enabled") is True, f"{label}: applicationObservability.enabled must be true"
    assert ao.get("collector") == "alloy-logs", f"{label}: applicationObservability.collector must be alloy-logs"
    assert ao.get("destinations") == ["tempo"], f"{label}: applicationObservability.destinations must be ['tempo']"
    assert (ao.get("metrics") or {}).get("enabled") is False, f"{label}: applicationObservability.metrics.enabled must be false"
    assert (ao.get("logs") or {}).get("enabled") is False, f"{label}: applicationObservability.logs.enabled must be false"
    assert (ao.get("traces") or {}).get("enabled") is True, f"{label}: applicationObservability.traces.enabled must be true"

    otlp = (ao.get("receivers") or {}).get("otlp") or {}
    grpc = otlp.get("grpc") or {}
    http = otlp.get("http") or {}
    assert grpc.get("enabled") is True and grpc.get("port") == 4317, f"{label}: OTLP gRPC 4317 must be enabled"
    assert http.get("enabled") is True and http.get("port") == 4318, f"{label}: OTLP HTTP 4318 must be enabled"

    meta = ((ao.get("processors") or {}).get("k8sattributes") or {}).get("metadata") or []
    for required_attr in ("k8s.namespace.name", "k8s.pod.name", "k8s.deployment.name"):
        assert required_attr in meta, f"{label}: k8sattributes.metadata missing {required_attr}"
    assert "k8s.cronjob.name" not in meta, (
        f"{label}: k8sattributes.metadata must omit k8s.cronjob.name (no batch/jobs ClusterRole rule)"
    )


def verify(root: Path) -> None:
    k8smon_doc = yaml.safe_load((root / K8SMON_YAML).read_text(encoding="utf-8"))
    k8smon_vals = k8smon_doc["spec"]["source"]["helm"]["valuesObject"]
    _check_values(k8smon_vals, "k8s-monitoring.yaml")

    boot_vals = _extract_k8smon_heredoc((root / MON_SH).read_text(encoding="utf-8"))
    _check_values(boot_vals, "08-2-monitoring.sh")

    assert k8smon_vals["destinations"]["tempo"] == boot_vals["destinations"]["tempo"], (
        "destinations.tempo parity mismatch between k8s-monitoring.yaml and 08-2-monitoring.sh"
    )
    assert k8smon_vals["applicationObservability"] == boot_vals["applicationObservability"], (
        "applicationObservability parity mismatch between k8s-monitoring.yaml and 08-2-monitoring.sh"
    )

    prom_doc = yaml.safe_load((root / PROM_YAML).read_text(encoding="utf-8"))
    graf_ini = prom_doc["spec"]["source"]["helm"]["valuesObject"]["grafana"]["grafana.ini"]
    otlp_addr = (graf_ini.get("tracing.opentelemetry.otlp") or {}).get("address")
    assert otlp_addr == "k8s-monitoring-alloy-logs.monitoring.svc.cluster.local:4317", (
        f"prometheus-stack.yaml Grafana tracing address must route via Alloy, got {otlp_addr!r}"
    )


def _find_chart(root: Path) -> str | None:
    for base in (root, *root.parents[:3]):
        matches = sorted(glob.glob(str(base / "narwhal-airgap-bundle-*/charts/k8s-monitoring-4.2.0.tgz")))
        if matches:
            return matches[0]
    return None


def verify_helm_renders(root: Path) -> bool:
    chart_tgz = _find_chart(root)
    if not chart_tgz:
        return False

    k8smon_vals = yaml.safe_load((root / K8SMON_YAML).read_text(encoding="utf-8"))["spec"]["source"]["helm"]["valuesObject"]
    boot_vals = _extract_k8smon_heredoc((root / MON_SH).read_text(encoding="utf-8"))

    for label, vals in (("gitops", k8smon_vals), ("08-2", boot_vals)):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as tf:
            yaml.dump(vals, tf)
            tf.flush()
            out = subprocess.run(
                [
                    "helm", "template", "k8s-monitoring", chart_tgz,
                    "-n", "monitoring", "-f", tf.name,
                    "--show-only", "templates/alloy-config.yaml",
                    "--show-only", "templates/alloy.yaml",
                ],
                capture_output=True, text=True, check=True,
            ).stdout

        docs = [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]
        cm = next(d for d in docs if d["kind"] == "ConfigMap")
        alloy_cr = next(d for d in docs if d["kind"] == "Alloy")

        cfg = cm["data"]["config.alloy"]
        assert 'otelcol.receiver.otlp "receiver"' in cfg, f"{label}: missing otelcol.receiver.otlp"
        assert 'endpoint = "0.0.0.0:4317"' in cfg, f"{label}: missing 4317 grpc listener"
        assert 'endpoint = "0.0.0.0:4318"' in cfg, f"{label}: missing 4318 http listener"
        assert 'otelcol.processor.k8sattributes "default"' in cfg, f"{label}: missing k8sattributes processor"
        assert '"k8s.namespace.name"' in cfg, f"{label}: missing k8s.namespace.name in extract"
        assert '"k8s.pod.name"' in cfg, f"{label}: missing k8s.pod.name in extract"
        assert '"k8s.deployment.name"' in cfg, f"{label}: missing k8s.deployment.name in extract"
        assert 'from = "connection"' in cfg, f"{label}: missing connection pod_association"
        assert 'otelcol.exporter.otlp "tempo"' in cfg, f"{label}: missing tempo otlp exporter"
        assert 'endpoint = "tempo.monitoring.svc.cluster.local:4317"' in cfg, f"{label}: missing tempo endpoint"
        assert 'connector.host_info' not in cfg and 'spanmetrics' not in cfg and 'spanlogs' not in cfg, (
            f"{label}: unexpected extra connectors rendered"
        )

        spec = alloy_cr["spec"]
        ports = {(p["port"], p["protocol"]) for p in spec["alloy"]["extraPorts"]}
        assert (4317, "TCP") in ports and (4318, "TCP") in ports, f"{label}: missing OTLP extraPorts: {ports}"
        assert spec["alloy"]["securityContext"]["readOnlyRootFilesystem"] is True, f"{label}: ro-rootfs regressed"
        assert "NET_RAW" not in spec["alloy"]["securityContext"]["capabilities"]["add"], f"{label}: NET_RAW regressed"
    return True


def mutation_verify(root: Path) -> int:
    sources = {
        rel: (root / rel).read_text(encoding="utf-8")
        for rel in (K8SMON_YAML, PROM_YAML, MON_SH)
    }
    mutations = [
        (K8SMON_YAML, "applicationObservability:\n          enabled: true", "applicationObservability:\n          enabled: false"),
        (K8SMON_YAML, "- k8s.namespace.name", "- k8s.pod.uid"),
        (K8SMON_YAML, "- k8s.deployment.name", "- k8s.cronjob.name"),
        (K8SMON_YAML, "port: 4317", "port: 14317"),
        (K8SMON_YAML, "port: 4318", "port: 14318"),
        (K8SMON_YAML, "url: tempo.monitoring.svc.cluster.local:4317", "url: tempo.monitoring.svc.cluster.local:4318"),
        (K8SMON_YAML, "traces:\n              enabled: true", "traces:\n              enabled: false"),
        (MON_SH, "applicationObservability:\n  enabled: true", "applicationObservability:\n  enabled: false"),
        (MON_SH, "- k8s.namespace.name", "- k8s.pod.uid"),
        (MON_SH, "port: 4317", "port: 14317"),
        (PROM_YAML, "address: k8s-monitoring-alloy-logs.monitoring.svc.cluster.local:4317", "address: tempo.monitoring.svc.cluster.local:4317"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp)
        for rel in sources:
            (fixture / rel).parent.mkdir(parents=True, exist_ok=True)
        for rel, old, new in mutations:
            for k, v in sources.items():
                (fixture / k).write_text(v, encoding="utf-8")
            assert old in sources[rel], f"mutation anchor missing in {rel}: {old!r}"
            (fixture / rel).write_text(sources[rel].replace(old, new, 1), encoding="utf-8")
            try:
                verify(fixture)
            except AssertionError:
                continue
            raise AssertionError(f"mutation survived: {rel}: {old!r}")
    return len(mutations)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[3]
    verify(root)
    rendered = verify_helm_renders(root)
    render_msg = "helm render ok" if rendered else "helm render skipped (no bundle)"
    if args.mutation_verify:
        caught = mutation_verify(root)
        print(f"PASS: R371 OTLP k8s.namespace.name enrichment + {render_msg} ({caught} mutations rejected)")
    else:
        print(f"PASS: R371 OTLP k8s.namespace.name enrichment + {render_msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
