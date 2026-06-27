# IDP Portal Plan

## Executive Summary

| 항목 | 내용 |
|------|------|
| Feature | idp-portal |
| 작성일 | 2026-03-24 |
| 상태 | Plan |

### Value Delivered

| 관점 | 내용 |
|------|------|
| **Problem** | Narwhal IDP의 각 OSS(ArgoCD, Gitea, Harbor 등)가 분산되어 있어 개발자가 플랫폼 상태 파악과 도구 접근에 높은 인지 부하 발생 |
| **Solution** | Authentik SSO 연동 Next.js 포털로 설정 관리·메트릭·OSS 링크를 단일 화면에 통합 |
| **Function UX Effect** | 역할별(cluster-admin/developer/viewer) 맞춤 뷰, 실시간 클러스터 헬스, 원클릭 도구 접근, kubeconfig 자동 생성으로 온보딩 시간 단축 |
| **Core Value** | 개발자가 kubectl 없이도 플랫폼 상태를 파악하고 필요한 도구에 즉시 접근할 수 있는 단일 진입점 |

---

## 1. 배경 및 목적

### 1.1 현재 상황
- Narwhal IDP는 ArgoCD, Gitea, Harbor, Grafana, Headlamp 등 10개 이상의 OSS로 구성
- 각 도구별 URL을 개별적으로 기억해야 하며, 플랫폼 전체 상태를 한눈에 볼 수 없음
- 새로운 개발자 온보딩 시 kubeconfig 설정, kubectl 설치 등 수동 작업 필요
- 설정 변경(사용자 추가, APISIX 라우트 등)은 각 도구 UI에 직접 접속 필요

### 1.2 목적
- 모든 플랫폼 도구의 단일 진입점(Single Pane of Glass) 제공
- Authentik OIDC SSO로 한 번 로그인으로 전체 플랫폼 접근
- 역할 기반 화면으로 필요한 정보만 표시

### 1.3 레퍼런스
- **Cloudforet (SpaceONE)**: 멀티클라우드 관리 포털 — 대시보드 위젯 구성, 서비스 카드 레이아웃, RBAC 통합 패턴 참고

---

## 2. 요건 정의

### 2.1 기능 요건

#### FR-01: SSO 로그인
- Authentik OIDC로 로그인 (NextAuth.js)
- 그룹 클레임 기반 역할 자동 매핑 (cluster-admin / developer / viewer)
- APISIX `openid-connect` 플러그인으로 포털 자체 보호

#### FR-02: 홈 대시보드 (메트릭)
- **클러스터 상태**: 노드 수 / Ready 상태, 전체 파드 수 / Running 수
- **리소스 사용률**: CPU / Memory 사용률 (Prometheus API)
- **ArgoCD 상태**: Synced / OutOfSync / Degraded 앱 카운트
- **알럿**: Alertmanager 현재 발화 중인 알럿 목록 (Critical/Warning)
- **스토리지**: PVC 사용률 요약
- 30초 자동 갱신 (SWR)

#### FR-03: 플랫폼 도구 링크
서비스를 카드 형태로 표시, 헬스 상태(정상/경고/오프라인) 표시 후 클릭 시 해당 URL로 이동

| 카테고리 | 도구 |
|----------|------|
| GitOps | ArgoCD |
| 소스코드 | Gitea |
| 레지스트리 | Harbor |
| 모니터링 | Grafana, Prometheus, Alertmanager |
| 인프라 | Headlamp, Hubble UI |
| 보안 | OpenBao UI |
| 백업 | Velero UI |

#### FR-04: 설정 관리 (cluster-admin 전용)

**사용자/그룹 관리** (Authentik API)
- 사용자 목록 조회 / 생성 / 비활성화
- 그룹 멤버십 변경 (cluster-admin / developer / viewer / guest)
- 사용자별 마지막 로그인 시각 표시

**APISIX 라우트 관리** (APISIX Admin API)
- 현재 라우트 목록 조회
- 라우트 활성화 / 비활성화 토글
- SSO 보호 여부 표시

**cert-manager 인증서 상태** (K8s API)
- 인증서 목록 (만료일, 갱신 상태)
- 만료 임박 인증서 경고 (30일 이내)

**Kyverno 정책 상태** (K8s API)
- 정책 목록 및 위반 카운트
- Enforce/Audit 모드 표시

#### FR-05: 역할별 뷰

| 메뉴 | cluster-admin | developer | viewer |
|------|:---:|:---:|:---:|
| 홈 대시보드 | 전체 | 전체 | 전체 |
| 플랫폼 도구 링크 | 전체 | ArgoCD/Gitea/Harbor/Grafana | Grafana |
| 설정 > 사용자/그룹 | 읽기+쓰기 | 없음 | 없음 |
| 설정 > APISIX 라우트 | 읽기+쓰기 | 없음 | 없음 |
| 설정 > 인증서 | 읽기+쓰기 | 읽기 | 없음 |
| 설정 > Kyverno | 읽기+쓰기 | 읽기 | 없음 |
| 온보딩 | 전체 | 전체 | 전체 |

#### FR-06: 개발자 온보딩
- **kubeconfig 다운로드**: 로그인 사용자의 OIDC 토큰 기반 kubeconfig 자동 생성
- **kubectl 설정 가이드**: OS별 단계별 가이드 (macOS / Linux / Windows)
- **첫 배포 가이드**: Gitea → Harbor → ArgoCD 플로우 설명
- **플랫폼 아키텍처 다이어그램**: Mermaid 컴포넌트 관계도

### 2.2 비기능 요건

| 항목 | 요건 |
|------|------|
| 인증 | Authentik OIDC, 세션 토큰 자동 갱신 |
| 성능 | 초기 로드 < 3초, 메트릭 갱신 < 1초 |
| 보안 | APISIX openid-connect 플러그인으로 미인증 접근 차단 |
| 배포 | ArgoCD GitOps 관리 (`gitops/apps/idp-portal.yaml`) |
| URL | `https://portal.local.narwhal.internal` |
| 언어 | 한국어 기본 |

---

## 3. 기술 스택

### 3.1 프론트엔드
- **Next.js 15** (App Router, TypeScript)
- **pnpm** — 패키지 매니저 (속도, 디스크 효율)
- **Tailwind CSS** + **shadcn/ui** (폼/레이아웃) + **Tremor** (대시보드 위젯/차트)
- **NextAuth.js v5 (auth.js)** — OIDC 인증 (Authentik provider)
- **TanStack Query v5** — 데이터 페칭, mutation, 캐시 무효화 (SWR 대신)
- **Zustand** — 가벼운 전역 상태 (사용자 역할, UI 상태)

### 3.2 실시간 데이터

| 방식 | 용도 | 비고 |
|------|------|------|
| **TanStack Query polling** (30초) | Prometheus 메트릭, ArgoCD 상태, 알럿 | 대부분의 메트릭 |
| **SSE (Server-Sent Events)** | K8s Pod 로그 스트리밍, 배포 진행 상태 | 단방향 실시간 스트림 |
| HTTP polling (5분) | cert-manager 인증서 상태 | 변화 빈도 낮음 |

> Socket.io는 이 프로젝트 규모에서 과잉 — SSE로 충분히 구현 가능

### 3.3 백엔드 API 연동

| 소스 | 용도 | 인증 방식 |
|------|------|-----------|
| Prometheus HTTP API | 메트릭 조회 | 없음 (클러스터 내부) |
| ArgoCD API | 앱 상태 | ArgoCD API Token |
| Alertmanager API | 알럿 조회 | 없음 (클러스터 내부) |
| Loki HTTP API | 서비스 로그 조회/스트리밍 | 없음 (클러스터 내부) |
| Authentik API (`@goauthentik/api`) | 사용자/그룹 관리 | Admin API Token |
| APISIX Admin API | 라우트 조회/토글 | X-API-Key |
| Kubernetes API (`@kubernetes/client-node`) | 노드/파드/인증서/정책 | ServiceAccount Token |

### 3.4 Secret 관리 — OpenBao 연동

K8s Secret에 평문 저장하는 대신 **OpenBao**에서 동적으로 조회:

```
[idp-portal Pod 시작]
  → OpenBao Agent Injector (사이드카)
    → OpenBao KV에서 시크릿 조회
      - argocd-api-token
      - authentik-admin-token
      - apisix-x-api-key
    → /vault/secrets/ 경로에 파일로 마운트
  → Next.js가 파일에서 시크릿 읽음 (환경변수 대신)
```

> OpenBao는 Narwhal 스택에 이미 포함 (`storage` namespace)
> K8s Auth Method로 ServiceAccount 기반 인증 사용

### 3.5 캐싱 레이어 — Valkey

K8s API / Prometheus 호출 빈도를 줄이기 위해 **Valkey**를 서버단 캐시로 운용:

```
[Next.js API Route]
  → Valkey에서 캐시 조회 (HIT → 즉시 반환)
  → MISS 시 K8s API / Prometheus 호출
  → 결과를 Valkey에 TTL 설정 후 저장
```

| 데이터 | TTL | 비고 |
|--------|-----|------|
| 노드/파드 상태 | 15초 | K8s API 부하 절감 |
| Prometheus 메트릭 | 15초 | scrape interval과 맞춤 |
| ArgoCD 앱 상태 | 10초 | 배포 중 빠른 반영 |
| cert-manager 인증서 | 5분 | 변화 빈도 낮음 |
| APISIX 라우트 목록 | 30초 | 설정 변경 빈도 낮음 |

**Valkey 배포 전략**:
- Gitea의 `gitea-valkey`와 **분리** — 포털 전용 Valkey 인스턴스 배포
- namespace: `devtools`, DB 번호 분리도 가능하나 독립 인스턴스 권장
- `ioredis` 클라이언트 (TypeScript 지원)

> TanStack Query 클라이언트 캐시(staleTime)와 서버단 Valkey 캐시를 2계층으로 운용

### 3.6 배포 구조
```
[사용자 브라우저]
    → APISIX (portal.local.narwhal.internal)
        → openid-connect 플러그인 → Authentik 인증
        → Next.js Pod (namespace: devtools)
            ├── OpenBao Agent 사이드카 (시크릿 → /vault/secrets/ 마운트)
            ├── Valkey (포털 전용, devtools namespace)
            └── Next.js API Route / Server Actions (서버사이드)
                    → Prometheus, ArgoCD, Alertmanager, Loki
                    → Authentik API (@goauthentik/api)
                    → APISIX Admin API
                    → Kubernetes API (@kubernetes/client-node, ServiceAccount)
```

---

## 4. 화면 구성

### 4.1 전체 레이아웃
```
┌─────────────────────────────────────────────────────────┐
│  [Narwhal IDP]    홈 | 도구 | 설정 | 온보딩    [user v] │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  [ 홈 대시보드 ]                                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ 노드      │ │ 파드      │ │ CPU      │ │ Memory   │  │
│  │ 6/6 Ready│ │ 142 Run  │ │ 23%      │ │ 61%      │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
│                                                         │
│  ┌─ ArgoCD 앱 상태 ────┐  ┌─ 활성 알럿 ──────────────┐ │
│  │ ✅ 22 Synced        │  │ ⚠ disk-pressure (Warning) │ │
│  │ ⚠  1 OutOfSync     │  │ ⚠ ...                    │ │
│  └────────────────────┘  └──────────────────────────┘ │
│                                                         │
│  [ 플랫폼 도구 ]                                         │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ...       │
│  │ ArgoCD │ │ Gitea  │ │ Harbor │ │ Grafana│           │
│  │  ✅    │ │  ✅    │ │  ✅    │ │  ✅    │           │
│  └────────┘ └────────┘ └────────┘ └────────┘           │
└─────────────────────────────────────────────────────────┘
```

### 4.2 설정 > 사용자 관리 (cluster-admin)
```
┌─ 사용자 관리 ────────────────────────────────────────────┐
│  [+ 사용자 추가]                        [검색...]         │
│                                                         │
│  이름     이메일               그룹           마지막 로그인│
│  admin    admin@narwhal.io    cluster-admin   방금        │
│  dev      dev@narwhal.io      developer       1시간 전    │
│  viewer   view@narwhal.io     viewer          3일 전      │
└─────────────────────────────────────────────────────────┘
```

---

## 5. 구현 단계 (Milestone)

### M1: 기반 설정
- [ ] Next.js 15 프로젝트 초기화 (TypeScript + Tailwind + shadcn/ui)
- [ ] NextAuth.js v5 + Authentik OIDC 연동
- [ ] APISIX 라우트 추가 (`portal.local.narwhal.internal`)
- [ ] K8s Deployment + Service YAML
- [ ] ArgoCD Application YAML (`gitops/apps/idp-portal.yaml`)

### M2: 홈 대시보드
- [ ] Prometheus API 연동 (노드, 파드, CPU/Memory 쿼리)
- [ ] ArgoCD API 연동 (앱 상태 집계)
- [ ] Alertmanager API 연동 (알럿 목록)
- [ ] 메트릭 카드 위젯 컴포넌트
- [ ] 30초 자동 갱신 (SWR + polling)

### M3: 플랫폼 도구 링크
- [ ] 서비스 카드 컴포넌트 (아이콘 + 상태 배지 + 링크)
- [ ] 각 서비스 헬스체크 (HTTP ping)
- [ ] 역할별 카드 필터링

### M4: 설정 관리
- [ ] Authentik API 연동 (사용자/그룹 CRUD)
- [ ] APISIX Admin API 연동 (라우트 목록/토글)
- [ ] cert-manager CRD 조회 (Certificate 리소스)
- [ ] Kyverno CRD 조회 (ClusterPolicy + PolicyReport)

### M5: 온보딩
- [ ] kubeconfig 자동 생성 API 엔드포인트
- [ ] OS별 kubectl 가이드 페이지
- [ ] 플랫폼 아키텍처 다이어그램 (Mermaid)

---

## 6. 완료 조건

- [ ] `https://portal.local.narwhal.internal` 접근 시 Authentik 로그인 리다이렉트
- [ ] 로그인 후 역할(그룹)에 따른 메뉴 차등 표시
- [ ] 홈 대시보드: Prometheus 기반 클러스터 메트릭 표시
- [ ] 홈 대시보드: ArgoCD 앱 상태 요약 표시
- [ ] 홈 대시보드: Alertmanager 발화 알럿 표시
- [ ] 도구 링크: 전체 OSS 카드 + 상태 표시
- [ ] 설정: cluster-admin으로 사용자 생성/그룹 변경 가능
- [ ] 설정: APISIX 라우트 목록 조회 및 토글 가능
- [ ] 설정: 인증서 만료일 표시
- [ ] 온보딩: kubeconfig 다운로드 동작
- [ ] ArgoCD GitOps로 배포 및 selfHeal 동작
