#!/usr/bin/env bash
set -euo pipefail

anonymous_probe_ok() { [[ "$1" == 401 ]]; }
admission_has_node_restriction() {
  grep -Eq -- '--enable-admission-plugins(=|[[:space:]])[^[:space:]]*NodeRestriction' <<<"$1"
}

if [[ "${1:-}" != "--self-test" && "${EUID}" -ne 0 ]]; then
  echo "Run on a control-plane node with sudo: sudo $0" >&2
  exit 2
fi

if [[ "${1:-}" == "--self-test" ]]; then
  anonymous_probe_ok 401
  if anonymous_probe_ok 403; then
    echo "FAIL 403 anonymous probe accepted" >&2
    exit 1
  fi
  admission_has_node_restriction '--enable-admission-plugins=NodeRestriction'
  if admission_has_node_restriction '--authorization-mode=Node,RBAC'; then
    echo "FAIL missing NodeRestriction accepted" >&2
    exit 1
  fi
  configz_fixture='{"kubeletconfig":{"authentication":{"anonymous":{"enabled":false}},"authorization":{"mode":"Webhook"}}}'
  [[ "$(jq -r '(.kubeletconfig.readOnlyPort // 0) == 0' <<<"${configz_fixture}")" == true ]]
  configz_mutated='{"kubeletconfig":{"readOnlyPort":10255}}'
  [[ "$(jq -r '(.kubeletconfig.readOnlyPort // 0) == 0' <<<"${configz_mutated}")" == false ]]
  python3 - <<'PY'
def matches(rule, resource):
    if resource.startswith("nodes/") and not any(g in ("", "*") for g in rule.get("apiGroups", [])):
        return False
    return any(r == "*" or r == resource or
               ("/" in resource and r == resource.split("/", 1)[0] + "/*")
               for r in rule.get("resources", []))


def proxy_check(inventory, extras=""):
    resources = ("nodes/proxy", "nodes/log", "nodes/stats", "nodes/metrics",
                 "nodes/exec", "nodes/attach", "nodes/portforward", "nodes/configz",
                 "nodes/spec", "nodes/healthz", "pods/exec", "pods/attach", "pods/portforward")
    allowlist = {
        "group:system:masters", "group:system:nodes", "group:kubeadm:cluster-admins",
        "user:kube-apiserver-kubelet-client", "user:system:kube-controller-manager",
        "serviceaccount:kube-system:generic-garbage-collector",
        "serviceaccount:kube-system:namespace-controller",
        "serviceaccount:kube-system:resourcequota-controller",
    }
    allowlist.update(x.strip() for x in extras.split(",") if x.strip())
    roles = {item["metadata"]["name"]: item for item in inventory["items"] if item["kind"] == "ClusterRole"}
    subjects_with_proxy = set()
    for binding in inventory["items"]:
        if binding["kind"] != "ClusterRoleBinding":
            continue
        role = roles.get(binding.get("roleRef", {}).get("name"))
        if not role:
            continue
        if not any(matches(rule, "nodes/proxy") for rule in role.get("rules", [])):
            continue
        for subject in binding.get("subjects", []):
            kind = subject["kind"]
            key = (f"group:{subject['name']}" if kind == "Group" else
                   f"user:{subject['name']}" if kind == "User" else
                   f"serviceaccount:{subject.get('namespace', 'default')}:{subject['name']}")
            subjects_with_proxy.add(key)
    unexpected = subjects_with_proxy - allowlist
    return not unexpected, sorted(unexpected)


fixture = {"items": [
    {"kind": "ClusterRole", "metadata": {"name": "proxy"}, "rules": [{"apiGroups": [""], "resources": ["nodes/proxy"], "verbs": ["get"]}]},
    {"kind": "ClusterRole", "metadata": {"name": "cluster-admin"}, "rules": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]},
    {"kind": "ClusterRoleBinding", "roleRef": {"name": "proxy"}, "subjects": [
        {"kind": "ServiceAccount", "namespace": "default", "name": "probe"}]},
    {"kind": "ClusterRoleBinding", "roleRef": {"name": "proxy"}, "subjects": [
        {"kind": "Group", "name": "kubeadm:cluster-admins"},
        {"kind": "ServiceAccount", "namespace": "kube-system", "name": "generic-garbage-collector"},
        {"kind": "ServiceAccount", "namespace": "kube-system", "name": "namespace-controller"},
        {"kind": "ServiceAccount", "namespace": "kube-system", "name": "resourcequota-controller"},
        {"kind": "User", "name": "kube-apiserver-kubelet-client"},
        {"kind": "User", "name": "system:kube-controller-manager"}]},
    {"kind": "ClusterRoleBinding", "roleRef": {"name": "cluster-admin"}, "subjects": [
        {"kind": "ServiceAccount", "namespace": "storage", "name": "velero-server"}]}
]}
assert proxy_check(fixture)[0] is False
assert proxy_check({"items": fixture["items"][0:1] + fixture["items"][2:3]}, "serviceaccount:default:probe")[0] is True
assert proxy_check({"items": fixture["items"][0:1] + fixture["items"][3:4]})[0] is True
assert proxy_check({"items": fixture["items"][1:2] + fixture["items"][4:5]})[0] is False
assert matches({"apiGroups": ["apps"], "resources": ["nodes/proxy"]}, "nodes/proxy") is False
print("PASS kubelet-authz self-test")
PY
  exit
fi

PASS=0
FAIL=0
check() {
  local label="$1" result="$2" detail="${3:-}"
  if [[ "${result}" == "true" ]]; then
    printf 'PASS %s%s\n' "${label}" "${detail:+: ${detail}}"
    PASS=$((PASS + 1))
  else
    printf 'FAIL %s%s\n' "${label}" "${detail:+: ${detail}}"
    FAIL=$((FAIL + 1))
  fi
}

nodes_json=$(kubectl get nodes -o json)
node_names=$(jq -r '.items[].metadata.name' <<<"${nodes_json}")
if [[ -z "${node_names}" ]]; then
  echo "FAIL no nodes returned by the apiserver"
  exit 1
fi

while IFS= read -r node; do
  [[ -n "${node}" ]] || continue
  if ! configz=$(kubectl get --raw "/api/v1/nodes/${node}/proxy/configz" 2>/dev/null); then
    check "${node} configz reachable" false
    continue
  fi
  check "${node} anonymous authentication disabled" \
    "$(jq -r '.kubeletconfig.authentication.anonymous.enabled == false' <<<"${configz}")"
  check "${node} webhook authentication enabled" \
    "$(jq -r '.kubeletconfig.authentication.webhook.enabled == true' <<<"${configz}")"
  check "${node} webhook authorization enabled" \
    "$(jq -r '.kubeletconfig.authorization.mode == "Webhook"' <<<"${configz}")"
  check "${node} readOnlyPort is zero" \
    "$(jq -r '(.kubeletconfig.readOnlyPort // 0) == 0' <<<"${configz}")"

  node_ip=$(jq -r --arg node "${node}" \
    '.items[] | select(.metadata.name == $node) | [.status.addresses[] | select(.type == "InternalIP")][0].address // empty' \
    <<<"${nodes_json}")
  if [[ -z "${node_ip}" ]]; then
    check "${node} anonymous HTTPS /pods denied" false "no InternalIP"
    check "${node} TCP 10255 closed" false "no InternalIP"
    continue
  fi
  if timeout 3 bash -c ':</dev/tcp/"$1"/10255' _ "${node_ip}" 2>/dev/null; then
    check "${node} TCP 10255 closed" false "connection accepted"
  else
    check "${node} TCP 10255 closed" true
  fi
  status=$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' "https://${node_ip}:10250/pods" || true)
  detail="HTTP ${status}"
  [[ "${status}" == 403 ]] && detail="anonymous authentication appears enabled (403 from authz)"
  check "${node} anonymous HTTPS /pods denied" \
    "$(anonymous_probe_ok "${status}" && echo true || echo false)" "${detail}"
done <<<"${node_names}"

apiserver_pods=$(kubectl get pods -n kube-system -l component=kube-apiserver -o json)
apiserver_names=$(jq -r '.items[].metadata.name' <<<"${apiserver_pods}")
if [[ -z "${apiserver_names}" ]]; then
  check "apiserver process flags available" false "no kube-apiserver pods returned"
else
  while IFS= read -r pod; do
    [[ -n "${pod}" ]] || continue
    process_flags=$(jq -r --arg pod "${pod}" \
      '.items[] | select(.metadata.name == $pod) | [.spec.containers[] | (.command[]?, .args[]?)] | join(" ")' \
      <<<"${apiserver_pods}")
    check "${pod} process authorization includes Node,RBAC" \
      "$(grep -Eq -- '--authorization-mode(=|[[:space:]])([^[:space:]]*Node[^[:space:]]*,[^[:space:]]*RBAC|[^[:space:]]*RBAC[^[:space:]]*,[^[:space:]]*Node)' <<<"${process_flags}" && echo true || echo false)" \
      "${process_flags:-flags missing}"
    check "${pod} process admission includes NodeRestriction" \
      "$(admission_has_node_restriction "${process_flags}" && echo true || echo false)" \
      "${process_flags:-flags missing}"
  done <<<"${apiserver_names}"
fi

rbac_file=$(mktemp)
trap 'rm -f "${rbac_file}"' EXIT
kubectl get clusterroles,clusterrolebindings -o json >"${rbac_file}"
if python3 - "${rbac_file}" "${EXTRA_PROXY_ALLOWLIST:-}" <<'PY'
import json
import sys


def matches(rule, resource):
    if resource.startswith("nodes/") and not any(g in ("", "*") for g in rule.get("apiGroups", [])):
        return False
    return any(r == "*" or r == resource or
               ("/" in resource and r == resource.split("/", 1)[0] + "/*")
               for r in rule.get("resources", []))


inventory = json.load(open(sys.argv[1], encoding="utf-8"))
resources = ("nodes/proxy", "nodes/log", "nodes/stats", "nodes/metrics",
             "nodes/exec", "nodes/attach", "nodes/portforward", "nodes/configz",
             "nodes/spec", "nodes/healthz", "pods/exec", "pods/attach", "pods/portforward")
allowlist = {
    "group:system:masters", "group:system:nodes", "group:kubeadm:cluster-admins",
    "user:kube-apiserver-kubelet-client", "user:system:kube-controller-manager",
    "serviceaccount:kube-system:generic-garbage-collector",
    "serviceaccount:kube-system:namespace-controller",
    "serviceaccount:kube-system:resourcequota-controller",
}
allowlist.update(x.strip() for x in sys.argv[2].split(",") if x.strip())
roles = {item["metadata"]["name"]: item for item in inventory["items"] if item["kind"] == "ClusterRole"}
bindings = [item for item in inventory["items"] if item["kind"] == "ClusterRoleBinding"]
proxy_subjects = set()
# Only nodes/proxy decides the exit code; the other resources are inventory only.
for binding in bindings:
    role = roles.get(binding.get("roleRef", {}).get("name"))
    if not role:
        continue
    grants = [r for r in role.get("rules", []) if any(matches(r, x) for x in resources)]
    if not grants:
        continue
    subjects = binding.get("subjects", [])
    print(f"RBAC {role['metadata']['name']} -> " +
          (", ".join(f"{s.get('kind')}:{s.get('namespace', '') + ':' if s.get('namespace') else ''}{s['name']}" for s in subjects) or "<no subjects>"))
    proxy_rules = [r for r in role.get("rules", []) if matches(r, "nodes/proxy")]
    if proxy_rules:
        explicit_proxy = any("nodes/proxy" in rule.get("resources", []) for rule in proxy_rules)
        grant_type = "explicit nodes/proxy grant" if explicit_proxy else "reaches nodes/proxy only via a wildcard rule"
        print(f"  {grant_type}")
    for rule in grants:
        granted = [r for r in resources if matches(rule, r)]
        print(f"  verbs={','.join(rule.get('verbs', []))} resources={','.join(granted)}")
        if matches(rule, "nodes/proxy"):
            for subject in subjects:
                key = (f"group:{subject['name']}" if subject["kind"] == "Group" else
                       f"user:{subject['name']}" if subject["kind"] == "User" else
                       f"serviceaccount:{subject.get('namespace', 'default')}:{subject['name']}")
                proxy_subjects.add(key)

unexpected = sorted(proxy_subjects - allowlist)
print("nodes/proxy classified PRIVILEGED, NON-READ-ONLY")
print("nodes/proxy subjects: " + (", ".join(sorted(proxy_subjects)) or "<none>"))
print("nodes/proxy allowlist: " + ", ".join(sorted(allowlist)))
if unexpected:
    print("FAIL nodes/proxy has non-allowlisted subjects: " + ", ".join(unexpected))
    sys.exit(1)
print("PASS nodes/proxy grants are within the documented allowlist")
PY
then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
fi

printf 'Summary: %d PASS, %d FAIL\n' "${PASS}" "${FAIL}"
[[ "${FAIL}" -eq 0 ]]
