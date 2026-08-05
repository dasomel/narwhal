#!/bin/bash
# Resilient vagrant up wrapper for Narwhal IDP.
#
# Handles the intermittent VMware Fusion guest-communication races:
#   1. SSH key-replacement during boot fails; `vagrant up` aborts the VM
#      mid-sequence, leaving it "running" but un-provisioned. A plain
#      `vagrant up` retry re-runs the SAME flaky boot-time SSH check and
#      aborts again — so it never reaches the later VMs (workers).
#   2. The "Configuring network adapters" race that leaves a VM "not created".
#
# Strategy — per-VM, not whole-fleet:
#   * "not created" VM   -> `vagrant up <vm>`        (boot + provision)
#   * "running" but not a Ready k8s node -> `vagrant provision <vm>`
#     (skips the flaky boot-time SSH setup; just runs the — idempotent —
#      shell provisioners, which complete whatever was left half-done).
#   * "running" AND a Ready node -> skip.
#
# The provisioning scripts are idempotent (02-init-cluster.sh skips when
# manifests exist; 02-join-{worker,control-plane}.sh skip when kubelet/admin
# conf exist), so re-provisioning converges the cluster.
#
# Usage: ./scripts/up.sh
set -euo pipefail

PROVIDER="${PROVIDER:-vmware_desktop}"

# Timestamped progress line. vagrant's own output carries no clock, which is what made the
# 2026-08-04 SSH drop uncorrelatable against the guests' journals — the drop was visible in
# this log and invisible in time. Milestones this script prints go through say() so the next
# occurrence can be lined up with `journalctl --since/--until` on the VM.
say() { echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*"; }

MAX_ATTEMPTS="${MAX_ATTEMPTS:-6}"
EXPECTED_NODES="${EXPECTED_NODES:-6}"
ALL_VMS="master-1 master-2 master-3 worker-1 worker-2 worker-3"
# Seconds to wait between attempts — lets the VMware Fusion SSH key-replacement
# race settle. Some VMs become SSH-able only a couple of minutes after boot;
# without this delay the retry budget burns out while they're still racing.
SETTLE_DELAY="${SETTLE_DELAY:-90}"

cd "$(dirname "$0")/.."

# vm_state <name>: prints vagrant's state word for one VM (running|not_created|...).
vm_state() {
  vagrant status "$1" 2>/dev/null \
    | awk -v n="$1" '$1 == n {print $2}'
}

# master1_exec: run a command on master-1 over the HOST-ONLY address, falling back to
# `vagrant ssh`.
#
# `vagrant ssh` is not a reliable way to reach a specific VM here. The VMware provider
# derives each machine's forwarded SSH port from its NAT address, and VMware's NAT DHCP
# hands the same address to two different MACs often enough to matter — measured 2026-08-05
# on a live fleet: master-2 and worker-3 both on 172.16.221.135, both forwarded from host
# port 2204; master-1 and worker-2 both on .133 / port 2203. Whichever VM wins the ARP race
# answers, so a connection meant for one lands on the other, whose key does not match. That
# is the `SSH authentication failed` that has been interrupting one machine per run.
#
# The readiness probe below is the expensive victim: it reported 0 Ready nodes for a cluster
# with 5 Ready nodes, and this script duly "recovered" a healthy master-1 with a reload.
# 192.168.56.10 is static, unique per VM and never shared, so none of that applies.
MASTER1_IP="${MASTER1_IP:-192.168.56.10}"
MASTER1_KEY=".vagrant/machines/master-1/vmware_desktop/private_key"
master1_exec() {
  local out=""
  if [ -r "${MASTER1_KEY}" ]; then
    out=$(ssh -n -i "${MASTER1_KEY}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -o LogLevel=ERROR -o ConnectTimeout=10 "vagrant@${MASTER1_IP}" "$1" 2>/dev/null || true)
    if [ -n "${out}" ]; then printf '%s\n' "${out}" | tr -d '\r'; return 0; fi
  fi
  vagrant ssh master-1 -c "$1" 2>/dev/null | tr -d '\r' || true
}

# k8s_ready_nodes: prints Ready node short-names (narwhal- prefix stripped),
# one per line. Empty if the API is unreachable.
k8s_ready_nodes() {
  master1_exec "kubectl get nodes --no-headers 2>/dev/null | awk '\$2==\"Ready\"{print \$1}'" \
    | sed 's/narwhal-//' || true
}

# master1_ssh_ok: returns 0 if master-1 responds to SSH, 1 otherwise.
# No `| grep -q`: it closes the pipe early and pipefail turns the upstream's SIGPIPE into a
# false negative (see the CLAUDE.md shell rule).
master1_ssh_ok() {
  case "$(master1_exec 'echo SSH_OK')" in *SSH_OK*) return 0 ;; *) return 1 ;; esac
}

# nat_ip_duplicates: prints "<ip>: vm vm" for every NAT address held by more than one VM.
# Read from vmrun rather than `vagrant ssh-config`, which reports the forwarded port and so
# inherits the very confusion being detected.
nat_ip_duplicates() {
  local vm vmx ip
  for vm in ${ALL_VMS}; do
    vmx=$(find ".vagrant/machines/${vm}" -name '*.vmx' 2>/dev/null | awk '!seen {print; seen=1}')
    [ -n "${vmx}" ] || continue
    ip=$(vmrun getGuestIPAddress "${vmx}" 2>/dev/null || true)
    case "${ip}" in [0-9]*) echo "${ip} ${vm}" ;; esac
  done | awk '{a[$1]=a[$1]" "$2; n[$1]++} END {for (k in a) if (n[k]>1) print k ":" a[k]}'
}

# repair_nat_duplicates: reboot one VM out of each colliding pair so DHCP gives it a fresh
# address. A reload is what actually moves it — an in-guest `networkctl renew` does not, the
# server just re-offers the same lease to the same MAC (measured). Reboot the worker where
# there is one, so a quorate control plane is left alone.
repair_nat_duplicates() {
  local line ip vms victim
  while IFS= read -r line; do
    [ -n "${line}" ] || continue
    ip="${line%%:*}"; vms="${line#*:}"
    victim=""
    for v in ${vms}; do case "${v}" in worker-*) victim="${v}" ;; esac; done
    [ -n "${victim}" ] || victim=$(printf '%s\n' ${vms} | awk '!seen {print; seen=1}')
    say "  duplicate NAT address ${ip} held by${vms} — reloading ${victim} to force a new lease"
    vagrant reload "${victim}" --no-provision || true
  done <<EOF
$(nat_ip_duplicates)
EOF
}

# recover_master1_ssh: two-stage recovery for master-1 SSH.
#   Stage 1 — light: poll up to ~2 min (8×15s) without reloading; the key-
#             replacement race sometimes resolves on its own within seconds.
#   Stage 2 — reload: if still unreachable, run `vagrant reload master-1`
#             (reboots the VM and re-syncs the key), then poll up to ~10 min
#             (40×15s). Safe in a 3-master HA cluster because the control plane
#             remains quorate during a single-master reboot.
# Returns 0 on success, 1 on timeout.
# ROOT CAUSE (2026-08-05): the "SSH drop" was never a drop. VMware's NAT DHCP gives the same
# address to two different MACs, and the VMware provider derives the forwarded SSH port from
# that address — so two machines end up sharing both, and `vagrant ssh <a>` reaches <b>,
# whose key does not match. Measured on a live fleet with distinct MACs: master-2 + worker-3
# on 172.16.221.135 / port 2204, master-1 + worker-2 on .133 / port 2203.
#
# That explains every earlier dead end, all of which correctly cleared their suspect on
# 2026-08-04: sshd never killed the session; no OOM; the host never suspended; and the lease
# renews without changing the address — true, and beside the point, because the failure is
# two VMs sharing one address, not one VM's address changing. It landed at a different step
# each run because it depends on which machine boots into the collision, not on what the
# provisioner was doing.
#
# nat_ip_duplicates()/repair_nat_duplicates() above now detect and clear it at the top of
# each attempt, and master1_exec() routes around it entirely for the readiness probe. This
# recovery path is kept for genuine boot-time key races.
recover_master1_ssh() {
  # Stage 1: light poll — no reboot
  say "master-1 SSH unreachable — light recovery: polling up to ~2 min before reload"
  local i=0
  while [ "${i}" -lt 8 ]; do
    i=$((i + 1))
    echo "  SSH light-poll ${i}/8 ..."
    if master1_ssh_ok; then
      echo "master-1 SSH recovered (light poll, ${i} polls)."
      return 0
    fi
    sleep 15
  done

  # Stage 2: reload fallback — reboot + re-sync key
  echo "master-1 SSH still unreachable — running vagrant reload master-1 to clear VMware key-race"
  vagrant reload master-1 || true
  i=0
  while [ "${i}" -lt 40 ]; do
    i=$((i + 1))
    echo "  SSH reload-poll ${i}/40 ..."
    if master1_ssh_ok; then
      echo "master-1 SSH recovered after reload (${i} polls)."
      return 0
    fi
    sleep 15
  done
  echo "ERROR: master-1 SSH did not recover after ~10 minutes post-reload." >&2
  return 1
}

attempt=1
while [ "${attempt}" -le "${MAX_ATTEMPTS}" ]; do
  say "=== attempt ${attempt}/${MAX_ATTEMPTS} ==="

  # Before touching any VM: if two of them share a NAT address, every `vagrant ssh` and
  # `vagrant provision` below is a coin flip on which machine it reaches. Fix that first,
  # otherwise the loop "repairs" whichever VM happened to answer.
  dups=$(nat_ip_duplicates || true)
  if [ -n "${dups}" ]; then
    say "duplicate NAT addresses detected — vagrant would reach the wrong VM:"
    printf '  %s\n' ${dups}
    repair_nat_duplicates
  fi

  ready_nodes=$(k8s_ready_nodes)

  for vm in ${ALL_VMS}; do
    state=$(vm_state "${vm}")
    if grep -qx "${vm}" <<<"${ready_nodes}"; then
      continue  # already a Ready k8s node
    fi
    case "${state}" in
      running)
        # `vagrant provision` skips the flaky boot-time SSH setup. If it fails
        # (VM's SSH still racing), `vagrant reload --provision` reboots the
        # guest — a clean boot usually clears the key-replacement race.
        echo "  ${vm}: running but not Ready -> vagrant provision"
        if ! vagrant provision "${vm}"; then
          echo "  ${vm}: provision failed -> vagrant reload --provision"
          vagrant reload "${vm}" --provision || true
        fi
        ;;
      *)
        echo "  ${vm}: ${state:-absent} -> vagrant up"
        vagrant up "${vm}" --provider="${PROVIDER}" || true
        ;;
    esac
  done

  ready_count=$(k8s_ready_nodes | grep -c . || true)
  if [ "${ready_count}" = "${EXPECTED_NODES}" ]; then
    echo "All ${EXPECTED_NODES} nodes Ready."
    vagrant ssh master-1 -c "kubectl get nodes" 2>/dev/null || true

    # D7: up.sh is the single authoritative Phase 2 driver.
    # The Vagrantfile worker-3 trigger.after :up that previously launched
    # phase2-platform has been removed because it fired before all nodes were
    # stably Ready, causing node flapping during 08-1's kubectl operations.
    # We always run Phase 2 here — 06-phase2-start.sh and its sub-scripts are
    # idempotent, so re-running after a partial trigger-driven attempt is safe.

    # Ensure master-1 SSH is healthy before attempting Phase 2. VMware's key-
    # replacement race can leave a k8s-Ready node with a broken SSH channel.
    if ! master1_ssh_ok; then
      if ! recover_master1_ssh; then
        echo "ERROR: cannot reach master-1 via SSH; aborting." >&2
        exit 1
      fi
    fi

    # phase2_complete: returns 0 only when all key namespaces created by Phase 2
    # scripts 08-x/09/11/12/13 are present, indicating the run reached the end.
    # Namespaces: platform-system (08-1), monitoring (08-2), security-system (08-3),
    # storage (08-4), istio-system (09), iam (11), devtools (12/13).
    # Ask master-1 for the namespace list ONCE, then decide locally.
    #
    # The previous version ran `vagrant ssh -c "kubectl get ns X"` per namespace and read
    # only the exit code, so an unreachable master-1 — the normal state right after the
    # VMware SSH race that killed the attempt — was reported as "namespace 'X' not found".
    # On 2026-08-04 that message named platform-system twice while the namespace demonstrably
    # existed, and it is the first thing anyone reads when diagnosing a failed Phase 2.
    # "Could not ask" and "the answer is no" now say different things.
    phase2_complete() {
      local have ns
      if ! have=$(vagrant ssh master-1 -c \
            "kubectl get ns -o name" 2>/dev/null); then
        echo "  Phase 2 status unknown: master-1 unreachable (SSH or kubectl failed)."
        return 1
      fi
      for ns in platform-system monitoring security-system storage istio-system iam devtools; do
        if ! grep -qx "namespace/${ns}" <<<"${have}"; then
          echo "  Phase 2 incomplete: namespace '${ns}' not found."
          return 1
        fi
      done
      return 0
    }

    # D11: Replace single-retry with a bounded retry loop to handle the VMware
    # SSH key-replacement race that SIGHUPs the Phase 2 orchestrator mid-run.
    # phase2-platform (06-phase2-start.sh and sub-scripts) is idempotent, so
    # re-running after a partial failure safely resumes from where it stalled.
    # 5, not 3. On 2026-08-04 a clean install used all three and still needed a manual
    # `vagrant provision master-1 --provision-with phase2-platform` to finish, purely
    # because the SSH drop below is unfixed and each drop costs an attempt. Raising the
    # budget is cheap and honest here: every attempt genuinely resumes now that the
    # re-install path works (verified — attempt 2 carried on past where attempt 1 died),
    # so a spent attempt is lost wall-clock, not lost work. It does NOT make the drop
    # acceptable; see the SSH note in recover_master1_ssh.
    PHASE2_MAX_ATTEMPTS="${PHASE2_MAX_ATTEMPTS:-5}"
    p2_attempt=1
    while [ "${p2_attempt}" -le "${PHASE2_MAX_ATTEMPTS}" ]; do
      say "Running Phase 2 platform provision — attempt ${p2_attempt}/${PHASE2_MAX_ATTEMPTS} (up.sh is the sole driver)..."

      # Ensure SSH is healthy before each attempt; SSH failure is non-fatal
      # for the loop — recover and continue unless recovery itself gives up.
      if ! master1_ssh_ok; then
        echo "Phase 2 attempt ${p2_attempt}: master-1 SSH unreachable, attempting recovery..."
        if ! recover_master1_ssh; then
          echo "WARNING: master-1 SSH recovery failed on attempt ${p2_attempt}; continuing to next attempt." >&2
          p2_attempt=$((p2_attempt + 1))
          continue
        fi
      fi

      phase2_rc=0
      vagrant provision master-1 --provision-with phase2-platform || phase2_rc=$?
      if [ "${phase2_rc}" -ne 0 ]; then
        say "Phase 2 provision failed (rc=${phase2_rc}) on attempt ${p2_attempt}/${PHASE2_MAX_ATTEMPTS}."
      fi

      # phase2_complete is the authoritative gate regardless of provision rc.
      # A non-zero rc may reflect an SSH SIGHUP mid-run while sub-scripts
      # continued; always check namespaces.
      if phase2_complete; then
        echo "Phase 2 complete (attempt ${p2_attempt})."
        exit 0
      fi

      if [ "${p2_attempt}" -lt "${PHASE2_MAX_ATTEMPTS}" ]; then
        say "Phase 2 incomplete after attempt ${p2_attempt}/${PHASE2_MAX_ATTEMPTS} — recovering SSH and retrying..."
        if ! master1_ssh_ok; then
          recover_master1_ssh || true
        fi
      fi
      p2_attempt=$((p2_attempt + 1))
    done

    # Exhausted all attempts
    echo "ERROR: Phase 2 did not complete after ${PHASE2_MAX_ATTEMPTS} attempts." >&2
    echo "       Namespaces above were still missing — a critical script likely" >&2
    echo "       failed mid-run (DNS/transient issues are the usual cause)." >&2
    echo "       Re-run: vagrant provision master-1 --provision-with phase2-platform" >&2
    exit 1
  fi

  say "  ${ready_count}/${EXPECTED_NODES} nodes Ready — settle ${SETTLE_DELAY}s, then retry"
  sleep "${SETTLE_DELAY}"
  attempt=$((attempt + 1))
done

echo "ERROR: cluster did not reach ${EXPECTED_NODES} Ready nodes after ${MAX_ATTEMPTS} attempts." >&2
vagrant status >&2 || true
vagrant ssh master-1 -c "kubectl get nodes" >&2 || true
exit 1
