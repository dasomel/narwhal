# Narwhal Disaster Recovery 런북

> Narwhal Kubernetes IDP 클러스터의 장애 복구 절차 문서
>
> **환경**: Vagrant + VMware Desktop, K8s 3 Master + 3 Worker
> **VIP**: 192.168.56.100 (kube-vip), **MetalLB**: 192.168.56.200, **NFS**: master-1(/srv/nfs/k8s)

---

## 목차

1. [단일 Worker 노드 장애](#1-단일-worker-노드-장애)
2. [단일 Master 노드 장애 (master-2/3)](#2-단일-master-노드-장애-master-23)
3. [Master-1 (Primary) 장애](#3-master-1-primary-장애)
4. [etcd 클러스터 복구](#4-etcd-클러스터-복구)
5. [데이터베이스 (CNPG) 복구](#5-데이터베이스-cnpg-복구)
6. [Velero 전체 클러스터 복원](#6-velero-전체-클러스터-복원)
7. [인증서 만료 대응](#7-인증서-만료-대응)
8. [Keycloak SSO 장애](#8-keycloak-sso-장애)
9. [ArgoCD GitOps 장애](#9-argocd-gitops-장애)
10. [NFS 스토리지 장애](#10-nfs-스토리지-장애)
11. [Istio Ambient Mesh 장애](#11-istio-ambient-mesh-장애)
12. [OpenBao 장애 및 Unseal](#12-openbao-장애-및-unseal)
13. [전체 클러스터 재구축](#13-전체-클러스터-재구축)
14. [부록: 빠른 진단 명령어](#부록-빠른-진단-명령어)

---

## 1. 단일 Worker 노드 장애

### 증상

```bash
kubectl get nodes
# NAME          STATUS     ROLES    AGE
# worker-1      NotReady   <none>   1d    ← 장애
# worker-2      Ready      <none>   1d
# worker-3      Ready      <none>   1d
```

워커 노드 하나가 `NotReady`이면 해당 노드의 Pod가 다른 노드로 재스케줄링됩니다.

### 복구 절차

**Step 1: 장애 원인 파악**

```bash
# 호스트에서 VM 상태 확인
vagrant status

# 노드 이벤트/조건 확인
kubectl describe node worker-1 | grep -A 10 "Conditions:"
kubectl describe node worker-1 | grep -A 20 "Events:"
```

**Step 2: VM 재시작**

```bash
# 호스트 머신에서
vagrant halt worker-1
vagrant up worker-1

# VM 기동 후 kubelet 상태 확인 (VM 내에서)
vagrant ssh worker-1 -c "sudo systemctl status kubelet"
vagrant ssh worker-1 -c "sudo journalctl -u kubelet -n 50"
```

**Step 3: 노드 상태 확인**

```bash
# Ready 상태 복귀 확인 (최대 2-3분 대기)
kubectl get nodes -w

# 해당 노드의 Pod 재스케줄링 확인
kubectl get pods -A -o wide | grep worker-1
```

**Step 4: 노드가 자동 복귀하지 않을 경우**

```bash
# kubelet 재시작
vagrant ssh worker-1 -c "sudo systemctl restart kubelet"

# containerd 재시작 (런타임 문제일 경우)
vagrant ssh worker-1 -c "sudo systemctl restart containerd"

# 노드 drain 후 uncordon (Pod 재분배 강제)
kubectl drain worker-1 --ignore-daemonsets --delete-emptydir-data
# 문제 해결 후
kubectl uncordon worker-1
```

**Step 5: 디스크 압력 (DiskPressure) 발생 시**

```bash
# 디스크 사용량 확인
vagrant ssh worker-1 -c "df -h"

# 미사용 컨테이너 이미지 정리
vagrant ssh worker-1 -c "sudo crictl rmi --prune"

# 로그 정리
vagrant ssh worker-1 -c "sudo journalctl --vacuum-size=1G"
```

### 검증

```bash
kubectl get nodes
# NAME       STATUS   ROLES    AGE
# worker-1   Ready    <none>   ...  ← 복구 완료

kubectl get pods -A -o wide | grep worker-1
# 모든 Pod가 Running 상태인지 확인
```

---

## 2. 단일 Master 노드 장애 (master-2/3)

### 증상

master-2 또는 master-3이 `NotReady`이지만 VIP(192.168.56.100)는 정상 동작합니다.
etcd 3노드 클러스터에서 1노드 장애는 쿼럼(2/3)을 유지하므로 클러스터 운영에 영향 없습니다.

```bash
kubectl get nodes
# master-1   Ready    control-plane   1d
# master-2   NotReady control-plane   1d    ← 장애
# master-3   Ready    control-plane   1d
```

### 복구 절차

**Step 1: VM 재시작**

```bash
# 호스트에서
vagrant halt master-2
vagrant up master-2

# kubelet 상태 확인
vagrant ssh master-2 -c "sudo systemctl status kubelet"
vagrant ssh master-2 -c "sudo systemctl status containerd"
```

**Step 2: etcd 멤버 상태 확인**

```bash
# master-1에서 etcd 멤버 목록 확인 (etcd는 distroless, etcdctl 직접 호출)
vagrant ssh master-1 -c "kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl member list \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key"
```

**Step 3: etcd 멤버가 `unstarted` 상태인 경우**

```bash
# 문제 있는 멤버 ID 확인
MEMBER_ID=$(vagrant ssh master-1 -c "kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl member list \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  -w json" | python3 -c "import sys,json; \
  [print(m['ID']) for m in json.load(sys.stdin)['members'] \
  if 'master-2' in m.get('name','')]")

# 멤버 제거
vagrant ssh master-1 -c "kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl member remove ${MEMBER_ID} \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key"

# master-2에서 etcd 데이터 초기화 후 재조인
vagrant ssh master-2 -c "sudo rm -rf /var/lib/etcd/member"
vagrant ssh master-2 -c "sudo systemctl restart kubelet"
```

**Step 4: kube-vip 상태 확인**

```bash
# master-2의 kube-vip Static Pod 확인
vagrant ssh master-2 -c "sudo cat /etc/kubernetes/manifests/kube-vip.yaml | head -20"
vagrant ssh master-2 -c "sudo crictl ps | grep kube-vip"
```

### 검증

```bash
# 모든 마스터 Ready 확인
kubectl get nodes | grep master

# etcd 클러스터 헬스 확인
vagrant ssh master-1 -c "kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl endpoint health \
  --endpoints=https://192.168.56.10:2379,https://192.168.56.11:2379,https://192.168.56.12:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key"
```

---

## 3. Master-1 (Primary) 장애

### 증상

master-1이 장애를 일으키면 다음 영향이 발생합니다:

- **kube-vip**: VIP(192.168.56.100)가 master-2 또는 master-3으로 ARP 기반 자동 페일오버
- **NFS 서버**: master-1 전용이므로 `/srv/nfs/k8s` PVC I/O 중단
- **dnsmasq**: master-1의 dnsmasq 중단 (master-2, master-3에도 설치됨)
- **API 서버 접근**: VIP 페일오버 후 약 10-30초 내 복구

```bash
# VIP 현재 위치 확인
for node in master-1 master-2 master-3; do
  echo -n "${node}: "
  vagrant ssh ${node} -c "ip addr show | grep 192.168.56.100" 2>/dev/null || echo "(no VIP)"
done
```

### 복구 절차

**Step 1: VIP 페일오버 확인**

```bash
# API 서버 접근 테스트
kubectl get nodes

# VIP 소유 노드 확인
vagrant ssh master-2 -c "ip addr show | grep 192.168.56.100"
vagrant ssh master-3 -c "ip addr show | grep 192.168.56.100"
```

**Step 2: master-1 VM 재시작**

```bash
vagrant halt master-1
vagrant up master-1

# 기동 후 상태 확인
vagrant ssh master-1 -c "sudo systemctl status kubelet"
vagrant ssh master-1 -c "sudo crictl ps | grep -E 'etcd|apiserver|controller|scheduler'"
```

**Step 3: NFS 서버 복구**

```bash
# NFS 서비스 상태 확인
vagrant ssh master-1 -c "sudo systemctl status nfs-kernel-server"

# NFS 서비스 재시작
vagrant ssh master-1 -c "sudo systemctl restart nfs-kernel-server"

# NFS exports 재적용
vagrant ssh master-1 -c "sudo exportfs -ra"

# NFS 마운트 확인 (worker 노드에서)
vagrant ssh worker-1 -c "df -h | grep nfs || showmount -e 192.168.56.10"
```

**Step 4: dnsmasq 서비스 확인**

```bash
# dnsmasq 상태 확인
vagrant ssh master-1 -c "sudo systemctl status dnsmasq"

# dnsmasq 재시작
vagrant ssh master-1 -c "sudo systemctl restart dnsmasq"

# DNS 해석 테스트
vagrant ssh master-1 -c "dig @192.168.56.10 keycloak.local.narwhal.internal"
```

**Step 5: NFS 기반 PVC Pod 복구**

```bash
# NFS 관련 PVC 상태 확인
kubectl get pvc -A | grep nfs-csi

# NFS I/O 오류로 Pending/Unknown 상태인 Pod 재시작
kubectl get pods -A --field-selector=status.phase=Unknown
kubectl delete pod <pod-name> -n <namespace> --force --grace-period=0
```

**Step 6: kube-vip 재확인 (master-1 복귀 후)**

```bash
# master-1이 복귀하면 VIP가 다시 master-1으로 돌아올 수 있음
# kube-vip은 선출 기반이므로 자동 처리

# kube-vip Pod 로그 확인
kubectl logs -n kube-system kube-vip-master-1 --tail=20

# 현재 VIP 소유 확인
vagrant ssh master-1 -c "ip addr show | grep 192.168.56.100"
```

### 검증

```bash
# 전체 노드 Ready 확인
kubectl get nodes

# API 서버 접근 (VIP 경유)
kubectl --server=https://192.168.56.100:6443 get nodes

# NFS PVC 정상화 확인
kubectl get pvc -A | grep -v Bound

# dnsmasq DNS 확인
vagrant ssh master-1 -c "dig @127.0.0.1 grafana.local.narwhal.internal +short"
```

---

## 4. etcd 클러스터 복구

### etcd 상태 진단

```bash
# etcd 멤버 목록 및 상태 (master-1에서 실행)
vagrant ssh master-1

kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl member list \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  -w table

# 헬스 체크
kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl endpoint health \
  --endpoints=https://192.168.56.10:2379,https://192.168.56.11:2379,https://192.168.56.12:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  -w table
```

### 시나리오 1: 2/3 멤버 정상 (쿼럼 유지)

etcd가 2개 이상 정상이면 클러스터 자동 복구됩니다.
[단일 Master 노드 장애 절차](#2-단일-master-노드-장애-master-23)를 따르세요.

### 시나리오 2: 1/3 멤버만 정상 (쿼럼 손실)

**Step 1: 단일 멤버로 etcd 강제 복구**

```bash
# 정상인 master (예: master-1)에 SSH 접속
vagrant ssh master-1

# etcd를 force-new-cluster 모드로 재시작
sudo sed -i 's|--initial-cluster-state=existing|--initial-cluster-state=new|' \
  /etc/kubernetes/manifests/etcd.yaml
sudo sed -i '/--initial-cluster=/s/,.*//' \
  /etc/kubernetes/manifests/etcd.yaml
# 또는 직접 편집
sudo vi /etc/kubernetes/manifests/etcd.yaml
# 아래 플래그 추가:
# --force-new-cluster=true

# kubelet이 Static Pod를 자동 재시작하도록 manifest 저장 후 대기
sleep 30
kubectl get pods -n kube-system | grep etcd
```

**Step 2: etcd 멤버 재추가 (master-2, master-3)**

```bash
# master-1의 etcd에서 기존 멤버 제거
kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl member list \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key

# 각 멤버 제거 후 재추가 (master-2 예시)
kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl member add master-2 \
  --peer-urls=https://192.168.56.11:2380 \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key

# master-2에서 etcd 데이터 초기화 후 재시작
vagrant ssh master-2 -c "sudo rm -rf /var/lib/etcd/member && sudo systemctl restart kubelet"
```

### 시나리오 3: etcd Snapshot에서 복구

**Step 1: 스냅샷 백업 생성 (정기 실행 권장)**

```bash
vagrant ssh master-1

# 스냅샷 저장
kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl snapshot save /var/lib/etcd/backup-$(date +%Y%m%d-%H%M%S).db \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key

# 호스트로 복사 (안전 보관)
vagrant scp master-1:/var/lib/etcd/backup-*.db ./etcd-backup/
```

**Step 2: 스냅샷에서 복원**

```bash
vagrant ssh master-1

SNAPSHOT="/var/lib/etcd/backup-20260226-020000.db"

# API 서버, etcd Static Pod 중지
sudo mv /etc/kubernetes/manifests/etcd.yaml /tmp/etcd.yaml.bak
sudo mv /etc/kubernetes/manifests/kube-apiserver.yaml /tmp/kube-apiserver.yaml.bak
sleep 10

# 기존 etcd 데이터 백업
sudo mv /var/lib/etcd /var/lib/etcd-old

# 스냅샷에서 복원
sudo etcdctl snapshot restore ${SNAPSHOT} \
  --name=master-1 \
  --initial-cluster=master-1=https://192.168.56.10:2380,master-2=https://192.168.56.11:2380,master-3=https://192.168.56.12:2380 \
  --initial-cluster-token=narwhal-etcd \
  --initial-advertise-peer-urls=https://192.168.56.10:2380 \
  --data-dir=/var/lib/etcd

# Static Pod manifest 복구
sudo mv /tmp/etcd.yaml.bak /etc/kubernetes/manifests/etcd.yaml
sudo mv /tmp/kube-apiserver.yaml.bak /etc/kubernetes/manifests/kube-apiserver.yaml
sleep 30
kubectl get nodes
```

### 검증

```bash
# etcd 헬스 확인
kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl endpoint health \
  --endpoints=https://192.168.56.10:2379,https://192.168.56.11:2379,https://192.168.56.12:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  -w table

# API 서버 접근 확인
kubectl get nodes
kubectl get pods -n kube-system
```

---

## 5. 데이터베이스 (CNPG) 복구

Narwhal은 단일 CNPG 클러스터 `narwhal-db` (namespace: `database`)에
keycloak, harbor, gitea 3개 DB를 통합 운영합니다.

### 상태 진단

```bash
# CNPG 클러스터 상태
kubectl get cluster narwhal-db -n database
kubectl get pods -n database -l cnpg.io/cluster=narwhal-db

# 현재 Primary 확인
kubectl get cluster narwhal-db -n database \
  -o jsonpath='{.status.currentPrimary}'

# PgBouncer Pooler 확인
kubectl get pooler narwhal-db-pooler-rw -n database
kubectl get pods -n database -l cnpg.io/poolerName=narwhal-db-pooler-rw

# 이벤트 확인
kubectl get events -n database --sort-by='.lastTimestamp' | tail -20
```

### 시나리오 1: 단일 인스턴스 장애 (Replica 장애)

CNPG Operator가 자동으로 PVC 재생성 및 WAL replay로 복구합니다.

```bash
# 문제 있는 Pod 확인
kubectl get pods -n database

# 자동 복구 대기 (약 5-10분)
kubectl get pods -n database -w

# 자동 복구가 안 될 경우: 문제 Pod와 PVC 삭제
kubectl delete pod narwhal-db-2 -n database
kubectl delete pvc narwhal-db-2 -n database
# CNPG가 자동으로 PVC 재생성 및 WAL replay 수행
```

### 시나리오 2: Primary 장애 (자동 Failover)

CNPG Operator가 자동으로 Replica를 Primary로 승격합니다.

```bash
# Failover 진행 모니터링
kubectl get cluster narwhal-db -n database -w

# 수동 Failover (특정 인스턴스로)
kubectl cnpg promote narwhal-db narwhal-db-2 -n database

# Primary 변경 확인
kubectl get cluster narwhal-db -n database \
  -o jsonpath='{.status.currentPrimary}{"\n"}'
```

### 시나리오 3: 전체 클러스터 장애

```bash
# CNPG Operator 재시작
kubectl rollout restart deployment cnpg-controller-manager -n platform-system

# Operator Ready 대기
kubectl wait --for=condition=Available deployment/cnpg-controller-manager \
  -n platform-system --timeout=120s

# narwhal-db 클러스터 재생성 (데이터 손실 없음: PVC 유지)
kubectl delete cluster narwhal-db -n database
# PVC는 유지됨 (finalizer가 없으면 즉시 재생성 가능)
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/07-cnpg.sh"
```

### 시나리오 4: CNPG Backup에서 PITR 복원

```bash
# 사용 가능한 백업 목록
kubectl get backup -n database

# 특정 시점으로 복원 (Point-in-Time Recovery)
cat <<EOF | kubectl apply -f -
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: narwhal-db-restored
  namespace: database
spec:
  instances: 2
  bootstrap:
    recovery:
      source: narwhal-db
      recoveryTarget:
        targetTime: "2026-02-26 02:00:00"
  externalClusters:
    - name: narwhal-db
      barmanObjectStore:
        serverName: narwhal-db
        s3Credentials:
          accessKeyId:
            name: cnpg-s3-credentials
            key: ACCESS_KEY_ID
          secretAccessKey:
            name: cnpg-s3-credentials
            key: ACCESS_SECRET_KEY
        endpointURL: http://seaweedfs-s3.storage.svc.cluster.local:8333
        destinationPath: s3://cnpg-backups/
EOF
```

### PgBouncer 재시작

연결 풀링 문제 발생 시:

```bash
kubectl rollout restart deployment \
  -n database -l cnpg.io/poolerName=narwhal-db-rw

kubectl wait --for=condition=Available deployment/narwhal-db-pooler-rw \
  -n database --timeout=60s
```

### 검증

```bash
# DB 연결 테스트 (임시 pod)
kubectl run -it --rm pg-test \
  --image=ghcr.io/cloudnative-pg/postgresql:18 \
  --restart=Never \
  --namespace=database \
  -- psql -h narwhal-db-rw.database.svc.cluster.local \
     -U keycloak -d keycloak -c "\l"

# PgBouncer 경유 테스트
kubectl run -it --rm pg-test \
  --image=ghcr.io/cloudnative-pg/postgresql:18 \
  --restart=Never \
  --namespace=database \
  -- psql -h narwhal-db-pooler-rw.database.svc.cluster.local \
     -U keycloak -d keycloak -c "SELECT 1;"
```

---

## 6. Velero 전체 클러스터 복원

Velero는 SeaweedFS S3(`http://seaweedfs-s3.storage.svc.cluster.local:8333`)에 백업을 저장합니다.

### 전제조건

- 새 클러스터 Phase 1 완료 (kubeadm init, CNI, addons까지)
- Velero 설치 완료
- SeaweedFS(또는 외부 S3) 접근 가능

### 백업 목록 확인

```bash
# 현재 사용 가능한 백업 목록
kubectl exec -n storage deployment/velero -- velero backup get

# 백업 상세 정보 (포함된 네임스페이스, 완료 시각)
kubectl exec -n storage deployment/velero -- \
  velero backup describe daily-full-<날짜> --details
```

### 전체 클러스터 복원 절차

**Step 1: 최신 백업 확인**

```bash
LATEST_BACKUP=$(kubectl exec -n storage deployment/velero -- \
  velero backup get -o json | \
  jq -r '[.items[] | select(.status.phase=="Completed")] | \
  sort_by(.status.completionTimestamp) | last | .metadata.name')
echo "최신 백업: ${LATEST_BACKUP}"
```

**Step 2: 복원 실행**

```bash
# 전체 클러스터 복원
kubectl exec -n storage deployment/velero -- \
  velero restore create full-restore-$(date +%Y%m%d) \
  --from-backup ${LATEST_BACKUP} \
  --include-namespaces '*' \
  --exclude-namespaces kube-system,storage,monitoring

# 복원 진행 모니터링
kubectl exec -n storage deployment/velero -- \
  velero restore describe full-restore-$(date +%Y%m%d) --details
```

**Step 3: 복원 상태 확인**

```bash
# 복원 완료 대기
kubectl exec -n storage deployment/velero -- \
  velero restore get

# 복원 로그 확인 (오류 메시지 검색)
kubectl exec -n storage deployment/velero -- \
  velero restore logs full-restore-$(date +%Y%m%d) | grep -i error | head -20
```

**Step 4: 네임스페이스별 리소스 확인**

```bash
# 주요 네임스페이스 리소스 복원 확인
for ns in iam devtools monitoring; do
  echo "=== ${ns} ==="
  kubectl get pods -n ${ns} | head -10
done

# PVC 복원 확인
kubectl get pvc -A | grep -v Bound
```

**Step 5: Phase 2 스크립트로 미복원 컴포넌트 재설치**

Velero 복원 후에도 일부 컴포넌트(cert-manager webhook, APISIX 등)가 누락될 수 있습니다.

```bash
# cert-manager, APISIX, MetalLB 재확인
vagrant ssh master-1 -c "kubectl get pods -n platform-system"

# 문제 있는 컴포넌트 재설치 (전체 Phase 2 재실행)
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/06-phase2-start.sh"
```

**Step 6: DNS/라우팅 확인**

```bash
# MetalLB LB IP 확인
kubectl get svc -n platform-system apisix-gateway -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
# 결과: 192.168.56.200 이어야 함

# ApisixRoute 정상화 확인
kubectl get apisixroute -A

# DNS 해석 테스트
dig @192.168.56.10 grafana.local.narwhal.internal +short
# 결과: 192.168.56.200 이어야 함
```

### 개별 네임스페이스 복원

특정 앱만 복원할 경우:

```bash
# Gitea만 복원 (예시)
kubectl exec -n storage deployment/velero -- \
  velero restore create restore-gitea-$(date +%Y%m%d) \
  --from-backup daily-gitea-<날짜> \
  --include-namespaces devtools \
  --selector 'app.kubernetes.io/name=gitea'

# Harbor만 복원 (예시)
kubectl exec -n storage deployment/velero -- \
  velero restore create restore-harbor-$(date +%Y%m%d) \
  --from-backup daily-harbor-<날짜> \
  --include-namespaces devtools \
  --selector 'app.kubernetes.io/name=harbor'
```

### 검증

```bash
# 전체 클러스터 검증
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh"

# SSO 통합 테스트
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh"
```

---

## 7. 인증서 만료 대응

cert-manager가 self-signed CA 기반 wildcard 인증서(`*.local.narwhal.internal`)를 자동 관리합니다.

### 인증서 상태 진단

```bash
# 전체 인증서 목록 및 만료일 확인
kubectl get certificates -A
kubectl get certificates -A -o json | \
  jq -r '.items[] | "\(.metadata.namespace)/\(.metadata.name): \(.status.notAfter)"'

# cert-manager Pod 상태 확인
kubectl get pods -n cert-manager

# Certificate 이벤트 확인
kubectl describe certificate narwhal-wildcard-cert -n cert-manager | grep -A 10 Events
```

### 시나리오 1: cert-manager 자동 갱신 실패

```bash
# CertificateRequest 상태 확인
kubectl get certificaterequests -n cert-manager

# 갱신 실패한 Certificate 강제 갱신
# TLS Secret 삭제 → cert-manager가 자동 재발급
kubectl delete secret narwhal-wildcard-tls -n cert-manager

# 갱신 진행 확인 (약 30초 이내)
kubectl get certificate narwhal-wildcard-cert -n cert-manager -w
```

### 시나리오 2: CA 인증서 만료

```bash
# CA 인증서 만료일 확인
kubectl get secret narwhal-ca-cert -n cert-manager \
  -o jsonpath='{.data.tls\.crt}' | base64 -d | \
  openssl x509 -noout -dates

# CA가 만료된 경우: ClusterIssuer/CA 재생성 (ArgoCD가 자동 재적용)
kubectl delete clusterissuer narwhal-ca-issuer
# cert-manager ArgoCD app sync
kubectl annotate application cert-manager \
  -n devtools argocd.argoproj.io/refresh=normal
```

### 시나리오 3: OIDC 인증서 관련 API 서버 장애

API 서버가 `--oidc-ca-file`로 Keycloak 인증서 검증 실패 시:

```bash
# API 서버 로그 확인
vagrant ssh master-1 -c "sudo journalctl -u kubelet -n 50 | grep oidc"
kubectl logs -n kube-system kube-apiserver-master-1 --tail=50 | grep oidc

# Keycloak 인증서 재추출
vagrant ssh master-1 -c "openssl s_client -connect keycloak.local.narwhal.internal:443 \
  -servername keycloak.local.narwhal.internal </dev/null 2>/dev/null | \
  openssl x509 -outform PEM > /etc/kubernetes/pki/oidc-ca.crt"

# API 서버 자동 재시작 (manifest 수정 시)
vagrant ssh master-1 -c "sudo touch /etc/kubernetes/manifests/kube-apiserver.yaml"
```

### 앱 네임스페이스 CA cert 배포 확인

cert-manager는 각 앱 네임스페이스에 CA cert를 복사해야 합니다:

```bash
# CA cert Secret이 없는 네임스페이스 확인
for ns in iam devtools monitoring headlamp; do
  echo -n "${ns}: "
  kubectl get secret narwhal-ca-cert -n ${ns} 2>/dev/null && echo "OK" || echo "MISSING"
done

# 누락된 네임스페이스에 수동 복사 (cert-manager ArgoCD 앱 재동기화로도 가능)
kubectl get secret narwhal-ca-cert -n cert-manager -o yaml | \
  sed 's/namespace: cert-manager/namespace: headlamp/' | \
  kubectl apply -f -
```

### 검증

```bash
# 인증서 Ready 상태 확인
kubectl get certificates -A | grep -v True

# HTTPS 접근 테스트
curl -k https://grafana.local.narwhal.internal/api/health
curl -k https://keycloak.local.narwhal.internal/health/ready
```

---

## 8. Keycloak SSO 장애

### 증상

- 모든 앱(Grafana, Harbor, Gitea, ArgoCD, Headlamp 등) 로그인 불가
- OAuth2-Proxy 502/503 응답
- 브라우저에서 `ERR_CONNECTION_REFUSED` 또는 OIDC 에러

### 상태 진단

```bash
# Keycloak Pod 상태
kubectl get pods -n iam -l app=keycloak

# Keycloak 이벤트
kubectl get events -n iam --sort-by='.lastTimestamp' | tail -20

# Keycloak 로그
kubectl logs -n iam -l app=keycloak --tail=100 | grep -i "error\|warn\|fatal"

# OAuth2-Proxy 상태
kubectl get pods -n devtools -l app.kubernetes.io/name=oauth2-proxy
kubectl logs -n devtools -l app.kubernetes.io/name=oauth2-proxy --tail=50

# DB 연결 확인
kubectl get cluster narwhal-db -n database
kubectl get pods -n database -l cnpg.io/cluster=narwhal-db
```

### 복구 절차

**Step 1: Keycloak Pod 재시작**

```bash
kubectl rollout restart deployment keycloak-operator -n iam
# Operator가 Keycloak CR을 재조정

# 또는 Keycloak StatefulSet/Deployment 직접 재시작
kubectl get deployment,statefulset -n iam
kubectl rollout restart statefulset keycloak -n iam
```

**Step 2: DB 연결 확인**

```bash
# CNPG 클러스터 상태
kubectl get cluster narwhal-db -n database

# Keycloak DB Secret 확인
kubectl get secret narwhal-db-credentials -n database -o jsonpath='{.data.password}' | base64 -d

# ExternalName Service 확인
kubectl get svc keycloak-db-rw -n iam
```

**Step 3: OIDC 엔드포인트 접근 테스트**

```bash
# Keycloak HTTPS 엔드포인트 테스트
kubectl run -it --rm curl-test \
  --image=curlimages/curl:latest \
  --restart=Never \
  -- curl -k https://keycloak.local.narwhal.internal/realms/kubernetes/.well-known/openid-configuration

# 내부 서비스 경유 테스트
kubectl run -it --rm curl-test \
  --image=curlimages/curl:latest \
  --restart=Never \
  -- curl http://keycloak-service.iam.svc.cluster.local:8080/realms/kubernetes/.well-known/openid-configuration
```

**Step 4: OAuth2-Proxy 재시작**

```bash
kubectl rollout restart deployment oauth2-proxy -n devtools
kubectl wait --for=condition=Available deployment/oauth2-proxy -n devtools --timeout=60s
```

**Step 5: Keycloak 재설정 (최후 수단)**

Realm 설정이 손상된 경우:

```bash
# Keycloak 재설치 (기존 DB 유지) - 4단계 순차 실행
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-1-keycloak-operator.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-2-keycloak-realm.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-3-keycloak-clients.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-4-keycloak-apiserver.sh"
```

### 중요 설정 검증

```bash
# SSO 통합 테스트 전체 실행
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/test-sso.sh"

# OIDC 토큰 발급 테스트
KEYCLOAK_URL="https://keycloak.local.narwhal.internal"
vagrant ssh master-1 -c "curl -k -s -X POST \
  ${KEYCLOAK_URL}/realms/kubernetes/protocol/openid-connect/token \
  -d 'grant_type=password&client_id=kubernetes&username=admin&password=<pass>' \
  | jq .access_token"
```

### 알려진 문제: Istio Ambient Mesh Cookie 손상

SSO 콜백 후 `http: named cookie not present` 에러가 발생하면:

```bash
# SSO 웹 서버 Pod에 ambient opt-out 레이블 확인
kubectl get pods -n devtools -l 'app.kubernetes.io/name in (argocd-server,grafana,harbor-core)' \
  -o jsonpath='{range .items[*]}{.metadata.name}: {.metadata.labels.istio\.io/dataplane-mode}{"\n"}{end}'

# 레이블이 없으면 추가 (ArgoCD 관리 리소스의 경우 GitOps values 수정 필요)
kubectl label pod <pod-name> -n <namespace> istio.io/dataplane-mode=none
```

---

## 9. ArgoCD GitOps 장애

### 증상

- ArgoCD UI 접근 불가
- Application이 `OutOfSync` 또는 `Degraded` 상태 고착
- Gitea 리포지토리 접근 실패

### 상태 진단

```bash
# ArgoCD Pod 상태
kubectl get pods -n devtools -l app.kubernetes.io/part-of=argocd

# Application 상태
kubectl get applications -n devtools

# ArgoCD 서버 로그
kubectl logs -n devtools deployment/argocd-server --tail=50

# repo-server 로그 (Git 접근 문제 확인)
kubectl logs -n devtools deployment/argocd-repo-server --tail=50
```

### 복구 절차

**Step 1: ArgoCD Pod 재시작**

```bash
kubectl rollout restart deployment \
  argocd-server argocd-repo-server argocd-application-controller \
  -n devtools

kubectl wait --for=condition=Available deployment/argocd-server \
  -n devtools --timeout=120s
```

**Step 2: Gitea 연결 확인**

```bash
# Gitea 서비스 확인
kubectl get pods -n devtools -l app=gitea

# ArgoCD → Gitea 연결 테스트
kubectl exec -n devtools deployment/argocd-repo-server -- \
  curl -s http://gitea-http.gitea.svc.cluster.local:3000/gitea-admin/narwhal.git/info/refs?service=git-upload-pack | head -5
```

**Step 3: Application 강제 동기화**

```bash
# 특정 앱 강제 동기화
kubectl patch application harbor \
  -n devtools \
  --type merge \
  -p '{"operation":{"initiatedBy":{"username":"admin"},"sync":{"revision":"HEAD","force":true}}}'

# 전체 앱 동기화 (argocd CLI 사용)
vagrant ssh master-1 -c "argocd app sync --all --server argocd.local.narwhal.internal"
```

**Step 4: App-of-Apps 재bootstrap**

```bash
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/14-gitops-bootstrap.sh"
```

### ClusterRoleBinding namespace 불일치 수정

ArgoCD를 `devtools` 네임스페이스에 설치 시 발생할 수 있는 권한 문제:

```bash
# Subject namespace 확인
kubectl get clusterrolebinding argocd-application-controller \
  -o jsonpath='{.subjects[0].namespace}'

# devtools로 수정
for crb in argocd-application-controller argocd-applicationset-controller argocd-server; do
  kubectl patch clusterrolebinding ${crb} \
    --type json \
    -p '[{"op":"replace","path":"/subjects/0/namespace","value":"devtools"}]'
done
```

---

## 10. NFS 스토리지 장애

NFS는 master-1 전용으로 `/srv/nfs/k8s`를 제공합니다.

### 증상

```bash
# PVC가 Pending 상태
kubectl get pvc -A | grep Pending

# Pod가 ContainerCreating 고착
kubectl get pods -A | grep ContainerCreating
kubectl describe pod <pod-name> -n <namespace> | grep -A 5 "Warning"
# "Unable to attach or mount volumes: ...NFS..." 에러
```

### 복구 절차

**Step 1: NFS 서버 상태 확인**

```bash
# NFS 서비스 상태
vagrant ssh master-1 -c "sudo systemctl status nfs-kernel-server"
vagrant ssh master-1 -c "sudo exportfs -v"

# NFS 공유 디렉토리 확인
vagrant ssh master-1 -c "ls -la /srv/nfs/k8s/"
vagrant ssh master-1 -c "df -h /srv/nfs/k8s"
```

**Step 2: NFS 서비스 재시작**

```bash
vagrant ssh master-1 -c "sudo systemctl restart nfs-kernel-server"
vagrant ssh master-1 -c "sudo exportfs -ra"

# worker 노드에서 NFS 마운트 테스트
vagrant ssh worker-1 -c "sudo mount -t nfs 192.168.56.10:/srv/nfs/k8s /tmp/nfs-test && \
  ls /tmp/nfs-test && sudo umount /tmp/nfs-test"
```

**Step 3: csi-driver-nfs 재시작**

```bash
kubectl rollout restart daemonset csi-nfs-node -n kube-system
kubectl rollout restart deployment csi-nfs-controller -n kube-system

# CSI 드라이버 Pod 상태 확인
kubectl get pods -n kube-system -l app=csi-nfs-node
```

**Step 4: PVC 재생성 필요 시**

```bash
# 문제 PVC의 PV 확인
kubectl get pv | grep <pvc-name>

# PVC 삭제 후 재생성 (데이터 손실 주의!)
kubectl delete pvc <pvc-name> -n <namespace>
# ArgoCD가 자동으로 PVC를 재생성하거나 앱 Helm values에서 재생성
```

**Step 5: 디스크 공간 부족 시**

```bash
# NFS 서버 디스크 사용량
vagrant ssh master-1 -c "df -h /srv/nfs/k8s"
vagrant ssh master-1 -c "du -sh /srv/nfs/k8s/*/ | sort -rh | head -10"

# 오래된 Velero 백업 정리 (Velero가 정상일 경우)
kubectl exec -n storage deployment/velero -- \
  velero backup delete --older-than 720h --confirm

# 직접 정리 (주의: 사용 중인 PVC 삭제 금지)
vagrant ssh master-1 -c "sudo ls /srv/nfs/k8s/"
```

### 검증

```bash
# PVC 전체 Bound 확인
kubectl get pvc -A | grep -v Bound

# NFS PV 상태 확인
kubectl get pv | grep nfs
```

---

## 11. Istio Ambient Mesh 장애

### 증상

- 서비스 간 통신 불가 (mTLS 핸드셰이크 실패)
- Pod가 CrashLoopBackOff (kubelet probe 차단)
- SSO 쿠키 손상 (`http: named cookie not present`)

### 상태 진단

```bash
# Istio 컴포넌트 상태
kubectl get pods -n istio-system

# ztunnel 로그 (mTLS 문제 확인)
kubectl logs -n istio-system -l app=ztunnel --tail=50 | grep -i "error\|warn"

# Ambient 네임스페이스 확인
kubectl get ns -L istio.io/dataplane-mode

# PeerAuthentication 정책 확인
kubectl get peerauthentication -A
```

### 복구 절차

**Step 1: ztunnel DaemonSet 재시작**

```bash
kubectl rollout restart daemonset ztunnel -n istio-system
kubectl wait --for=condition=Ready pod -l app=ztunnel -n istio-system --timeout=120s
```

**Step 2: istiod 재시작**

```bash
kubectl rollout restart deployment istiod -n istio-system
kubectl wait --for=condition=Available deployment/istiod -n istio-system --timeout=120s
```

**Step 3: CrashLoopBackOff Pod 진단 (probe 차단)**

```bash
# 영향 받는 Pod 확인
kubectl get pods -A | grep CrashLoopBackOff

# Pod의 ambient opt-out 레이블 확인
kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.metadata.labels.istio\.io/dataplane-mode}'
# SSO 웹 서버는 "none"이어야 함

# opt-out 레이블 추가 (임시 조치)
kubectl label pod <pod-name> -n <namespace> istio.io/dataplane-mode=none
```

**Step 4: Cilium + Istio 공존 설정 확인**

```bash
# Cilium ConfigMap 확인 (cni.exclusive=false, socketLB.hostNamespaceOnly=true)
kubectl get cm cilium-config -n kube-system -o yaml | \
  grep -E "cni-exclusive|socket-lb-host-ns-only"

# 값이 없거나 잘못된 경우
kubectl patch cm cilium-config -n kube-system \
  --type merge \
  -p '{"data":{"cni-exclusive":"false","socket-lb-host-ns-only":"true"}}'

# Cilium DaemonSet 재시작
kubectl rollout restart daemonset cilium -n kube-system
```

**Step 5: NetworkPolicy HBONE 포트 (15008) 누락**

```bash
# Ambient 네임스페이스의 NetworkPolicy에 15008 포트 누락 여부 확인
kubectl get networkpolicy -n devtools -o yaml | grep -A 5 "port: 15008"

# 누락 시 별도 NetworkPolicy 생성 (예: devtools 네임스페이스)
cat <<EOF | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-hbone
  namespace: devtools
spec:
  podSelector: {}
  policyTypes:
    - Ingress
  ingress:
    - ports:
        - port: 15008
          protocol: TCP
EOF
```

---

## 12. OpenBao 장애 및 Unseal

OpenBao는 VM 재시작 시마다 sealed 상태로 기동됩니다.

### 증상

```bash
# OpenBao Pod가 Running이지만 시크릿 접근 불가
kubectl get pods -n storage -l app.kubernetes.io/name=openbao

# Sealed 상태 확인
kubectl exec -n storage openbao-0 -- bao status
# Sealed: true  ← unseal 필요
```

### 복구 절차

**Unseal 실행**

```bash
# Unseal Key는 최초 초기화 시 안전하게 보관해야 함
vagrant ssh master-1

# Sealed 상태 확인
kubectl exec -n storage openbao-0 -- bao status

# Unseal 실행 (저장해 둔 Unseal Key 사용)
kubectl exec -n storage openbao-0 -- bao operator unseal <UNSEAL_KEY>

# 상태 확인
kubectl exec -n storage openbao-0 -- bao status
# Sealed: false 확인
```

### OpenBao 완전 초기화 (데이터 손실)

Unseal Key 분실 시:

```bash
# OpenBao 데이터 삭제 후 재초기화 (모든 시크릿 손실!)
kubectl delete pvc data-openbao-0 -n storage
kubectl delete pod openbao-0 -n storage

# 재기동 후 재초기화
kubectl exec -n storage openbao-0 -- bao operator init \
  -key-shares=1 \
  -key-threshold=1

# 출력되는 Unseal Key와 Root Token을 반드시 저장
kubectl exec -n storage openbao-0 -- bao operator unseal <NEW_UNSEAL_KEY>
```

### Velero에서 OpenBao 데이터 복원

```bash
# OpenBao 백업 (daily-openbao 스케줄)
kubectl exec -n storage deployment/velero -- velero backup get | grep openbao

# 복원
kubectl exec -n storage deployment/velero -- \
  velero restore create restore-openbao-$(date +%Y%m%d) \
  --from-backup daily-openbao-<날짜> \
  --include-namespaces storage \
  --selector app.kubernetes.io/name=openbao
```

---

## 13. 전체 클러스터 재구축

데이터 손실 없이 전체 클러스터를 재구축하는 절차입니다.

### 전제조건

- Velero 백업이 외부 스토리지(SeaweedFS S3 또는 외부 S3)에 있는 경우
- etcd 스냅샷 백업 보유

### 절차

**Step 1: 사전 백업 확인**

```bash
# 최신 Velero 백업 확인
kubectl exec -n storage deployment/velero -- velero backup get

# etcd 스냅샷 수동 생성
vagrant ssh master-1 -c "kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl snapshot save /tmp/etcd-backup-$(date +%Y%m%d).db \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key"

# 로컬에 복사
vagrant scp master-1:/tmp/etcd-backup-$(date +%Y%m%d).db ./
```

**Step 2: 클러스터 삭제 및 재생성**

```bash
# 전체 VM 삭제
vagrant destroy -f

# 클러스터 재생성 (Phase 1)
vagrant up --provider=vmware_desktop

# Phase 1 완료 후 Phase 2 실행
vagrant provision master-1 --provision-with phase2-platform
```

**Step 3: Velero 복원**

[6번 항목: Velero 전체 클러스터 복원](#6-velero-전체-클러스터-복원) 절차를 따르세요.

**Step 4: Phase 2 스크립트 재실행 (필요 시)**

```bash
# Keycloak SSO 재설정 - 4단계 순차 실행
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-1-keycloak-operator.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-2-keycloak-realm.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-3-keycloak-clients.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-4-keycloak-apiserver.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/12-gitea.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/13-argocd.sh"
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/14-gitops-bootstrap.sh"
```

**Step 5: OpenBao Unseal**

[12번 항목: OpenBao Unseal](#12-openbao-장애-및-unseal) 절차를 따르세요.

---

## 부록: 빠른 진단 명령어

### 전체 클러스터 상태 한눈에 보기

```bash
# 노드 상태
kubectl get nodes -o wide

# 문제 있는 Pod 전체 확인
kubectl get pods -A | grep -v Running | grep -v Completed

# 최근 이벤트 (Warning 포함)
kubectl get events -A --sort-by='.lastTimestamp' | grep Warning | tail -30

# 전체 검증 스크립트 실행
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/verify-cluster.sh"
```

### 컴포넌트별 빠른 상태 확인

```bash
# 클러스터 핵심 컴포넌트
kubectl get pods -n kube-system
kubectl get pods -n platform-system

# 네트워킹 (Cilium + Istio)
kubectl get pods -n kube-system -l k8s-app=cilium
kubectl get pods -n istio-system

# 스토리지
kubectl get pods -n database     # CNPG PostgreSQL
kubectl get pods -n storage      # SeaweedFS, Velero, OpenBao

# 플랫폼 앱
kubectl get pods -n iam          # Keycloak
kubectl get pods -n devtools     # Gitea, ArgoCD, Harbor, Headlamp, OAuth2-Proxy

# 모니터링
kubectl get pods -n monitoring   # Prometheus, Grafana, Loki, Tempo
```

### VIP / LB / DNS 진단

```bash
# VIP (192.168.56.100) 소유 노드 확인
for node in master-1 master-2 master-3; do
  echo -n "${node}: "
  vagrant ssh ${node} -c "ip addr show | grep 192.168.56.100 && echo VIP_HERE" 2>/dev/null || echo "-"
done

# MetalLB LB IP 확인
kubectl get svc -n platform-system apisix-gateway -o jsonpath='{.status.loadBalancer.ingress[0].ip}'

# dnsmasq DNS 테스트
vagrant ssh master-1 -c "dig @127.0.0.1 grafana.local.narwhal.internal +short"
vagrant ssh master-1 -c "dig @127.0.0.1 keycloak.local.narwhal.internal +short"
```

### etcd 빠른 진단

```bash
# etcd 멤버 목록
vagrant ssh master-1 -c "kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl member list \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  -w table"

# etcd 헬스 체크
vagrant ssh master-1 -c "kubectl exec -n kube-system etcd-master-1 -- \
  etcdctl endpoint health \
  --endpoints=https://192.168.56.10:2379,https://192.168.56.11:2379,https://192.168.56.12:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key"
```

### 로그 수집 (트러블슈팅 시)

```bash
# 특정 Pod 로그 (최근 100줄)
kubectl logs -n <namespace> <pod-name> --tail=100

# 이전 Pod 로그 (CrashLoopBackOff 분석 시)
kubectl logs -n <namespace> <pod-name> --previous --tail=100

# 전체 네임스페이스 Pod 로그 병합
kubectl logs -n <namespace> -l app=<label> --all-containers --tail=50

# kubelet 로그 (노드 문제 시)
vagrant ssh master-1 -c "sudo journalctl -u kubelet -n 100 --no-pager"
```

---

## 관련 문서

- [reboot-survivability.md](reboot-survivability.md) - VM 리부트 생존성 아키텍처
- [operations.md](operations.md) - 일상 운영 가이드
- [troubleshooting.md](../common/troubleshooting.md) - 트러블슈팅 가이드
- [architecture.md](../common/architecture.md) - 아키텍처 개요
- [database.md](../common/database.md) - 데이터베이스 관리
- [dns-access.md](dns-access.md) - 서비스 URL, SSO 로그인, 기본 자격 증명
- [dns-access.md](dns-access.md) - DNS 및 접근 방법
