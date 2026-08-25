#!/usr/bin/env bash
set -euo pipefail

MODE="all"
TEMPLATE=""
RUNTIME_CONTAINER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="${2:?--mode requires a value}"; shift 2 ;;
    --template) TEMPLATE="${2:?--template requires a chart path}"; shift 2 ;;
    *) echo "ERROR: unknown Keycloak adapter argument: $1" >&2; exit 2 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
DEFAULT_TEMPLATE="${REPO_ROOT}/gitops/charts/narwhal-platform"
if [ -z "${TEMPLATE}" ]; then
  TEMPLATE="${DEFAULT_TEMPLATE}"
fi

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $1" >&2
    exit 1
  }
}

assert_yq() {
  local expression="$1" expected="$2" actual
  actual="$(yq eval "${expression}" "$3")"
  if [ "${actual}" != "${expected}" ]; then
    printf 'ERROR: Keycloak desired-state mismatch: %s expected %s, got %s\n' \
      "${expression}" "${expected}" "${actual}" >&2
    exit 1
  fi
}

render_contract() {
  require_command helm
  require_command yq
  [ -f "${TEMPLATE}/Chart.yaml" ] || {
    echo "ERROR: --template is not a Helm chart: ${TEMPLATE}" >&2
    exit 1
  }
  local rendered keycloak_cr chaos_file catalog_file catalog_target catalog_namespace catalog_pod catalog_component
  rendered="$(mktemp)"
  keycloak_cr="$(mktemp)"
  trap 'rm -f "${rendered}" "${keycloak_cr}"' RETURN
  helm template narwhal-platform "${TEMPLATE}" --set baseDomain=t2.narwhal.test > "${rendered}"
  yq eval 'select(.apiVersion == "k8s.keycloak.org/v2alpha1" and .kind == "Keycloak" and .metadata.name == "keycloak")' \
    "${rendered}" > "${keycloak_cr}"
  [ -s "${keycloak_cr}" ] || { echo "ERROR: rendered chart has no Keycloak CR" >&2; exit 1; }

  catalog_file="${REPO_ROOT}/docs/common/failure-injection-catalog.md"
  if ! catalog_target="$(awk -F '|' '
    $2 ~ /`keycloak-kill`/ {
      scenario = $2
      target = $3
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", scenario)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", target)
      if (scenario == "`keycloak-kill`") {
        scenarios++
        if (split(target, parts, "/") == 2 && parts[1] != "" && parts[2] != "") {
          print target
          matches++
        }
      }
    }
    END { exit scenarios == 1 && matches == 1 ? 0 : 1 }
  ' "${catalog_file}")"; then
    echo "ERROR: failure-injection catalog has no single well-formed keycloak-kill row" >&2
    exit 1
  fi
  catalog_namespace="${catalog_target%%/*}"
  catalog_pod="${catalog_target#*/}"
  catalog_component="${catalog_pod%-0}"

  assert_yq '.metadata.namespace' "${catalog_namespace}" "${keycloak_cr}"
  assert_yq '.metadata.name' "${catalog_component}" "${keycloak_cr}"
  assert_yq '.apiVersion' 'k8s.keycloak.org/v2alpha1' "${keycloak_cr}"
  assert_yq '.spec.hostname.hostname' 'https://keycloak.t2.narwhal.test' "${keycloak_cr}"
  assert_yq '.spec.db.usernameSecret.name' 'keycloak-db-secret' "${keycloak_cr}"
  assert_yq '.spec.db.passwordSecret.name' 'keycloak-db-secret' "${keycloak_cr}"
  assert_yq '.spec.resources.requests.cpu' '600m' "${keycloak_cr}"
  assert_yq '.spec.unsupported.podTemplate.metadata.labels."istio.io/dataplane-mode"' 'none' "${keycloak_cr}"
  assert_yq '.spec.unsupported.podTemplate.spec.containers[] | select(.name == "keycloak") | .livenessProbe.httpGet.path' '/health/live' "${keycloak_cr}"
  assert_yq '.spec.unsupported.podTemplate.spec.containers[] | select(.name == "keycloak") | .readinessProbe.httpGet.path' '/health/ready' "${keycloak_cr}"
  assert_yq '.spec.unsupported.podTemplate.spec.containers[] | select(.name == "keycloak") | .startupProbe.httpGet.path' '/health/started' "${keycloak_cr}"
  assert_yq '.spec.unsupported.podTemplate.spec.containers[] | select(.name == "keycloak") | .volumeMounts[] | select(.mountPath == "/opt/keycloak/themes/narwhal/login/theme.properties") | .mountPath' '/opt/keycloak/themes/narwhal/login/theme.properties' "${keycloak_cr}"

  chaos_file="${REPO_ROOT}/tests/chaos/experiments/keycloak-kill.yaml"
  assert_yq '.spec.selector.namespaces[0]' "${catalog_namespace}" "${chaos_file}"
  assert_yq '.spec.selector.labelSelectors.app' "${catalog_component}" "${chaos_file}"
  echo "PASS T2 Keycloak render: offline desired-state contract (Helm render + Keycloak CR + catalog-linked Chaos selector)"
  echo "LIMITATION: this does not apply CRDs, start an Operator, or execute Chaos Mesh; it is not a live-cluster integration result."
}

runtime_contract() {
  require_command docker
  require_command curl
  require_command openssl
  require_command python3
  require_command yq
  local image keycloak_version admin_password realm client username user_password port health_port token payload
  keycloak_version="$(awk -F ':-' '/^KEYCLOAK_VERSION=/{value=$2; sub(/[}"].*$/, "", value); print value; exit}' \
    "${REPO_ROOT}/scripts/cluster/11-keycloak.sh")"
  [ -n "${keycloak_version}" ] || { echo "ERROR: cannot read KEYCLOAK_VERSION from 11-keycloak.sh" >&2; exit 1; }
  image="quay.io/keycloak/keycloak:${keycloak_version}"
  grep -qF "| Keycloak | ${keycloak_version} |" "${REPO_ROOT}/VERSIONS.md" || {
    echo "ERROR: VERSIONS.md does not confirm Keycloak ${keycloak_version}" >&2
    exit 1
  }
  grep -qxF "${image}" "${REPO_ROOT}/scripts/airgap/images.txt" || {
    echo "ERROR: airgap image list does not contain ${image}" >&2
    exit 1
  }
  if ! python3 -c "import subprocess; subprocess.run(['docker', 'info'], timeout=3, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)" 2>/dev/null; then
    echo "ERROR: T2 Keycloak runtime requires a running Docker daemon; Docker daemon is unresponsive or not running." >&2
    exit 1
  fi
  if ! docker image inspect "${image}" >/dev/null 2>&1; then
    echo "ERROR: T2 Keycloak runtime requires local image ${image}; image pulls are intentionally disabled." >&2
    exit 1
  fi
  port="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
PY
)"
  health_port="$(python3 - <<'PY'
import socket
s = socket.socket()
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
PY
)"
  admin_password="$(openssl rand -hex 24)"
  realm="t2-$(openssl rand -hex 6)"
  client="t2-client-$(openssl rand -hex 6)"
  username="t2-user-$(openssl rand -hex 6)"
  user_password="$(openssl rand -hex 24)"
  RUNTIME_CONTAINER="t2-keycloak-${realm}"
  cleanup_runtime() {
    [ -z "${RUNTIME_CONTAINER}" ] || docker rm -f "${RUNTIME_CONTAINER}" >/dev/null 2>&1 || true
  }
  trap cleanup_runtime EXIT
  docker run -d --rm --name "${RUNTIME_CONTAINER}" -p "127.0.0.1:${port}:8080" -p "127.0.0.1:${health_port}:9000" \
    -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD="${admin_password}" \
    "${image}" start-dev --http-port=8080 --health-enabled=true --hostname-strict=false >/dev/null
  local attempt=1
  while [ "${attempt}" -le 60 ]; do
    if curl --fail --silent --show-error "http://127.0.0.1:${health_port}/health/ready" >/dev/null; then break; fi
    sleep 1
    attempt=$((attempt + 1))
  done
  curl --fail --silent --show-error "http://127.0.0.1:${health_port}/health/ready" >/dev/null
  docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh config credentials \
    --server http://localhost:8080 --realm master --user admin --password "${admin_password}" >/dev/null
  docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh create realms -s "realm=${realm}" -s enabled=true >/dev/null
  docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh create clients -r "${realm}" \
    -s "clientId=${client}" -s enabled=true -s publicClient=true -s directAccessGrantsEnabled=true >/dev/null
  local client_id group_id user_id
  client_id="$(docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh get clients -r "${realm}" -q "clientId=${client}" --fields id -F id)"
  docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh create groups -r "${realm}" -s name=platform-team >/dev/null
  group_id="$(docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh get groups -r "${realm}" -q search=platform-team --fields id -F id)"
  docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh create users -r "${realm}" -s "username=${username}" -s enabled=true >/dev/null
  user_id="$(docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh get users -r "${realm}" -q "username=${username}" --fields id -F id)"
  docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh set-password -r "${realm}" --userid "${user_id}" --new-password "${user_password}" >/dev/null
  docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh update "users/${user_id}/groups/${group_id}" -r "${realm}" -n >/dev/null
  docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh create "clients/${client_id}/protocol-mappers/models" -r "${realm}" \
    -s name=groups -s protocol=openid-connect -s protocolMapper=oidc-group-membership-mapper \
    -s 'config."claim.name"=groups' -s 'config."full.path"=false' -s 'config."access.token.claim"=true' >/dev/null
  docker exec "${RUNTIME_CONTAINER}" /opt/keycloak/bin/kcadm.sh create "clients/${client_id}/protocol-mappers/models" -r "${realm}" \
    -s name=audience -s protocol=openid-connect -s protocolMapper=oidc-audience-mapper \
    -s "config.\"included.client.audience\"=${client}" -s 'config."access.token.claim"=true' >/dev/null
  token="$(curl --fail --silent --show-error -X POST "http://127.0.0.1:${port}/realms/${realm}/protocol/openid-connect/token" \
    --data-urlencode grant_type=password --data-urlencode "client_id=${client}" \
    --data-urlencode "username=${username}" --data-urlencode "password=${user_password}" | yq -r '.access_token')"
  payload="$(python3 - "${token}" "${client}" <<'PY'
import base64
import json
import sys

token, client = sys.argv[1:]
payload = token.split('.')[1] + '=' * (-len(token.split('.')[1]) % 4)
claims = json.loads(base64.urlsafe_b64decode(payload))
assert claims.get('groups') == ['platform-team'], claims
aud = claims.get('aud')
assert aud == client or client in aud, claims
print('claims verified')
PY
)"
  echo "PASS T2 Keycloak runtime: ${payload}; ephemeral realm/client/user token has bare groups and client audience"
  echo "LIMITATION: local start-dev on Docker's default bridge validates Keycloak's API/token behavior only; it does not prove offline network isolation, or validate the Operator, PostgreSQL, Kubernetes, Istio, or Chaos Mesh."
  cleanup_runtime
  RUNTIME_CONTAINER=""
  trap - EXIT
}

case "${MODE}" in
  render) render_contract ;;
  runtime) runtime_contract ;;
  all) render_contract; runtime_contract ;;
  *) echo "ERROR: invalid mode: ${MODE}" >&2; exit 2 ;;
esac
