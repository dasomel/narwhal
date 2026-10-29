#!/usr/bin/env bash
#=========================================================================
# destroy-all.sh
# Tear down the whole Kakao Cloud deployment managed from this directory: every resource in
# terraform.tfstate, including the destroy-guarded public IPs and the NFS data volume.
#
# D-KAKAO-TEARDOWN (2026-10-29):
#   Reason: six `prevent_destroy` guards (D-PUBIP on the bastion, master-LB, worker-LB and node
#   public IPs; D-NFS-VOL on the NFS data volume) make a plain `tofu destroy` fail with
#   "Instance cannot be destroyed". README "Cleanup" option A releases them with sed and restores
#   them by hand afterwards, and a forgotten restore leaves the next `tofu apply` unguarded. This
#   script releases them, runs `tofu destroy`, and restores them from a trap when it ends: after
#   success, a cancel at the prompt, a failed destroy, Ctrl-C, TERM, HUP or QUIT. Only SIGKILL,
#   which nothing can catch, leaves the guards released (the .bak pre-check then stops a rerun).
#   Cost: IRREVERSIBLE. Everything in the state goes, data included (README "Cleanup" lists it):
#   the VMs with their disks (etcd; the bastion mirror registry; the SeaweedFS object store on a
#   worker's hostPath, which holds the Velero and CNPG backups and the Loki/Tempo data), both load
#   balancers, the public IPs (released, so a rebuild gets new addresses), the VPC, subnets and
#   security groups, the NFS data volume with every NFS-backed PVC, the Kakao keypair and the
#   local KPAAS_KEYPAIR.pem.
#   Escape hatch: answer anything but "yes" at the plan prompt; the guards are restored on exit.
#   To keep the addresses or the data volume, use README "Cleanup" option B instead.
#
# Usage (runs in this directory, wherever it is called from):
#   csp/kakao-cloud/terraform/destroy-all.sh                 # shows the plan, asks for "yes"
#   csp/kakao-cloud/terraform/destroy-all.sh -auto-approve   # extra args go to `tofu destroy`
#=========================================================================
set -euo pipefail

cd "$(dirname "$0")"

GUARDED=(bastion.tf modules/compute/main.tf modules/loadbalancer/main.tf)
DATA_RE='(^|\.)data\.'

die() { echo "ERROR: $*" >&2; exit 1; }

# managed_of <state list>: the non-data-source lines (empty when there are none).
managed_of() { grep -vE "${DATA_RE}" <<<"$1" | grep . || true; }

command -v tofu >/dev/null 2>&1 || die "tofu not found on PATH"
[ -f terraform.tfstate ] || die "no terraform.tfstate in $(pwd): run the copy in the checkout that holds the state"
for f in "${GUARDED[@]}"; do
  [ -f "${f}" ] || die "${f} not found in $(pwd)"
  # A leftover backup means an earlier run did not restore it; restoring now would undo edits.
  [ ! -e "${f}.bak" ] || die "${f}.bak already exists: compare it with ${f}, keep the right one, remove the .bak, rerun"
done

state="$(tofu state list)"
managed="$(managed_of "${state}" | grep -c . || true)"
[ "${managed}" -gt 0 ] || die "the state has no managed resources: nothing to destroy"
guards_before="$(cat "${GUARDED[@]}" | grep -c 'prevent_destroy = true' || true)"

echo "=== About to destroy ${managed} managed resources in $(pwd) ==="
echo "  VMs:             $(grep -c 'kakaocloud_instance\.' <<<"${state}" || true) (with their disks: etcd, mirror registry, SeaweedFS backups)"
echo "  load balancers:  $(grep -c 'kakaocloud_load_balancer\.' <<<"${state}" || true)"
echo "  public IPs:      $(grep -c 'kakaocloud_public_ip\.' <<<"${state}" || true) (released)"
echo "  NFS data volume: $(grep -c 'kakaocloud_volume\.' <<<"${state}" || true) (every NFS-backed PVC)"
echo "  guards released for this run: ${guards_before}"

restore_guards() {
  local f
  for f in "${GUARDED[@]}"; do
    if [ -f "${f}.bak" ]; then
      mv "${f}.bak" "${f}"
    fi
  done
  echo "prevent_destroy guards restored: $(cat "${GUARDED[@]}" | grep -c 'prevent_destroy = true' || true) of ${guards_before}"
}
# bash already runs the EXIT trap on INT, TERM and HUP; QUIT needs an explicit exit (R292).
trap restore_guards EXIT
trap 'exit 131' QUIT

sed -i.bak '/prevent_destroy = true/s/true/false/' "${GUARDED[@]}"

rc=0
tofu destroy "$@" || rc=$?

if ! after="$(tofu state list 2>/dev/null)"; then
  echo "WARN: could not read the state after destroy, so nothing is reported or cleared:" >&2
  tofu state list >/dev/null || true   # shows tofu's own error on stderr
  [ "${rc}" -ne 0 ] || rc=1
  exit "${rc}"
fi
left="$(managed_of "${after}")"
vpc_status=""
# Only when the destroy got as far as the VPC (no VM left); after a cancel the VMs are still there.
if grep -q 'kakaocloud_vpc\.' <<<"${left}" && ! grep -q 'kakaocloud_instance\.' <<<"${left}"; then
  # The local state is as of the failed delete. Refresh (read-only) for the live status; the
  # delete may also have landed late, in which case the VPC drops out of the state here.
  tofu refresh -input=false >/dev/null 2>&1 || true
  if after="$(tofu state list 2>/dev/null)"; then
    left="$(managed_of "${after}")"
  fi
  if grep -q 'kakaocloud_vpc\.' <<<"${left}"; then
    vpc_status="$(tofu state show module.network.kakaocloud_vpc.vpc 2>/dev/null \
      | sed -nE 's/^    provisioning_status += "([A-Z_]+)"/\1/p' | head -n 1 || true)"
  fi
fi
if [ -z "${left}" ]; then
  echo "=== All managed resources destroyed ==="
else
  echo "=== $(grep -c . <<<"${left}") managed resource(s) still in state ==="
  sed 's/^/  /' <<<"${left}"
  case "${vpc_status}" in
    PENDING_DELETE)
      echo "  VPC is PENDING_DELETE: the delete was accepted and runs server-side, which can take"
      echo "  hours. Wait and refresh until it leaves the state (README \"Cleanup\"); do not apply over it." ;;
    ACTIVE)
      echo "  VPC is still ACTIVE: the delete was rejected, not queued. This happened right after a"
      echo "  5m40s subnet delete on 2026-10-29. Wait a few minutes, then rerun this script; if it fails"
      echo "  again, read the full error and check the VPC in the Kakao Cloud console." ;;
    "") : ;;
    *)
      echo "  VPC status: ${vpc_status}" ;;
  esac
fi

# lib-ssh.sh pins host keys here; the keys of destroyed VMs would clash with a rebuild's new ones.
if ! grep -q 'kakaocloud_instance\.' <<<"${left}" && [ -s kakao_known_hosts ]; then
  : > kakao_known_hosts
  echo "cleared kakao_known_hosts (the pinned host keys belonged to the destroyed VMs)"
fi

exit "${rc}"
