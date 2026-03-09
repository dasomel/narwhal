# Narwhal 프로젝트 문서 점검 보고서

**점검 일시**: 2026-03-08  
**점검 범위**: docs/, README.md, VERSIONS.md, CLAUDE.md  
**대상 프로젝트**: `/Users/m/Documents/IdeaProjects/narwhal`

---

## 1. 주요 발견사항 (Critical Issues)

### 1.1 스크립트 파일명 불일치 (최우선)

**상황**: 최근 리팩토링으로 인해 스크립트 파일이 분할되었으나, 문서가 구식 파일명을 계속 참조하고 있습니다.

#### 08-platform-apps.sh 분할 (실제 구조)
```
구식 (문서): 08-platform-apps.sh (1개 파일, 존재하지 않음)
신규 (실제): 
  - 08-1-networking.sh    (MetalLB, Traefik, Gateway API)
  - 08-2-monitoring.sh    (Prometheus, Loki, Tempo, Promtail)
  - 08-3-security.sh      (cert-manager, OpenBao, Kyverno)
  - 08-4-storage.sh       (SeaweedFS, csi-driver-nfs)
  - 08-5-registry.sh      (Harbor)
  - 08-6-tls-routes.sh    (Traefik HTTPRoute 설정)
```

#### 11-keycloak.sh 분할 (실제 구조)
```
구식 (문서): 11-keycloak.sh (1개 파일, 존재하지 않음)
신규 (실제):
  - 11-1-keycloak-operator.sh    (Operator 설치)
  - 11-2-keycloak-realm.sh       (Realm + Users 설정)
  - 11-3-keycloak-clients.sh     (OIDC 클라이언트 + 권한)
  - 11-4-keycloak-apiserver.sh   (API Server OIDC 통합)
```

#### 영향을 받는 문서 (20개 참조)

| 파일 | 라인 수 | 상태 |
|------|--------|------|
| **architecture.md** | 8개 | ❌ 심각: 플로우 섹션 (L532-655), Secrets 매핑 테이블 |
| **disaster-recovery.md** | 4개 | ❌ 심각: 복구 절차 명령어 (L697, 927, 1348-1349) |
| **operations.md** | 3개 | ❌ 심각: 수동 실행 명령어 (L95, 104, 107) |
| **troubleshooting.md** | 2개 | ⚠️ 중간: 설치 순서 설명 (L16, 118) |
| **keycloak-sso.md** | 2개 | ⚠️ 중간: 설정 순서 설명 (L21, 24) |
| **CLAUDE.md** | 8개 | ⚠️ 중간: Core Flows 테이블, Gemini 예제 |

### 1.2 문서 간 정보 불일치

#### CLAUDE.md vs architecture.md
- **CLAUDE.md "Core Flows" 섹션**: 구식 08-platform-apps.sh, 11-keycloak.sh 참조
- **architecture.md "플로우" 섹션**: 동일하게 구식 파일명 참조
- 둘 다 실제 파일명(08-1~08-6, 11-1~11-4)과 맞지 않음

#### VERSIONS.md 업데이트 상태
- ✅ **상태**: 최신 (2026-03-08 체크됨)
- ✅ Traefik v39.0.0 (chart) / v3.6.7 (app) 명시
- ✅ Keycloak v26.5.3 (Operator 기반)
- ✅ Kubernetes v1.35.1, Cilium v1.19.0, Istio v1.29.0
- ✅ 모든 주요 컴포넌트 버전 정확함

#### README.md 업데이트 상태
- ✅ **상태**: 최신
- ✅ 컴포넌트 목록 최신 (Line 89-118)
- ✅ 스크립트 참조는 없음 (파일명 불일치 영향 없음)
- ✅ 접속 URL, 자격증명 정보 정확

---

## 2. 문서 품질 평가

### 2.1 docs/ 디렉토리 구조 및 완성도

| 파일 | 크기 | 최종 수정 | 상태 |
|------|------|---------|------|
| **disaster-recovery.md** | 1465줄 | 2026-02-26 | ✅ 매우 상세, 절차 명확 |
| **operations.md** | 852줄 | 2026-02-20 | ✅ 운영 체크리스트 완전 |
| **architecture.md** | 696줄 | 2026-02-26 | ⚠️ 상세하나 파일명 오래됨 |
| **reboot-survivability.md** | 466줄 | 2026-02-26 | ✅ 리부트 복구 아키텍처 완전 |
| **developer-onboarding.md** | 367줄 | 2026-02-26 | ✅ 개발자 가이드 충분 |
| **troubleshooting.md** | 308줄 | 2026-02-20 | ✅ K8s 1.35+ OIDC 문제 상세 |
| **database.md** | 278줄 | 2026-02-20 | ✅ CNPG, narwhal-db 구조 명확 |
| **dns-access.md** | 209줄 | 2026-02-19 | ✅ dnsmasq, Traefik 라우팅 |
| **kubeconfig.md** | 201줄 | 2026-02-24 | ✅ OIDC 토큰 기반 접근 |
| **keycloak-accounts.md** | 134줄 | 2026-03-05 | ✅ SSO 계정 가이드 최신 |
| **keycloak-sso.md** | 539줄 | 2026-02-25 | ⚠️ 상세하나 파일명 오래됨 |
| **security.md** | 112줄 | 2026-02-19 | ⚠️ 미니멀, 보안 정책만 |

**총계**: 5627줄 (12개 파일)

### 2.2 문서 강점

1. **완벽한 SSO 아키텍처 문서화** (keycloak-sso.md, architecture.md)
   - Gateway-Level ForwardAuth + OAuth2-Proxy 패턴
   - 그룹 기반 접근 제어 (RBAC)
   - 앱별 리다이렉트 맵

2. **상세한 재해 복구 절차** (disaster-recovery.md)
   - 마스터 노드 장애 복구
   - 클러스터 재시작 체크리스트
   - CNPG WAL 아카이브 복구

3. **명확한 운영 가이드** (operations.md)
   - 일상 운영 체크리스트
   - 클러스터 버전 업그레이드
   - Pod 디버깅 패턴

4. **리부트 생존성 설계 문서** (reboot-survivability.md)
   - VM 재시작 후 자동 복구
   - kube-vip VIP 재획득
   - 네트워크 초기화 시나리오

### 2.3 문서 약점

1. **구식 스크립트 파일명 참조** ❌
   - 08-platform-apps.sh 분할 이후 미반영
   - 11-keycloak.sh 분할 이후 미반영
   - 사용자가 실행하려는 명령어가 실패함

2. **security.md 미흡** ⚠️
   - 112줄, 최소한의 정책만 제시
   - Kyverno 정책, mTLS, RBAC 세부사항 부재

3. **docs/ 검색성 낮음** ⚠️
   - 목차(TOC) 없음
   - docs/index.md 또는 README.md 가이드 없음
   - 어떤 문서부터 읽어야 하는지 불명확

---

## 3. 스크립트 참조 상세 분석

### 3.1 CLAUDE.md (프로젝트 가이드)

**위치**: `/Users/m/Documents/IdeaProjects/narwhal/CLAUDE.md`

**문제 섹션** (L174-195): "## Core Flows" → "### 2. Master 노드 설정 플로우"

```markdown
[문제] L189: 플랫폼 앱 | `scripts/cluster/08-platform-apps.sh` | MetalLB, Traefik, cert-manager 등
[문제] L192: Keycloak | `scripts/cluster/11-keycloak.sh` | IAM/SSO + OIDC
[문제] L193: Gitea | `scripts/cluster/12-gitea.sh` | Git 서버 (OK, 분할 없음)
```

**문제 섹션** (L391, 456, 458): Gemini CLI 사용 예제
```bash
cat scripts/cluster/11-keycloak.sh | gemini ...  # 파일 존재 안 함
sed -n '50,80p' scripts/cluster/11-keycloak.sh | gemini ...  # 파일 존재 안 함
```

### 3.2 architecture.md

**위치**: `/Users/m/Documents/IdeaProjects/narwhal/docs/architecture.md`

**문제 섹션** (L532-655): "## 클러스터 프로비저닝 흐름" 및 "Secrets 매핑 테이블"

```markdown
[문제] L532:  08-platform-apps.sh  → MetalLB, Traefik, cert-manager, Prometheus,
[문제] L537:  11-keycloak.sh       → Keycloak Operator + OIDC 설정 + API Server 연동 (HTTPS)
[문제] L538:  12-gitea.sh          → Gitea Git 서버 (shared narwhal-db)
[문제] L650-655: 테이블의 "11-keycloak.sh", "08-platform-apps.sh" 참조
```

### 3.3 disaster-recovery.md

**위치**: `/Users/m/Documents/IdeaProjects/narwhal/docs/disaster-recovery.md`

**위험도**: 🔴 **높음** (사용자가 수동 복구 명령어 실행 시 실패)

```markdown
[문제] L697:   vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/08-platform-apps.sh"
[문제] L927:   vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-keycloak.sh"
[문제] L1348:  vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-keycloak.sh"
[문제] L1349:  vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/12-gitea.sh"
```

실행 시 결과:
```
bash: /home/vagrant/scripts/cluster/08-platform-apps.sh: No such file or directory
bash: /home/vagrant/scripts/cluster/11-keycloak.sh: No such file or directory
```

### 3.4 operations.md

**위치**: `/Users/m/Documents/IdeaProjects/narwhal/docs/operations.md`

**위험도**: 🔴 **높음** (수동 운영 시 명령어 실패)

```markdown
[문제] L95:  vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/08-platform-apps.sh"
[문제] L104: vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/11-keycloak.sh"
[문제] L107: vagrant ssh master-1 -c "sudo bash /home/vagrant/scripts/cluster/12-gitea.sh"
```

### 3.5 troubleshooting.md & keycloak-sso.md

**위험도**: 🟡 **중간** (설명 문구, 실행 명령 아님)

```markdown
troubleshooting.md L16:  설치 순서: 08-platform-apps (cert-manager/Traefik) → ...
keycloak-sso.md L21:     08-platform-apps.sh  → cert-manager + Traefik TLS 설치
```

---

## 4. 개선 권고사항

### 4.1 즉시 조치 (Priority 1)

**A. 스크립트 파일명 일괄 업데이트**

영향받는 파일:
- [ ] CLAUDE.md (L189, 192, 391, 456, 458)
- [ ] docs/architecture.md (L532, 537, 538, 650-655)
- [ ] docs/disaster-recovery.md (L697, 927, 1348-1349)
- [ ] docs/operations.md (L95, 104, 107)
- [ ] docs/keycloak-sso.md (L21, 24)
- [ ] docs/troubleshooting.md (L16, 118)

**수정 패턴**:

```bash
# 문서에서 직접 실행 명령인 경우
08-platform-apps.sh → 
  "08-1-networking.sh && 08-2-monitoring.sh && ... && 08-6-tls-routes.sh"

# 설명 문구인 경우
11-keycloak.sh → "11-1,11-2,11-3,11-4-keycloak*.sh (Operator, Realm, Clients, APIServer)"

# 테이블 참조인 경우
| ... | 08-platform-apps.sh | ... | →
| ... | 08-1~08-6-*.sh | ... |
```

**B. 06-phase2-start.sh 실제 구현 확인**

06-phase2-start.sh가 08-1~08-6을 어떻게 호출하는지 문서화 필요.

```bash
vagrant ssh master-1 -c "cat /home/vagrant/scripts/cluster/06-phase2-start.sh | grep -E '08-|11-'"
```

### 4.2 단기 개선 (Priority 2)

**A. docs/index.md 또는 README 추가**

```markdown
# Narwhal 문서 가이드

## 빠른 시작
- [Quick Start](../README.md#quick-start)

## 설정 후 (한 번만)
1. [DNS 접속 설정](dns-access.md)
2. [Kubeconfig 설정](kubeconfig.md)
3. [Keycloak SSO 계정](keycloak-accounts.md)

## 일상 운영
1. [운영 가이드](operations.md)
2. [트러블슈팅](troubleshooting.md)
3. [재해 복구](disaster-recovery.md)

## 고급 주제
1. [아키텍처](architecture.md)
2. [보안 정책](security.md)
3. [개발자 온보딩](developer-onboarding.md)
```

**B. security.md 강화**

현재: 112줄 (미니멀)
제안: 
- Kyverno 정책 예제 추가
- RBAC 역할 정의 확대
- mTLS (Istio ambient) 설정
- NetworkPolicy 패턴

### 4.3 장기 개선 (Priority 3)

**A. 버전 호환성 매트릭스 (VERSIONS.md 확장)**

```markdown
## Compatibility Matrix (추가)

### K8s 버전별 지원 컴포넌트

| K8s | Cilium | Istio | Keycloak | ArgoCD |
|-----|--------|-------|----------|--------|
| 1.35 | 1.19+ | 1.29+ | 26+ | 3.3+ |
| 1.34 | 1.17+ | 1.28+ | 25+ | 3.2+ |
| 1.33 | 1.16+ | 1.27+ | 24+ | 3.1+ |
```

**B. 스크립트 자동 문서화**

각 스크립트 헤더에 metadata 추가:
```bash
#!/bin/bash
# @desc: Install MetalLB and initial Traefik setup
# @component: Networking (MetalLB, Traefik Gateway API)
# @depends_on: 07-cnpg.sh
# @depends_for: 08-2-monitoring.sh, 08-3-security.sh
```

스크립트에서 자동으로 문서 생성 가능.

---

## 5. 최종 평가

### 📊 종합 점수: 7.5/10

| 항목 | 점수 | 평가 |
|------|------|------|
| **완성도** | 8/10 | 모든 주요 기능 문서화됨 |
| **정확성** | 6/10 | ❌ 스크립트 파일명 불일치로 감점 |
| **최신성** | 7/10 | VERSIONS.md는 최신, 스크립트 참조는 구식 |
| **접근성** | 7/10 | ⚠️ 문서 간 탐색 어려움 (목차 부재) |
| **실행 가능성** | 6/10 | ❌ 명령어 실패 위험 (08-*, 11-* 파일 미존재) |

### ✅ 강점
- SSO 아키텍처 상세 설명
- 재해 복구 절차 완전
- 운영 체크리스트 명확

### ⚠️ 약점
- **스크립트 파일명 불일치** (20개 참조)
- 문서 네비게이션 부족
- security.md 미흡
- 개발자 온보딩 미싱 항목

### 🎯 즉시 해결 필요
1. **CLAUDE.md, architecture.md, disaster-recovery.md, operations.md 파일명 수정**
   - 영향도: 높음 (사용자 명령어 실패)
   - 작업 시간: ~30분
   
2. **Vagrantfile 또는 06-phase2-start.sh 확인**
   - 실제 스크립트 호출 순서 검증

---

## 6. 추가 정보

### 최근 변경 이력 (git log)
```
ecaf2f1 fix(traefik): add kube-system to Gateway allowedRoutes for Hubble
bfbce4a fix(keycloak): fix user password defaults to match documentation
efa3e36 fix: Remove hardcoded S3/DB credentials and align RBAC with GitOps policy
77b0216 fix: Resolve ArgoCD RBAC conflict, add Harbor body limit, clean up deprecated DB files
34f3906 fix: Build TLS CA chain, fix OIDC CA propagation, and harden SSO config
```

→ 최근 커밋에서 스크립트 리팩토링 이력이 보이지 않음.  
   문서와 스크립트의 동기화 시점 불명확. 리팩토링 후 문서 업데이트 스킵된 것으로 추정.

