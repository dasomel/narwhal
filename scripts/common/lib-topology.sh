#!/bin/bash
# Shared Vagrant expectations for the host driver and verification scripts.
# D-SINGLE-NODE-RECORD: match Vagrantfile, anchored to this checkout even outside cwd.
# Cost: duplicate resolver; R342 exercises both. Escape hatch: destroy the recorded fleet.
TOPOLOGY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOPOLOGY_RECORD="${TOPOLOGY_ROOT}/.vagrant/narwhal-topology"
topology_created=false
for topology_id in "${TOPOLOGY_ROOT}"/.vagrant/machines/*/*/id; do
  [ -f "${topology_id}" ] || continue
  if [ -n "$(tr -d '[:space:]' < "${topology_id}")" ]; then topology_created=true; fi
done
recorded_topology=""
if [ "${topology_created}" = true ] && [ -f "${TOPOLOGY_RECORD}" ]; then
  recorded_topology="$(cat "${TOPOLOGY_RECORD}")"
fi
if [ -n "${NARWHAL_TOPOLOGY:-}" ] && [ -n "${recorded_topology}" ] && [ "${NARWHAL_TOPOLOGY}" != "${recorded_topology}" ]; then
  echo "REFUSING: requested topology ${NARWHAL_TOPOLOGY}, recorded topology ${recorded_topology}. Destroy all machines using the recorded topology, then switch topology." >&2
  exit 1
fi
export NARWHAL_TOPOLOGY="${NARWHAL_TOPOLOGY:-${recorded_topology:-ha}}"
case "${NARWHAL_TOPOLOGY}" in
  ha)
    TOPOLOGY_MASTERS=3
    TOPOLOGY_NODES=6
    ALL_VMS="master-1 master-2 master-3 worker-1 worker-2 worker-3"
    ;;
  single)
    TOPOLOGY_MASTERS=1
    TOPOLOGY_NODES=1
    ALL_VMS="master-1"
    ;;
  *)
    echo "NARWHAL_TOPOLOGY must be ha or single (got ${NARWHAL_TOPOLOGY})" >&2
    exit 1
    ;;
esac
export TOPOLOGY_MASTERS TOPOLOGY_NODES ALL_VMS

# D-SINGLE-NODE-GUARD: fail before the host driver can recover/reload a partial fleet.
# Cost: mirror the Vagrantfile's id-file guard; escape hatch: use the original topology.
check_topology_machines() {
  local id machine unexpected=""
  for id in "${TOPOLOGY_ROOT}"/.vagrant/machines/*/*/id; do
    [ -f "${id}" ] || continue
    [ -n "$(tr -d '[:space:]' < "${id}")" ] || continue
    machine="${id%/*/id}"
    machine="${machine##*/}"
    case " ${ALL_VMS} " in
      *" ${machine} "*) ;;
      *) unexpected="${unexpected} ${machine}" ;;
    esac
  done
  if [ -n "${unexpected}" ]; then
    echo "REFUSING: NARWHAL_TOPOLOGY=${NARWHAL_TOPOLOGY:-ha} does not define created machines:${unexpected}. Destroy them using their original topology or switch topology." >&2
    return 1
  fi
}
