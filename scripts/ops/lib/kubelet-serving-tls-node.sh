#!/usr/bin/env bash
# Executed as root on the target node, locally or over SSH.
set -euo pipefail

config=/var/lib/kubelet/config.yaml

setting() {
  local value
  yq -e '.kind == "KubeletConfiguration" and .apiVersion == "kubelet.config.k8s.io/v1beta1"' "$config" >/dev/null
  value=$(yq -r '.serverTLSBootstrap | select(tag == "!!bool" or tag == "!!null")' "$config")
  printf '%s\n' "${value/null/absent}"
}

case "$1" in
  read)
    subject=$(openssl x509 -in /var/lib/kubelet/pki/kubelet-client-current.pem -noout -subject -nameopt RFC2253)
    case "${subject#subject=}" in
      "CN=system:node:$2,O=system:nodes"|"O=system:nodes,CN=system:node:$2") ;;
      *) exit 1 ;;
    esac
    setting
    ;;
  peer)
    systemctl is-active --quiet kubelet
    # The client has no kubelet credentials. A successful TLS handshake suffices
    # to return the leaf for verification by the caller.
    timeout 5 openssl s_client -connect "$2:10250" </dev/null 2>/dev/null |
      openssl x509 -outform PEM
    ;;
  set)
    # D4: serialize file edits and compare the field just read. Cost: a
    # concurrent change fails for re-inspection. Escape hatch: re-run after it.
    exec 9>/run/lock/narwhal-kubelet-serving-tls.lock
    flock 9
    [[ "$(setting)" == "$2" ]]
    if [[ "$2" != "$3" ]]; then
      tmp="$(mktemp "${config}.XXXXXXXX")"
      trap 'rm -f "$tmp"' EXIT
      case "$3" in
        absent) yq 'del(.serverTLSBootstrap)' "$config" > "$tmp" ;;
        true|false) BOOTSTRAP="$3" yq '.serverTLSBootstrap = env(BOOTSTRAP)' "$config" > "$tmp" ;;
        *) exit 2 ;;
      esac
      chmod 600 "$tmp"
      chown root:root "$tmp"
      mv -f "$tmp" "$config"
    fi
    timeout 60 systemctl restart kubelet
    ;;
  *) exit 2 ;;
esac
