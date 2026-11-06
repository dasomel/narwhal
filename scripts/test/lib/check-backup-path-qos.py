#!/usr/bin/env python3
"""R310: the backup storage path must be Burstable and bounded (D-sw-qos, D-datapath-qos)."""
import json
import sys

import yaml

T = "gitops/charts/narwhal-apps/templates/"
errors = []

# Memory floors (MiB) from observed use on 2026-11-06: at rest filer ~94, s3 ~41, volume ~30,
# master ~26; filer and s3 buffer chunks during uploads. A limit under the floor would trade the
# node OOM kill for a container OOM kill, so this checks values, not only presence.
LIMIT_FLOOR = {"master": 256, "volume": 256, "filer": 512, "s3": 256}
REQUEST_FLOOR = {"master": 32, "volume": 32, "filer": 128, "s3": 64}
DATAPATH_LIMIT_FLOOR = 512


def mib(q):
    q = str(q)
    for suf, mult in (("Gi", 1024), ("Mi", 1), ("Ki", 1 / 1024)):
        if q.endswith(suf):
            return float(q[: -len(suf)]) * mult
    return float(q) / (1024 * 1024)

sw = yaml.safe_load(open(T + "seaweedfs.yaml"))
values = sw["spec"]["source"]["helm"]["valuesObject"]
for comp in ("master", "volume", "filer", "s3"):
    res = values.get(comp, {}).get("resources", {})
    if not res.get("requests", {}).get("memory"):
        errors.append(f"seaweedfs {comp}: no memory request (BestEffort)")
    if not res.get("requests", {}).get("cpu"):
        errors.append(f"seaweedfs {comp}: no cpu request")
    if not res.get("limits", {}).get("memory"):
        errors.append(f"seaweedfs {comp}: no memory limit")
        continue
    if mib(res["limits"]["memory"]) < LIMIT_FLOOR[comp]:
        errors.append(f"seaweedfs {comp}: memory limit below {LIMIT_FLOOR[comp]}Mi")
    if res.get("requests", {}).get("memory") and mib(res["requests"]["memory"]) < REQUEST_FLOOR[comp]:
        errors.append(f"seaweedfs {comp}: memory request below {REQUEST_FLOOR[comp]}Mi")

# velero.yaml has no Helm template directives; parse the ConfigMap JSON out of it.
vel = yaml.safe_load(open(T + "velero.yaml"))
objs = vel["spec"]["source"]["helm"]["valuesObject"].get("extraObjects", [])
cm = [o for o in objs if o.get("metadata", {}).get("name") == "velero-node-agent-config"]
if len(cm) != 1:
    errors.append("velero-node-agent-config ConfigMap not found exactly once")
else:
    cfg = json.loads(cm[0]["data"]["node-agent-config.json"])
    if cfg.get("loadConcurrency", {}).get("globalConfig") != 1:
        errors.append("node-agent config: loadConcurrency.globalConfig must be 1")
    pr = cfg.get("podResources", {})
    for k in ("cpuRequest", "memoryRequest", "memoryLimit"):
        if not pr.get(k):
            errors.append(f"node-agent config: podResources.{k} missing")
    if pr.get("memoryLimit") and mib(pr["memoryLimit"]) < DATAPATH_LIMIT_FLOOR:
        errors.append(f"node-agent config: podResources.memoryLimit below {DATAPATH_LIMIT_FLOOR}Mi")
    if "podLabels" not in cfg:
        errors.append("node-agent config: podLabels (D-datapath-labels) lost")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print("backup path QoS ok")
