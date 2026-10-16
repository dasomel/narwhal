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

# The root mode depends on the export's squash mode. Under root_squash (the default since narwhal#186)
# the share root must stay 750: only the squashed provisioner identity writes there. A cluster
# installed before that change keeps no_root_squash and a 777 root (live Kakao, 2026-10-16), where
# 750 would be a false FAIL; report it as WARN and keep the hardening intent visible.
SHARE_MODE="$(stat -c '%a' "${SHARE_ROOT}")"
EXPORT_LINES="$(sudo grep -hE "^[[:space:]]*${SHARE_ROOT}[[:space:]]" /etc/exports /etc/exports.d/*.exports 2>/dev/null || true)"
# Judge every client option group "(...)": WARN only when all of them are no_root_squash.
TOTAL_CLIENTS="$(printf '%s\n' "${EXPORT_LINES}" | grep -oE '\([^)]*\)' | wc -l | tr -d ' ')"
NRS_CLIENTS="$(printf '%s\n' "${EXPORT_LINES}" | grep -oE '\([^)]*no_root_squash[^)]*\)' | wc -l | tr -d ' ')"
if [ -z "${EXPORT_LINES}" ] || [ "${TOTAL_CLIENTS}" -eq 0 ]; then
  echo "FAIL: no export line for ${SHARE_ROOT} in /etc/exports, cannot judge the share root mode (${SHARE_MODE})" >&2
  exit 1
elif [ "${NRS_CLIENTS}" -eq "${TOTAL_CLIENTS}" ]; then
  if [ "${SHARE_MODE}" != "750" ]; then
    echo "WARN: ${SHARE_ROOT} is exported no_root_squash to every client and has mode ${SHARE_MODE} (hardened target: root_squash + 750; see the root_squash section of docs/runbooks/nfs-xfs-migration.md)" >&2
  fi
elif [ "${SHARE_MODE}" != "750" ]; then
  echo "FAIL: ${SHARE_ROOT} has a root_squash client but mode is ${SHARE_MODE}, expected 750" >&2
  exit 1
fi

echo "PASS: uid 26 wrote to nfs-csi PVC; pod Completed; ${SHARE_ROOT} mode ${SHARE_MODE}"
