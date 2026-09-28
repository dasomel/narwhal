#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="nfs-nonroot-check-$(date +%s)-$$"
POD="nfs-write-check"
SHARE_ROOT="${NFS_SHARE_PATH:-/srv/nfs/k8s}"

cleanup() {
  sudo kubectl delete namespace "${NAMESPACE}" --wait=false >/dev/null 2>&1 || true
}
trap cleanup EXIT

sudo kubectl create namespace "${NAMESPACE}"
cat <<YAML_EOF | sudo kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: nfs-write-check
  namespace: ${NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: nfs-csi
  resources:
    requests:
      storage: 64Mi
---
apiVersion: v1
kind: Pod
metadata:
  name: ${POD}
  namespace: ${NAMESPACE}
spec:
  restartPolicy: Never
  securityContext:
    runAsUser: 26
    runAsGroup: 26
    fsGroup: 26
  containers:
    - name: writer
      image: docker.io/library/busybox:1.36
      command:
        - /bin/sh
        - -ec
        # The server timer converges within five seconds. Retry to cover a pod that
        # starts before chmod; otherwise a one-shot process can fail before convergence.
        - |
          attempt=0
          until touch /data/nonroot-write-check; do
            attempt=\$((attempt + 1))
            if [ "\${attempt}" -ge 30 ]; then
              echo "NFS PV directory did not become writable" >&2
              exit 1
            fi
            sleep 1
          done
          test "\$(id -u)" = 26
          echo "uid 26 wrote successfully"
      volumeMounts:
        - name: data
          mountPath: /data
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: nfs-write-check
YAML_EOF

sudo kubectl wait --for=jsonpath='{.status.phase}'=Bound pvc/nfs-write-check \
  -n "${NAMESPACE}" --timeout=180s
sudo kubectl wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${POD}" \
  -n "${NAMESPACE}" --timeout=180s
sudo kubectl logs "${POD}" -n "${NAMESPACE}"

SHARE_MODE="$(stat -c '%a' "${SHARE_ROOT}")"
if [ "${SHARE_MODE}" != "750" ]; then
  echo "FAIL: ${SHARE_ROOT} mode is ${SHARE_MODE}, expected 750" >&2
  exit 1
fi

echo "PASS: uid 26 wrote to nfs-csi PVC; pod Completed; ${SHARE_ROOT} remains 750"
