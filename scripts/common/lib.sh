#!/bin/bash
set -euo pipefail
# Narwhal common library - shared utilities for cluster scripts
# Usage: source /home/vagrant/scripts/common/lib.sh

# Detect the host OS release for version-specific provisioning branches.
# os_version_id -> "24.04" | "26.04" | "unknown"; os_codename -> "noble" | "resolute" | ...
# Pattern: ver="$(os_version_id)"; case "$ver" in 24.04) ... ;; 26.04) ... ;; esac
os_version_id() {
  ( . /etc/os-release 2>/dev/null && echo "${VERSION_ID:-unknown}" )
}
os_codename() {
  ( . /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME:-unknown}" )
}

# Generate a 24-char URL-safe random password
generate_password() {
  openssl rand -base64 16 | tr -d '=/+' | head -c 24
}

# read_secret_field <secret> <ns> <data-key>: prints the decoded value ("" when the Secret or
# key does not exist). Fails closed: only a kubectl NotFound counts as "absent"; any other
# error (API blip, timeout, RBAC) returns 1 so the caller aborts instead of defaulting over
# real credentials. Values are never logged.
read_secret_field() {
  local out err rc=0 errf
  errf=$(mktemp) || return 1
  out=$(kubectl get secret "$1" -n "$2" -o "jsonpath={.data.$3}" 2>"${errf}") || rc=$?
  err=$(cat "${errf}" 2>/dev/null || true)
  rm -f "${errf}"
  if [ "${rc}" -ne 0 ]; then
    # Only kubectl's own server NotFound counts as absent. A bare "not found" would also
    # match the shell's "kubectl: command not found" (rc 127) and fail open.
    if [ "${rc}" -ne 127 ]; then
      case "${err}" in
        *"Error from server (NotFound)"*) return 0 ;;
      esac
    fi
    echo "ERROR: cannot read secret $2/$1 (kubectl failed, not NotFound); refusing to default credentials." >&2
    return 1
  fi
  if [ -n "${out}" ]; then
    printf '%s' "${out}" | base64 -d 2>/dev/null || { echo "ERROR: secret $2/$1 key $3 is not valid base64." >&2; return 1; }
  fi
}

# --- SeaweedFS S3 identities (D-s3auth, 2026-10-16) ---------------------------------------
# One identity per client (loki, tempo, velero, cnpg), each scoped to its own bucket. The four
# client Secrets are the single source of truth; storage/seaweedfs-s3-config (the server JSON,
# key seaweedfs_s3_config) and the Velero `cloud` credentials file are derived from them on
# every run, so a partial earlier run converges instead of diverging. No value is ever printed.

# s3_identity_spec <name>: "<namespace> <secret> <access-field> <secret-field> <bucket>".
s3_identity_spec() {
  case "$1" in
    loki)   echo "monitoring loki-s3-credentials S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY loki" ;;
    tempo)  echo "monitoring tempo-s3-credentials S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY tempo" ;;
    velero) echo "storage velero-s3-credentials access-key secret-key velero" ;;
    cnpg)   echo "database cnpg-s3-credentials ACCESS_KEY_ID ACCESS_SECRET_KEY cnpg-backup" ;;
    *) return 1 ;;
  esac
}

# secret_exists <secret> <ns>: 0 present, 1 absent (kubectl NotFound only). Any other failure
# aborts the caller (return 2): a present-but-unreadable Secret must never look absent.
secret_exists() {
  local err rc=0 errf
  errf=$(mktemp) || return 2
  kubectl get secret "$1" -n "$2" -o name >/dev/null 2>"${errf}" || rc=$?
  err=$(cat "${errf}" 2>/dev/null || true)
  rm -f "${errf}"
  [ "${rc}" -eq 0 ] && return 0
  if [ "${rc}" -ne 127 ]; then
    case "${err}" in *"Error from server (NotFound)"*) return 1 ;; esac
  fi
  echo "ERROR: cannot check secret $2/$1 (kubectl failed, not NotFound)." >&2
  return 2
}

# s3_key_state <access> <secret>: absent (both empty) | default (the historic inline
# admin/admin pair) | usable | invalid. invalid covers a half-filled pair, one default half,
# and characters that would break the JSON/INI/env projections: callers fail closed on it.
s3_key_state() {
  if [ -z "$1" ] && [ -z "$2" ]; then echo absent; return 0; fi
  if [ -z "$1" ] || [ -z "$2" ]; then echo invalid; return 0; fi
  if [ "$1" = admin ] && [ "$2" = admin ]; then echo default; return 0; fi
  if [ "$1" = admin ] || [ "$2" = admin ]; then echo invalid; return 0; fi
  case "$1$2" in *[!A-Za-z0-9+/=_.-]*) echo invalid; return 0 ;; esac
  echo usable
}

# ensure_s3_identities: sets S3_IDENTITIES_CHANGED=true when any key was generated or replaced
# (informational only: the S3 restart decision is s3_sync_server_config, which compares the
# server config with what the running pods loaded, so a repaired or earlier-written config still
# rolls the server). Existing random keys are kept. Only absent -> generate and the exact default pair ->
# replace (once: afterwards the pair is no longer the default). A half-filled/odd pair or any
# Secret read error other than NotFound returns 1 (fail closed, never regenerate over data).
# shellcheck disable=SC2034  # S3_IDENTITIES_CHANGED is read by the sourcing script
ensure_s3_identities() {
  local name spec ns secret af sf bucket ak sk state i prev rc json=""
  local -a names=(loki tempo velero cnpg) aks=() sks=() buckets=()
  S3_IDENTITIES_CHANGED=false
  for name in "${names[@]}"; do
    spec=$(s3_identity_spec "${name}") || return 1
    read -r ns secret af sf bucket <<<"${spec}"
    rc=0; secret_exists "${secret}" "${ns}" || rc=$?
    if [ "${rc}" -eq 1 ]; then
      ak=""; sk=""; state=absent
    elif [ "${rc}" -ne 0 ]; then
      return 1
    else
      ak=$(read_secret_field "${secret}" "${ns}" "${af}") || return 1
      sk=$(read_secret_field "${secret}" "${ns}" "${sf}") || return 1
      state=$(s3_key_state "${ak}" "${sk}")
      # a Secret that exists with both fields empty/missing is corrupt, not absent: fail closed
      if [ "${state}" = absent ]; then state=invalid; fi
    fi
    # SeaweedFS maps identities by access key and the later one wins, so a key shared by two
    # clients would hand one of them the other's bucket. The first holder (loki, tempo, velero,
    # cnpg order) keeps its pair; later holders are rotated like the default.
    if [ "${state}" = usable ]; then
      for prev in ${aks[@]+"${aks[@]}"}; do
        if [ "${prev}" = "${ak}" ]; then state=duplicate; break; fi
      done
    fi
    case "${state}" in
      usable) echo "  s3 identity ${name}: keeping existing keys" >&2 ;;
      absent|default|duplicate)
        ak=$(openssl rand -hex 10 | tr a-f A-F) || return 1
        sk=$(openssl rand -hex 24) || return 1
        S3_IDENTITIES_CHANGED=true
        if [ "${state}" = default ]; then
          echo "  s3 identity ${name}: replacing the default admin/admin pair with generated keys" >&2
        elif [ "${state}" = duplicate ]; then
          echo "  s3 identity ${name}: duplicate access key shared with another client, rotating it" >&2
        else
          echo "  s3 identity ${name}: generated new keys" >&2
        fi ;;
      *)
        echo "ERROR: secret ${ns}/${secret} holds a partial, empty or unsupported S3 key pair; refusing to regenerate. Fix or delete it by hand." >&2
        return 1 ;;
    esac
    aks+=("${ak}"); sks+=("${sk}"); buckets+=("${bucket}")
  done

  if [ "$(printf '%s\n' "${aks[@]}" | sort -u | wc -l | tr -d ' ')" -ne "${#names[@]}" ]; then
    echo "ERROR: S3 access keys are not unique across identities; refusing to render seaweedfs_s3_config." >&2
    return 1
  fi

  for name in "${names[@]}"; do
    spec=$(s3_identity_spec "${name}")
    kubectl create namespace "${spec%% *}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null || return 1
  done

  for i in "${!names[@]}"; do
    json+="${json:+,}{\"name\":\"${names[$i]}\",\"credentials\":[{\"accessKey\":\"${aks[$i]}\",\"secretKey\":\"${sks[$i]}\"}],\"actions\":[\"Read:${buckets[$i]}\",\"Write:${buckets[$i]}\",\"List:${buckets[$i]}\",\"Tagging:${buckets[$i]}\"]}"
  done
  # D-s3ops-5: the key-bearing temp file is removed on success, apply failure and TERM/INT. Only
  # the write+apply section runs in a subshell (its own trap, the caller's is untouched);
  # S3_IDENTITIES_CHANGED and the arrays stay in this shell. Cost: SIGKILL still leaks the file.
  (
    tmp=$(mktemp) || exit 1
    trap 'rm -f "${tmp}"' EXIT
    trap 'exit 143' INT TERM
    for i in "${!names[@]}"; do
      name="${names[$i]}"
      read -r ns secret af sf bucket <<<"$(s3_identity_spec "${name}")"
      # D-s3apply-5: encode first and check the status; inside "$(...)" a failing base64 would leave
      # an empty value that printf happily writes and apply then wipes the live credentials with.
      b_ak=$(s3_b64 "${aks[$i]}") && b_sk=$(s3_b64 "${sks[$i]}") && b_cloud= && {
        [ "${name}" != velero ] || {
          # printf -v, not "$(...)": a command substitution would strip the final LF the old `cloud: |` block kept
          printf -v cloud_file '[default]\naws_access_key_id = %s\naws_secret_access_key = %s\n' "${aks[$i]}" "${sks[$i]}"
          b_cloud=$(s3_b64 "${cloud_file}")
        }
      } || { echo "ERROR: could not base64-encode the S3 credentials of ${name}; nothing was applied for it." >&2; exit 1; }
      {
        printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: %s\n  namespace: %s\n' "${secret}" "${ns}"
        # D-s3apply-4: base64 `data`, not `stringData`. The API server converts stringData to data on
        # write, so under server-side apply the manager would own a field that never persists and a
        # key dropped here could linger; `data` makes the manager own exactly these keys. It also
        # keeps an all-digit or leading-zero key a string (D-s3ops-5).
        printf '  labels:\n    narwhal.io/s3-identity: %s\ntype: Opaque\ndata:\n' "${name}"
        printf '  %s: %s\n  %s: %s\n' "${af}" "${b_ak}" "${sf}" "${b_sk}"
        if [ "${name}" = velero ]; then
          # velero-plugin-for-aws reads this AWS credentials-file blob (key `cloud`)
          printf '  cloud: %s\n' "${b_cloud}"
        fi
      } >"${tmp}" || exit 1
      s3_apply_secret "${tmp}" "${ns}" "${secret}" || exit 1
    done
    b_cfg=$(s3_b64 "{\"identities\":[${json}]}") ||
      { echo "ERROR: could not base64-encode the SeaweedFS S3 server config; nothing was applied for it." >&2; exit 1; }
    {
      printf 'apiVersion: v1\nkind: Secret\nmetadata:\n  name: seaweedfs-s3-config\n  namespace: storage\n'
      printf 'type: Opaque\ndata:\n'
      printf '  seaweedfs_s3_config: %s\n' "${b_cfg}"
    } >"${tmp}" || exit 1
    s3_apply_secret "${tmp}" storage seaweedfs-s3-config || exit 1
  ) || return 1
}

# s3_apply_secret <file> <namespace> <secret> -- applies a key-bearing Secret server-side so the keys are
# not copied into the kubectl.kubernetes.io/last-applied-configuration annotation (#415).
# D-s3apply-1: --force-conflicts is deliberate. This script is the only intended writer of these
#   Secrets (D-s3auth) and its values are authoritative (rotation of a default/duplicate key must win
#   over the old client-side-apply owner "kubectl-client-side-apply"); without it the first rotation
#   after the switch would fail with a field conflict. Cost: it also overrides an ad-hoc edit.
# D-s3apply-2: field manager name is fixed (narwhal-s3-identities) so ownership is stable across reruns.
# D-s3apply-3: objects created by the old client-side apply still carry the plaintext annotation;
#   it is removed after every apply (the object exists by then; `annotate key-` on an absent
#   annotation is a no-op success). A real failure fails the run (fail closed): leaving the old keys
#   in the annotation would defeat #415. The message carries no key.
s3_apply_secret() {
  kubectl apply -f "$1" --server-side --field-manager=narwhal-s3-identities --force-conflicts >/dev/null || return 1
  kubectl annotate secret "$3" -n "$2" kubectl.kubernetes.io/last-applied-configuration- >/dev/null 2>&1 || {
    echo "ERROR: could not clear the last-applied annotation of secret ${2}/${3}; it may still hold the previous keys. Remove it by hand and rerun." >&2
    return 1
  }
}

# s3_b64 <value> -- base64 on one line (GNU base64 wraps at 76 columns); fails on an encoder error or
# empty output. Callers assign the result to a variable and check the status (a command substitution
# inside printf arguments would hide the failure).
s3_b64() {
  local o
  o=$(printf '%s' "$1" | base64) && [ -n "${o}" ] || return 1
  printf '%s' "${o//$'\n'/}"
}

# is_cidr <value> -- strict IPv4 CIDR (octets <= 255, prefix 0-32).
is_cidr() {
  local ip prefix o
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || return 1
  ip="${1%/*}"; prefix="${1#*/}"
  [ "${prefix}" -le 32 ] || return 1
  for o in ${ip//./ }; do [ "$((10#${o}))" -le 255 ] || return 1; done
}

# resolve_apisix_admin_cidr -- prints the pod CIDR allowed to reach the APISIX Admin API.
# Deliberately NOT POD_NETWORK_CIDR: that value also drives pod-cidr-consistency-check, NFS
# exports and trusted proxies, and a legacy Cilium pool (Kakao: 10.0.0.0/8 pool, CiliumNodes
# 10.0.x.0/24) differs from it. Order (D1): APISIX_ADMIN_CIDR env -> cilium-config with
# ipam=cluster-pool -> cluster-pool-ipv4-cidr -> kubeadm-config podSubnet -> chart default
# 10.244.0.0/16. Defaulting happens only when a ConfigMap is NotFound; any other kubectl
# error, an unparsable value or a multi-CIDR pool returns non-zero (fail closed): silently
# defaulting on a live cluster re-creates the Admin API 403 (ResourceSyncAborted).
resolve_apisix_admin_cidr() {
  local cidr="${APISIX_ADMIN_CIDR:-}" out err ipam
  err=$(mktemp)
  if [ -z "${cidr}" ]; then
    if out=$(kubectl -n kube-system get configmap cilium-config \
        -o 'jsonpath={.data.ipam}{"\t"}{.data.cluster-pool-ipv4-cidr}' 2>"${err}"); then
      ipam="${out%%$'\t'*}"
      [ "${ipam}" = cluster-pool ] && cidr="${out#*$'\t'}"
    elif ! grep -q 'Error from server (NotFound)' "${err}"; then
      echo "ERROR: cannot read configmap/cilium-config: $(head -c 200 "${err}")" >&2
      rm -f "${err}"; return 1
    fi
  fi
  if [ -z "${cidr}" ]; then
    if out=$(kubectl -n kube-system get configmap kubeadm-config \
        -o 'jsonpath={.data.ClusterConfiguration}' 2>"${err}"); then
      cidr=$(printf '%s\n' "${out}" | awk '/^[[:space:]]*podSubnet:/ {print $2; exit}')
    elif ! grep -q 'Error from server (NotFound)' "${err}"; then
      echo "ERROR: cannot read configmap/kubeadm-config: $(head -c 200 "${err}")" >&2
      rm -f "${err}"; return 1
    fi
  fi
  rm -f "${err}"
  cidr="${cidr:-10.244.0.0/16}"
  if ! is_cidr "${cidr}"; then
    echo "ERROR: APISIX admin allowlist '${cidr}' is not a single IPv4 CIDR; set APISIX_ADMIN_CIDR" >&2
    return 1
  fi
  printf '%s\n' "${cidr}"
}

# --- APISIX Admin API keys (Secret platform-system/apisix-admin-key, fields key + viewer) ---
# The upstream APISIX/ingress-controller chart defaults are public. They are recognised by
# sha256 only (D-apisix-key: no key literal in git, not even the one to refuse) and are never
# reused: anything matching is replaced by a fresh random key, which is also the rotation path.
APISIX_DEFAULT_KEY_SHA256S="477a2767fa27931db11a3581100a4124b5720f6b6468ddd64062170cec157185 228ab1b6080ec4178302ba76f9fa4af4f2dc14fc105a364b324bdb9045af03a1"

# apisix_key_usable <value>: succeeds for a real, non-default key. Empty values, env
# placeholders (`${{APISIX_ADMIN_KEY}}`) and the public upstream defaults fail.
apisix_key_usable() {
  local h
  case "$1" in ''|*'{'*|*'$'*) return 1 ;; esac
  h=$(printf '%s' "$1" | openssl dgst -sha256 -r | cut -d' ' -f1)
  case " ${APISIX_DEFAULT_KEY_SHA256S} " in *" ${h} "*) return 1 ;; esac
  return 0
}

# apisix_cm_key <admin|viewer>: literal key of that role in configmap/apisix, "" when absent or
# on any error. Layout is a YAML list where `- key: <k>` precedes `name: <role>` (name may be
# quoted): remember the last `- key:` and print it on the matching name line.
apisix_cm_key() {
  kubectl get configmap apisix -n platform-system -o jsonpath='{.data.config\.yaml}' 2>/dev/null \
    | awk -v role="$1" '/^[[:space:]]*-[[:space:]]*key:/ {k=$3; gsub(/"/, "", k)} $0 ~ "^[[:space:]]*name:[[:space:]]*\"?" role "\"?[[:space:]]*$" && !f {print k; f=1}' || true
}

# shellcheck disable=SC2034  # outputs are read by the sourcing script
# resolve_apisix_keys: sets APISIX_ADMIN_KEY / APISIX_VIEWER_KEY and APISIX_KEYS_GENERATED
# (true when either was newly generated, i.e. consumers must restart). Per role: the existing
# Secret value, else the bootstrap configmap literal, else a new `openssl rand -hex 16`; a
# default/placeholder/empty value is skipped. A non-NotFound Secret read error returns 1
# (fail closed, never regenerate over real credentials). Never prints values.
resolve_apisix_keys() {
  local role field var v
  APISIX_KEYS_GENERATED=false
  for role in admin viewer; do
    field=key; var=APISIX_ADMIN_KEY
    if [ "${role}" = viewer ]; then field=viewer; var=APISIX_VIEWER_KEY; fi
    v=$(read_secret_field apisix-admin-key platform-system "${field}") || return 1
    apisix_key_usable "${v}" || v=$(apisix_cm_key "${role}")
    if ! apisix_key_usable "${v}"; then
      v=$(openssl rand -hex 16)
      APISIX_KEYS_GENERATED=true
    fi
    printf -v "${var}" '%s' "${v}"
  done
}

# Wait for Kubernetes API server to be reachable
wait_for_api_server() {
  local max_attempts="${1:-30}"
  echo "Waiting for API server..."
  for i in $(seq 1 "${max_attempts}"); do
    if kubectl get nodes &>/dev/null; then
      echo "API server is reachable"
      return 0
    fi
    echo "  Attempt ${i}/${max_attempts}: API server not ready, waiting 10s..."
    sleep 10
  done
  echo "ERROR: API server did not become ready after ${max_attempts} attempts"
  return 1
}

# Ensure a Kubernetes namespace exists
ensure_namespace() {
  local ns="$1"
  kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f -
}

# Ensure a secret key exists (create with random value if absent)
# Usage: ensure_secret_key <namespace> <secret-name> <key>
ensure_secret_key() {
  local ns="$1"
  local secret="$2"
  local key="$3"
  if ! kubectl get secret "${secret}" -n "${ns}" &>/dev/null; then
    return 1
  fi
  if ! kubectl get secret "${secret}" -n "${ns}" -o jsonpath="{.data.${key}}" 2>/dev/null | grep -q .; then
    local value
    value=$(generate_password)
    kubectl patch secret "${secret}" -n "${ns}" \
      --type='json' \
      -p="[{\"op\":\"add\",\"path\":\"/data/${key}\",\"value\":\"$(echo -n "${value}" | base64 -w0)\"}]"
  fi
}

# s3_sync_server_config: SeaweedFS reads its identities file once at start (no hot reload).
# Compare sha256 of storage/seaweedfs-s3-config with the pod-template annotation
# narwhal.io/s3-config-sha256 of Deployment seaweedfs-s3 and patch it when they differ; the
# patch changes the pod template, which is the rollout. Idempotent, independent of which run
# wrote which Secret (partial failures and 07-before-08-4 both converge). No Deployment yet
# (07, or first 08-4 run before helm) is a no-op; any other read error fails closed.
s3_sync_server_config() {
  local cfg sum cur err rc=0 errf
  cfg=$(read_secret_field seaweedfs-s3-config storage seaweedfs_s3_config) || return 1
  [ -n "${cfg}" ] || { echo "ERROR: storage/seaweedfs-s3-config is missing or empty." >&2; return 1; }
  sum=$(printf '%s' "${cfg}" | openssl dgst -sha256 -r | cut -d' ' -f1) || return 1
  errf=$(mktemp) || return 1
  cur=$(kubectl get deployment seaweedfs-s3 -n storage \
    -o 'jsonpath={.spec.template.metadata.annotations.narwhal\.io/s3-config-sha256}' 2>"${errf}") || rc=$?
  err=$(cat "${errf}" 2>/dev/null || true)
  rm -f "${errf}"
  if [ "${rc}" -ne 0 ]; then
    case "${err}" in
      *"Error from server (NotFound)"*) return 0 ;;
      *) echo "ERROR: cannot read Deployment storage/seaweedfs-s3." >&2; return 1 ;;
    esac
  fi
  if [ "${cur}" = "${sum}" ]; then return 0; fi
  echo "  seaweedfs-s3 runs a different identities file; rolling it" >&2
  kubectl patch deployment seaweedfs-s3 -n storage --type=merge \
    -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"narwhal.io/s3-config-sha256\":\"${sum}\"}}}}}" >/dev/null
}

# guard_s3_auth_cutover (D-s3ops-4, #414): read-only pre-flight for 08-4. Enabling S3 auth on a
# live anonymous seaweedfs-s3 while Loki/Tempo do not yet consume their new keys locks them out
# at the next restart. Allowed: no Deployment yet (clean install, NotFound only), a server whose
# command+args (any container) hold a non-empty -config= (re-run), or an anonymous server whose
# Loki and Tempo StatefulSets (monitoring) both reference their S3 Secret via envFrom (staged
# cutover, runbook step 2 done); a client whose StatefulSet is NotFound is not installed and
# cannot be locked out. Otherwise abort unless NARWHAL_ALLOW_S3_AUTH_CUTOVER=true, which
# bypasses ONLY that transition abort, never a kubectl/RBAC/parse error (fail closed).
# Why not Secret existence: 07's ensure_s3_identities creates both Secrets before 08-4 runs, so
# on a full Phase 2 re-run that test always passed. Cost: envFrom proves the workload is wired,
# not that its pods restarted and loaded the keys; the runbook gate owns that.
guard_s3_auth_cutover() {
  local out err errf rc=0 name refs cfg_re='-config=[^"[:space:]]' notconsuming=""
  errf=$(mktemp) || return 1
  out=$(kubectl get deployment seaweedfs-s3 -n storage -o \
    'jsonpath={range .spec.template.spec.containers[*]}{.name}{"\t"}{.command}{"\t"}{.args}{"\n"}{end}' \
    2>"${errf}") || rc=$?
  err=$(cat "${errf}" 2>/dev/null || true)
  if [ "${rc}" -ne 0 ]; then
    rm -f "${errf}"
    case "${err}" in
      *"Error from server (NotFound)"*) return 0 ;;
      *) echo "ERROR: cannot read Deployment storage/seaweedfs-s3: ${err:0:200}" >&2; return 1 ;;
    esac
  fi
  case "${out}" in
    ?*$'\t'*$'\t'*) ;;
    *) rm -f "${errf}"; echo "ERROR: cannot parse the container spec of Deployment storage/seaweedfs-s3." >&2; return 1 ;;
  esac
  if [[ "${out}" =~ ${cfg_re} ]]; then rm -f "${errf}"; return 0; fi
  for name in loki tempo; do
    rc=0
    refs=$(kubectl get statefulset "${name}" -n monitoring -o \
      'jsonpath={range .spec.template.spec.containers[*]}{range .envFrom[*]}{.secretRef.name}{"\n"}{end}{end}' \
      2>"${errf}") || rc=$?
    err=$(cat "${errf}" 2>/dev/null || true)
    if [ "${rc}" -ne 0 ]; then
      case "${err}" in
        *"Error from server (NotFound)"*) continue ;;
        *) rm -f "${errf}"; echo "ERROR: cannot read StatefulSet monitoring/${name}: ${err:0:200}" >&2; return 1 ;;
      esac
    fi
    case $'\n'"${refs}"$'\n' in
      *$'\n'"${name}-s3-credentials"$'\n'*) ;;
      *) notconsuming="${notconsuming:+${notconsuming} }${name}" ;;
    esac
  done
  rm -f "${errf}"
  [ -n "${notconsuming}" ] || return 0
  if [ "${NARWHAL_ALLOW_S3_AUTH_CUTOVER:-}" = true ]; then
    echo "WARNING: NARWHAL_ALLOW_S3_AUTH_CUTOVER=true: enabling S3 auth while ${notconsuming} do not consume their S3 credential Secrets." >&2
    return 0
  fi
  echo "ERROR: existing SeaweedFS S3 has no -config; Loki/Tempo do not consume their S3 credential Secrets yet. Follow docs/runbooks/seaweedfs-s3-auth.md before enabling auth. Approved immediate cutover only: NARWHAL_ALLOW_S3_AUTH_CUTOVER=true." >&2
  return 1
}

# gitea_upsert_keycloak_source <gitea_pod> <client_secret> <discovery_url>
# D-GITEA-OAUTH (2026-10-27, #491): Gitea browser login uses Gitea's own 'keycloak' OAuth2
# source instead of X-WEBAUTH-USER reverse-proxy auth.
# Reason: once any user links to the source, DeleteSource refuses with ErrSourceInUse (and
# `gitea admin auth delete` only accepts --id, so the old `delete --name keycloak` never ran
# and `add-oauth` failed on every re-run with ErrSourceAlreadyExist, so a later secret rotation
# or group-map change could never be applied). Look up the source ID by exact Name=='keycloak'
# and update-oauth in place (passing --skip-local-2fa on every update because runUpdateOauth
# clears TwoFactorPolicy whenever that flag is absent), or add-oauth when absent. Never logs the
# client secret or discards gitea CLI stderr. --group-team-map-removal makes the team map
# two-way: on login a user is removed from a mapped team whose group it no longer has (only the
# teams named in the map are touched), so developer -> viewer in Keycloak drops write access.
# Cost: two kubectl exec calls per run (auth list + update-oauth/add-oauth); callers keep their
# existing WARN-and-continue behaviour when Gitea is not ready.
# Escape hatch: manual `gitea admin auth update-oauth --id <id> ...` inside the Gitea pod.
gitea_upsert_keycloak_source() {
  local pod="$1" secret="$2" discovery_url="$3" list_out id
  local -a common_flags=(
    --provider openidConnect
    --key gitea
    --secret "${secret}"
    --auto-discover-url "${discovery_url}"
    --group-claim-name groups
    --admin-group cluster-admin
    --restricted-group guest
    --group-team-map '{"developer":{"narwhal":["Developers"]},"viewer":{"narwhal":["Viewers"]}}'
    --group-team-map-removal
    --skip-local-2fa
    --scopes "openid profile email groups"
  )
  if ! list_out=$(kubectl exec -n devtools "${pod}" -- /app/gitea/gitea admin auth list); then
    echo "WARN: could not list Gitea auth sources in ${pod}" >&2
    return 1
  fi
  id=$(awk 'NR > 1 && $2 == "keycloak" && !found { print $1; found = 1 }' <<< "${list_out}")
  if [ -n "${id}" ]; then
    kubectl exec -n devtools "${pod}" -- \
      /app/gitea/gitea admin auth update-oauth --id "${id}" "${common_flags[@]}" \
      && echo "Gitea OAuth source 'keycloak' updated (id=${id})"
  else
    kubectl exec -n devtools "${pod}" -- \
      /app/gitea/gitea admin auth add-oauth --name keycloak "${common_flags[@]}" \
      && echo "Gitea OAuth source 'keycloak' created"
  fi
}
