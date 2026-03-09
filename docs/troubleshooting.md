# Narwhal 트러블슈팅 가이드

## 1. K8s 1.35 OIDC HTTPS 요구사항

**증상**: API 서버가 시작 직후 crash, `kube-apiserver` Pod가 CrashLoopBackOff

**에러 메시지**:
```
jwt[0].issuer.url: Invalid value: "http://...": URL scheme must be https
```

**원인**: K8s 1.35는 `--oidc-*` 플래그를 내부적으로 StructuredAuthenticationConfiguration으로 변환하며, HTTPS가 필수

**해결법**:
- cert-manager + Traefik TLS가 설치된 후에만 OIDC 플래그 활성화
- 설치 순서: 08-1-networking (cert-manager/Traefik) → 09-istio-ambient → 10-dnsmasq → 11-1~11-4-keycloak-* (OIDC)
- 긴급 복구: `/etc/kubernetes/manifests/kube-apiserver.yaml`에서 `--oidc-*` 플래그 주석 처리

**검증**:
```bash
# HTTPS 엔드포인트 확인
curl -k https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration
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

**증상**: Pod에서 `*.local.narwhal.io` 도메인 해석 불가

**확인 순서**:
```bash
# 1. dnsmasq 실행 확인 (master-1, master-2)
systemctl status dnsmasq

# 2. CoreDNS forward 설정 확인
kubectl get configmap coredns -n kube-system -o yaml | grep -A5 "local.narwhal.io"

# 3. Pod에서 DNS 테스트
kubectl run -it --rm dns-test --image=alpine/k8s:1.31.4 --restart=Never -- nslookup keycloak.local.narwhal.io

# 4. 노드 DNS 설정 확인
resolvectl status | grep -A3 "DNS Servers"
```

**일반적 원인**:
- CoreDNS가 `local.narwhal.io`를 dnsmasq로 forward하지 않음 → `10-dnsmasq.sh` 재실행
- worker/master-2에서 public DNS로 해석 → systemd-resolved에 `Domains=~local.narwhal.io` 설정 필요
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
curl -k -X POST "https://keycloak.local.narwhal.io/realms/kubernetes/protocol/openid-connect/token" \
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

---

## 관련 문서

- [`architecture.md`](./architecture.md) - 아키텍처 개요
- [`keycloak-sso.md`](./keycloak-sso.md) - SSO 상세 설정
- [`dns-access.md`](./dns-access.md) - DNS 및 접근 방법
- [`database.md`](./database.md) - 데이터베이스 관리
- [`operations.md`](./operations.md) - 운영 가이드
