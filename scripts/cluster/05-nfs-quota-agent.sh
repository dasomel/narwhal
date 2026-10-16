#!/bin/bash
set -euo pipefail

# Charts come from the airgap bundle.
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-nqa.sh

# Same retry() as 03-k8s-install.sh and 03-cni-install.sh. These downloads cross the
# bastion proxy to a public CDN and fail transiently; without a retry one flaky fetch
# fails the whole stage.
retry() {
  local n=1 max=5
  until "$@"; do
    if [ "$n" -ge "$max" ]; then
      echo "ERROR: command failed after ${max} attempts: $*" >&2
      return 1
    fi
    echo "  attempt ${n}/${max} failed, retrying in 15s..." >&2
    n=$((n + 1))
    sleep 15
  done
}


NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"
MASTER_HOSTNAME="${MASTER_HOSTNAME:-narwhal-master}"
NFS_QUOTA_AGENT_VERSION="${NFS_QUOTA_AGENT_VERSION:-v0.5.0}"

echo "=== NFS Quota Agent Installation ==="

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

# Wait for cluster to be ready
echo "Waiting for cluster to be ready..."
until kubectl get nodes &>/dev/null; do
  sleep 2
done

# Label master node as NFS server
echo "Labeling master node as NFS server..."
kubectl label node "${MASTER_HOSTNAME}" nfs-server=true --overwrite

# Unpack the Helm chart from the airgap bundle.
#
# --strip-components=1 rather than extracting into /tmp: the tarball's top-level directory
# is `nfs-quota-agent/`, not `nfs-quota-agent-chart/`, so extracting alongside CHART_DIR
# left helm with `path "/tmp/nfs-quota-agent-chart" not found` right after a "successful"
# extraction. The name only matched while the chart came from a GitHub tarball; swapping
# the source without re-checking the layout is what broke it.
CHART_DIR="/tmp/nfs-quota-agent-chart"
echo "Unpacking Helm chart from the bundle..."
rm -rf "${CHART_DIR}"
mkdir -p "${CHART_DIR}"
tar xzf "$(chart nfs-quota-agent)" -C "${CHART_DIR}" --strip-components=1

# One value set for both the skew guard (helm template) and the install.
HELM_ARGS=(
  --namespace nfs-quota-agent
  --set image.repository=ghcr.io/dasomel/nfs-quota-agent
  --set image.tag="${NFS_QUOTA_AGENT_VERSION}"
  --set config.nfsBasePath=/export
  --set config.nfsServerPath="${NFS_SHARE_PATH}"
  --set config.provisionerName=nfs.csi.k8s.io
  --set config.syncInterval=30s
  --set nfsExport.hostPath="${NFS_SHARE_PATH}"
  --set webUI.enabled=true
  --set webUI.addr=":8080"
  --set audit.enabled=true
  --set cleanup.enabled=true
  --set history.enabled=true
  --set policy.enabled=true
  --set-string nodeSelector.nfs-server=true
  --set tolerations[0].key=node-role.kubernetes.io/control-plane
  --set tolerations[0].operator=Exists
  --set tolerations[0].effect=NoSchedule
  --set tolerations[1].key=node.kubernetes.io/disk-pressure
  --set tolerations[1].operator=Exists
  --set tolerations[1].effect=NoSchedule
)

# Refuse a chart/image mismatch before rolling anything out: every flag the rendered chart passes
# must exist in the image (see lib-nqa.sh).
nqa_check_skew "${CHART_DIR}" "${NFS_QUOTA_AGENT_VERSION}" "${HELM_ARGS[@]}" || exit 1

# Create namespace
kubectl create namespace nfs-quota-agent --dry-run=client -o yaml | kubectl apply -f -

# Ensure project files exist on host (must be regular files, not directories)
for f in /etc/projid /etc/projects; do
  if [ -d "$f" ]; then
    sudo rm -rf "$f"
  fi
  sudo touch "$f"
done

# prjquota: the agent enforces through project quotas, which ext4/xfs only honour when the export
# filesystem is mounted with prjquota. D-nqa-prjquota: warn and keep deploying, because the web UI,
# metrics, audit and history still work and the agent reports failed applies per PV; failing the
# stage would block Phase 2 on a filesystem remount that needs downtime. Enforcement is OFF until fixed.
mnt_opts=$(findmnt -no OPTIONS -T "${NFS_SHARE_PATH}" 2>/dev/null || true)
case ",${mnt_opts}," in
  *,prjquota,* | *,pquota,*) ;;
  *)
    echo "WARN: ${NFS_SHARE_PATH} is not mounted with prjquota (options: ${mnt_opts:-unknown}); nfs-quota-agent cannot enforce quotas." >&2
    echo "      Remediation (needs a brief NFS outage): ext4 -> 'tune2fs -O project,quota <dev>' while unmounted, then" >&2
    echo "      add 'prjquota' to the fstab options and remount; xfs -> add 'prjquota' and remount." >&2
    ;;
esac

# Install via Helm
echo "Installing nfs-quota-agent..."
helm upgrade --install nfs-quota-agent "${CHART_DIR}" "${HELM_ARGS[@]}"

# The chart renders a DaemonSet (pinned to the nfs-server node), not a Deployment. Wait on it with a
# timeout, then watch the pod through a stabilization window: a crash-looping pod is briefly
# Running between restarts, so the state at one instant (CrashLoopBackOff or not) proves nothing.
echo "Waiting for nfs-quota-agent daemonset..."
nqa_fail() {
  echo "ERROR: nfs-quota-agent is not healthy: $1" >&2
  kubectl get pods -n nfs-quota-agent -o wide >&2 || true
  kubectl logs -n nfs-quota-agent daemonset/nfs-quota-agent --previous --tail=20 >&2 || true
  exit 1
}
kubectl rollout status daemonset/nfs-quota-agent -n nfs-quota-agent --timeout=180s ||
  nqa_fail "rollout did not complete within 180s"
# Stabilization window (NQA_STABLE_WINDOW, default 60s): a past one-off restart is fine, a pod that is
# not Ready, restarts, or sits in CrashLoopBackOff/Error during the window is not.
bad_pods=$(nqa_wait_stable nfs-quota-agent) || nqa_fail "${bad_pods}"

# Cleanup
rm -rf "${CHART_DIR}"

# Verify installation
echo "=== NFS Quota Agent Status ==="
kubectl get pods -n nfs-quota-agent
kubectl get daemonset -n nfs-quota-agent

echo "=== NFS Quota Agent Installation Done ==="
