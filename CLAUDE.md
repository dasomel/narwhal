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

### GitOps/ArgoCD 실수
| 날짜 | 실수 | 해결책 |
|------|------|--------|
| - | values 파일 경로 오타 | `valueFiles` 경로는 repoURL 기준 상대경로 |
| - | targetRevision 형식 오류 | 차트 버전은 `"1.0.0"` (문자열), Git ref는 `HEAD` |

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
| NFS 클라이언트 | `scripts/common/04-nfs-client.sh` | NFS 마운트 설정 |

### 2. Master 노드 설정 플로우

| 단계 | 파일 | 설명 |
|------|------|------|
| NFS 서버 | `scripts/master/00-nfs-server.sh` | NFS 서버 설정 |
| kube-vip | `scripts/master/01-kube-vip.sh` | Control Plane VIP |
| 클러스터 초기화 | `scripts/master/02-init-cluster.sh` | kubeadm init |
| CNI 설치 | `scripts/master/03-cni-install.sh` | Cilium + Hubble |
| 애드온 | `scripts/master/04-addons.sh` | metrics-server, csi-driver-nfs |
| NFS 쿼터 | `scripts/master/05-nfs-quota-agent.sh` | NFS 프로젝트 쿼터 |
| PostgreSQL | `scripts/master/06-cnpg.sh` | CloudNative-PG Operator |
| Keycloak | `scripts/master/07-keycloak.sh` | IAM/SSO |
| 플랫폼 앱 | `scripts/master/08-platform-apps.sh` | cert-manager 등 |
| dnsmasq | `scripts/master/09-dnsmasq.sh` | 로컬 DNS (*.local) |
| Gitea | `scripts/master/10-gitea.sh` | Git 서버 |
| ArgoCD | `scripts/master/11-argocd.sh` | GitOps CD |
| Bootstrap | `scripts/master/12-gitops-bootstrap.sh` | App-of-Apps 배포 |

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
vagrant up master
vagrant up worker-1

# SSH 접속
vagrant ssh master

# kubectl 확인
vagrant ssh master -c "kubectl get nodes"

# 재프로비저닝
vagrant provision master

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
| Master IP | 192.168.56.10 |
| Worker IPs | 192.168.56.21-22 |
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
   vagrant ssh master -c "kubectl get nodes"
   vagrant ssh master -c "kubectl get pods -A"
   ```

4. **ArgoCD 동기화 확인**
   ```bash
   vagrant ssh master -c "kubectl get applications -n argocd"
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

## Parallel Processing Tips

여러 Claude 세션을 동시에 실행하여 속도 극대화:

1. **터미널 탭 번호 매기기**: 1~5번 탭에서 독립 작업
2. **각 탭은 별도 git checkout**: `git worktree`로 독립 브랜치
3. **웹 세션 활용**: claude.ai/code에서 추가 세션
4. **--teleport로 세션 이동**: 결과 공유

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
2. gitops/values/velero-values.yaml 생성
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
