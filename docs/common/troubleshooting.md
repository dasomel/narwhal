# Narwhal 트러블슈팅 가이드

> **범위:** 대부분은 배포 대상과 무관한 플랫폼 계층이다. **3(kube-vip)·4(VMware
> 네트워킹)·5(Phase 2 프로비저닝)·13(VM Clock Skew)절은 Vagrant 전용**이며 Kakao Cloud에는
> 해당하지 않는다. 클라우드 쪽 증상은 [`../kakao/cloud-deployment.md`](../kakao/cloud-deployment.md)와
> [`../kakao/service-domains.md`](../kakao/service-domains.md)의 문제 해결 절을 본다.

## 1. K8s 1.35 OIDC HTTPS 요구사항

**증상**: API 서버가 시작 직후 crash, `kube-apiserver` Pod가 CrashLoopBackOff

**에러 메시지**:
```
jwt[0].issuer.url: Invalid value: "http://...": URL scheme must be https
```

**원인**: K8s 1.35는 `--oidc-*` 플래그를 내부적으로 StructuredAuthenticationConfiguration으로 변환하며, HTTPS가 필수

**해결법**:
- cert-manager + APISIX TLS가 설치된 후에만 OIDC 플래그 활성화
- 설치 순서: 08-1-networking (cert-manager/APISIX) → 09-istio-ambient → 10-dnsmasq → 11-1~11-4-keycloak-* (OIDC)
- 긴급 복구: `/etc/kubernetes/manifests/kube-apiserver.yaml`에서 `--oidc-*` 플래그 주석 처리

**검증**:
```bash
# HTTPS 엔드포인트 확인
curl -k https://keycloak.local.narwhal.internal/realms/kubernetes/.well-known/openid-configuration
# API 서버 로그 확인
kubectl logs -n kube-system kube-apiserver-master-1 --tail=50
```

## 2. Distroless 컨테이너

**증상**: `kubectl exec` 시 `OCI runtime exec failed: exec failed: unable to start container process: exec: "sh": executable file not found`

**영향 받는 컨테이너**:

| 컨테이너 | 이미지 | 대안 |
|-----------|--------|------|
| etcd | `registry.k8s.io/etcd` | `kubectl exec -- etcdctl ...` 직접 호출 (sh -c 불가) |
| kubectl | `registry.k8s.io/kubectl` | `docker.io/alpine/k8s:1.31.4` 사용 |
| kube-vip | `ghcr.io/kube-vip/kube-vip` | kubeconfig를 `/.kube/config`에 마운트 (HOME=/) |

**musl/glibc 비호환**:
- `alpine/k8s` (musl) 바이너리를 glibc 컨테이너에서 실행 불가
- `exec: no such file or directory` 에러 발생 (dynamic linker 경로 다름)
- Velero: `upgradeCRDs: false` 설정 필수 (CRD hook이 musl kubectl 사용)

## 3. kube-vip

**증상 1**: VIP (192.168.56.100)에 접근 불가

**원인/해결**:
- `vip_subnet` 값: `"32"` 사용 (NOT `"/32"`) - 내부에서 `address + "/" + vip_subnet` 조합
- kubeconfig 마운트: `/.kube/config` (distroless 이미지 HOME=/)
- VIP 순환 의존성: kube-vip이 API에 접근해야 VIP를 바인딩하지만, admin.conf은 VIP 사용
  - 해결: `kube-vip.conf` 별도 생성, 로컬 master IP로 서버 주소 변경
  ```bash
  sudo cp /etc/kubernetes/admin.conf /etc/kubernetes/kube-vip.conf
  sudo sed -i "s|192.168.56.100:6443|192.168.56.10:6443|" /etc/kubernetes/kube-vip.conf
  ```

**증상 2**: master-1 초기화 시 chicken-and-egg 문제
- init 전에 kube-vip manifest를 생성하면 안 됨
- 00-kube-vip.sh: 이미지 pull + 설정 저장만
- 02-init-cluster.sh: 수동 VIP 바인딩 → kubeadm init → manifest 생성

**진단**:
```bash
# kube-vip Pod 로그
kubectl logs -n kube-system kube-vip-master-1
# VIP 바인딩 확인
ip addr show | grep 192.168.56.100
# kube-vip manifest 확인
cat /etc/kubernetes/manifests/kube-vip.yaml
```

## 4. VMware 네트워킹

**증상**: 노드가 NAT 인터페이스 IP (172.16.x.x)를 사용하여 클러스터에 조인

**원인**: VMware Vagrant 플러그인이 호스트 전용 네트워크 대신 NAT 인터페이스를 감지

**해결**:
- `kubeadm join` 시 `--apiserver-advertise-address=192.168.56.x` 명시
- `01-prerequisites.sh`에서 netplan 직접 생성 (VMware 플러그인 버그 대응)
- netplan 파일 `chmod 600` 필수 (`set -euo pipefail` 환경에서 경고가 에러 처리됨)

**진단**:
```bash
# 노드 IP 확인
kubectl get nodes -o wide
# netplan 설정 확인
cat /etc/netplan/50-vagrant.yaml
# 인터페이스 확인
ip addr show
```

## 5. Phase 2 프로비저닝 실패

**증상**: `vagrant provision master-1 --provision-with phase2-platform` 실행 후 앱 설치 실패

**일반적 원인**:
1. 모든 노드가 Ready 상태가 아님 → `kubectl get nodes` 확인
2. master-1 메모리 부족 (6GB 미만) → OOM으로 API 서버 재시작
3. NFS 서버 미실행 → PVC Pending

**진단**:
```bash
# 노드 상태
kubectl get nodes
# Pod 상태 전체 확인
kubectl get pods -A | grep -v Running | grep -v Completed
# 이벤트 확인
kubectl get events -A --sort-by='.lastTimestamp' | tail -20
# phase2 로그
cat /var/log/phase2-platform.log
```

**개별 스크립트 재실행**:
```bash
# 특정 스크립트만 재실행 (vagrant 밖에서)
vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/06-phase2-start.sh"
```

## 6. Pod 실패 (OOMKill, Disk Pressure)

**OOMKill**:
```bash
# OOMKill된 Pod 찾기
kubectl get pods -A | grep OOMKill
# 메모리 사용량 확인
kubectl top nodes
kubectl top pods -A --sort-by=memory | head -20
```

**Disk Pressure**:
```bash
# 디스크 사용량 확인
df -h /
# 이미지 정리
crictl rmi --prune
# 미사용 리소스 정리
kubectl delete pods --field-selector=status.phase=Failed -A
```

**리소스 조정**: Worker 메모리는 최소 6GB (platform apps 실행), Master는 4GB (control-plane only, NoSchedule taint), CNPG는 1 인스턴스 + 1 PgBouncer로 운영

## 7. ArgoCD 동기화 실패

**Field Manager 충돌**:
```
error: Apply failed with 1 conflict: conflict with "helm" using ...
```
해결: `kubectl apply --server-side --force-conflicts` 또는 `kubectl set image`로 직접 패치

**CRD 262KB 초과** (applicationsets):
```bash
kubectl apply --server-side --force-conflicts -f https://...applicationsets-crd.yaml
```

**repo-server GitHub Pages IPv6 실패**: VM에서 IPv6 미지원 시 transient 에러, 자동 복구 대기

**ArgoCD v3.x `server.insecure` 설정**:
- `argocd-cmd-params-cm` ConfigMap에 설정 (NOT `argocd-cm`)
- `argocd-cm`의 `server.insecure`는 legacy이며 무시됨

**진단**:
```bash
# 앱 동기화 상태
kubectl get applications -n argocd
# 특정 앱 상세
kubectl describe application <app-name> -n argocd
# repo-server 로그
kubectl logs -n argocd -l app.kubernetes.io/component=repo-server --tail=30
```

## 8. Helm 설치 실패

**`--wait` 타임아웃으로 릴리스 롤백**:
- `--wait`는 타임아웃 시 atomic 롤백 수행 → 비핵심 앱에서는 제거
- `--timeout`만 사용하면 롤백 없이 타임아웃

**`--set` 타입 에러** (float64 vs int64):
- 포트 번호 등 정수값을 `--set`으로 전달 시 타입 에러 발생
- 해결: values 파일로 전달

**`--set-string` 필요한 경우**:
- `nodeSelector` 등 boolean 문자열: `--set-string nodeSelector.key=true`

**진단**:
```bash
# 릴리스 상태
helm list -A
# 실패한 릴리스 히스토리
helm history <release-name> -n <namespace>
# 릴리스 values 확인
helm get values <release-name> -n <namespace>
```

## 9. DNS 해석 실패

**증상**: Pod에서 `*.local.narwhal.internal` 도메인 해석 불가

**확인 순서**:
```bash
# 1. dnsmasq 실행 확인 (master-1, master-2)
systemctl status dnsmasq

# 2. CoreDNS forward 설정 확인
kubectl get configmap coredns -n kube-system -o yaml | grep -A5 "local.narwhal.internal"

# 3. Pod에서 DNS 테스트
kubectl run -it --rm dns-test --image=alpine/k8s:1.31.4 --restart=Never -- nslookup keycloak.local.narwhal.internal

# 4. 노드 DNS 설정 확인
resolvectl status | grep -A3 "DNS Servers"
```

**일반적 원인**:
- CoreDNS가 `local.narwhal.internal`를 dnsmasq로 forward하지 않음 → `10-dnsmasq.sh` 재실행
- worker/master-2에서 public DNS로 해석 → systemd-resolved에 `Domains=~local.narwhal.internal` 설정 필요
- dnsmasq HA: 3개 master 모두 dnsmasq 실행, CoreDNS forward에 3개 모두 등록

## 10. Istio Ambient Mesh

**증상**: ztunnel/istio-cni Pod 실패, mTLS 미적용

**진단**:
```bash
# Istio 컴포넌트 상태
kubectl get pods -n istio-system
# ztunnel 로그
kubectl logs -n istio-system -l app=ztunnel --tail=50
# PeerAuthentication 확인
kubectl get peerauthentication -A
# ambient 네임스페이스 라벨
kubectl get ns -L istio.io/dataplane-mode
# Cilium CNI 공존 설정 확인
kubectl get cm cilium-config -n kube-system -o yaml | grep -E "cni-exclusive|socket-lb-host-ns-only"
```

**일반적 원인**:
- Cilium `cni.exclusive=false` 미설정 → Istio CNI 설정이 삭제됨 → `03-cni-install.sh` 재실행
- Cilium `socketLB.hostNamespaceOnly=true` 미설정 → ztunnel 트래픽 바이패스
- Istio CRD annotation 262KB 초과 → ArgoCD `ServerSideApply=true` 필요
- Istio 1.28은 K8s 1.35 미지원 → Istio 1.29.x 사용 필수
- 네임스페이스에 ambient 라벨 누락 → `kubectl label ns <ns> istio.io/dataplane-mode=ambient`

## 11. 메모리 압박

**증상**: Pod eviction, API 서버 느려짐

**진단**:
```bash
# 노드 메모리 사용량
kubectl top nodes
# 메모리 순 Pod 목록
kubectl top pods -A --sort-by=memory | head -20
# 노드 condition 확인
kubectl describe node master-1 | grep -A5 Conditions
```

**대응**:
- Worker 메모리 6GB 미만이면 Vagrantfile에서 증가 (platform apps는 worker에서 실행)
- CNPG 인스턴스 수 축소 (2 → 1)
- 불필요한 앱 스케일 다운: `kubectl scale deployment <name> --replicas=0 -n <ns>`
- 디스크 압력 toleration 추가: `node.kubernetes.io/disk-pressure:NoSchedule`

## 12. Keycloak SSO 문제

**`invalid_scope` 에러**:
- `groups` client scope가 realm-level로 생성되지 않음
- 해결: realm-level `groups` scope 생성 → `oidc-group-membership-mapper` 추가 → 전체 클라이언트에 default scope 할당

**`x509: certificate signed by unknown authority`**:
- 앱이 self-signed CA cert를 신뢰하지 않음
- 해결: 앱별 TLS skip 또는 CA cert 마운트
  - ArgoCD: `argocd-cm` → `oidc.tls.insecure.skip.verify: "true"`
  - Grafana: `auth.generic_oauth.tls_skip_verify_insecure: true`
  - Harbor: `oidc_verify_cert: "false"`
  - Headlamp: CA cert를 `/etc/ssl/certs/narwhal-ca.crt`에 마운트 (v0.40.0에 skip 플래그 없음)

**토큰 발급 테스트**:
```bash
# 비밀번호 grant로 토큰 발급
curl -k -X POST "https://keycloak.local.narwhal.internal/realms/kubernetes/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=kubernetes" \
  -d "client_secret=kubernetes-client-secret" \
  -d "username=admin" \
  -d "password=admin" \
  -d "scope=openid groups"

# 토큰 디코딩 (groups claim 확인)
echo "<access_token>" | cut -d. -f2 | base64 -d 2>/dev/null | jq .groups
```

**Keycloak Pod 진단**:
```bash
kubectl logs -n keycloak keycloak-0 --tail=50
kubectl describe pod -n keycloak keycloak-0
```

## 13. VM Clock Skew (시간 동기화 불일치)

**증상**: 클러스터 전체에서 `Unauthorized` 에러 폭발, Cilium/metallb-controller/istio-cni가 `0/1 Ready` 상태로 멈춤

**에러 메시지**:
```
# cilium 로그
level=error msg=k8sError error="failed to list *v2.CiliumNode: Unauthorized"
# kube-apiserver 로그
"Unable to authenticate the request" err="[invalid bearer token, service account token is not valid yet]"
# cilium-operator 로그
"Error retrieving lease lock" err="Unauthorized" lock="kube-system/cilium-operator-resource-lock"
```

**원인**: Vagrant VM이 `vagrant halt` 후 재부팅될 때 NTP 동기화 전에 시스템 시간이 슬립 전 시간 그대로 유지됨.
다른 노드에서 발행한 ServiceAccount 토큰의 `nbf` (Not Before) 가 미래 시점으로 인식되어 API 서버가 거부.

**즉시 복구**:
```bash
# 모든 노드에서 시간 동기화 재시작
for node in master-1 master-2 master-3 worker-1 worker-2 worker-3; do
  vagrant ssh $node -c "sudo systemctl restart systemd-timesyncd"
done

# 동기화 확인
for node in master-1 master-2 master-3 worker-1 worker-2 worker-3; do
  echo -n "$node: "; vagrant ssh $node -c "date"
done

# 정체된 Cilium pod 재시작
kubectl rollout restart ds -n kube-system cilium
```

**진단**:
```bash
# 노드별 시간 확인 (skew 감지)
for node in master-1 master-2 master-3 worker-1 worker-2 worker-3; do
  echo -n "$node: "; vagrant ssh $node -c "date"
done

# kube-apiserver 토큰 에러 확인
vagrant ssh master-1 -c "sudo crictl logs \$(sudo crictl ps --name kube-apiserver -q | head -1) 2>&1 | grep 'not valid yet'"

# cilium 연속 Unauthorized 에러 확인
kubectl logs -n kube-system -l k8s-app=cilium --tail=10 | grep Unauthorized
```

**영구 해결**: `reboot-survivability.md`의 NTP 설정 참조. 추가로 `vagrant halt` 시 chrony/timesyncd를 재시작하는 hook 고려.

> ⚠️ **주의**: `vagrant halt && vagrant up` 또는 Mac 절전 모드 후 VM 복귀 시 반드시 발생할 수 있는 패턴임.
> 클러스터 복구 첫 단계로 시간 동기화 확인을 습관화할 것.

---

## 14. Cilium 장애로 인한 카스케이드 장애 (APISIX → Authentik → kube-apiserver)

**증상**: pod sandbox 생성 실패 (`cilium.sock: no such file or directory`), `kube-apiserver-master-1` CrashLoopBackOff

**에러 메시지** (kubelet):
```
Failed to create pod sandbox: plugin type="cilium-cni" failed (add): unable to connect to Cilium agent:
  Get "http://localhost/v1/config": dial unix /var/run/cilium/cilium.sock: connect: no such file or directory
```

**카스케이드 장애 구조**:
```
Cilium pod (특정 노드) Not Ready
  → MetalLB controller (해당 노드에 배치) Not Ready
    → MetalLB L2 라우팅 불안정
      → 192.168.56.200 (APISIX gateway LoadBalancer IP) 응답 없음
        → authentik.local.narwhal.internal 접근 불가
          → kube-apiserver OIDC 초기화 실패
            → kube-apiserver CrashLoopBackOff
              → 해당 master의 cilium도 Unauthorized → ContainerCreating 정지
```

**진단 순서**:
```bash
# 1. kube-apiserver crash 원인 확인
vagrant ssh master-1 -c "sudo crictl logs \$(sudo crictl ps -a --name kube-apiserver --state exited -q | head -1) 2>&1 | tail -30"
# → "oidc authenticator: initializing plugin: ... no route to host" 확인

# 2. APISIX LB IP 연결 테스트
vagrant ssh master-1 -c "ping -c3 192.168.56.200"

# 3. MetalLB controller 상태 확인
kubectl get pod -n platform-system -l app.kubernetes.io/name=metallb

# 4. Cilium 상태 확인 (핵심)
kubectl get pods -n kube-system -l k8s-app=cilium -o wide
# → 0/1 Running 인 pod와 그 노드 확인

# 5. 문제 cilium pod 로그
kubectl logs -n kube-system <cilium-pod> --tail=20 | grep -E 'error|warn|panic|Unauthorized|PodCIDR'
```

**복구 절차**:
```bash
# Step 1. 시간 동기화 확인 및 복구 (13번 참조)

# Step 2. master-1 /etc/hosts에 Authentik ClusterIP 직접 등록 (APISIX 우회)
# → kube-apiserver가 OIDC endpoint에 직접 접근 가능하게 함
AUTHENTIK_IP=$(kubectl get svc -n iam authentik-server -o jsonpath='{.spec.clusterIP}')
vagrant ssh master-1 -c "echo '${AUTHENTIK_IP} authentik.local.narwhal.internal' | sudo tee -a /etc/hosts"

# Step 3. kube-apiserver 재시작 유도 (static pod manifest touch)
vagrant ssh master-1 -c "sudo touch /etc/kubernetes/manifests/kube-apiserver.yaml"

# Step 4. Cilium 전체 재시작
kubectl rollout restart ds -n kube-system cilium

# Step 5. MetalLB controller 재시작
kubectl delete pod -n platform-system -l app.kubernetes.io/name=metallb,app.kubernetes.io/component=controller --force --grace-period=0

# Step 6. 상태 확인
kubectl get pods -n kube-system -l k8s-app=cilium
kubectl get pods -n platform-system -l app.kubernetes.io/name=metallb
vagrant ssh master-1 -c "ping -c3 192.168.56.200"
```

**etcd 연결 에러 (`127.0.0.1:2379 operation was canceled`) 구분**:
- kube-apiserver 로그에 etcd 연결 에러가 보여도 **etcd 자체 문제가 아닐 수 있음**
- OIDC 초기화가 blocking되면서 etcd connection pool이 timeout되는 것
- etcd 건강 상태를 먼저 확인:
```bash
vagrant ssh master-1 -c "sudo crictl exec \$(sudo crictl ps --name etcd -q) etcdctl \
  --endpoints=https://192.168.56.10:2379,https://192.168.56.11:2379,https://192.168.56.12:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/peer.crt \
  --key=/etc/kubernetes/pki/etcd/peer.key \
  endpoint health"
```

---

## 15. OpenBao 재부팅 후 Sealed 상태

**증상**: `openbao-0` pod가 `Running` 이지만 `0/1 Ready`, 의존 서비스들이 비밀값을 못 가져옴

**원인**: OpenBao(Vault 호환)는 보안상 이유로 재시작 시 자동 unseal을 하지 않음. Shamir 키를 사용해 수동 unseal 필요.

**즉시 복구**:
```bash
# Sealed 상태 확인
kubectl exec -n storage openbao-0 -- bao status
# → Sealed: true 이면 복구 필요

# openbao-init 시크릿에서 unseal 키 추출 및 적용
BAO_UNSEAL_KEY=$(kubectl get secret openbao-init -n storage -o jsonpath='{.data.unseal_keys_b64}' | base64 -d)
kubectl exec -n storage openbao-0 -- bao operator unseal $BAO_UNSEAL_KEY

# 정상화 확인
kubectl exec -n storage openbao-0 -- bao status
# → Sealed: false 확인
kubectl get pod -n storage openbao-0
# → 1/1 Running 확인
```

**자동화 권장**: Vault Auto-Unseal (KMS 또는 Transit) 도입 또는 재부팅 후 unseal을 수행하는 CronJob/operator 검토.

---

## 관련 문서

- [`architecture.md`](./architecture.md) - 아키텍처 개요
- [`../vagrant/dns-access.md`](../vagrant/dns-access.md) · [`../kakao/service-domains.md`](../kakao/service-domains.md) - 서비스 도메인과 SSO 연동 방식
- [`dns-access.md`](../vagrant/dns-access.md) - DNS 및 접근 방법
- [`database.md`](./database.md) - 데이터베이스 관리
- [`operations.md`](../vagrant/operations.md) - 운영 가이드
- [`reboot-survivability.md`](../vagrant/reboot-survivability.md) - 리부트 생존성 아키텍처
