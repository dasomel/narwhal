# narwhal K8s 플랫폼 보안 검토 보고서 — KISA + 국제표준 갭 분석

> 작성: 2026-06-27 · 대상: narwhal 클러스터 (Vagrant 기반 K8s IDP) · 유형: **read-only 검토** (코드 수정 없음)
> 범위: KISA 중심 + CIS Kubernetes Benchmark · NSA/CISA Hardening · NIST SP 800-190 · MITRE ATT&CK Containers
> 방법: 표준 통제 체크리스트(웹 리서치) ↔ narwhal 실구성(repo 증거) 대조

---

## 0. 요약 (Executive Summary)

narwhal은 **OIDC(Keycloak)·Cilium 정책·Kyverno·OpenBao·Istio STRICT mTLS·Trivy·Falco** 등 보안 기반 요소를 폭넓게 갖춘, 성숙도 중상 수준의 IDP다. 그러나 KISA/CIS/NSA 표준 대비 **감사·암호화·시크릿 보호·정책 적용 범위**에서 구조적 갭이 존재한다.

| 심각도 | 건수 | 핵심 |
|--------|------|------|
| 🔴 CRITICAL | 3 | API 감사로그 부재, etcd 저장 암호화 부재, OpenBao unseal 키 평문 Secret |
| 🟠 HIGH | 4 | APISIX etcd 평문 HTTP, Harbor `skip_verify=true`, 온디맨드 cluster-admin SA, mTLS 광범위 opt-out |
| 🟡 MEDIUM | 3 | PSA 라벨 부재, Alertmanager 수신자 미설정, ingress default-deny 부재 |
| ⚪ LOW/관찰 | 다수 | `:latest` 태그, 10년 CA, Cosign 미서명, CIS 노드 하드닝 미적용 등 |

> **결론**: 즉시 반영 권장은 🔴 3건 + 🟠 4건. 대부분 **현행 기반 위에 설정 추가**로 해결 가능(가역적). KISA 주통가이드(2026 클라우드 신설)·ISMS-P 대응 관점에서 감사로그·암호화·로그 알림은 "상" 등급 점검 항목과 직접 매핑된다.

---

## 1. 적용 표준 (출처·버전)

| 표준 | 버전/연도 | 비고 |
|------|----------|------|
| KISA 클라우드 취약점 점검 가이드 | 2024년판 (39항목, K8s Master #27/Worker #28) | CCE 점검 대상 |
| KISA 주요정보통신기반시설 기술적 취약점 분석·평가 상세가이드 | **2026년판** (2025-12 등록, 클라우드 분야 신설 19항목, "상" 7개) | 국내 기반시설 강제 |
| ISMS-P 인증기준 안내서 | 2023-11 | 2.7(암호화)·2.11(사고대응)·2.6(접근통제) |
| CIS Kubernetes Benchmark | v2.0.1 | upstream |
| NSA/CISA Kubernetes Hardening Guide | v1.2 (2022-08) | |
| NIST SP 800-190 | Final (2017) — 현행 | 컨테이너 보안 |
| MITRE ATT&CK for Containers | Enterprise v16.x | 위협 매핑 |

---

## 2. 심각도별 발견사항 (반영 권장 순)

### 🔴 CRITICAL

**C-1. Kubernetes API 감사 로그 미설정**
- 현황: kube-apiserver에 `--audit-log-path`·`--audit-policy-file` 부재. Falco의 `k8s_audit_rules.yaml`는 로드되나 audit webhook 백엔드가 없어 이벤트 수신 불가.
- 표준: CIS 1.2.x, NSA/CISA §7, **KISA 주통 "운영관리" / ISMS-P 2.11**. ATT&CK: Defense Evasion 가시성 상실.
- 위험: API 서버 활동의 포렌식 추적 전무 → 침해 사고 시 행위 재구성 불가.
- 권고: kubeadm `ClusterConfiguration.apiServer.extraArgs`에 audit 정책/경로 추가, 로그를 Loki로 출하. **KISA 점검에서 가장 먼저 지적될 항목.**

**C-2. etcd 저장 시 암호화(at-rest) 부재**
- 현황: `--encryption-provider-config` 없음 → Secret·ConfigMap 등 모든 객체가 etcd에 평문 저장.
- 표준: CIS 2.x, NSA/CISA §3, **ISMS-P 2.7.1(저장 데이터 암호화)**, KISA 클라우드 가이드.
- 위험: etcd 백업/디스크 유출 = 전 클러스터 시크릿 유출. C-3와 결합 시 영향 증폭.
- 권고: aescbc/secretbox 또는 KMS provider로 EncryptionConfiguration 적용 후 기존 Secret 재암호화(`kubectl get secrets -A -o ... | kubectl replace`).

**C-3. OpenBao unseal 키가 같은 네임스페이스 평문 K8s Secret에 저장**
- 현황: `storage/openbao-init` 에 단일 Shamir 키 평문 보관 — OpenBao와 동일 네임스페이스.
- 표준: NIST 800-190 §4.5, ISMS-P 2.7, KISA. ATT&CK: T1552 Unsecured Credentials.
- 위험: `storage` ns에서 `get secrets` 권한 보유 주체는 누구나 Vault 언실 가능 → 시크릿 보호 체계 무력화.
- 권고: unseal 키를 클러스터 밖(KMS auto-unseal, 또는 운영자 보관)으로 분리. 최소한 별도 ns + RBAC 격리. C-2 적용 시 평문 저장 위험 일부 완화.

### 🟠 HIGH

**H-1. APISIX etcd 평문 HTTP·비인증** (`gitops/.../apisix-infra.yaml:27-28`)
- 모든 APISIX 라우트(OIDC client secret을 `$env://`로 주입 포함)의 설정 저장소가 비인증 HTTP. TLS·peer auth 없음.
- 표준: CIS, NSA/CISA, KISA 네트워크 통제. 권고: APISIX etcd에 TLS+인증, 또는 mesh 내부로 한정 + NetworkPolicy.

**H-2. Harbor `skip_verify=true` (전 노드 containerd)** (`scripts/common/02-containerd.sh:53`)
- 내부 레지스트리 TLS 인증서 검증 비활성 → 이미지 pull 경로 MitM 위험.
- 표준: NIST 800-190 §3.1, KISA Docker #26, CIS. 권고: Harbor CA를 노드 신뢰 스토어에 등록하고 `skip_verify` 제거. (과거 노드-CA 블록 제거 이력 있음 — 재설계 필요)

**H-3. 온디맨드 cluster-admin SA 토큰 생성** (`set-config.sh:133-141`)
- `token` 모드가 `kube-system`에 `admin-user` SA + `cluster-admin` ClusterRoleBinding + 8760h 토큰을 생성, 바인딩이 클러스터에 잔존.
- 표준: CIS 5.1(최소권한), NSA/CISA RBAC, ISMS-P 2.6. ATT&CK: T1078. 권고: 단명 토큰(TokenRequest, 짧은 TTL)·작업 후 바인딩 회수·cluster-admin 직접 부여 지양.

**H-4. Istio mTLS 광범위 opt-out**
- ArgoCD·Grafana·Harbor·Gitea·APISIX·OpenBao·Falco·Trivy·`dev` ns가 `istio.io/dataplane-mode: none`. 핵심 서비스 간 east-west 트래픽이 mesh 강제 경계 밖.
- 표준: NSA/CISA(전송 암호화), ISMS-P 2.7.1. 권고: opt-out 최소화, 불가 구간은 NetworkPolicy로 보완. AuthorizationPolicy 부재도 함께 해소.

### 🟡 MEDIUM

**M-1. 네임스페이스 PSA(Pod Security Admission) 라벨 부재**
- K8s 내장 PSA 미사용 → 워크로드 보안이 전적으로 Kyverno 가동에 의존. `kube-system`·`istio-system`·`platform-system`은 Kyverno 정책 제외 → 무보호.
- 표준: CIS 5.2, Pod Security Standards(restricted), KISA. 권고: 네임스페이스별 `pod-security.kubernetes.io/enforce` 라벨 부여(이중 방어).

**M-2. Alertmanager 수신자 미설정** (`alertmanager-config.yaml:29-44`)
- 모든 알림 룰이 이벤트는 생성하나 실제 통지 경로 없음 → Falco critical·인증서 만료·노드 압박이 무음 처리.
- 표준: ISMS-P 2.11.1, KISA 운영관리. 권고: Slack/Email/웹훅 수신자 구성 + 보안 전용 알림 룰(Falco critical, Trivy CVE 심각도) 추가.

**M-3. Ingress default-deny NetworkPolicy 부재**
- ingress NetworkPolicy가 어디에도 없음 → 클러스터 내 모든 pod가 상호 인바운드 수신 가능. mTLS opt-out(H-4)과 결합 시 측면 이동 위험.
- 표준: CIS 5.3, NSA/CISA §4, KISA 망분리. 권고: 네임스페이스별 default-deny ingress + 명시적 허용. (egress allowlist는 일부 존재)

### ⚪ LOW / 관찰 (반영 선택)

- **이미지 `:latest` 태그**: Harbor 8개 컴포넌트·narwhal-portal. Kyverno `:latest` 금지 정책이 **Audit(비강제)**. → Enforce 전환 + 다이제스트 핀.
- **Cosign 이미지 서명/검증 부재**: 공급망 무결성(NSA/CISA, SLSA). → Kyverno verifyImages 정책.
- **10년 self-signed CA + 와일드카드 인증서**: 단일 침해점. → 수명 단축·서비스별 인증서.
- **CIS 노드 하드닝 미적용**: AppArmor/SELinux 미사용, kubelet `read-only-port=0` 미설정, control-plane taint 제거, 마스터 간 SSH `StrictHostKeyChecking=no`. → CIS Node Benchmark 적용.
- **Valkey 인증 없음**, **포털 단일 번들 Secret에 전 자격증명 집약**, **CronJob `BAO_SKIP_VERIFY`**, **Grafana TLS skip**.
- **automountServiceAccountToken: false 미설정** (대부분 SA).

---

## 3. 도메인별 컴플라이언스 매트릭스

| # | 도메인 | 구성된 것 | 주요 갭 | 등급 |
|---|--------|----------|---------|------|
| 1 | 컨트롤플레인/API | OIDC, certSANs, kube-vip HA | 감사로그·anonymous-auth·admission·암호화 설정 없음 | 🔴 |
| 2 | etcd | kubeadm TLS, commit latency 알림 | at-rest 암호화 없음, APISIX etcd 평문 | 🔴 |
| 3 | RBAC/인증 | 커스텀 ClusterRole, Keycloak OIDC, groups 매핑 | 온디맨드 cluster-admin SA, 1년 토큰, automount | 🟠 |
| 4 | 네트워크 | Cilium+Hubble, egress allowlist | ingress default-deny 없음, 일부 ns 무커버 | 🟡 |
| 5 | Pod 보안 | Kyverno Enforce(no priv/hostNS), 변형 주입 | PSA 라벨 없음, 핵심 ns 제외, Velero/Falco privileged | 🟡 |
| 6 | Admission/정책 | Kyverno 3.8.1(3replica), 6 정책 | 이미지 서명검증 없음, fail-open, 제외 ns | 🟠 |
| 7 | 시크릿 | OpenBao(TLS+raft+audit) | unseal 키 평문, KMS·ESO 없음, Valkey 무인증 | 🔴 |
| 8 | 이미지/공급망 | Harbor, Trivy(취약점+시크릿 스캔) | skip_verify, Cosign 없음, :latest Audit only | 🟠 |
| 9 | 런타임/감사 | Falco(modern_ebpf, Loki+AM fanout) | API 감사로그 없음, AM 수신자 빈값, Falco full privileged | 🔴 |
| 10 | 노드/OS | chrony, cgroup systemd, apt-hold | CIS 하드닝·AppArmor·kubelet RO포트 없음 | 🟡 |
| 11 | 관측 | Prometheus/Loki/Tempo/Grafana | 보안 전용 알림 없음, SIEM 없음 | 🟡 |
| 12 | TLS/mesh | cert-manager, Istio STRICT mTLS | 광범위 opt-out, AuthorizationPolicy 없음, 10년 CA | 🟠 |

---

## 4. KISA / 국내 특화 강조점

- **주통가이드 2026 클라우드 신설**(계정·권한·가상리소스·운영관리 19항목, "상" 7개) → narwhal의 **감사로그(C-1)·로그 알림(M-2)·최소권한(H-3)** 이 직접 점검 대상.
- **CCE 점검 대상 분리**: 쿠버네티스 Master(#27)/Worker(#28)/Docker(#26) 독립 점검 → 노드 하드닝(도메인10) 별도 평가.
- **ISMS-P 2.7.1 암호화**: etcd at-rest(C-2)·외부 시크릿 관리(C-3) 미흡 시 인증 결함.
- **망분리 요건**(국내 금융·공공): 물리/논리 분리 → ingress default-deny(M-3)·NetworkPolicy 강화 필요.
- **로그 보존**: 업종별 최대 5년 — Loki 보존정책 점검 필요.
- **국정원 가이드**(국가·공공기관): 별도 비공개 기준 추가 준수 — 공개 참조 불가, 해당 시 별도 확인.

---

## 5. 반영 권장 로드맵 (단계별, 모두 가역적)

**Phase 1 — 즉시 (🔴, 1~2일)**
1. API 감사 정책·로그 활성화(C-1) → Loki 출하
2. etcd EncryptionConfiguration 적용 + 기존 Secret 재암호화(C-2)
3. OpenBao unseal 키 클러스터 외부 분리 또는 ns/RBAC 격리(C-3)

**Phase 2 — 단기 (🟠, ~1주)**
4. APISIX etcd TLS+인증(H-1) / 5. Harbor CA 신뢰 등록 + skip_verify 제거(H-2)
6. cluster-admin SA 온디맨드 → 단명 토큰화(H-3) / 7. mTLS opt-out 축소 + AuthorizationPolicy(H-4)

**Phase 3 — 중기 (🟡, ~2주)**
8. 네임스페이스 PSA enforce 라벨(M-1) / 9. Alertmanager 수신자 + 보안 알림 룰(M-2) / 10. ingress default-deny(M-3)

**Phase 4 — 강화 (⚪)**
- `:latest` Enforce 전환·다이제스트 핀, Cosign 서명검증, CIS 노드 하드닝, CA 수명 단축, Valkey 인증, automount 비활성.

> 실제 적용은 narwhal-ops 하니스로 위임 권장(GitOps 기반 — Gitea push 필요, kubectl 직접 변경은 selfHeal로 복원됨). 본 검토는 분석/권고만.

---

## 6. 검증 한계 (정직 표기)

- 발견사항은 **정적 분석(repo) + 표준 리서치** 기반. 런타임 실측(실 클러스터 kube-apiserver 플래그 dump, `kubectl get` 확인)은 미수행 — 일부는 매니페스트 의도와 런타임 실태가 다를 수 있음.
- Falco 상태: 과거 "GitOps 제외" 기록과 달리 현재 `gitops/apps/falco.yaml`는 **활성+full privileged**로 확인됨(교차검증 완료). 런타임 탐지는 동작하나 privileged 구동은 별도 위험.
- KISA 2026 주통가이드는 등록 직후(2025-12)로 세부 항목 번호는 공식 PDF 재확인 권장.
- 표준 버전(CIS v2.0.1 등)은 리서치 시점 최신 — 적용 시 최신본 재확인.

---

*본 보고서는 read-only 검토 산출물이며 어떤 소스도 수정하지 않았다. 반영 결정·구현은 사용자 승인 후 narwhal-ops 하니스로 진행.*
