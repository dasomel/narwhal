# Narwhal - Claude Code Guide

> Vagrant 기반 Kubernetes Internal Developer Platform (IDP) 클러스터 자동 구축 프로젝트

## Quick Overview

로컬 환경에서 완전한 Kubernetes IDP 스택(GitOps, SSO, Monitoring, Storage, Backup)을 Vagrant VM으로 자동 프로비저닝하는 인프라 프로젝트.

---

## Plan Mode Guide (Shift+Tab 2회)

**좋은 계획이 성공의 90%** - 대부분의 세션을 Plan 모드로 시작하세요.

### 언제 Plan Mode를 사용하나요?
- 새로운 컴포넌트 추가 시
- 기존 스크립트 대규모 수정 시
- GitOps 앱 구조 변경 시
- 버전 업그레이드 시

### Plan Mode 워크플로우
1. `shift+tab` 2회로 Plan 모드 진입
2. 계획에 만족할 때까지 Claude와 논의
3. 계획 확정 후 자동 수락 모드로 전환
4. 한 번에 완성

---

## Mistakes Log (Compounding Engineering)

> 클로드가 실수할 때마다 여기에 추가하세요. 같은 실수를 반복하지 않습니다.
> 코드 리뷰 시 `@.claude` 태그로 CLAUDE.md 업데이트 요청하세요.

### Shell Script 실수
| 날짜 | 실수 | 해결책 |
|------|------|--------|
| 2025-01-27 | `set -e` 없이 스크립트 작성 | 항상 `set -euo pipefail` 첫 줄에 추가 |
| - | heredoc에서 변수 확장 실수 | `<<'EOF'` (따옴표)로 변수 확장 방지, `<<EOF`로 확장 허용 |
| - | apt 명령어에 `-y` 누락 | 항상 `apt-get install -y` 사용 |

### Kubernetes/Helm 실수
| 날짜 | 실수 | 해결책 |
|------|------|--------|
| - | namespace 생성 누락 | `--create-namespace` 또는 `CreateNamespace=true` syncOption 사용 |
| - | CRD 의존성 무시 | Operator 설치 후 CR 생성, ArgoCD sync-wave 활용 |
| - | PVC 크기 변경 시도 | PVC는 확장만 가능, 축소 불가 |
| 2026-01-30 | OAuth2 Proxy cookie-secret 빈값 | `cookieSecret: ""`이면 에러, `openssl rand -hex 16` 으로 정확히 32바이트 생성 |
| 2026-01-30 | OAuth2 Proxy service.port deprecated | `service.portNumber` 사용 (v7.x 차트) |
| 2026-01-30 | Keycloak issuer URL 불일치 | `insecure_oidc_skip_issuer_verification = true` 또는 Keycloak hostname 설정 일치 |
| 2026-01-30 | Loki chunks/results cache 실패 | `chunksCache.enabled: false`, `resultsCache.enabled: false` 개발환경 |
| 2026-01-30 | Headlamp Helm repo URL 변경 | `https://kubernetes-sigs.github.io/headlamp/` 사용 |
| 2026-01-30 | Keycloak 서비스명 오타 | `keycloak-service` (not `keycloak`), 포트 8080 명시 |
| 2026-02-04 | sed로 YAML 수정 시 파싱 오류 | `yq`로 YAML 안전하게 수정 (예: API 서버 manifest OIDC 설정) |
| 2026-02-05 | Helm `--set` nodeSelector boolean 에러 | `--set-string nodeSelector.key=true` 사용 (문자열 강제) |
| 2026-02-14 | kube-vip `vip_subnet` 값에 `/` 포함 | `"32"` 사용 (NOT `"/32"`), 내부에서 `address + "/" + vip_subnet` 조합 |
| 2026-02-14 | kube-vip kubeconfig 경로 인식 실패 | `/.kube/config`에 마운트 (distroless 이미지 HOME=/) |
| 2026-02-14 | kube-vip admin.conf VIP 순환 의존성 | `kube-vip.conf` 별도 생성, 로컬 IP로 서버 주소 변경 |
| 2026-02-14 | kube-vip static pod init 전 생성 시 chicken-and-egg | master-1은 init 후 manifest 생성, 수동 VIP 바인딩으로 부트스트랩 |
| 2026-02-14 | kubeadm join VMware NAT 인터페이스 감지 | `--apiserver-advertise-address=192.168.56.x` 명시 필수 |
| 2026-02-14 | VMware Vagrant private_network IP 미할당 | `01-prerequisites.sh`에서 netplan 직접 생성, `chmod 600` 필수 |
| 2026-02-14 | Master 4GB RAM에서 API 서버 OOM 재시작 (platform apps 설치 시) | 현재 topology: Master는 NoSchedule taint (control-plane only, 4GB OK), Worker 6GB에서 platform apps 실행 |
| 2026-02-14 | ArgoCD v3.x applicationsets CRD 262KB 초과 | `kubectl apply --server-side --force-conflicts` 사용 |
| 2026-02-14 | Helm `--wait`로 타임아웃 시 릴리스 롤백 | 비핵심 앱은 `--wait` 제거, `--timeout`만 사용 |
| 2026-02-15 | Velero CRD hook musl/glibc 비호환 (ARM64) | `upgradeCRDs: false` 설정, alpine/k8s musl→velero glibc 컨테이너에서 실행 불가 |
| 2026-02-15 | registry.k8s.io/kubectl은 distroless (shell 없음) | `docker.io/alpine/k8s:1.31.4` 사용 (shell+kubectl 포함) |
| 2026-02-15 | Traefik routes 적용 시 GatewayClass 미생성 | Traefik deployment+GatewayClass 대기 루프 후 routes 적용 |
| 2026-02-15 | etcd 컨테이너 `sh -c` 실행 불가 | etcd는 distroless, `kubectl exec -- etcdctl ...` 직접 호출 |
| 2026-02-15 | alpine/k8s 이미지 태그에 `v` prefix 없음 | `"1.31.4"` 사용 (NOT `v1.31.4`), Docker Hub 태그 형식 확인 |
| 2026-02-15 | ArgoCD v3.x `server.insecure` 설정 위치 오류 | `argocd-cmd-params-cm`에 설정해야 함 (NOT `argocd-cm`). `argocd-cm`은 legacy |
| 2026-02-15 | worker/master-2 DNS가 public IP로 해석 | `systemd-resolved`에 `Domains=~local.narwhal.io` + master dnsmasq를 DNS로 설정 |
| 2026-02-15 | Keycloak `groups` scope 누락 → `invalid_scope` 에러 | realm-level `groups` client scope 생성 + mapper 추가 + 전체 클라이언트에 default scope 할당 |
| 2026-02-15 | ArgoCD SSO에서 `x509: certificate signed by unknown authority` | `argocd-cm`에 `oidc.tls.insecure.skip.verify: "true"` 추가 (자체서명 인증서) |
| 2026-02-15 | Headlamp `extraArgs` root level에 놓으면 무시됨 | `config.extraArgs`에 배치해야 컨테이너 args에 추가됨 |
| 2026-02-15 | Harbor `configureUserSettings` 변경 시 API 거부 | env 변수로 주입되므로 API로 수정 불가, Helm values 변경 + 재배포 필요 |
| 2026-02-18 | Cilium + Istio CNI 충돌 (cni.exclusive 기본값 true) | `cni.exclusive=false` 필수, Cilium이 다른 CNI 설정 삭제 방지 |
| 2026-02-18 | Cilium socket LB가 ztunnel 트래픽 바이패스 | `socketLB.hostNamespaceOnly=true` 필수, 메시 트래픽이 ztunnel 우회 방지 |
| 2026-02-18 | Istio CRD annotation 262KB 초과 (ArgoCD) | ArgoCD syncOptions에 `ServerSideApply=true` 추가 |
| 2026-02-18 | Istio 1.28은 K8s 1.35 미지원 | Istio 1.29.x 사용 (K8s 1.31~1.35 지원) |
| 2026-02-18 | Istio 이미지 docker.io only (대안 없음) | Docker OSS rate limit 면제, `docker.io/istio/*` 사용 불가피 |
| 2026-02-19 | CiliumClusterwideNetworkPolicy `endpointSelector: {}` 전체 트래픽 차단 | 빈 endpointSelector는 모든 포드 선택 → deny-by-default 활성화. CCNP에 빈 selector 사용 금지 |
| 2026-02-19 | CoreDNS `forward . /etc/resolv.conf` dnsmasq 루프 | Master의 resolv.conf가 127.0.0.1(dnsmasq) → CoreDNS 내부 루프. `forward . 8.8.8.8 8.8.4.4` 명시 |
| 2026-02-19 | Istio ambient HBONE port 15008 NetworkPolicy 차단 | ambient mesh는 포드간 HBONE(15008) 사용. NetworkPolicy ingress에 15008/TCP 추가 필수 |
| 2026-02-19 | OAuth2-Proxy `insecure_oidc_skip_issuer_verification` TLS 미검증 아님 | issuer만 스킵, TLS 검증은 별도. `ssl_insecure_skip_verify = true` 추가 필요 |
| 2026-02-19 | Traefik Gateway API CRD field manager 충돌 | chart CRD 추출 → `--server-side --force-conflicts` 적용 → `helm install --skip-crds` |
| 2026-02-24 | Keycloak Operator 자동생성 NetworkPolicy에 HBONE 15008 누락 | Operator가 `keycloak-network-policy`를 관리하므로 직접 수정 불가. `keycloak-allow-hbone` 별도 NetworkPolicy로 15008/TCP 추가 (`11-keycloak.sh` 패턴 참조) |
| 2026-02-24 | Keycloak hostname v1/v2 옵션 충돌 | `additionalOptions`에 `hostname-url` (v1 deprecated) 사용 금지. v2: `hostname.hostname` + `hostname.strict: true` + `proxy.headers: xforwarded` |
| 2026-02-24 | yq로 URL에 셸 따옴표 삽입 → API 서버 크래시 | `yq -i ".spec... += [\"--flag=value\"]"` 사용 (NOT `'...'`). 쉘 변수 확장 시 바깥 따옴표를 `"` 사용 |
| 2026-02-24 | API 서버 OIDC 플래그 추가 시 HTTPRoute 미존재 | `11-keycloak.sh`에서 OIDC 검증 전에 Keycloak HTTPRoute 먼저 생성 필수. GitOps bootstrap(14)보다 먼저 실행됨 |
| 2026-02-25 | Keycloak OIDC 토큰 `aud` 클레임이 `account`만 포함 (K8s API 서버 Unauthorized) | Keycloak `kubernetes` 클라이언트에 audience mapper 추가 필수: `oidc-audience-mapper`, `included.client.audience=kubernetes`. 설정 없으면 `oidc: expected audience "kubernetes" got ["account"]` 에러 |
| 2026-02-25 | API 서버 `--oidc-ca-file` 누락 → self-signed 인증서로 JWKS 검증 실패 | Keycloak이 self-signed cert 사용 시 `--oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt` 필수. 인증서 추출: `openssl s_client -connect ... \| openssl x509 -outform PEM` |
| 2026-02-25 | `kcadm.sh --format csv --noquotes \| tail -1` 잘못된 ID 반환 | CSV 형식은 결과 순서/필드가 불안정. 대신 `kcadm.sh get ... 2>/dev/null \| jq -r '.[] \| select(.name=="X") \| .id'` 사용 |
| 2026-02-25 | `kubectl --token=X`가 kubeconfig client-cert를 override 안 함 | X.509 인증이 토큰보다 우선. OIDC 테스트 시 `KUBECONFIG=/dev/null kubectl --server=... --certificate-authority=... --token=...` 사용 필수 |
| 2026-02-25 | Istio ambient mesh에서 SSO 웹 서버 쿠키 손상 → `http: named cookie not present` | ambient namespace의 웹 서버(ArgoCD, Grafana, Harbor, Gitea, OAuth2-Proxy, Headlamp)는 반드시 `istio.io/dataplane-mode: none` pod label로 opt-out. ztunnel HBONE이 Set-Cookie/Cookie 헤더를 손상시킴 |
| 2026-02-25 | Gitea OAuth2 소스 이름 대소문자 불일치 → `/user/oauth2/Keycloak` 500 에러 | URL path의 소스 이름이 case-sensitive. `gitea admin auth add-oauth --name "keycloak"` (소문자)로 등록하면 `/user/oauth2/keycloak`으로 접근 |
| 2026-02-25 | `microprofile-jwt` 스코프에 `groups` 클레임 매퍼 중복 | Keycloak 기본 `microprofile-jwt` 스코프에 realm-role을 `groups`로 매핑하는 mapper 존재. 커스텀 `groups` 스코프 생성 후 다른 스코프의 `groups` 매퍼 삭제 필요 |
| 2026-02-25 | Keycloak 모든 클라이언트의 `aud` 클레임이 `["account"]`만 포함 | `kubernetes` 클라이언트뿐 아니라 **모든 OIDC 클라이언트**에 audience mapper 추가 필수. ArgoCD 등 토큰을 직접 검증하는 앱에서 `expected audience "argocd" got ["account"]` 에러 발생 |
| 2026-02-26 | Keycloak 사용자 `emailVerified=false` → OAuth2-Proxy 500 에러 | `kcadm.sh create users`에 `-s emailVerified=true` 필수. 또는 OAuth2-Proxy에 `insecure_oidc_allow_unverified_email = true` 추가 |
| 2026-02-26 | Traefik Errors 미들웨어가 401 상태코드 보존 → 브라우저 자동 리다이렉트 안 됨 | ExternalName→OAuth2-Proxy 대신 nginx+JS 리다이렉트 페이지 사용. `window.location.href`로 강제 리다이렉트 |
| 2026-02-26 | Traefik ExternalName 서비스 기본 차단 | `providers.kubernetesCRD.allowExternalNameServices: true` 설정 필수. 없으면 404 `externalName services not allowed` |
| 2026-02-26 | OAuth2-Proxy PKCE 충돌 (동시 다중 OAuth 플로우) | 여러 보호 앱이 동시 리다이렉트 → code_verifier 충돌. JS에 sessionStorage 5초 디바운스 추가 |
| 2026-02-26 | Traefik LB 어노테이션 `io.cilium/lb-ipam-ips`는 MetalLB에서 무시됨 | MetalLB용: `metallb.universe.tf/loadBalancerIPs: "IP"` 사용 |

### GitOps/ArgoCD 실수
| 날짜 | 실수 | 해결책 |
|------|------|--------|
| - | values 파일 경로 오타 | `valueFiles` 경로는 repoURL 기준 상대경로 |
| - | targetRevision 형식 오류 | 차트 버전은 `"1.0.0"` (문자열), Git ref는 `HEAD` |
| 2026-02-14 | app-of-apps repoURL `https://` 사용 → Gitea는 HTTP only | `http://gitea-http.gitea.svc.cluster.local:3000/...` 사용 |
| 2026-02-14 | Harbor gitops YAML에 ARM64 이미지 오버라이드 누락 | `ghcr.io/dasomel/goharbor/*:latest` 전체 컴포넌트 지정 필수 |
| 2026-02-14 | SeaweedFS chart 버전 4.0.410 → Docker Hub 이미지 4.10 없음 | appVersion=image tag 확인 후 존재하는 버전 사용 (4.0.407→4.07) |
| 2026-02-14 | ArgoCD repo-server GitHub Pages IPv6 연결 실패 | VM에서 IPv6 미지원, ArgoCD가 IPv6로 시도하면 실패 |
| 2026-02-14 | ArgoCD 관리 리소스 Helm upgrade 충돌 | `kubectl set image`로 직접 패치, Helm 대신 kubectl 사용 |
| 2026-02-15 | Headlamp v0.40.0에 `-oidc-skip-issuer-tls-verify` 플래그 없음 | CA cert를 `/etc/ssl/certs/`에 subPath 마운트, `SSL_CERT_FILE` 사용 금지 |
| 2026-02-15 | Grafana `assertNoLeakedSecrets` 차트 검증 실패 | `grafana.assertNoLeakedSecrets: false` 설정 필수 |
| 2026-02-15 | Prometheus Helm 릴리스명 불일치 (`prometheus` vs `prometheus-stack`) | ArgoCD app name과 일치시켜야 HTTPRoute/ConfigMap 정상 작동 |
| 2026-02-15 | Gitea OIDC 소스 추가 시 self-signed cert 검증 실패 | Gitea 컨테이너에 CA cert 마운트 후 `add-oauth` 실행 |
| 2026-02-24 | ArgoCD를 비기본 네임스페이스(devtools)에 설치 시 ClusterRoleBinding Subject 불일치 | upstream install.yaml은 항상 Subject namespace를 `argocd`로 설정. 설치 후 `kubectl patch clusterrolebinding argocd-application-controller argocd-applicationset-controller argocd-server`로 namespace를 실제 설치 네임스페이스로 수정 필수 |
| 2026-02-24 | Istio ambient namespace의 ArgoCD 헬스체크 포트(8082/8084/9001) ztunnel에 의해 차단 | ztunnel은 ambient namespace의 모든 인바운드 트래픽을 가로챔. kubelet probe(plain HTTP)가 mTLS를 기대하는 ztunnel과 충돌 → CrashLoopBackOff. pod template에 `istio.io/dataplane-mode: none` 레이블 추가로 해당 pod를 ambient에서 제외. `traffic.sidecar.istio.io/excludeInboundPorts` 어노테이션은 ambient 모드에서 동작 안 함 |
| 2026-02-26 | ArgoCD selfHeal이 kubectl apply 변경사항을 되돌림 | ArgoCD 관리 리소스는 kubectl로 직접 수정해도 selfHeal이 Gitea 상태로 되돌림. 반드시 Gitea 레포에 push해야 영속 |
| 2026-02-26 | Gitea headless 서비스 (ClusterIP: None) → git clone 실패 | Gitea 서비스가 headless이면 DNS 해석 안 됨. Pod IP 직접 사용: `kubectl get pod -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].status.podIP}'` |

### Vagrant/Infrastructure 실수
| 날짜 | 실수 | 해결책 |
|------|------|--------|
| 2026-01-29 | Vagrant Cloud 401 에러 | HCP 토큰 필요 시 `VAGRANT_CLOUD_TOKEN=$(hcp auth print-access-token)` 사용 |
| 2026-01-29 | charts.keycloak.org URL 404 | 공식 Keycloak Operator 사용 (`keycloak-k8s-resources` GitHub) |
| 2026-01-29 | Bitnami 이미지 not found | Bitnami 상용화로 인해 공식 이미지 또는 Operator 사용 필요 |
| 2026-01-29 | vagrant provision 재실행 시 kubeadm init 실패 | 이미 초기화된 클러스터 확인 로직 추가 또는 개별 스크립트 직접 실행 |
| 2026-01-29 | Keycloak DB 연결 시 FQDN 사용 실패 | 같은 namespace면 짧은 서비스명 사용 (`keycloak-db-rw` not `keycloak-db-rw.keycloak.svc.cluster.local`) |
| 2026-01-29 | Gitea Helm chart valkey FQDN 문제 | `gitea-init` secret 내 스크립트가 FQDN 사용, 패치 필요. 스크립트에서 `valkey.enabled=true`, `valkey-cluster.enabled=false` 명시 |
| 2026-01-29 | Keycloak 2 replicas 리소스 부족 | 테스트 환경에서는 `instances: 1` 사용, 프로덕션에서만 HA 구성 |
| 2026-01-30 | API 서버에서 클러스터 DNS 접근 불가 | OIDC issuer URL은 NodePort로 노출 (`http://NODE_IP:30080/realms/kubernetes`) |
| 2026-01-31 | OpenBao v2.4.4 ARM64 이미지 없음 | quay.io/openbao/openbao에 ARM64 없음, tag `2.2.0` 사용 (default registry) |
| 2026-01-31 | Velero bitnami/kubectl 이미지 없음 | Bitnami 사용 금지, `docker.io/alpine/k8s` 사용 (shell 포함, ARM64 지원) |
| 2026-01-31 | 디스크 압력으로 Pod 축출 | `tolerations`에 `node.kubernetes.io/disk-pressure:NoSchedule` 추가 |
| 2026-01-31 | GitHub Actions get-changed-files workflow_dispatch 미지원 | `if: github.event_name != 'workflow_dispatch'` 조건 추가 |
| 2026-01-31 | CNPG 클러스터 replica WAL 아카이빙 실패 | 문제 있는 replica PVC 삭제 후 자동 재생성 대기, 인스턴스 수 줄여 안정화 |
| 2026-02-10 | MetalLB Helm upgrade CRD field manager 충돌 | 초기 설치는 정상, re-upgrade 시 CRD caBundle 충돌. Pods는 정상 작동 |
| 2026-02-10 | Harbor ARM64 `exec format error` | `ghcr.io/dasomel/goharbor/*:latest` 사용, 공식 이미지는 AMD64 only |
| 2026-02-10 | OAuth2 Proxy cookie_secret 34바이트 에러 | `openssl rand -base64 32`는 44바이트, `openssl rand -hex 16`으로 32바이트 |
| 2026-02-10 | Velero CRD job kubectl `/bin/sh` 없음 | `registry.k8s.io/kubectl`은 distroless, `docker.io/alpine/k8s` 사용 |
| 2026-02-10 | VERSIONS.md와 실제 배포 버전 불일치 | 스크립트에 고정된 chart 버전 기준으로 VERSIONS.md 동기화 필수 |
| 2026-02-11 | CNPG DB 비밀번호 하드코딩 불일치 | CNPG Secret에서 실제 비밀번호 조회 필수 (`harbor-db-credentials`) |
| 2026-02-11 | Loki 6.52.0 bucketNames 필수 | `loki.storage.bucketNames.{chunks,ruler,admin}` 명시 필요 |
| 2026-02-11 | Traefik 39.0.0 HTTPS certificateRefs 필수 | Gateway HTTPS 리스너에 TLS 인증서 참조 필수 |
| 2026-02-11 | Traefik Helm --set 포트 타입 에러 | `--set` 대신 values 파일 사용 (float64 vs int64) |
| 2026-02-11 | ArgoCD 관리 리소스 Helm 재설치 충돌 | 기존 리소스(SA, IngressClass, GatewayClass) 삭제 후 재설치 |

### 새 실수 추가하기
```markdown
| YYYY-MM-DD | 실수 내용 | 해결책 |
```

---

## Core Flows

### 1. 클러스터 프로비저닝 플로우

| 단계 | 파일 | 설명 |
|------|------|------|
| 사전 설정 | `scripts/common/01-prerequisites.sh` | 호스트명, /etc/hosts 설정 |
| 컨테이너 런타임 | `scripts/common/02-containerd.sh` | containerd 설치 |
| K8s 설치 | `scripts/common/03-k8s-install.sh` | kubeadm, kubelet, kubectl |

### 2. Master 노드 설정 플로우 (2-Phase 구조)

**Phase 1: 클러스터 인프라** (master-1 프로비저닝 시 실행)

| 단계 | 파일 | 설명 |
|------|------|------|
| kube-vip | `scripts/cluster/00-kube-vip.sh` | Control Plane VIP |
| NFS 서버 | `scripts/cluster/01-nfs-server.sh` | NFS 서버 설정 |
| 클러스터 초기화 | `scripts/cluster/02-init-cluster.sh` | kubeadm init |
| CNI 설치 | `scripts/cluster/03-cni-install.sh` | Cilium + Hubble |
| 애드온 | `scripts/cluster/04-addons.sh` | metrics-server, csi-driver-nfs |
| NFS 쿼터 | `scripts/cluster/05-nfs-quota-agent.sh` | NFS 프로젝트 쿼터 |

→ master-2 join → master-3 join → worker-1 join → worker-2 join → worker-3 join

**Phase 2: 플랫폼 앱** (마지막 worker join 후 자동 트리거)

| 단계 | 파일 | 설명 |
|------|------|------|
| Phase 2 래퍼 | `scripts/cluster/06-phase2-start.sh` | Phase 2 스크립트 실행 |
| PostgreSQL | `scripts/cluster/07-cnpg.sh` | CloudNative-PG Operator |
| 플랫폼 앱 | `scripts/cluster/08-platform-apps.sh` | MetalLB, Traefik, cert-manager 등 |
| Istio | `scripts/cluster/09-istio-ambient.sh` | Service Mesh (ambient mode) |
| dnsmasq | `scripts/cluster/10-dnsmasq.sh` | 로컬 DNS + CoreDNS forward |
| Keycloak | `scripts/cluster/11-keycloak.sh` | IAM/SSO + OIDC |
| Gitea | `scripts/cluster/12-gitea.sh` | Git 서버 |
| ArgoCD | `scripts/cluster/13-argocd.sh` | GitOps CD |
| Bootstrap | `scripts/cluster/14-gitops-bootstrap.sh` | App-of-Apps 배포 |

### 3. GitOps 앱 관리

| 앱 | 파일 | 설명 |
|-----|------|------|
| App-of-Apps | `gitops/apps/app-of-apps.yaml` | 모든 앱 관리 |
| cert-manager | `gitops/apps/cert-manager.yaml` | TLS 자동화 |
| Prometheus | `gitops/apps/prometheus-stack.yaml` | 모니터링 |
| Loki | `gitops/apps/loki.yaml` | 로그 수집 |
| Tempo | `gitops/apps/tempo.yaml` | 분산 추적 |
| Harbor | `gitops/apps/harbor.yaml` | 컨테이너 레지스트리 |
| OpenBao | `gitops/apps/openbao.yaml` | 시크릿 관리 |
| Kyverno | `gitops/apps/kyverno.yaml` | 정책 관리 |
| Headlamp | `gitops/apps/headlamp.yaml` | K8s UI |

## Development Commands

```bash
# 클러스터 생성
vagrant up --provider=vmware_desktop

# 특정 노드만 생성
vagrant up master-1
vagrant up worker-1

# SSH 접속
vagrant ssh master-1

# kubectl 확인
vagrant ssh master-1 -c "kubectl get nodes"

# Phase 2만 수동 실행 (클러스터 구성 후)
vagrant provision master-1 --provision-with phase2-platform

# 재프로비저닝
vagrant provision master-1

# 클러스터 중지
vagrant halt

# 클러스터 삭제
vagrant destroy -f

# 스크립트 검증 (shellcheck)
shellcheck scripts/**/*.sh
```

## Key Configuration

| 설정 | 파일 | 변수 |
|------|------|------|
| K8s 버전 | `Vagrantfile` | `K8S_VERSION` |
| Worker 수 | `Vagrantfile` | `WORKER_COUNT` |
| 메모리/CPU | `Vagrantfile` | `MASTER_MEMORY`, `WORKER_CPUS` |
| VIP 주소 | `Vagrantfile` | `VIP_ADDRESS` |
| 컴포넌트 버전 | `VERSIONS.md` | 전체 버전 관리 |

## Permissions

### 허용 작업
- scripts/ 폴더의 쉘 스크립트 수정
- gitops/apps/, gitops/resources/ YAML 파일 수정
- Vagrantfile 설정 변경
- 문서 (README.md, docs/) 업데이트

### 금지 작업
- .vagrant/ 폴더 직접 수정 금지
- 민감한 정보 (비밀번호, 토큰) 하드코딩 금지
- 쉘 스크립트에서 `set -euo pipefail` 제거 금지
- **Bitnami 이미지/차트 사용 금지** (대체재가 전혀 없는 경우에만 예외 허용)
  - Bitnami 상용화로 인해 이미지 삭제/접근 불가 리스크 있음
  - DB: 공식 이미지 또는 Operator (CloudNative-PG 등) 사용
  - 기타: 업스트림 공식 이미지 우선, Alpine 기반 커뮤니티 이미지 차선
- **docker.io (Docker Hub) 사용 최소화** (대체재 없을 때만 허용)
  - Rate limit 이슈 (익명 100pulls/6h, 인증 200pulls/6h)
  - 레지스트리 우선순위: `ghcr.io` > `registry.k8s.io` > `quay.io` > `docker.io`
  - 자체 이미지는 `ghcr.io/dasomel/` 사용
  - 불가피하게 docker.io 사용 시 주석으로 사유 명시

## Code Style

- **Shell Script**: `set -euo pipefail` 필수, 2 spaces 들여쓰기
- **YAML**: 2 spaces 들여쓰기
- **변수명**: ENV_VAR (환경변수), local_var (로컬)
- **파일명**: 숫자 prefix로 실행 순서 표시 (00-, 01-, ...)

## Network Info

| 항목 | 값 |
|------|-----|
| Master IPs | 192.168.56.10-12 (master-1, master-2, master-3) |
| Worker IPs | 192.168.56.21-23 (worker-1, worker-2, worker-3) |
| VIP | 192.168.56.100 |
| Pod CIDR | 10.244.0.0/16 |
| Service CIDR | 10.96.0.0/12 |

---

## Verification Loop (검증 루프)

> 클로드에게 자신의 작업을 검증할 방법을 제공하는 것이 품질을 2~3배 높입니다.

### 이 프로젝트의 검증 방법

1. **스크립트 검증**
   ```bash
   shellcheck scripts/**/*.sh
   ruby -c Vagrantfile
   ```

2. **YAML 검증**
   ```bash
   yq eval '.' gitops/apps/*.yaml > /dev/null
   ```

3. **실제 테스트** (VM 실행 중일 때)
   ```bash
   vagrant ssh master-1 -c "kubectl get nodes"
   vagrant ssh master-1 -c "kubectl get pods -A"
   ```

4. **ArgoCD 동기화 확인**
   ```bash
   vagrant ssh master-1 -c "kubectl get applications -n argocd"
   ```

### 검증 명령어
- `/verify` - 전체 검증 실행
- `/check` - 빠른 문법 검사

---

## Slash Commands (반복 작업 자동화)

| 명령어 | 설명 |
|--------|------|
| `/check` | 빠른 타입/문법 체크 |
| `/verify` | 전체 검증 루프 실행 |
| `/commit-push-pr` | 커밋 → 푸시 → PR 한 번에 |
| `/sync-versions` | VERSIONS.md 동기화 검사 |
| `/add-mistake` | 실수 패턴 기록 |
| `/compact` | 세션 컨텍스트 정리 및 요약 저장 |

---

## Team Contribution Guide

### CLAUDE.md 업데이트 방법

1. **실수 발견 시**: Mistakes Log 섹션에 추가
2. **새 패턴 발견 시**: Code Style 또는 Permissions에 추가
3. **코드 리뷰 시**: `@.claude` 태그로 업데이트 요청

### 커밋 메시지 컨벤션
```
docs(claude): add mistake pattern for XYZ
docs(claude): update verification steps
```

### 주간 리뷰
- 매주 팀원들이 CLAUDE.md에 기여
- 새로 발견된 실수 패턴 공유
- 검증 루프 개선 논의

---

## AI Team (Multi-Agent 협업)

> Claude Code가 Gemini CLI를 팀원으로 호출하여 협업합니다.

### 팀 구성

| Agent | 역할 | 호출 방법 |
|-------|------|-----------|
| **Claude** (주 에이전트) | 코드 작성, 리뷰, GitOps 관리 | 기본 |
| **Gemini** (보조 에이전트) | 세컨드 오피니언, 문서 검색, 대안 제시 | `gemini -p "..."` |

### Gemini CLI 사용 규칙

1. **Non-interactive 모드만 사용** (`-p` 플래그 필수)
   ```bash
   gemini -p "질문 내용" -o text
   ```

2. **파이프로 컨텍스트 전달 가능**
   ```bash
   cat scripts/cluster/11-keycloak.sh | gemini -p "이 스크립트의 보안 취약점을 분석해줘" -o text
   ```

3. **타임아웃 주의**: 30초 이내 응답 가능한 질문만 (복잡한 질문은 분할)

### 언제 Gemini를 호출하나요?

| 상황 | 예시 |
|------|------|
| **세컨드 오피니언** | "이 Helm values 설정이 적절한지 Gemini에게 확인" |
| **최신 정보 검색** | "Kubernetes 1.35 deprecation 목록 확인" |
| **대안 비교** | "Velero vs Kasten 장단점 비교" |
| **코드 리뷰 보조** | "이 shell script의 잠재적 문제점 분석" |
| **문서 초안** | "이 컴포넌트의 README 초안 작성" |

### 호출 패턴

```bash
# 1. 간단한 질문
gemini -p "Cilium 1.17에서 kube-proxy replacement 기본 설정이 변경되었나?" -o text

# 2. 파일 기반 리뷰
cat gitops/apps/harbor.yaml | gemini -p "이 ArgoCD Application YAML에 문제가 있는지 리뷰해줘" -o text

# 3. 비교 분석
gemini -p "Kubernetes에서 Ingress Controller로 Cilium Gateway API vs Nginx 장단점 비교" -o text

# 4. 트러블슈팅 보조
kubectl get events -n harbor 2>&1 | gemini -p "이 Kubernetes 이벤트에서 문제 원인을 분석해줘" -o text
```

### 주의사항

- Gemini 응답은 **참고용**으로만 사용, 최종 판단은 Claude가 수행
- 민감한 정보(시크릿, 토큰)를 Gemini에게 전달하지 않음
- Gemini 응답이 CLAUDE.md 규칙과 충돌하면 CLAUDE.md 규칙 우선
- 응답 시간이 길면 스킵하고 Claude가 직접 처리
- **429 에러 (MODEL_CAPACITY_EXHAUSTED)는 서버 측 용량 부족** — 사용자 할당량 소진이 아님
  - 10초 대기 후 1회 재시도, 실패 시 Claude가 직접 처리
  - 재시도 명령: `sleep 10 && gemini -p "..." -o text`

### 토큰 사용량 관리 및 만료 전략

> 어느 한 에이전트의 토큰/할당량이 소진되어도 작업이 중단되지 않도록 합니다.

#### 에이전트별 역할 전환 (Failover)

| 상황 | 전략 |
|------|------|
| **Claude 토큰 만료** | Gemini로 전환하여 세션 지속. `gemini -p "..." -o text`로 직접 작업 수행 |
| **Gemini 토큰 만료** | Claude 단독 작업. Gemini 호출 스킵, WebSearch/Context7 등 내장 도구 활용 |
| **양쪽 모두 만료** | 작업 중단 전 현재 상태를 `.claude/cache/SESSION_STATE.md`에 기록하여 다음 세션에서 이어가기 |

#### 토큰 절약 원칙

1. **작은 질문은 작은 모델에게**
   - 단순 사실 확인, 문법 검사 → Gemini (`gemini -p`)
   - 코드 작성, 복잡한 리팩토링 → Claude
   - Claude subagent 사용 시 단순 탐색은 `model: "haiku"` 지정

2. **컨텍스트 최소화**
   - 파일 전체를 파이프하지 말고 관련 부분만 추출 후 전달
   - `head -50`, `grep -A5` 등으로 필요한 부분만 전달
   ```bash
   # Bad: 전체 파일 전달 (토큰 낭비)
   cat scripts/cluster/11-keycloak.sh | gemini -p "리뷰해줘" -o text
   # Good: 관련 부분만 전달
   sed -n '50,80p' scripts/cluster/11-keycloak.sh | gemini -p "이 OIDC 설정 부분 리뷰해줘" -o text
   ```

3. **캐시 활용**
   - 반복 질문 결과는 `.claude/cache/`에 저장하여 재호출 방지
   - 동일 세션 내 같은 질문 반복 금지

#### 만료 감지 및 대응 플로우

```
[작업 시작]
    │
    ├─ Claude 응답 가능? ──Yes──→ Claude가 주 에이전트로 작업
    │       │                         │
    │      No                    필요 시 Gemini 보조 호출
    │       │                         │
    │       ▼                    Gemini 응답 실패?
    │  Gemini로 전환 ◄──Yes──────────┘
    │       │
    │  Gemini 응답 가능? ──Yes──→ Gemini 단독 작업 (코드 생성 포함)
    │       │
    │      No
    │       │
    │       ▼
    │  [세션 상태 저장]
    │  - 현재 작업 내용 → .claude/cache/SESSION_STATE.md
    │  - 미완료 TODO → TaskList에 기록
    │  - git stash 또는 WIP 커밋
    │       │
    │       ▼
    │  [사용자에게 알림]
    │  "토큰 소진. 진행 상황이 저장되었습니다.
    │   다음 세션에서 이어서 작업합니다."
    └───────────────────────────────────────┘
```

#### 세션 상태 저장 형식 (.claude/cache/SESSION_STATE.md)

```markdown
# Session State - [날짜]
## 작업 중이던 내용
- (현재 작업 요약)
## 완료된 항목
- [x] (완료 목록)
## 미완료 항목
- [ ] (남은 작업)
## 참고 사항
- (다음 세션에서 알아야 할 컨텍스트)
```

---

## AI Team 운영 원칙

### Gemini CLI 활용 (필수)
- **간단한 작업은 Gemini에게 위임**: 리서치, 버전 확인, breaking changes 조사, 코드 리뷰 등
- **호출**: `gemini -p "질문" -o text` (non-interactive 필수)
- **429 에러**: 서버 용량 부족 → 10초 후 1회 재시도, 실패 시 Claude 직접 처리
- **Gemini = 독립 작업자**: 단순 질문이 아닌 실제 업무 분배 (코드 생성, 검증, 리서치)
- **Claude 토큰 절약**: 불필요한 반복/장문 출력 금지, Gemini로 오프로드

### 멀티 에이전트 팀 구성
- **"팀 구성해"** = Task 도구로 서브에이전트 병렬 생성
- **작업량 많으면 팀 스케일 업**: 에이전트 4~8개 동시 실행
- **분배 원칙**: Gemini → 리서치/검증/간단한 작업, Claude 서브에이전트 → 실행/설치/복잡한 작업
- **병렬 최대화**: 독립 작업은 반드시 동시 실행, 순차 의존성 있을 때만 대기

### 서브에이전트 모델 선택 원칙 (필수)
| 모델 | 용도 | 예시 |
|------|------|------|
| **opus** | 설계/판단/아키텍처 의사결정 | DB 통합 설계, 보안 아키텍처, 마이그레이션 전략 |
| **sonnet** | 코드 실행/구현/설치 | Helm install, 스크립트 작성, 파일 수정, 디버깅 |
| **gemini** | 리서치/검증/버전확인 | breaking changes 조사, 이미지 태그 확인, 문서 검색 |

- Gemini 모델 로테이션: `gemini-2.5-flash` → `gemini-2.5-pro` → `gemini-3-flash-preview` → `gemini-3-pro-preview`
- 한 모델 429 에러 → 다음 모델로 자동 전환: `gemini -m gemini-2.5-pro -p "..." -o text`

### Parallel Processing
1. **터미널 탭 번호 매기기**: 1~5번 탭에서 독립 작업
2. **각 탭은 별도 git checkout**: `git worktree`로 독립 브랜치
3. **웹 세션 활용**: claude.ai/code에서 추가 세션
4. **--teleport로 세션 이동**: 결과 공유

### 디버깅 시 팀 적극 활용 (필수)

> 디버깅 상황에서는 단독으로 해결하려 하지 말고, 팀(서브에이전트 + Gemini)을 적극 활용하세요.

| 상황 | 팀 활용 방법 |
|------|-------------|
| **Pod 실패 디버깅** | Task 에이전트로 로그/이벤트/describe 병렬 수집, Gemini로 에러 메시지 분석 |
| **Helm 설치 실패** | Gemini로 차트 버전 호환성/breaking changes 확인, 서브에이전트로 values 검증 |
| **네트워크 문제** | 서브에이전트로 DNS/서비스/엔드포인트 동시 조사 |
| **이미지 문제** | Gemini로 ARM64 지원 여부/태그 확인, 서브에이전트로 레지스트리 접근 테스트 |
| **Vagrant 프로비저닝 실패** | 서브에이전트로 VM 로그/스크립트 출력 분석, Gemini로 대안 검색 |

#### 디버깅 팀 워크플로우

```
[에러 발생]
    │
    ├─ 즉시 병렬 수집 (Task 에이전트 2~3개)
    │   ├─ 에이전트 1: kubectl logs / events
    │   ├─ 에이전트 2: kubectl describe / get
    │   └─ 에이전트 3: helm status / values 확인
    │
    ├─ 에러 분석 (Gemini)
    │   └─ 에러 메시지 + 컨텍스트 → 원인 분석
    │
    └─ Claude가 종합 판단 → 수정 적용
```

---

## Ralph 기법 (by Geoffrey Huntley)

> "Ralph is a Bash loop" - https://ghuntley.com/ralph/

### 개념

PROMPT.md 파일을 무한 루프로 Claude에게 반복 전달하여 **자율적 개발**을 수행하는 기법.

```bash
# 기본 Ralph 루프
while :; do cat PROMPT.md | claude --dangerously-skip-permissions; done
```

### 언제 사용하나요?

- 대규모 반복 작업 (다수 파일 생성, 마이그레이션)
- 신규 프로젝트 스캐폴딩
- 자율적 버그 수정 및 테스트
- 장시간 무인 작업

### 이 프로젝트에서 Ralph 활용

```bash
# 1. PROMPT.md 작성
cat > PROMPT.md << 'EOF'
# Task: GitOps 앱 추가

## 목표
Velero 백업 애플리케이션을 GitOps로 추가

## 작업 목록
1. gitops/apps/velero.yaml 생성
2. gitops/apps/velero.yaml에 Helm values inline 추가
3. VERSIONS.md 업데이트
4. app-of-apps.yaml에 참조 추가

## 완료 조건
- 모든 YAML이 문법적으로 유효
- ArgoCD Application 스펙 준수
- VERSIONS.md와 버전 일치

## 검증
완료 후 `yq eval '.' gitops/apps/velero.yaml` 실행
EOF

# 2. Ralph 실행
.claude/scripts/ralph.sh
```

### 안전 장치

- `--dangerously-skip-permissions` 사용 시 주의
- PROMPT.md에 명확한 범위 제한 필수
- 정기적으로 git diff 확인
- AgentStop hook으로 검증

### Ralph PROMPT.md 템플릿

`.claude/templates/PROMPT.md` 참조

---

## Context Management (컨텍스트 관리)

> 컴팩션 후에도 CLAUDE.md는 항상 재로드됩니다. 이 섹션의 지침은 세션 연속성을 보장합니다.

### 자동 컴팩션 대응

컨텍스트 사용량이 **70% 이상**이라고 판단되면 즉시:

1. 현재 작업을 멈추고 사용자에게 알림
2. 아래 [세션 요약 포맷]에 따라 요약 생성
3. `.claude/cache/SESSION_STATE.md`에 저장
4. `/compact` 실행을 권장

### 세션 요약 포맷

요약 생성 시 반드시 아래 섹션을 포함:

```markdown
# Session State - [날짜]

## 목표
- 이 세션의 최종 목표

## 기술 환경
- 플랫폼, K8s 버전, 핵심 아키텍처 결정

## 완료된 작업
- [x] 해결된 문제 및 구현된 기능
- [x] 중요 코드 패턴 (스니펫 포함)

## 변경된 파일
- 파일 경로: 변경 내용 요약

## 미완료 작업
- [ ] 남은 작업 (우선순위 순)

## 현재 상태
- 요약 직전 작업 중이던 내용
- 즉시 실행할 다음 단계
```

### 세션 복원

`.claude/cache/SESSION_STATE.md` 파일이 존재하면 세션 시작 시 자동으로 읽고
사용자에게 "이전 세션 컨텍스트를 복원했습니다." 라고 알린 후 미완료 작업부터 재개.

### `/compact` 슬래시 커맨드

수동으로 정밀 요약이 필요할 때 `/compact` 실행.
