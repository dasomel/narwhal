#!/usr/bin/env python3
"""R310: the backup storage path must be Burstable and bounded (D-sw-qos, D-datapath-qos)."""
import json
import sys

import yaml

T = "gitops/charts/narwhal-apps/templates/"
errors = []

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
    if "podLabels" not in cfg:
        errors.append("node-agent config: podLabels (D-datapath-labels) lost")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print("backup path QoS ok")
