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
#   script releases them, runs `tofu destroy`, and restores them on every exit path (trap),
#   including a cancelled, failed or interrupted destroy.
#   Cost: IRREVERSIBLE. Everything in the state goes, data included (README "Cleanup" lists it):
#   the VMs (masters, workers, bastion with its mirror registry), both load balancers, the public
#   IPs (released, so a rebuild gets new addresses), the VPC, subnets and security groups, the
#   NFS data volume with every PVC (databases, Gitea, Loki/Prometheus, and SeaweedFS, which is
#   where Velero keeps its backups), the Kakao keypair and the local KPAAS_KEYPAIR.pem.
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

command -v tofu >/dev/null 2>&1 || die "tofu not found on PATH"
[ -f terraform.tfstate ] || die "no terraform.tfstate in $(pwd): run the copy in the checkout that holds the state"
for f in "${GUARDED[@]}"; do
  [ -f "${f}" ] || die "${f} not found in $(pwd)"
  # A leftover backup means an earlier run did not restore it; restoring now would undo edits.
  [ ! -e "${f}.bak" ] || die "${f}.bak already exists: compare it with ${f}, keep the right one, remove the .bak, rerun"
done

state="$(tofu state list)"
managed="$(grep -cvE "${DATA_RE}" <<<"${state}" || true)"
[ "${managed}" -gt 0 ] || die "the state has no managed resources: nothing to destroy"
guards_before="$(cat "${GUARDED[@]}" | grep -c 'prevent_destroy = true' || true)"

echo "=== About to destroy ${managed} managed resources in $(pwd) ==="
echo "  VMs:             $(grep -c 'kakaocloud_instance\.' <<<"${state}" || true)"
echo "  load balancers:  $(grep -c 'kakaocloud_load_balancer\.' <<<"${state}" || true)"
echo "  public IPs:      $(grep -c 'kakaocloud_public_ip\.' <<<"${state}" || true) (released)"
echo "  NFS data volume: $(grep -c 'kakaocloud_volume\.' <<<"${state}" || true) (every PVC, including the in-cluster Velero backups)"
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
# bash runs the EXIT trap on Ctrl-C and kill too (R292 case 4 sends SIGTERM mid-destroy).
trap restore_guards EXIT

sed -i.bak '/prevent_destroy = true/s/true/false/' "${GUARDED[@]}"

rc=0
tofu destroy "$@" || rc=$?

left="$(tofu state list 2>/dev/null | grep -vE "${DATA_RE}" || true)"
if [ -z "${left}" ]; then
  echo "=== All managed resources destroyed ==="
else
  echo "=== $(grep -c . <<<"${left}") managed resource(s) still in state ==="
  sed 's/^/  /' <<<"${left}"
  if grep -q 'kakaocloud_vpc\.' <<<"${left}"; then
    echo "  VPC $(tofu state show module.network.kakaocloud_vpc.vpc 2>/dev/null | grep -m1 provisioning_status | tr -s ' ' || echo 'status unknown')"
    echo "  A PENDING_DELETE VPC can take hours: wait, then refresh (README \"Cleanup\"); do not apply over it."
  fi
fi

# lib-ssh.sh pins host keys here; the keys of destroyed VMs would clash with a rebuild's new ones.
if ! grep -q 'kakaocloud_instance\.' <<<"${left}" && [ -s kakao_known_hosts ]; then
  : > kakao_known_hosts
  echo "cleared kakao_known_hosts (the pinned host keys belonged to the destroyed VMs)"
fi

exit "${rc}"
