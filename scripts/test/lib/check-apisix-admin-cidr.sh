#!/bin/bash
# R254: resolve_apisix_admin_cidr (scripts/common/lib.sh) against a fake kubectl.
# Run from the repo root. Scenario is chosen with FAKE_CILIUM / FAKE_KUBEADM.
set -euo pipefail

k=$(mktemp -d); trap 'rm -rf "$k"' EXIT
cat > "$k/kubectl" <<'FAKE'
#!/bin/bash
# args: -n kube-system get configmap <name> -o jsonpath=...
name="$5"
case "${name}" in
  cilium-config) v="${FAKE_CILIUM}" ;;
  kubeadm-config) v="${FAKE_KUBEADM}" ;;
esac
case "${v}" in
  notfound) echo "Error from server (NotFound): configmaps \"${name}\" not found" >&2; exit 1 ;;
  error) echo "Unable to connect to the server: net/http: TLS handshake timeout" >&2; exit 1 ;;
  *) printf '%b' "${v}" ;;
esac
FAKE
chmod +x "$k/kubectl"
export PATH="$k:$PATH"
# shellcheck source=/dev/null
source scripts/common/lib.sh

kubeadm_ok='apiServer: {}\nnetworking:\n  podSubnet: 10.200.0.0/16\n  serviceSubnet: 10.96.0.0/12\n'
run() { unset APISIX_ADMIN_CIDR; export FAKE_CILIUM="$1" FAKE_KUBEADM="$2"; resolve_apisix_admin_cidr 2>/dev/null; }
eq() { [ "$1" = "$2" ] || { echo "expected '$2' got '$1' ($3)" >&2; exit 1; }; }
fails() { if run "$1" "$2" >/dev/null; then echo "must fail: $3" >&2; exit 1; fi; }

eq "$(run 'cluster-pool\t10.0.0.0/8' "${kubeadm_ok}")" 10.0.0.0/8 "cilium cluster-pool wins"
eq "$(run 'kubernetes\t' "${kubeadm_ok}")" 10.200.0.0/16 "non-cluster-pool ipam -> kubeadm podSubnet"
eq "$(run 'cluster-pool\t' "${kubeadm_ok}")" 10.200.0.0/16 "cluster-pool without cidr -> kubeadm"
eq "$(run notfound "${kubeadm_ok}")" 10.200.0.0/16 "cilium NotFound -> kubeadm"
eq "$(run notfound notfound)" 10.244.0.0/16 "both NotFound -> chart default"
eq "$(run notfound 'networking: {}\n')" 10.244.0.0/16 "kubeadm without podSubnet -> chart default"
fails error "${kubeadm_ok}" "cilium API error must abort, not default"
fails notfound error "kubeadm API error must abort, not default"
fails 'cluster-pool\t10.0.0.0/8 10.1.0.0/16' "${kubeadm_ok}" "multi-CIDR pool is ambiguous"
fails 'cluster-pool\t10.0.0.0/33' "${kubeadm_ok}" "bad prefix"
fails 'cluster-pool\t300.0.0.0/8' "${kubeadm_ok}" "bad octet"
export FAKE_CILIUM=error FAKE_KUBEADM=error
eq "$(APISIX_ADMIN_CIDR=172.30.0.0/16 resolve_apisix_admin_cidr 2>/dev/null)" 172.30.0.0/16 "env override needs no API"
if APISIX_ADMIN_CIDR=nonsense resolve_apisix_admin_cidr >/dev/null 2>&1; then echo "invalid override must fail" >&2; exit 1; fi
