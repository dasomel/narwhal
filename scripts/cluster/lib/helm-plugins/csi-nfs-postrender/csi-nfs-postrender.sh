#!/bin/bash
set -euo pipefail

# D-CSI-NFS-POSTRENDER / D-CIS-E1-CSI-NFS-GID: Patch sidecar privilege escalation/rootfs and set
# pod-level runAsUser 0 + runAsGroup 10100 on both workloads (KSV021). containerd rejects pod-level
# runAsGroup without runAsUser, and the root NFS plugin + sidecars require UID 0 for host mounts,
# the 0750 plugins_registry, and the root-owned 0755 CSI socket. Cost: Helm must provide yq v4 and
# explicit runAsUser: 0 triggers KSV105 (accepted). Escape hatch: remove this post-renderer and its values.
command -v yq >/dev/null 2>&1 || { echo "ERROR: yq v4 is required for CSI NFS post-rendering" >&2; exit 1; }
yq --version | grep -q 'version v4\.' || { echo "ERROR: CSI NFS post-renderer requires yq v4" >&2; exit 1; }

manifest="$(cat)"
[ -n "${manifest}" ] || { echo "ERROR: empty CSI NFS manifest input" >&2; exit 1; }

for workload in 'DaemonSet csi-nfs-node nfs node-driver-registrar liveness-probe' \
                'Deployment csi-nfs-controller nfs csi-provisioner csi-resizer csi-snapshotter liveness-probe'; do
  read -r kind name _ <<<"${workload}"
  count="$(yq eval-all '[select(.kind == "'"${kind}"'" and .metadata.name == "'"${name}"'")] | length' - <<<"${manifest}")"
  [ "${count}" = 1 ] || { echo "ERROR: expected exactly one ${kind}/${name}, found ${count}" >&2; exit 1; }
  for container in ${workload#* * *}; do
    found="$(yq eval-all '[select(.kind == "'"${kind}"'" and .metadata.name == "'"${name}"'") | .spec.template.spec.containers[] | select(.name == "'"${container}"'")] | length' - <<<"${manifest}")"
    [ "${found}" = 1 ] || { echo "ERROR: expected ${kind}/${name} container ${container}, found ${found}" >&2; exit 1; }
  done
done

# yq updates only the named sidecars and sets pod-level runAsUser 0 + runAsGroup 10100 on the two known workloads.
# Existing rootfs values are preserved; allowPrivilegeEscalation is explicitly false everywhere.
yq eval-all 'if ((.kind == "DaemonSet" and .metadata.name == "csi-nfs-node") or (.kind == "Deployment" and .metadata.name == "csi-nfs-controller")) then .spec.template.spec.securityContext.runAsUser = 0 | .spec.template.spec.securityContext.runAsGroup = 10100 | .spec.template.spec.containers |= map(if (.name == "csi-provisioner" or .name == "csi-resizer" or .name == "csi-snapshotter" or .name == "liveness-probe" or .name == "node-driver-registrar") then .securityContext.allowPrivilegeEscalation = false | (if (.name == "csi-resizer" or .name == "csi-snapshotter" or .name == "node-driver-registrar") then .securityContext.readOnlyRootFilesystem = true else . end) else . end) else . end' - <<<"${manifest}"
