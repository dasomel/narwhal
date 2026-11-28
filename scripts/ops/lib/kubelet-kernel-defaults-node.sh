#!/usr/bin/env bash
# Executed as root on the target node, locally or over SSH (CIS 4.2.6 protectKernelDefaults).
set -euo pipefail

config=/var/lib/kubelet/config.yaml
sysctl_file=/etc/sysctl.d/95-kubelet-kernel-defaults.conf
backup_dir=/var/lib/narwhal/kubelet-kernel-defaults
proc_root=/proc/sys

SYSCTL_PAIRS=(
  "vm.overcommit_memory=1"
  "vm.panic_on_oom=0"
  "kernel.panic=10"
  "kernel.panic_on_oops=1"
  "kernel.keys.root_maxkeys=1000000"
  "kernel.keys.root_maxbytes=25000000"
)

setting() {
  local value
  yq -e '.kind == "KubeletConfiguration" and .apiVersion == "kubelet.config.k8s.io/v1beta1"' "$config" >/dev/null
  value=$(yq -r '.protectKernelDefaults | select(tag == "!!bool" or tag == "!!null")' "$config")
  printf '%s\n' "${value/null/absent}"
}

verify_sysctls() {
  local pair key expected path actual bad=0
  for pair in "${SYSCTL_PAIRS[@]}"; do
    key="${pair%%=*}"; expected="${pair#*=}"
    path="$proc_root/${key//./\/}"
    if [[ ! -r "$path" ]]; then
      echo "sysctl unreadable: $path (expected $key=$expected)" >&2
      bad=1; continue
    fi
    actual=$(tr -d '[:space:]' < "$path")
    if [[ "$actual" != "$expected" ]]; then
      echo "sysctl mismatch: $key=$actual (expected $expected)" >&2
      bad=1
    fi
  done
  return "$bad"
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
  apply-sysctl)
    install -d -o root -g root -m 755 "$(dirname "$sysctl_file")"
    tmp=$(mktemp "$(dirname "$sysctl_file")/.95-kubelet-kernel-defaults.XXXXXXXX")
    trap 'rm -f "$tmp"' EXIT
    printf '%s' "$2" | base64 -d > "$tmp"
    chown root:root "$tmp"
    chmod 644 "$tmp"
    mv -f "$tmp" "$sysctl_file"
    sysctl -p "$sysctl_file" >/dev/null
    # Reload full sysctl.d precedence order so any higher-numbered override fails NOW rather than on reboot.
    sysctl --system >/dev/null
    verify_sysctls
    ;;
  is-active)
    systemctl is-active --quiet kubelet
    ;;
  set)
    # D-KUBELET-KERNEL-DEFAULTS: serialize edits, verify live sysctls before enabling,
    # and store backups outside kubelet's config directory.
    exec 9>/run/lock/narwhal-kubelet-kernel-defaults.lock
    flock 9
    [[ "$(setting)" == "$2" ]]
    if [[ "$3" == true ]]; then
      verify_sysctls
    fi
    backup=""
    if [[ "$2" != "$3" ]]; then
      install -d -o root -g root -m 700 "$backup_dir"
      backup=$(mktemp "$backup_dir/config.XXXXXXXX")
      install -o root -g root -m 600 "$config" "$backup"
      tmp=$(mktemp "${config}.XXXXXXXX")
      trap 'rm -f "$tmp"' EXIT
      case "$3" in
        absent) yq 'del(.protectKernelDefaults)' "$config" > "$tmp" ;;
        true|false) PROTECT="$3" yq '.protectKernelDefaults = env(PROTECT)' "$config" > "$tmp" ;;
        *) exit 2 ;;
      esac
      chown root:root "$tmp"
      chmod 600 "$tmp"
      mv -f "$tmp" "$config"
    fi
    timeout 60 systemctl restart kubelet
    systemctl is-active --quiet kubelet
    [[ "$(setting)" == "$3" ]]
    echo "${backup:-none}"
    ;;
  *) exit 2 ;;
esac
