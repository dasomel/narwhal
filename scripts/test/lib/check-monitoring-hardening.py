#!/usr/bin/env python3
"""R360: Monitoring stack compliance hardening (D-MON-HARDENING, Lane 6).

Verifies resource requests/limits, readOnlyRootFilesystem, emptyDir mounts,
dropped capabilities (NET_RAW), and bootstrap parity across:
  - gitops/charts/narwhal-apps/templates/prometheus-stack.yaml
  - gitops/charts/narwhal-apps/templates/k8s-monitoring.yaml
  - gitops/charts/narwhal-apps/templates/loki.yaml
  - scripts/cluster/08-2-monitoring.sh
"""
import copy
import sys
import yaml

T = "gitops/charts/narwhal-apps/templates/"
S = "scripts/cluster/08-2-monitoring.sh"


def check_resources(res, label, errors):
    res = res or {}
    req = res.get("requests") or {}
    lim = res.get("limits") or {}
    if not req.get("cpu"):
        errors.append(f"{label}: missing cpu request")
    if not req.get("memory"):
        errors.append(f"{label}: missing memory request")
    if not lim.get("cpu") and "prometheusSpec" not in label and "loki singleBinary" not in label:
        errors.append(f"{label}: missing cpu limit")
    if not lim.get("memory"):
        errors.append(f"{label}: missing memory limit")


def check(apps, script_text):
    errors = []
    prom = apps["prometheus-stack"]["spec"]["source"]["helm"]["valuesObject"]
    k8smon = apps["k8s-monitoring"]["spec"]["source"]["helm"]["valuesObject"]
    loki = apps["loki"]["spec"]["source"]["helm"]["valuesObject"]

    # 1. prometheus-stack
    # node-exporter
    pne = prom.get("prometheus-node-exporter") or {}
    check_resources(pne.get("resources"), "prometheus-node-exporter", errors)
    pne_sec = pne.get("containerSecurityContext") or {}
    if not pne_sec.get("readOnlyRootFilesystem"):
        errors.append("prometheus-node-exporter: containerSecurityContext.readOnlyRootFilesystem must be true")
    if pne_sec.get("capabilities", {}).get("drop") != ["ALL"]:
        errors.append("prometheus-node-exporter: capabilities.drop must be ['ALL']")

    # kube-state-metrics
    ksm = prom.get("kube-state-metrics") or {}
    check_resources(ksm.get("resources"), "kube-state-metrics", errors)

    # prometheusOperator
    p_op = prom.get("prometheusOperator") or {}
    check_resources(p_op.get("resources"), "prometheusOperator", errors)
    reloader = p_op.get("prometheusConfigReloader") or {}
    check_resources(reloader.get("resources"), "prometheusOperator.prometheusConfigReloader", errors)

    # alertmanager
    am = prom.get("alertmanager", {}).get("alertmanagerSpec") or {}
    check_resources(am.get("resources"), "alertmanager.alertmanagerSpec", errors)

    # grafana
    graf = prom.get("grafana") or {}
    check_resources(graf.get("resources"), "grafana", errors)
    graf_sec = graf.get("containerSecurityContext") or {}
    if not graf_sec.get("readOnlyRootFilesystem"):
        errors.append("grafana: containerSecurityContext.readOnlyRootFilesystem must be true")
    extra_dirs = [m.get("mountPath") for m in graf.get("extraEmptyDirMounts", [])]
    if "/tmp" not in extra_dirs:
        errors.append("grafana: extraEmptyDirMounts must mount /tmp")

    sidecar = graf.get("sidecar") or {}
    check_resources(sidecar.get("resources"), "grafana.sidecar", errors)
    sc_sec = sidecar.get("securityContext") or {}
    if not sc_sec.get("readOnlyRootFilesystem"):
        errors.append("grafana.sidecar: securityContext.readOnlyRootFilesystem must be true")

    # 2. k8s-monitoring
    alloy_logs = k8smon.get("collectors", {}).get("alloy-logs") or {}
    alloy = alloy_logs.get("alloy") or {}

    # alloy container mounts /tmp
    alloy_mounts = [m.get("mountPath") for m in alloy.get("mounts", {}).get("extra", [])]
    if "/tmp" not in alloy_mounts:
        errors.append("alloy-logs: alloy.mounts.extra must mount /tmp")
    ctrl_vols = [v.get("name") for v in alloy_logs.get("controller", {}).get("volumes", {}).get("extra", []) if "emptyDir" in v]
    if "tmp" not in ctrl_vols:
        errors.append("alloy-logs: controller.volumes.extra must provide tmp emptyDir")

    # alloy securityContext: readOnlyRootFilesystem and no NET_RAW
    alloy_sec = alloy.get("securityContext") or {}
    if not alloy_sec.get("readOnlyRootFilesystem"):
        errors.append("alloy-logs: alloy.securityContext.readOnlyRootFilesystem must be true")
    add_caps = alloy_sec.get("capabilities", {}).get("add", [])
    if "NET_RAW" in add_caps:
        errors.append("alloy-logs: alloy capabilities.add must not contain NET_RAW")
    # D-alloy-caps (KSV022): the key must be present AND empty; omitting it lets the chart
    # merge its 14 default capabilities back in.
    if "add" not in alloy_sec.get("capabilities", {}) or add_caps:
        errors.append("alloy-logs: alloy capabilities.add must be present and empty (D-alloy-caps)")

    # configReloader
    cr = alloy_logs.get("configReloader") or {}
    check_resources(cr.get("resources"), "alloy-logs.configReloader", errors)
    cr_sec = cr.get("securityContext") or {}
    if not cr_sec.get("readOnlyRootFilesystem"):
        errors.append("alloy-logs: configReloader.securityContext.readOnlyRootFilesystem must be true")
    if not cr_sec.get("runAsNonRoot") or cr_sec.get("runAsUser") != 65534:
        errors.append("alloy-logs: configReloader must run as non-root user 65534")

    # alloy-operator
    ao = k8smon.get("alloy-operator") or {}
    check_resources(ao.get("resources"), "alloy-operator", errors)
    ao_psc = ao.get("podSecurityContext") or {}
    if not ao_psc.get("runAsNonRoot") or ao_psc.get("runAsUser") != 4242:
        errors.append("alloy-operator: podSecurityContext must run as non-root user 4242")
    ao_sec = ao.get("securityContext") or {}
    if not ao_sec.get("readOnlyRootFilesystem"):
        errors.append("alloy-operator: securityContext.readOnlyRootFilesystem must be true")

    # 3. loki
    loki_sc = loki.get("sidecar") or {}
    loki_sc_res = loki_sc.get("resources") or {}
    if not loki_sc_res.get("limits", {}).get("cpu"):
        errors.append("loki: sidecar.resources.limits.cpu must be specified")

    # 4. bootstrap parity (08-2-monitoring.sh)
    required_script_patterns = [
        "prometheus-node-exporter.resources=",
        "kube-state-metrics.resources=",
        "prometheusOperator.resources=",
        "prometheusOperator.prometheusConfigReloader.resources=",
        "alertmanager.alertmanagerSpec.resources=",
        "grafana.resources=",
        "grafana.containerSecurityContext=",
        "grafana.extraEmptyDirMounts=",
        "grafana.sidecar.resources=",
        "sidecar:\n  resources:\n    requests:\n      cpu: 10m",
        "alloy-operator:\n  podSecurityContext:",
        "runAsUser: 4242",
        "runAsUser: 65534",
    ]
    for pat in required_script_patterns:
        if pat not in script_text:
            errors.append(f"08-2-monitoring.sh: missing bootstrap parity pattern {pat!r}")
    # D-alloy-caps (KSV022) bootstrap parity: the alloy-logs values in the script must carry
    # an explicit empty capabilities.add and none of the chart's default capabilities.
    if "          add: []\n" not in script_text:
        errors.append("08-2-monitoring.sh: alloy capabilities.add must be an explicit empty list (D-alloy-caps)")
    for cap in ("DAC_OVERRIDE", "NET_BIND_SERVICE", "SETUID", "CHOWN", "SYS_CHROOT"):
        if f"- {cap}\n" in script_text:
            errors.append(f"08-2-monitoring.sh: alloy capability {cap} must not be added (D-alloy-caps)")

    return errors


def load():
    apps = {
        name: yaml.safe_load(open(T + name + ".yaml", encoding="utf-8"))
        for name in ("prometheus-stack", "k8s-monitoring", "loki")
    }
    script = open(S, encoding="utf-8").read()
    return apps, script


def mutate(name, apps_in, script_in):
    apps = copy.deepcopy(apps_in)
    script = script_in
    prom = apps["prometheus-stack"]["spec"]["source"]["helm"]["valuesObject"]
    k8smon = apps["k8s-monitoring"]["spec"]["source"]["helm"]["valuesObject"]
    loki = apps["loki"]["spec"]["source"]["helm"]["valuesObject"]

    if name == "prom node-exporter no resources":
        del prom["prometheus-node-exporter"]["resources"]
    elif name == "prom node-exporter no readOnlyRootFilesystem":
        prom["prometheus-node-exporter"]["containerSecurityContext"]["readOnlyRootFilesystem"] = False
    elif name == "prom ksm no resources":
        del prom["kube-state-metrics"]["resources"]
    elif name == "prom operator no resources":
        del prom["prometheusOperator"]["resources"]
    elif name == "prom operator configReloader no resources":
        del prom["prometheusOperator"]["prometheusConfigReloader"]["resources"]
    elif name == "prom alertmanager no resources":
        del prom["alertmanager"]["alertmanagerSpec"]["resources"]
    elif name == "prom grafana no resources":
        del prom["grafana"]["resources"]
    elif name == "prom grafana no readOnlyRootFilesystem":
        prom["grafana"]["containerSecurityContext"]["readOnlyRootFilesystem"] = False
    elif name == "prom grafana no tmp mount":
        prom["grafana"]["extraEmptyDirMounts"] = []
    elif name == "prom grafana sidecar no resources":
        del prom["grafana"]["sidecar"]["resources"]
    elif name == "prom grafana sidecar no readOnlyRootFilesystem":
        prom["grafana"]["sidecar"]["securityContext"]["readOnlyRootFilesystem"] = False
    elif name == "k8smon alloy-logs no tmp mount":
        prom["collectors"] = {}
        del k8smon["collectors"]["alloy-logs"]["alloy"]["mounts"]["extra"]
    elif name == "k8smon alloy-logs no readOnlyRootFilesystem":
        k8smon["collectors"]["alloy-logs"]["alloy"]["securityContext"]["readOnlyRootFilesystem"] = False
    elif name == "k8smon alloy-logs NET_RAW present":
        k8smon["collectors"]["alloy-logs"]["alloy"]["securityContext"]["capabilities"]["add"].append("NET_RAW")
    elif name == "k8smon configReloader no resources":
        del k8smon["collectors"]["alloy-logs"]["configReloader"]["resources"]
    elif name == "k8smon configReloader no readOnlyRootFilesystem":
        k8smon["collectors"]["alloy-logs"]["configReloader"]["securityContext"]["readOnlyRootFilesystem"] = False
    elif name == "k8smon alloy-operator no resources":
        del k8smon["alloy-operator"]["resources"]
    elif name == "k8smon alloy-operator no readOnlyRootFilesystem":
        k8smon["alloy-operator"]["securityContext"]["readOnlyRootFilesystem"] = False
    elif name == "loki sidecar no cpu limit":
        del loki["sidecar"]["resources"]["limits"]["cpu"]
    elif name == "08-2 script missing grafana hardening":
        script = script.replace("grafana.resources=", "grafana.disabled=")
    elif name == "08-2 script missing alloy hardening":
        script = script.replace("runAsUser: 4242", "runAsUser: 0")
    else:
        raise ValueError(f"Unknown mutation {name}")

    return apps, script


MUTATIONS = [
    "prom node-exporter no resources",
    "prom node-exporter no readOnlyRootFilesystem",
    "prom ksm no resources",
    "prom operator no resources",
    "prom operator configReloader no resources",
    "prom alertmanager no resources",
    "prom grafana no resources",
    "prom grafana no readOnlyRootFilesystem",
    "prom grafana no tmp mount",
    "prom grafana sidecar no resources",
    "prom grafana sidecar no readOnlyRootFilesystem",
    "k8smon alloy-logs no tmp mount",
    "k8smon alloy-logs no readOnlyRootFilesystem",
    "k8smon alloy-logs NET_RAW present",
    "k8smon configReloader no resources",
    "k8smon configReloader no readOnlyRootFilesystem",
    "k8smon alloy-operator no resources",
    "k8smon alloy-operator no readOnlyRootFilesystem",
    "loki sidecar no cpu limit",
    "08-2 script missing grafana hardening",
    "08-2 script missing alloy hardening",
]


def main():
    apps, script = load()
    errs = check(apps, script)
    if errs:
        print("FAIL: monitoring hardening errors found:")
        for e in errs:
            print(f"  - {e}")
        return 1

    if "--mutation-verify" in sys.argv:
        for m in MUTATIONS:
            m_apps, m_script = mutate(m, apps, script)
            m_errs = check(m_apps, m_script)
            if not m_errs:
                print(f"FAIL: mutation was not rejected: {m}")
                return 1
        print(f"PASS: {len(MUTATIONS)} mutations rejected")

    print("monitoring stack compliance hardening: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
