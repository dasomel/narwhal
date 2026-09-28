#!/usr/bin/env bash
set -euo pipefail

result() { printf '%s\n' "$*"; }

if ! kubectl get ciliumclusterwidenetworkpolicies.cilium.io allow-ambient-kubelet-probes -o json \
  | jq -e '
      .spec.endpointSelector == {}
      and .spec.enableDefaultDeny.ingress == false
      and .spec.enableDefaultDeny.egress == false
      and ([.spec.ingress[]?.fromCIDR[]?] | index("169.254.7.127/32") != null)
    ' >/dev/null; then
  result "FAIL probe-ccnp: allow-ambient-kubelet-probes missing or incorrect"
  exit 1
fi
result "PASS probe-ccnp: 169.254.7.127/32 allowed without default deny"

failed=0
while IFS= read -r namespace; do
  policies="$(kubectl get networkpolicies.networking.k8s.io -n "$namespace" -o json)"
  count="$(jq '[.items[] | select(((.spec.policyTypes // []) | index("Ingress") != null) or (.spec | has("ingress")))] | length' <<<"$policies")"
  [ "$count" -gt 0 ] || continue
  missing="$(jq -r '
      .items[]
      | select((((.spec.policyTypes // []) | index("Ingress") != null) or (.spec | has("ingress"))))
      | select(any(.spec.ingress[]?; has("ports") and (any(.ports[]?; .port == 15008 and (.protocol // "TCP") == "TCP") | not)))
      | select((.spec.ingress // []) | length > 0)
      | .metadata.name
    ' <<<"$policies")"
  if [ -z "$missing" ]; then
    result "PASS $namespace: each port-limited ingress rule permits TCP 15008 (or ingress is empty/all-ports)"
  else
    result "FAIL $namespace: ingress policy does not permit TCP 15008 or all ports: $(tr '\n' ' ' <<<"$missing")"
    failed=$((failed + 1))
  fi

  peerless="$(jq -r '
      .items[]
      | select((((.spec.policyTypes // []) | index("Ingress") != null) or (.spec | has("ingress"))))
      | select(any(.spec.ingress[]?; (has("from") | not) and (has("ports")) and
          ([.ports[]? | select(.port == 15008 and (.protocol // "TCP") == "TCP")] | length > 0) and
          ([.ports[]?.port | tostring] | unique == ["15008"])))
      | select(any(.spec.ingress[]?; has("from") and (.from | length > 0)))
      | .metadata.name
    ' <<<"$policies")"
  if [ -n "$peerless" ]; then
    result "FAIL $namespace: peer-less HBONE rule beside peer-restricted rules: $(tr '\n' ' ' <<<"$peerless")"
    failed=$((failed + 1))
  fi

  while IFS= read -r broad_policy; do
    [ -n "$broad_policy" ] || continue
    if ! jq -e --arg name "$broad_policy" '
        any(.items[];
          .metadata.name != $name
          and .metadata.labels["app.kubernetes.io/managed-by"] == "narwhal-gitops"
          and .spec.podSelector == {}
          and (((.spec.policyTypes // []) | index("Ingress") != null) or (.spec | has("ingress")))
          and (any(.spec.ingress[]?; (has("ports") | not) and (has("from") | not)) | not)
        )
      ' <<<"$policies" >/dev/null; then
      result "FAIL $namespace: repo-owned namespace-wide ingress policy $broad_policy has no other default-deny policy"
      failed=$((failed + 1))
    fi
  done < <(jq -r '
    .items[]
    | select(.metadata.labels["app.kubernetes.io/managed-by"] == "narwhal-gitops")
    | select(.metadata.name != "database-default-deny-ingress")
    | select(.spec.podSelector == {})
    | select(((.spec.policyTypes // []) | index("Ingress") != null) or (.spec | has("ingress")))
    | .metadata.name
  ' <<<"$policies")
done < <(kubectl get namespaces -o json | jq -r '.items[] | select(.metadata.labels["istio.io/dataplane-mode"] == "ambient") | .metadata.name' | sort)

exit "$failed"
