#!/bin/bash
# 11-4c-kubeadm-config-oidc.sh
# Persist the seven kube-apiserver --oidc-* flags in the kube-system/kubeadm-config
# ClusterConfiguration so a regenerated static pod manifest keeps them.
#
# D-KUBEADM-CONFIG-OIDC (#505):
#   Reason: 11-4b-apiserver-oidc-node.sh edits each master's /etc/kubernetes/manifests/
#   kube-apiserver.yaml, but `kubeadm upgrade apply|node` regenerates that manifest from
#   ClusterConfiguration, and a control plane joined later (02-join-control-plane.sh) renders
#   its manifest from it too. Without the flags there every master loses OIDC after an upgrade
#   (Dashboard/kubectl SSO 401, the #500 incident). This script adds them under
#   apiServer.extraArgs (kubeadm.k8s.io/v1beta4: a LIST of {name, value}; v1beta3 stored a map,
#   handled too) and touches nothing else. 02-init-cluster.sh stays unchanged: Keycloak, hence
#   the issuer and CA, does not exist at init time.
#   The CA needs no extraVolumes entry: --oidc-ca-file lives under /etc/kubernetes/pki, which
#   kubeadm always mounts into the pod (k8s-certs hostPath). With a readable local manifest this
#   script refuses to write unless a volumeMount already covers that path.
#   Fail closed: unparsable or unexpected input, a merge that changes anything but the oidc-*
#   entries, a missing backup, or a result that is not exactly the 7 flags writes nothing and
#   exits non-zero. Only data.ClusterConfiguration is patched (merge patch pinned to the
#   resourceVersion that was read), so every other ConfigMap key is untouched. The static pod
#   manifests are never touched here (11-4b owns them).
#   Cost: one ConfigMap patch; the running apiservers are not restarted. 0 writes on a re-run.
#   Escape hatch: the original ClusterConfiguration is saved next to the apiserver backups as
#   /etc/kubernetes/kubeadm-config.ClusterConfiguration.pre-oidc-<UTC stamp>.yaml; restore with
#   kubectl -n kube-system patch cm kubeadm-config --type merge \
#     -p "$(yq -n -o=json '.data.ClusterConfiguration = load_str("<file>")')"
#
# Usage (run where kubectl has cluster-admin, once per cluster; sudo for the backup directory):
#   sudo env KUBECONFIG=... scripts/cluster/11-4c-kubeadm-config-oidc.sh <issuer_url> [--dry-run]
set -euo pipefail

# yq (mikefarah) lives in /usr/local/bin, which sudo's secure_path may omit.
export PATH="${PATH}:/usr/local/bin"

OIDC_ISSUER_URL="${1:-${OIDC_ISSUER_URL:-}}"
DRY_RUN=false
[ "${2:-}" = "--dry-run" ] && DRY_RUN=true

KUBECTL="${KUBECTL:-kubectl}"
APISERVER_MANIFEST="${APISERVER_MANIFEST:-/etc/kubernetes/manifests/kube-apiserver.yaml}"
BACKUP_DIR="${KUBEADM_CONFIG_BACKUP_DIR:-/etc/kubernetes}"
OIDC_CA_FILE="/etc/kubernetes/pki/oidc-ca.crt"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

case "${OIDC_ISSUER_URL}" in
  https://*) ;;
  *) fail "usage: $0 <https issuer_url> [--dry-run] (got: ${OIDC_ISSUER_URL:-<empty>})" ;;
esac
command -v yq >/dev/null 2>&1 || fail "yq (mikefarah) not found on PATH"

# Expected "name=value" pairs, sorted. Same seven flags as 11-4b-apiserver-oidc-node.sh.
EXPECTED="$(printf '%s\n' \
  "oidc-ca-file=${OIDC_CA_FILE}" \
  "oidc-client-id=kubernetes" \
  "oidc-groups-claim=groups" \
  "oidc-groups-prefix=oidc:" \
  "oidc-issuer-url=${OIDC_ISSUER_URL}" \
  "oidc-username-claim=preferred_username" \
  "oidc-username-prefix=oidc:" | LC_ALL=C sort)"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# stdin: ClusterConfiguration; prints the sorted oidc-* "name=value" pairs it carries.
oidc_pairs() {
  local api_version
  api_version="$(yq '.apiVersion' "$1")"
  if [ "${api_version}" = "kubeadm.k8s.io/v1beta3" ]; then
    yq '(.apiServer.extraArgs // {}) | to_entries | .[] | select(.key | test("^oidc-")) | .key + "=" + (.value | tostring)' "$1" | LC_ALL=C sort
  else
    yq '(.apiServer.extraArgs // []) | .[] | select(.name | test("^oidc-")) | .name + "=" + (.value | tostring)' "$1" | LC_ALL=C sort
  fi
}

# Everything except the oidc-* extraArgs, as sorted JSON: must be identical before and after.
non_oidc_json() {
  local api_version
  api_version="$(yq '.apiVersion' "$1")"
  if [ "${api_version}" = "kubeadm.k8s.io/v1beta3" ]; then
    yq -o=json -I=0 'del(.apiServer.extraArgs | .[] | select(key | test("^oidc-"))) | del(.apiServer.extraArgs | select(length == 0)) | del(.apiServer | select(length == 0)) | sort_keys(..)' "$1"
  else
    yq -o=json -I=0 'del(.apiServer.extraArgs[] | select(.name | test("^oidc-"))) | del(.apiServer.extraArgs | select(length == 0)) | del(.apiServer | select(length == 0)) | sort_keys(..)' "$1"
  fi
}

validate_input() {
  local f="$1" kind api_version tag
  yq '.' "${f}" >/dev/null 2>&1 || fail "kubeadm-config ClusterConfiguration is not parsable YAML; nothing written"
  [ "$(yq 'documentIndex' "${f}" | wc -l | tr -d ' ')" = "1" ] || fail "ClusterConfiguration has more than one YAML document; nothing written"
  kind="$(yq '.kind' "${f}")"
  api_version="$(yq '.apiVersion' "${f}")"
  [ "${kind}" = "ClusterConfiguration" ] || fail "unexpected kind '${kind}' in kubeadm-config (want ClusterConfiguration); nothing written"
  tag="$(yq '.apiServer.extraArgs | tag' "${f}")"
  case "${api_version}:${tag}" in
    kubeadm.k8s.io/v1beta4:'!!null' | kubeadm.k8s.io/v1beta4:'!!seq') ;;
    kubeadm.k8s.io/v1beta3:'!!null' | kubeadm.k8s.io/v1beta3:'!!map') ;;
    *) fail "unsupported ClusterConfiguration shape (apiVersion ${api_version}, apiServer.extraArgs ${tag}); nothing written" ;;
  esac
}

cm_field() {
  "${KUBECTL}" -n kube-system get cm kubeadm-config -o "jsonpath=$1"
}

cur_file="${tmp}/current.yaml"
cm_field '{.data.ClusterConfiguration}' > "${cur_file}" \
  || fail "cannot read kube-system/kubeadm-config"
[ -s "${cur_file}" ] || fail "kube-system/kubeadm-config has an empty data.ClusterConfiguration; nothing written"
resource_version="$(cm_field '{.metadata.resourceVersion}')"
[ -n "${resource_version}" ] || fail "cannot read kubeadm-config resourceVersion; nothing written"

validate_input "${cur_file}"

if [ "$(oidc_pairs "${cur_file}")" = "${EXPECTED}" ]; then
  echo "kubeadm-config ClusterConfiguration already carries the 7 OIDC flags — no-op"
  exit 0
fi

# The CA must already be mounted into the pod by kubeadm's default pki volume.
if [ -r "${APISERVER_MANIFEST}" ]; then
  mounted="$(yq '.spec.containers[0].volumeMounts[].mountPath' "${APISERVER_MANIFEST}" 2>/dev/null || true)"
  covered=false
  while IFS= read -r mp; do
    [ -n "${mp}" ] || continue
    case "${OIDC_CA_FILE}" in
      "${mp}" | "${mp%/}"/*) covered=true ;;
    esac
  done <<<"${mounted}"
  [ "${covered}" = "true" ] || fail "${APISERVER_MANIFEST} has no volumeMount covering ${OIDC_CA_FILE}; a regenerated pod would not see the CA and an extraVolumes entry is needed first. Nothing written"
fi

new_file="${tmp}/new.yaml"
cp "${cur_file}" "${new_file}"
export OIDC_ISSUER_URL OIDC_CA_FILE
api_version="$(yq '.apiVersion' "${cur_file}")"
if [ "${api_version}" = "kubeadm.k8s.io/v1beta3" ]; then
  yq -i '.apiServer.extraArgs = ((.apiServer.extraArgs // {}) | with_entries(select(.key | test("^oidc-") | not))) + {
    "oidc-issuer-url": strenv(OIDC_ISSUER_URL),
    "oidc-client-id": "kubernetes",
    "oidc-username-claim": "preferred_username",
    "oidc-groups-claim": "groups",
    "oidc-username-prefix": "oidc:",
    "oidc-groups-prefix": "oidc:",
    "oidc-ca-file": strenv(OIDC_CA_FILE)
  }' "${new_file}" || fail "merge failed; nothing written"
else
  yq -i '.apiServer.extraArgs = ((.apiServer.extraArgs // []) | map(select(.name | test("^oidc-") | not))) + [
    {"name": "oidc-issuer-url", "value": strenv(OIDC_ISSUER_URL)},
    {"name": "oidc-client-id", "value": "kubernetes"},
    {"name": "oidc-username-claim", "value": "preferred_username"},
    {"name": "oidc-groups-claim", "value": "groups"},
    {"name": "oidc-username-prefix", "value": "oidc:"},
    {"name": "oidc-groups-prefix", "value": "oidc:"},
    {"name": "oidc-ca-file", "value": strenv(OIDC_CA_FILE)}
  ]' "${new_file}" || fail "merge failed; nothing written"
fi

[ "$(oidc_pairs "${new_file}")" = "${EXPECTED}" ] \
  || fail "merged ClusterConfiguration does not carry exactly the 7 expected OIDC flags; nothing written"
[ "$(non_oidc_json "${cur_file}")" = "$(non_oidc_json "${new_file}")" ] \
  || fail "merge would change more than the oidc-* extraArgs; nothing written"

if [ "${DRY_RUN}" = "true" ]; then
  echo "--dry-run: merged ClusterConfiguration (nothing written):"
  cat "${new_file}"
  exit 0
fi

mkdir -p "${BACKUP_DIR}" || fail "cannot create backup directory ${BACKUP_DIR}; nothing written"
stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
backup_path="${BACKUP_DIR}/kubeadm-config.ClusterConfiguration.pre-oidc-${stamp}.yaml"
n=1
while [ -e "${backup_path}" ]; do
  n=$((n + 1))
  backup_path="${BACKUP_DIR}/kubeadm-config.ClusterConfiguration.pre-oidc-${stamp}-${n}.yaml"
done
cp "${cur_file}" "${backup_path}" && [ -s "${backup_path}" ] \
  || fail "could not write backup ${backup_path}; nothing written"
echo "Backed up ClusterConfiguration to ${backup_path}"

# Merge patch of data.ClusterConfiguration only, pinned to the resourceVersion we read: a
# concurrent edit (another kubeadm run) makes the patch fail instead of being overwritten.
export NEW_CC RV
NEW_CC="$(cat "${new_file}")"
RV="${resource_version}"
yq -n -o=json '.metadata.resourceVersion = strenv(RV) | .data.ClusterConfiguration = strenv(NEW_CC)' > "${tmp}/patch.json" \
  || fail "could not build the patch; nothing written"
"${KUBECTL}" -n kube-system patch cm kubeadm-config --type merge --patch-file "${tmp}/patch.json" \
  || fail "patching kubeadm-config failed (backup: ${backup_path})"

after_file="${tmp}/after.yaml"
cm_field '{.data.ClusterConfiguration}' > "${after_file}" \
  || fail "cannot re-read kubeadm-config after the patch (backup: ${backup_path})"
[ "$(oidc_pairs "${after_file}")" = "${EXPECTED}" ] \
  || fail "kubeadm-config does not carry the 7 OIDC flags after the patch (backup: ${backup_path})"
echo "kubeadm-config ClusterConfiguration now carries the 7 OIDC flags (apiServer.extraArgs)"
