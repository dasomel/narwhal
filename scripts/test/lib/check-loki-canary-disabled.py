#!/usr/bin/env python3
"""R315: loki-canary DaemonSet is disabled via top-level lokiCanary.enabled=false (D-LOKI-CANARY-DISABLE).

In chart 18.4.0 (grafana-community/loki), the canary DaemonSet is controlled by the top-level
lokiCanary.enabled key, NOT monitoring.lokiCanary.enabled.
"""
import re
import sys
import yaml

LOKI_YAML = "gitops/charts/narwhal-apps/templates/loki.yaml"
MONITORING_SH = "scripts/cluster/08-2-monitoring.sh"


def extract_sh_loki_values(sh_content):
    match = re.search(r"cat <<'LOKIVALUES' > /tmp/loki-values\.yaml\n(.*?)\nLOKIVALUES", sh_content, re.DOTALL)
    if not match:
        return None
    return yaml.safe_load(match.group(1))


def check_loki_values(values, source_name):
    errors = []
    if not isinstance(values, dict):
        return [f"{source_name}: values is not a dictionary"]

    # 1. Must not have lokiCanary nested under monitoring
    if "monitoring" in values and isinstance(values["monitoring"], dict):
        if "lokiCanary" in values["monitoring"]:
            errors.append(f"{source_name}: lokiCanary is erroneously nested under monitoring (does not control canary in chart 18.4.0)")

    # 2. Must have top-level lokiCanary
    if "lokiCanary" not in values:
        errors.append(f"{source_name}: missing top-level lokiCanary key (chart default enables DaemonSet)")
    else:
        lc = values["lokiCanary"]
        if not isinstance(lc, dict):
            errors.append(f"{source_name}: lokiCanary must be a dictionary")
        elif lc.get("enabled") is not False:
            errors.append(f"{source_name}: lokiCanary.enabled must be explicitly false (got {lc.get('enabled')})")

    return errors


def check(loki_yaml_content=None, monitoring_sh_content=None):
    errors = []

    # Check loki.yaml
    if loki_yaml_content is None:
        with open(LOKI_YAML, "r", encoding="utf-8") as f:
            loki_yaml_content = f.read()

    doc = yaml.safe_load(loki_yaml_content)
    try:
        values = doc["spec"]["source"]["helm"]["valuesObject"]
    except (KeyError, TypeError) as exc:
        errors.append(f"{LOKI_YAML}: failed to access spec.source.helm.valuesObject ({exc})")
        values = None

    if values is not None:
        errors.extend(check_loki_values(values, LOKI_YAML))

    # Check 08-2-monitoring.sh
    if monitoring_sh_content is None:
        with open(MONITORING_SH, "r", encoding="utf-8") as f:
            monitoring_sh_content = f.read()

    sh_values = extract_sh_loki_values(monitoring_sh_content)
    if sh_values is None:
        errors.append(f"{MONITORING_SH}: failed to extract /tmp/loki-values.yaml heredoc")
    else:
        errors.extend(check_loki_values(sh_values, MONITORING_SH))

    return errors


MUTATIONS = [
    ("loki.yaml: lokiCanary nested under monitoring",
     lambda y, s: (
         re.sub(r"\n\s*lokiCanary:\n\s*enabled:\s*false", "\n        monitoring:\n          lokiCanary:\n            enabled: false", y),
         s
     )),
    ("loki.yaml: lokiCanary.enabled = true",
     lambda y, s: (
         re.sub(r"lokiCanary:\n\s*enabled:\s*false", "lokiCanary:\n          enabled: true", y),
         s
     )),
    ("loki.yaml: lokiCanary missing",
     lambda y, s: (
         re.sub(r"\n\s*lokiCanary:\n\s*enabled:\s*false", "", y),
         s
     )),
    ("08-2-monitoring.sh: lokiCanary nested under monitoring",
     lambda y, s: (
         y,
         re.sub(r"\n\s*lokiCanary:\n\s*enabled:\s*false", "\nmonitoring:\n  lokiCanary:\n    enabled: false", s)
     )),
    ("08-2-monitoring.sh: lokiCanary.enabled = true",
     lambda y, s: (
         y,
         re.sub(r"lokiCanary:\n\s*enabled:\s*false", "lokiCanary:\n  enabled: true", s)
     )),
    ("08-2-monitoring.sh: lokiCanary missing",
     lambda y, s: (
         y,
         re.sub(r"\n\s*lokiCanary:\n\s*enabled:\s*false", "", s)
     )),
]


def main():
    errors = check()
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1

    if "--mutation-verify" in sys.argv:
        with open(LOKI_YAML, "r", encoding="utf-8") as f:
            base_y = f.read()
        with open(MONITORING_SH, "r", encoding="utf-8") as f:
            base_s = f.read()

        for name, mutator in MUTATIONS:
            mut_y, mut_s = mutator(base_y, base_s)
            mut_errors = check(mut_y, mut_s)
            if not mut_errors:
                print(f"FAIL: mutation not rejected: {name}")
                return 1
        print(f"PASS: {len(MUTATIONS)} mutations rejected")

    print("loki-canary disabled check ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
