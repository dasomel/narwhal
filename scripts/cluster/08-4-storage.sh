#!/bin/bash
set -euo pipefail

echo "=== Installing Storage Apps (SeaweedFS, OpenBao, Velero) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# SeaweedFS (S3-compatible Object Storage)
#=========================================
echo "=== Installing SeaweedFS ==="
helm repo add seaweedfs https://seaweedfs.github.io/seaweedfs/helm
helm repo update seaweedfs

helm upgrade --install seaweedfs seaweedfs/seaweedfs \
  --namespace storage \
  --create-namespace \
  --version 4.0.407 \
  --set global.storageClass=nfs-csi \
  --set master.enabled=true \
  --set master.replicas=1 \
  --set master.data.type=persistentVolumeClaim \
  --set master.data.size=1Gi \
  --set master.data.storageClass=nfs-csi \
  --set volume.enabled=true \
  --set volume.replicas=1 \
  --set volume.data.type=persistentVolumeClaim \
  --set volume.data.size=50Gi \
  --set volume.data.storageClass=nfs-csi \
  --set filer.enabled=true \
  --set filer.replicas=1 \
  --set filer.data.type=persistentVolumeClaim \
  --set filer.data.size=5Gi \
  --set filer.data.storageClass=nfs-csi \
  --set filer.s3.enabled=true \
  --set filer.s3.port=8333 \
  --set filer.s3.allowEmptyFolder=true \
  --set s3.enabled=true || echo "WARN: SeaweedFS install issue, continuing..."

# Create S3 buckets for platform apps
# All apps using SeaweedFS S3: Tempo, Velero, Loki, CNPG backup
echo "Creating SeaweedFS S3 buckets..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=seaweedfs,app.kubernetes.io/component=filer -n storage --timeout=120s || true
for bucket in tempo velero loki cnpg-backup; do
  echo "  Creating bucket: ${bucket}"
  kubectl exec -n storage seaweedfs-filer-0 -- sh -c "echo 's3.bucket.create -name ${bucket}' | weed shell" 2>/dev/null || true
done

# Verify buckets were created
echo "Verifying S3 buckets..."
BUCKET_LIST=$(kubectl exec -n storage seaweedfs-filer-0 -- sh -c "echo 's3.bucket.list' | weed shell" 2>/dev/null || true)
for bucket in tempo velero loki cnpg-backup; do
  if echo "${BUCKET_LIST}" | grep -q "${bucket}"; then
    echo "  + ${bucket}"
  else
    echo "  - ${bucket} - WARN: bucket may not exist"
  fi
done
echo "S3 buckets ready"

echo "SeaweedFS installed"

#=========================================
# OpenBao (Secret Management)
#=========================================
echo "=== Installing OpenBao ==="
helm repo add openbao https://openbao.github.io/openbao-helm
helm repo update openbao

helm upgrade --install openbao openbao/openbao \
  --namespace storage \
  --create-namespace \
  --version 0.11.0 \
  --set server.image.tag=2.2.0 \
  --set server.ha.enabled=false \
  --set server.ha.replicas=1 \
  --set server.ha.raft.enabled=true \
  --set server.dataStorage.enabled=true \
  --set server.dataStorage.storageClass=nfs-csi \
  --set server.dataStorage.size=10Gi \
  --set server.auditStorage.enabled=true \
  --set server.auditStorage.storageClass=nfs-csi \
  --set server.auditStorage.size=5Gi \
  --set ui.enabled=true || echo "WARN: OpenBao install issue, continuing..."

# Auto init + unseal OpenBao
echo "Waiting for OpenBao pod..."
kubectl wait --for=condition=Ready=false pod/openbao-0 -n storage --timeout=120s 2>/dev/null || true
sleep 5

OPENBAO_INITIALIZED=$(kubectl exec openbao-0 -n storage -- bao status -format=json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("initialized",""))' 2>/dev/null || echo "")

if [ "${OPENBAO_INITIALIZED}" = "True" ]; then
  echo "OpenBao already initialized, checking unseal key..."
  UNSEAL_KEY=$(kubectl get secret openbao-init -n storage -o jsonpath='{.data.unseal_keys_b64}' 2>/dev/null | base64 -d 2>/dev/null || echo "")
  if [ -n "${UNSEAL_KEY}" ]; then
    echo "Unsealing OpenBao..."
    kubectl exec openbao-0 -n storage -- bao operator unseal "${UNSEAL_KEY}" || true
  else
    echo "WARN: OpenBao initialized but unseal key not found in openbao-init secret"
  fi
elif [ "${OPENBAO_INITIALIZED}" = "False" ]; then
  echo "Initializing OpenBao..."
  INIT_JSON=$(kubectl exec openbao-0 -n storage -- bao operator init -key-shares=1 -key-threshold=1 -format=json 2>/dev/null || echo "")
  if [ -n "${INIT_JSON}" ]; then
    UNSEAL_KEY=$(echo "${INIT_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["unseal_keys_b64"][0])')
    ROOT_TOKEN=$(echo "${INIT_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["root_token"])')
    echo "Unsealing OpenBao..."
    kubectl exec openbao-0 -n storage -- bao operator unseal "${UNSEAL_KEY}" || true
    echo "Saving credentials to openbao-init secret..."
    kubectl create secret generic openbao-init -n storage \
      --from-literal=unseal_keys_b64="${UNSEAL_KEY}" \
      --from-literal=root_token="${ROOT_TOKEN}" \
      --dry-run=client -o yaml | kubectl apply -f -
    echo "OpenBao initialized and unsealed"
  else
    echo "WARN: OpenBao init failed, manual init required"
  fi
else
  echo "WARN: Could not determine OpenBao state, skipping init"
fi

echo "OpenBao installed"

#=========================================
# Velero (Backup & Restore)
#=========================================
echo "=== Installing Velero ==="
helm repo add vmware-tanzu https://vmware-tanzu.github.io/helm-charts
helm repo update vmware-tanzu

# S3 credentials — prefer environment overrides, fall back to Secret or defaults
S3_ACCESS_KEY="${S3_ACCESS_KEY:-$(kubectl get secret velero-s3-credentials -n storage \
  -o jsonpath='{.data.access-key}' 2>/dev/null | base64 -d || echo "admin")}"
S3_SECRET_KEY="${S3_SECRET_KEY:-$(kubectl get secret velero-s3-credentials -n storage \
  -o jsonpath='{.data.secret-key}' 2>/dev/null | base64 -d || echo "")}"

# Persist S3 credentials into a dedicated Secret (idempotent)
# 'cloud' key uses AWS credentials file format required by velero-plugin-for-aws
# (referenced by gitops/apps/velero.yaml as existingSecret: velero-s3-credentials)
kubectl create namespace storage --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic velero-s3-credentials \
  --from-literal=access-key="${S3_ACCESS_KEY}" \
  --from-literal=secret-key="${S3_SECRET_KEY}" \
  --from-literal=cloud="[default]
aws_access_key_id = ${S3_ACCESS_KEY}
aws_secret_access_key = ${S3_SECRET_KEY}" \
  -n storage --dry-run=client -o yaml | kubectl apply -f -

cat > /tmp/velero-values.yaml << EOF
initContainers:
  - name: velero-plugin-for-aws
    image: velero/velero-plugin-for-aws:v1.11.1
    volumeMounts:
      - mountPath: /target
        name: plugins
configuration:
  backupStorageLocation:
    - name: default
      provider: aws
      bucket: velero
      config:
        region: us-east-1
        s3ForcePathStyle: "true"
        s3Url: http://seaweedfs-s3.storage.svc.cluster.local:8333
  volumeSnapshotLocation:
    - name: default
      provider: aws
      config:
        region: us-east-1
  defaultBackupStorageLocation: default
  uploaderType: kopia
  defaultVolumesToFsBackup: true
credentials:
  useSecret: true
  secretContents:
    cloud: |
      [default]
      aws_access_key_id = ${S3_ACCESS_KEY}
      aws_secret_access_key = ${S3_SECRET_KEY}
snapshotsEnabled: false
# Disable CRD upgrade hook - alpine/k8s musl binaries can't exec in velero glibc container
upgradeCRDs: false
kubectl:
  image:
    # docker.io: registry.k8s.io/kubectl은 distroless(shell 없음), ghcr.io 대안 부재
    repository: docker.io/alpine/k8s
    tag: "1.31.4"
deployNodeAgent: true
nodeAgent:
  podVolumePath: /var/lib/kubelet/pods
  privileged: true
  tolerations:
    - key: node-role.kubernetes.io/control-plane
      operator: Exists
      effect: NoSchedule
EOF

helm upgrade --install velero vmware-tanzu/velero \
  --namespace storage \
  --create-namespace \
  --version 11.3.2 \
  -f /tmp/velero-values.yaml || echo "WARN: Velero install issue, continuing..."

rm /tmp/velero-values.yaml
echo "Velero installed"

echo "=== Storage Apps Installation Complete ==="
