#!/bin/bash
# Drive a full Narwhal bring-up on Kakao Cloud from the operator host.
#
# Vagrant gets its ordering from the Vagrantfile's provisioners; the cloud has no
# equivalent, so this is it. Every value the scripts need comes from OpenTofu state
# rather than being typed twice, and every step runs over the bastion — the nodes have
# no public IP and never talk to each other.
#
# Stages, in dependency order:
#   proxy   squid on the bastion + apt/shell/containerd proxy config (all nodes).
#           Not optional: the nodes have no public IP and there is no NAT gateway,
#           so base's first apt-get has nowhere to go until this runs.
#   registry airgap bundle -> registry:2 on the bastion. Also not optional: the mirror
#           stage points containerd at this registry, so something has to fill it.
#   base    01-prerequisites, 02-containerd, 03-k8s-install, 06-boot-heal-install (all nodes)
#   runtime 02-containerd + mirror (all nodes) — upgrade without redoing base
#   mirror  06-configure-mirrors --local-only (all nodes)
#   init    01-nfs-server, 02-init-cluster, 03-cni-install (master-1)
#   join    distribute artifacts, then control-planes serially, then workers
#   nfs     01-nfs-server (master-1) — re-export without re-running kubeadm init
#   phase1  04-addons, 05-nfs-quota-agent (master-1)
#   phase2  06-phase2-start (master-1) — runs the 18 platform scripts itself
#
# Usage (from the repo root):
#   ./scripts/cloud/provision-kakao.sh all
#   ./scripts/cloud/provision-kakao.sh phase2
#   ./scripts/cloud/provision-kakao.sh base mirror init
#
# Stages are idempotent: each records a sentinel under /home/vagrant/.narwhal-stage/
# on the node it ran against and is skipped on re-run unless FORCE=1.
set -euo pipefail

TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"
SSH_USER="${NODE_SSH_USER:-ubuntu}"
STAGE_DIR="${STAGE_DIR:-/home/vagrant}"
SENTINELS="${STAGE_DIR}/.narwhal-stage"
FORCE="${FORCE:-0}"
# kakao.*, not local.*: the Vagrant cluster serves the same service names, and both
# resolve only through /etc/hosts on the operator machine. Sharing one domain means the
# two clusters overwrite each other's entries and you silently browse the wrong one.
DOMAIN="${DOMAIN:-kakao.narwhal.internal}"

cd "$(dirname "$0")/../.."

[ $# -gt 0 ] || { echo "usage: $0 <proxy|registry|base|runtime|mirror|init|join|nfs|phase1|phase2|all>..." >&2; exit 1; }

# ── OpenTofu state is the single source of truth for every address ───────────────
BASTION_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_public_ip)
VIP=$(cd "${TF_DIR}" && tofu output -raw master_lb_vip)
APISIX_LB_IP=$(cd "${TF_DIR}" && tofu output -raw worker_lb_vip)
BASTION_PRIVATE_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_private_ip)
SUBNET_CIDR=$(cd "${TF_DIR}" && tofu output -raw subnet_cidr)
# The bootstrap registry runs on the bastion (04-bootstrap-registry.sh needs a
# container runtime, and a cluster node has only ctr).
AIRGAP_REGISTRY="${AIRGAP_REGISTRY:-${BASTION_PRIVATE_IP}:5000}"
SSH_KEY=$(cd "${TF_DIR}" && tofu output -raw ssh_key_path 2>/dev/null || true)
[ -n "${SSH_KEY}" ] || SSH_KEY="${TF_DIR}/KPAAS_KEYPAIR.pem"
case "${SSH_KEY}" in /*) ;; *) SSH_KEY="${TF_DIR}/${SSH_KEY#./}" ;; esac
[ -f "${SSH_KEY}" ] || { echo "ERROR: private key not found at ${SSH_KEY}" >&2; exit 1; }

read_ips() { printf '%s' "$1" | python3 -c 'import json,sys
for ip in json.load(sys.stdin): print(ip)'; }

MASTERS=(); WORKERS=()
while IFS= read -r _i; do [ -n "${_i}" ] && MASTERS+=("${_i}"); done \
  < <(read_ips "$(cd "${TF_DIR}" && tofu output -json master_private_ips)")
while IFS= read -r _i; do [ -n "${_i}" ] && WORKERS+=("${_i}"); done \
  < <(read_ips "$(cd "${TF_DIR}" && tofu output -json worker_private_ips)")
MASTER1="${MASTERS[0]}"
ALL_NODES=("${MASTERS[@]}" "${WORKERS[@]}")

# Derived from the private IPs so the scripts' ${MASTER_IP_BASE}${idx} arithmetic
# resolves to the addresses the compute module actually pinned.
MASTER_IP_BASE="${MASTER1%.*}.$(( ${MASTER1##*.} / 10 ))"
WORKER_IP_BASE="${WORKERS[0]%.*}.$(( ${WORKERS[0]##*.} / 10 ))"
MASTER_IPS="${MASTERS[*]:1}"

ssh_node() {
  local ip="$1"; shift
  ssh -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
    -o ConnectTimeout=25 -o ServerAliveInterval=30 \
    -o ProxyCommand="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -W %h:%p ${SSH_USER}@${BASTION_IP}" \
    "${SSH_USER}@${ip}" "$@"
}

# Cloud images keep their DHCP hostname (host-172-16-0-10), while the scripts default
# to the Vagrant naming. 05-nfs-quota-agent.sh reads MASTER_HOSTNAME and died with
# `nodes "narwhal-master" not found` until this was passed through.
MASTER_HOSTNAME="$(ssh_node "${MASTER1}" hostname | tr -d '\r')"

# sudo -E is ignored on these images ("preserving the entire environment is not
# supported"), so every variable is passed as an explicit `sudo env VAR=...`.
node_env() {
  printf '%s' "PROVIDER=kakao \
DOMAIN=${DOMAIN} \
VIP_ADDRESS=${VIP} \
MASTER_IP=${VIP} \
APISIX_LB_IP=${APISIX_LB_IP} \
MASTER_IP_BASE=${MASTER_IP_BASE} \
WORKER_IP_BASE=${WORKER_IP_BASE} \
MASTER_COUNT=${#MASTERS[@]} \
MASTER_IPS='${MASTER_IPS}' \
MASTER_HOSTNAME=${MASTER_HOSTNAME} \
NFS_SERVER_IP=${MASTER1} \
HOST_NETWORK_CIDR=${SUBNET_CIDR} \
AIRGAP_REGISTRY=${AIRGAP_REGISTRY} \
KUBECONFIG=${STAGE_DIR}/.kube/config-local"
}

stage_done() {
  local ip="$1" stage="$2"
  [ "${FORCE}" = "1" ] && return 1
  ssh_node "${ip}" "test -f '${SENTINELS}/${stage}'" 2>/dev/null
}

mark_done() {
  ssh_node "$1" "sudo mkdir -p '${SENTINELS}' && sudo touch '${SENTINELS}/$2'"
}

# Runs a list of repo-relative scripts on one node, streaming to a per-stage log the
# node keeps. Returns non-zero on the first failure so callers can stop.
run_scripts() {
  local ip="$1" stage="$2"; shift 2
  local list="$*"
  ssh_node "${ip}" "sudo mkdir -p '${SENTINELS}'
    sudo env $(node_env) sh -c '
      set -e
      for s in ${list}; do
        echo \"### \$s\"
        ${STAGE_DIR}/scripts/\$s || { echo \"FAILED: \$s\"; exit 1; }
      done' > ${STAGE_DIR}/${stage}.log 2>&1"
}

log()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }

tail_log() {
  ssh_node "$1" "tail -${3:-6} ${STAGE_DIR}/$2.log 2>/dev/null" | sed 's/^/     /'
}

# ── stages ──────────────────────────────────────────────────────────────────────
stage_proxy() {
  log "proxy: squid on the bastion, then point every node at it"
  # This has to come before `base`, and it is not optional: the nodes have no public
  # IP and provider 0.4.4 has no NAT gateway, so 01-prerequisites' first apt-get has
  # nowhere to go without it. It used to be two commands you were expected to know
  # about — the quick-start listed stage + provision and nothing else, which is a
  # clean install that cannot work.
  #
  # No sentinel: both scripts are cheap and idempotent, and squid is exactly the kind
  # of thing an unattended-upgrades run restarts out from under us, so re-asserting it
  # on every run is worth more than skipping it.
  if ! ./scripts/cloud/setup-bastion-proxy.sh; then
    note "bastion proxy FAILED"; return 1
  fi
  if ! ./scripts/cloud/configure-node-proxy.sh; then
    note "node proxy config FAILED"; return 1
  fi
  note "ok"
}

ssh_bastion() {
  ssh -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
    -o ConnectTimeout=25 -o ServerAliveInterval=30 \
    "${SSH_USER}@${BASTION_IP}" "$@"
}

stage_registry() {
  log "registry: airgap bundle -> registry:2 on the bastion (${AIRGAP_REGISTRY})"
  # The mirror stage points containerd at this registry; something has to have put the
  # images in it first. That was a manual sequence nobody wrote down — install docker,
  # copy 6.7 GB, bootstrap, load — so the documented quick-start could not actually
  # produce a working cluster from scratch.
  #
  # The bastion, not a node: 04-bootstrap-registry.sh needs docker or nerdctl and the
  # nodes have only ctr, and the bastion is the one host with public egress to install
  # docker from.
  # The bundle is arch-specific and the bastion is the machine that will serve it, so
  # ask the bastion rather than assuming the operator's laptop matches.
  local arch bundle remote_home
  arch=$(ssh_bastion "dpkg --print-architecture" | tr -d '\r')
  bundle="${AIRGAP_BUNDLE_DIR:-narwhal-airgap-bundle-${arch}}"
  remote_home="/home/${SSH_USER}"
  if [ ! -d "${bundle}/oci" ]; then
    note "no ${arch} bundle at ${bundle} — run scripts/airgap/01..03 first"; return 1
  fi

  if [ "${FORCE:-0}" != "1" ] \
    && ssh_bastion "curl -sf -o /dev/null http://127.0.0.1:5000/v2/" >/dev/null 2>&1; then
    note "registry already serving; skipping (FORCE=1 to redo)"
    return 0
  fi

  note "installing docker on the bastion"
  ssh_bastion "command -v docker >/dev/null 2>&1 || {
    sudo apt-get update -qq &&
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io >/dev/null
  }" || { note "docker install FAILED"; return 1; }

  local rsh="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

  # rsync rather than tar-over-ssh: several GB, and a re-run after an interrupted copy
  # should move only what is missing. COPYFILE_DISABLE stops macOS emitting ._* sidecars
  # — the same ones that corrupted the vendored charts in Gitea on 2026-07-27.
  note "syncing ${arch} bundle from ${bundle} (several GB)"
  COPYFILE_DISABLE=1 rsync -a -e "${rsh}" \
    "${bundle}/" "${SSH_USER}@${BASTION_IP}:${remote_home}/airgap-bundle/" \
    || { note "bundle sync FAILED"; return 1; }

  # rsync creates only the LAST component of the destination path, so scripts/airgap
  # fails with "No such file or directory" while airgap-bundle/ succeeds — the parent
  # has to exist first.
  note "syncing airgap scripts"
  ssh_bastion "mkdir -p ${remote_home}/scripts" \
    || { note "could not create ${remote_home}/scripts"; return 1; }
  COPYFILE_DISABLE=1 rsync -a -e "${rsh}" \
    scripts/airgap/ "${SSH_USER}@${BASTION_IP}:${remote_home}/scripts/airgap/" \
    || { note "script sync FAILED"; return 1; }

  note "bootstrapping registry:2"
  ssh_bastion "sudo env AIRGAP_REGISTRY=${AIRGAP_REGISTRY} \
    bash ${remote_home}/scripts/airgap/04-bootstrap-registry.sh \
    --addr ${AIRGAP_REGISTRY} --bundle ${remote_home}/airgap-bundle >/tmp/registry-boot.log 2>&1" \
    || { note "bootstrap FAILED"; ssh_bastion "tail -20 /tmp/registry-boot.log" | sed 's/^/     /'; return 1; }

  note "loading images"
  ssh_bastion "sudo env AIRGAP_REGISTRY=${AIRGAP_REGISTRY} \
    bash ${remote_home}/scripts/airgap/05-load-images.sh \
    --bundle ${remote_home}/airgap-bundle --registry ${AIRGAP_REGISTRY} >/tmp/registry-load.log 2>&1" \
    || { note "load FAILED"; ssh_bastion "tail -20 /tmp/registry-load.log" | sed 's/^/     /'; return 1; }

  note "ok — $(ssh_bastion "curl -s http://127.0.0.1:5000/v2/_catalog" | head -c 120)"
}

stage_base() {
  log "base: kernel prerequisites, containerd, kubeadm, boot-heal (${#ALL_NODES[@]} nodes)"
  for ip in "${ALL_NODES[@]}"; do
    if stage_done "${ip}" base; then note "${ip} skip (done)"; continue; fi
    if run_scripts "${ip}" base \
         common/01-prerequisites.sh common/02-containerd.sh \
         common/03-k8s-install.sh common/06-boot-heal-install.sh; then
      mark_done "${ip}" base; note "${ip} ok"
    else
      note "${ip} FAILED"; tail_log "${ip}" base; return 1
    fi
  done
}

stage_mirror() {
  log "mirror: containerd -> ${AIRGAP_REGISTRY}"
  for ip in "${ALL_NODES[@]}"; do
    ssh_node "${ip}" "sudo env AIRGAP_REGISTRY=${AIRGAP_REGISTRY} \
      ${STAGE_DIR}/scripts/airgap/06-configure-mirrors.sh --local-only >/tmp/mirror.log 2>&1"
    note "${ip} $(ssh_node "${ip}" "sudo grep -h '^\[host' /etc/containerd/certs.d/registry.k8s.io/hosts.toml" | tr -d '\r')"
  done
}

stage_init() {
  log "init: nfs-server, kubeadm init, CNI on master-1 (${MASTER1})"
  if stage_done "${MASTER1}" init; then note "skip (done)"; return 0; fi
  if run_scripts "${MASTER1}" init \
       cluster/01-nfs-server.sh cluster/02-init-cluster.sh cluster/03-cni-install.sh; then
    mark_done "${MASTER1}" init; note "ok"
  else
    note "FAILED"; tail_log "${MASTER1}" init 12; return 1
  fi
}

stage_join() {
  log "join: distribute artifacts, then control-planes serially, then workers"
  ./scripts/cloud/distribute-join-kakao.sh | sed 's/^/   /'
  # Control planes one at a time: two etcd members joining together can lose quorum.
  for ip in "${MASTERS[@]:1}"; do
    if stage_done "${ip}" join; then note "${ip} skip (done)"; continue; fi
    if run_scripts "${ip}" join cluster/02-join-control-plane.sh; then
      mark_done "${ip}" join; note "${ip} control-plane ok"
    else
      note "${ip} FAILED"; tail_log "${ip}" join; return 1
    fi
  done
  for ip in "${WORKERS[@]}"; do
    if stage_done "${ip}" join; then note "${ip} skip (done)"; continue; fi
    if run_scripts "${ip}" join cluster/02-join-worker.sh; then
      mark_done "${ip}" join; note "${ip} worker ok"
    else
      note "${ip} FAILED"; tail_log "${ip}" join; return 1
    fi
  done
}

# containerd only, without the rest of `base`. Re-running 02-containerd.sh rewrites
# config.toml from `containerd config default`, so the mirror stage has to follow it
# or /etc/containerd/certs.d stops being consulted.
stage_runtime() {
  log "runtime: containerd upgrade on ${#ALL_NODES[@]} nodes"
  for ip in "${ALL_NODES[@]}"; do
    ssh_node "${ip}" "sudo env $(node_env) ${STAGE_DIR}/scripts/common/02-containerd.sh >/tmp/containerd.log 2>&1"
    note "${ip} $(ssh_node "${ip}" "dpkg-query -W -f='\${Version}' containerd 2>/dev/null" | tr -d '\r')"
  done
  stage_mirror
}

# Separate from `init` on purpose: re-exporting NFS must not mean re-running
# kubeadm init. 01-nfs-server.sh is idempotent, so this is safe to repeat.
stage_nfs() {
  log "nfs: re-export /srv/nfs/k8s for ${SUBNET_CIDR} on master-1"
  ssh_node "${MASTER1}" "sudo env $(node_env) ${STAGE_DIR}/scripts/cluster/01-nfs-server.sh >/tmp/nfs.log 2>&1"
  note "exports: $(ssh_node "${MASTER1}" "sudo exportfs -s" | tr -d '\r' | tr '\n' ' ')"
}

stage_phase1() {
  log "phase1: addons, nfs-quota-agent (MASTER_HOSTNAME=${MASTER_HOSTNAME})"
  if stage_done "${MASTER1}" phase1; then note "skip (done)"; return 0; fi
  if run_scripts "${MASTER1}" phase1 cluster/04-addons.sh cluster/05-nfs-quota-agent.sh; then
    mark_done "${MASTER1}" phase1; note "ok"
  else
    note "FAILED"; tail_log "${MASTER1}" phase1 12; return 1
  fi
}

stage_phase2() {
  log "phase2: 06-phase2-start drives the 18 platform scripts"
  if stage_done "${MASTER1}" phase2; then note "skip (done)"; return 0; fi
  if run_scripts "${MASTER1}" phase2 cluster/06-phase2-start.sh; then
    mark_done "${MASTER1}" phase2; note "ok"
  else
    note "FAILED"; tail_log "${MASTER1}" phase2 20; return 1
  fi
}

# ── main ────────────────────────────────────────────────────────────────────────
echo "bastion=${BASTION_IP}  master-1=${MASTER1} (${MASTER_HOSTNAME})"
echo "VIP=${VIP}  apisix-lb=${APISIX_LB_IP}  registry=${AIRGAP_REGISTRY}"
echo "masters=${MASTERS[*]}  workers=${WORKERS[*]}"

# A failing stage has to make the whole run fail. The `&&` chain below is the last
# command in its case branch, so without this the for-loop simply moves on and the final
# `log "done"` exits 0 — the same silent-success bug `up.sh` shipped on 2026-06-17, where
# a clean-install failure reported rc=0 and nobody noticed until the cluster was probed.
run_stage() {
  if ! "$@"; then
    printf '\n\033[1;31m== FAILED: %s\033[0m\n' "${1#stage_}" >&2
    exit 1
  fi
}

for arg in "$@"; do
  case "${arg}" in
    proxy)  run_stage stage_proxy ;;
    registry) run_stage stage_registry ;;
    base)   run_stage stage_base ;;
    nfs)    run_stage stage_nfs ;;
    runtime) run_stage stage_runtime ;;
    mirror) run_stage stage_mirror ;;
    init)   run_stage stage_init ;;
    join)   run_stage stage_join ;;
    phase1) run_stage stage_phase1 ;;
    phase2) run_stage stage_phase2 ;;
    all)    for s in proxy registry base mirror init join nfs phase1 phase2; do
              run_stage "stage_${s}"
            done ;;
    *) echo "unknown stage: ${arg}" >&2; exit 1 ;;
  esac
done

log "done"
