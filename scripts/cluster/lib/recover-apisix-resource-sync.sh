#!/bin/bash
set -euo pipefail
NAMESPACE="${1:-platform-system}"
MAX_ROUNDS="${APISIX_RECOVERY_MAX_ROUNDS:-3}"
RESOURCE_TYPES=(apisixupstreams.apisix.apache.org apisixroutes.apisix.apache.org)
aborted_resources() {
  local kind="$1"
  kubectl get "${kind}" -n "${NAMESPACE}" -o json | python3 -c '
import json, sys
for item in json.load(sys.stdin).get("items", []):
    conditions = item.get("status", {}).get("conditions", [])
    if any("ResourceSyncAborted" in (str(c.get("type", "")) + " " + str(c.get("reason", "")) + " " + str(c.get("message", ""))) for c in conditions):
        print(item["metadata"]["name"])
'
}
for ((round=1; round<=MAX_ROUNDS; round++)); do
  count=0
  for kind in "${RESOURCE_TYPES[@]}"; do
    names=$(aborted_resources "${kind}")
    while IFS= read -r name; do
      [ -n "${name}" ] || continue
      count=$((count + 1))
      echo "APISIX recovery round ${round}/${MAX_ROUNDS}: recreating ${kind}/${name}"
      manifest=$(kubectl get "${kind}" "${name}" -n "${NAMESPACE}" -o json | python3 -c '
import json, sys
obj=json.load(sys.stdin); obj.pop("status", None); meta=obj["metadata"]
for key in ("uid", "resourceVersion", "managedFields", "creationTimestamp", "generation"): meta.pop(key, None)
print(json.dumps(obj))
')
      kubectl delete "${kind}" "${name}" -n "${NAMESPACE}" --wait=true
      printf '%s\n' "${manifest}" | kubectl apply -f -
    done <<<"${names}"
  done
  [ "${count}" -gt 0 ] || break
  sleep 5
done
remaining=""
for kind in "${RESOURCE_TYPES[@]}"; do
  names=$(aborted_resources "${kind}")
  if [ -n "${names}" ]; then remaining="${remaining}${kind}: ${names//$'\n'/ }\n"; fi
done
if [ -n "${remaining}" ]; then
  printf 'ERROR: APISIX resources remain ResourceSyncAborted after %s rounds:\n%b' "${MAX_ROUNDS}" "${remaining}" >&2
  exit 1
fi
echo "APISIX ResourceSyncAborted recovery complete."
