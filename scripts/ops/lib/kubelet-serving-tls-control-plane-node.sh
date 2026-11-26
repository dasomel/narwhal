#!/usr/bin/env bash
# Stage 3 renderer and root-side static Pod operations (local or over SSH).
set -euo pipefail

export PATH="${PATH}:/usr/local/bin"
ca=/etc/kubernetes/pki/ca.crt
ca_name=kubelet-certificate-authority
rotate=RotateKubeletServerCertificate=true
manifests=/etc/kubernetes/manifests
backup=/var/lib/narwhal/kubelet-serving-tls/kube-apiserver.pre-ca.yaml

die() { echo "ERROR: $*" >&2; exit 1; }

desired_gates() {
  local values="$1" gate found=false
  local -a entries=()
  IFS=, read -r -a entries <<< "$values"
  for gate in "${entries[@]+"${entries[@]}"}"; do
    [[ "$gate" == RotateKubeletServerCertificate=* ]] || continue
    [[ "$gate" == "$rotate" && "$found" == false ]] || die "conflicting RotateKubeletServerCertificate gate"
    found=true
  done
  if [[ "$found" == false ]]; then
    values="${values:+$values,}$rotate"
  fi
  printf '%s\n' "$values"
}

config_args() {
  yq -e '.kind == "ClusterConfiguration" and .apiVersion == "kubeadm.k8s.io/v1beta4"' "$1" >/dev/null || return 1
  yq -o=json -I=0 '.apiServer.extraArgs // []' "$1" |
    jq -ce --arg name "$ca_name" '[.[] | select(.name == $name) | .value]'
}

render_config() {
  local target="$1" file="$2" ca_args gates existing="" wanted
  ca_args=$(config_args "$file") || die "cannot read kubeadm ClusterConfiguration"
  check_ca_args "$ca_args"
  if [[ "$target" == false && "$ca_args" == "[]" ]]; then
    cat "$file"
  elif [[ "$target" == false ]]; then
    NAME="$ca_name" yq 'del(.apiServer.extraArgs[] | select(.name == strenv(NAME)))' "$file"
  else
    gates=$(yq -o=json -I=0 '.controllerManager.extraArgs // []' "$file" |
      jq -ce '[.[] | select(.name == "feature-gates") | .value]')
    if [[ "$gates" != "[]" ]]; then
      existing=$(jq -er 'select(length == 1) | .[0] | strings' <<< "$gates") ||
        die "expected a single feature-gates entry in kubeadm-config"
    fi
    wanted=$(desired_gates "$existing")
    if [[ "$ca_args" != "[]" && "$wanted" == "$existing" ]]; then
      cat "$file"
      return
    fi
    NAME="$ca_name" CA="$ca" GATES="$wanted" yq '
      .apiServer.extraArgs |= ((. // []) | map(select(.name != strenv(NAME))) +
        [{"name": strenv(NAME), "value": strenv(CA)}]) |
      .controllerManager.extraArgs |= ((. // []) | map(select(.name != "feature-gates")) +
        [{"name": "feature-gates", "value": strenv(GATES)}])' "$file"
  fi
}

check_ca_args() {
  [[ "$1" == '[]' || "$1" == "[\"$ca\"]" ]] || die "conflicting --$ca_name; inspect before changing it"
}

pod_command() {
  COMPONENT="$1" yq -o=json -I=0 '.spec.containers[] | select(.name == strenv(COMPONENT)) | .command' "$2" |
    jq -ce --arg component "$1" 'select(.[0] == $component)'
}

flag_values() {
  jq -ce --arg name "--$1" '
    [.[] | select(. == $name or startswith($name + "=")) | ltrimstr($name) | ltrimstr("=")]' <<< "$2"
}

render_pod() {
  local mode="$1" component="$2" file="$3" command args existing="" gates preferred
  command=$(pod_command "$component" "$file") || die "cannot read $file command"
  if [[ "$component" == kube-controller-manager ]]; then
    args=$(flag_values feature-gates "$command")
    if [[ "$args" != "[]" ]]; then
      existing=$(jq -er 'select(length == 1) | .[0] | select(length > 0)' <<< "$args") ||
        die "expected a single --feature-gates=value argument in $file"
    fi
    gates=$(desired_gates "$existing")
    if [[ "$existing" == "$gates" ]]; then
      cat "$file"
    else
      GATES="--feature-gates=$gates" yq '(.spec.containers[] | select(.name == "kube-controller-manager")).command |=
        (map(select(test("^--feature-gates=") | not)) + [strenv(GATES)])' "$file"
    fi
    return
  fi

  args=$(flag_values "$ca_name" "$command")
  check_ca_args "$args"
  if [[ "$mode" == true ]]; then
    if ! preferred=$(flag_values kubelet-preferred-address-types "$command" | jq -er 'select(length == 1) | .[0]') ||
      [[ "${preferred%%,*}" != InternalIP ]]; then
      die "expected kube-apiserver to prefer the verified InternalIP addresses"
    fi
    if [[ "$args" == "[]" ]]; then
      FLAG="--$ca_name=$ca" yq '(.spec.containers[] | select(.name == "kube-apiserver")).command += [strenv(FLAG)]' "$file"
      return
    fi
  elif [[ "$args" != "[]" ]]; then
    if [[ -s "$backup" ]] && render_pod true "$component" "$backup" | cmp -s "$file" -; then
      cat "$backup"
    else
      # D-R416-RESTORE: preserve kubeadm/OIDC updates on joined or edited masters.
      # Cost: surgical removal. Escape: the saved manifest remains on disk.
      FLAG="--$ca_name=$ca" yq '(.spec.containers[] | select(.name == "kube-apiserver")).command |=
        map(select(. != strenv(FLAG)))' "$file"
    fi
    return
  fi
  cat "$file"
}

local_kube() {
  kubectl --kubeconfig=/etc/kubernetes/admin.conf --server="https://$ip:6443" --request-timeout=5s "$@"
}

wait_pod() {
  local component="$1" manifest="$2" deadline=$((SECONDS + limit)) expected pod
  expected=$(pod_command "$component" "$manifest") || return 1
  # D-R416-SERIAL: match the new local mirror command and Ready, since the old
  # API process can answer /readyz while kubelet has yet to pick up the rename.
  # Cost: wait one restart per changed Pod. Escape: restore before continuing.
  while ((SECONDS < deadline)); do
    if local_kube get --raw /readyz >/dev/null 2>&1 &&
      pod=$(local_kube -n kube-system get pod "$component-$node" -o json 2>/dev/null) &&
      jq -e --arg name "$component" --argjson command "$expected" '
        .metadata.deletionTimestamp == null and
        any(.status.conditions[]?; .type == "Ready" and .status == "True") and
        any(.spec.containers[]; .name == $name and .command == $command)' <<< "$pod" >/dev/null; then
      echo "$node: $component Ready with expected command"
      return 0
    fi
    sleep 2
  done
  echo "ERROR: $node: timed out waiting for $component" >&2
  return 1
}

write_pod() {
  local component="$1" file="$manifests/$1.yaml" candidate="$work/$1"
  local stage
  cp -p "$file" "$work/previous"
  render_pod "$target" "$component" "$work/previous" > "$candidate"
  if cmp -s "$work/previous" "$candidate"; then
    wait_pod "$component" "$file"
    return
  fi
  cmp -s "$work/previous" "$file" || die "$node: $file changed during rollout"
  if [[ "$component" == kube-apiserver && "$target" == true ]]; then
    install -d -m 700 "${backup%/*}"
    install -m 600 "$work/previous" "$backup"
    echo "$node: saved $backup"
  fi
  stage=$(mktemp "$manifests/.$component.XXXXXXXX")
  install -m 600 "$candidate" "$stage"
  mv -f "$stage" "$file"
  if ! wait_pod "$component" "$file"; then
    cmp -s "$candidate" "$file" || die "$node: $file changed during restart; inspect before restoring"
    echo "$node: restoring previous $component manifest" >&2
    install -m 600 "$work/previous" "$stage"
    mv -f "$stage" "$file"
    wait_pod "$component" "$file" || echo "ERROR: $node: recovery did not settle" >&2
    return 1
  fi
}

case "$1" in
  config) render_config "$2" "$3" ;;
  guard-config)
    [[ "$(config_args "$2")" == "[]" ]] || die 'run --control-plane --rollback before kubelet rollback'
    ;;
  verify)
    shift
    # D-R416-VERIFY: use the real API-server trust root from each control plane.
    # Cost: one handshake per node pair. Escape: fix issuance or reachability
    # and re-run while the API-server manifest still uses fallback TLS.
    for ip in "$@"; do
      timeout 5 openssl s_client -connect "$ip:10250" -purpose sslserver \
        -verifyCAfile "$ca" -verify_ip "$ip" -verify_return_error -brief </dev/null >/dev/null ||
        die "$(hostname): $ip:10250 failed kubelet TLS validation"
      echo "$(hostname): $ip:10250 verifies against $ca with its IP SAN"
    done
    ;;
  plan)
    if [[ "$2" == true ]]; then
      render_pod true kube-controller-manager "$manifests/kube-controller-manager.yaml" >/dev/null
    fi
    render_pod "$2" kube-apiserver "$manifests/kube-apiserver.yaml" >/dev/null
    echo "$3: plan API server CA enforcement=$2"
    ;;
  guard)
    node="$2" ip="$3" limit="$4"
    file="$manifests/kube-apiserver.yaml"
    command=$(pod_command kube-apiserver "$file")
    [[ "$(flag_values "$ca_name" "$command")" == "[]" ]] ||
      die "$node: run --control-plane --rollback before kubelet rollback"
    wait_pod kube-apiserver "$file"
    ;;
  write)
    target="$2" node="$3" ip="$4" limit="$5"
    exec 9>/run/lock/narwhal-kubelet-serving-tls-control-plane.lock
    flock 9
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
    if [[ "$target" == true ]]; then
      write_pod kube-controller-manager
    fi
    write_pod kube-apiserver
    ;;
  *) exit 2 ;;
esac
