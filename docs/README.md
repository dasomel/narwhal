# Narwhal 문서

문서는 **배포 대상**을 기준으로 나뉜다. 같은 플랫폼(APISIX, Keycloak, ArgoCD, CNPG, 스토리지)이
Vagrant VM 위에도, Kakao Cloud 위에도 올라가는데 — 그 아래 인프라 계층(노드 주소, 로드밸런서,
DNS, 이미지 반입 경로)은 완전히 다르다. 섞어두면 어느 절차가 내 환경에 해당하는지 알 수 없다.

| 디렉토리 | 무엇이 들어있나 | 언제 보나 |
|----------|-----------------|-----------|
| [`common/`](./common/) | 배포 대상과 무관한 플랫폼 계층 | 대부분의 경우 |
| [`vagrant/`](./vagrant/) | Vagrant VM 전용 (`192.168.56.x`, MetalLB, dnsmasq) | 로컬 클러스터 작업 |
| [`kakao/`](./kakao/) | Kakao Cloud 전용 (프라이빗 서브넷, LB, bastion, airgap) | 클라우드 클러스터 작업 |
| [`evaluation/`](./evaluation/) | 단계별 검증 가이드 (정적 검사 → E2E → 스모크 → 부하 → 카오스) | 커밋/배포 전, 클러스터 검증 시 |

> **판단 기준:** 노드 주소·로드밸런서·DNS·이미지 반입처럼 **인프라가 답을 바꾸는** 내용은
> `vagrant/` 또는 `kakao/`로, 나머지는 `common/`으로 간다. 두 계층이 한 문서에 섞여 있으면
> 쪼개지 않고 주된 소속으로 보내고 문서 상단에 범위 주석을 달았다 — 예: `common/architecture.md`의
> 인프라 절은 Vagrant 기준이다.

---

## common/ — 배포 대상 무관

| 문서 | 내용 |
|------|------|
| [architecture.md](./common/architecture.md) | 플랫폼 아키텍처 (인프라 절은 Vagrant 기준) |
| [kubeconfig.md](./common/kubeconfig.md) | kubectl 인증 (cert / token / OIDC) |
| [developer-onboarding.md](./common/developer-onboarding.md) | 개발자 온보딩 — 로그인, 배포, 레지스트리 |
| [developer-kaniko-builds.md](./common/developer-kaniko-builds.md) | 클러스터 내 Kaniko 빌드 |
| [database.md](./common/database.md) | CNPG PostgreSQL 운영 |
| [security.md](./common/security.md) | 보안 정책 |
| [compliance-hardening.md](./common/compliance-hardening.md) | 컴플라이언스 하드닝 |
| [gitops-push.md](./common/gitops-push.md) | GitOps 변경 반영 (Gitea 푸시) |
| [apisix-etcd-recovery.md](./common/apisix-etcd-recovery.md) | apisix-etcd 빈-prefix 교착 복구 |
| [etcd-operations.md](./common/etcd-operations.md) | kubeadm control-plane etcd 운영 기준 및 제안 절차 |
| [master-memory-pressure.md](./common/master-memory-pressure.md) | 마스터 메모리 압박 진단 |
| [rtk-token-compression-policy.md](./common/rtk-token-compression-policy.md) | RTK 토큰 압축 정책 |
| [troubleshooting.md](./common/troubleshooting.md) | 트러블슈팅 (3·4·5·13절은 Vagrant 전용) |
| [lessons-log.md](./common/lessons-log.md) | 사건 기록 — 양쪽 배포 대상 모두 |
| [test-strategy.md](./common/test-strategy.md) | T1-T7 테스트 계층 전략, 기존 체크 매핑, 리포트 export |
| [failure-injection-catalog.md](./common/failure-injection-catalog.md) | T5 chaos/장애주입 시나리오 카탈로그 |
| [unified-observability-contract.md](./common/unified-observability-contract.md) | 관측 신호·SLO·상관·보존·비용 귀속 계약 초안 |
| [agent-operational-rules.md](./common/agent-operational-rules.md) | 에이전트용 비자명 운영 규칙 (셸·GitOps·이미지·Kyverno·Istio·Keycloak·시크릿) |
| [storage-protection-policy.md](./common/storage-protection-policy.md) | 스토리지 보호·펜싱·일관성 그룹 정책 |
| [compatibility-matrix.md](./common/compatibility-matrix.md) | Kubernetes 및 OSS 호환성 행렬과 지원 정책 |
| [supply-chain-policy.md](./common/supply-chain-policy.md) | Narwhal / Narwhal-Portal 공급망 및 의존성 채택 정책 |
| [workload-security-profile.md](./common/workload-security-profile.md) | 워크로드 보안 프로파일 정책 |
| [admission-webhook-continuity.md](./common/admission-webhook-continuity.md) | Admission Webhook 연속성 정책 |
| [static-pod-manifest-protection.md](./common/static-pod-manifest-protection.md) | Control Plane Static Pod Manifest / HostPath 보호 정책 |
| [oidc-rbac-contract.md](./common/oidc-rbac-contract.md) | OIDC 그룹 클레임 ↔ Kubernetes RBAC ↔ ArgoCD ↔ Portal 인가 계약 |
| [app-sso-permission-matrix.md](./common/app-sso-permission-matrix.md) | 애플리케이션 SSO 및 권한 매트릭스 |
| [sso-accounts.md](./common/sso-accounts.md) | SSO 로그인 계정 확인 — 시드 계정, 비밀번호 위치, kcadm 상태 확인 |
| [apisix-admin-api.md](./common/apisix-admin-api.md) | APISIX Admin API 신뢰 경계와 접근 가이드 |
| [apisix-upgrade-sequence.md](./common/apisix-upgrade-sequence.md) | APISIX 업그레이드 순서와 롤백 기준 |
| [upgrade-orchestration.md](./common/upgrade-orchestration.md) | 업그레이드 오케스트레이션 모델 |
| [airgap-isolation-testing.md](./common/airgap-isolation-testing.md) | 격리(Isolation) 검증과 전환 — 로컬·카카오 공통 |
| [cluster-lifecycle-adr.md](./common/cluster-lifecycle-adr.md) | ADR: 클러스터 라이프사이클 관리 방식 |
| [multicluster-control-plane.md](./common/multicluster-control-plane.md) | 멀티클러스터 컨트롤 플레인 자격 증명 내보내기와 준비 상태 |
| [management-plane-ha-recovery-contract.md](./common/management-plane-ha-recovery-contract.md) | 멀티클러스터 관리 평면 HA 및 복구 계약 (제안) |
| [fleet-membership-security-lifecycle.md](./common/fleet-membership-security-lifecycle.md) | Fleet 클러스터 멤버십 보안 라이프사이클 (제안) |
| [versioned-management-api-contract.md](./common/versioned-management-api-contract.md) | 버전 관리되는 관리 API / CLI / Webhook / 이벤트 계약 |
| [identity-policy-gateway-request-flow.md](./common/identity-policy-gateway-request-flow.md) | 외부 egress identity → policy → gateway 요청 흐름 |
| [ai-llmops-extension-contract.md](./common/ai-llmops-extension-contract.md) | 선택적 AI/LLMOps Extension 경계 및 라이프사이클 계약 초안 |
| [accelerator-backend-conformance-matrix.md](./common/accelerator-backend-conformance-matrix.md) | 가속기 백엔드 capability / conformance 매트릭스 (제안) |
| [gpu-partitioning-policy.md](./common/gpu-partitioning-policy.md) | GPU 파티셔닝 및 consumable-capacity 정책 |

## vagrant/ — 로컬 (Vagrant VM)

| 문서 | 내용 |
|------|------|
| [dns-access.md](./vagrant/dns-access.md) | `*.local.narwhal.internal` DNS 및 서비스 접근 |
| [operations.md](./vagrant/operations.md) | 일상 운영 (`vagrant up/halt`, 백업, 스케일) |
| [disaster-recovery.md](./vagrant/disaster-recovery.md) | 장애 복구 런북 |
| [reboot-recovery.md](./vagrant/reboot-recovery.md) | 재부팅 후 복구 |
| [reboot-survivability.md](./vagrant/reboot-survivability.md) | 재부팅 내성 설계 |

## kakao/ — Kakao Cloud

| 문서 | 내용 |
|------|------|
| [cloud-deployment.md](./kakao/cloud-deployment.md) | 토폴로지, egress 프록시, airgap 레지스트리, provider-aware GitOps |
| [service-domains.md](./kakao/service-domains.md) | `*.kakao.narwhal.internal` 서비스별 도메인, SSO 방식, 접속·검증 |
| [clean-install-rca.md](./kakao/clean-install-rca.md) | 2026-08 클린 설치 RCA — 결함 19건의 원인 분류와 탐지 공백 |
| [nfs-xfs-migration-2026-10-16.md](./kakao/nfs-xfs-migration-2026-10-16.md) | NFS export ext4 → XFS(prjquota) 이전 기록 (2026-10-16) |

Terraform 사용법은 문서 디렉토리가 아니라 코드 옆에 있다:
[`csp/kakao-cloud/terraform/README.ko.md`](../csp/kakao-cloud/terraform/README.ko.md)
([EN](../csp/kakao-cloud/terraform/README.md)).

## runbooks/ — 라이브 클러스터 절차

모두 공유 클러스터를 건드리므로 실행 전 명시적 소유자 승인이 필요하다.

| 문서 | 내용 |
|------|------|
| [nfs-xfs-migration.md](./runbooks/nfs-xfs-migration.md) | Kakao NFS export를 ext4에서 전용 XFS prjquota 볼륨으로 이전 |
| [seaweedfs-s3-auth.md](./runbooks/seaweedfs-s3-auth.md) | SeaweedFS S3 인증을 클라이언트별 랜덤 키로 활성화 |
| [openbao-snapshot-backup.md](./runbooks/openbao-snapshot-backup.md) | OpenBao raft snapshot 암호화 백업 활성화·점검·키 교체 |

## adr/ — 아키텍처 결정 기록

| 문서 | 내용 |
|------|------|
| [0001-consolidate-narwhal-portal-into-monorepo.md](./adr/0001-consolidate-narwhal-portal-into-monorepo.md) ([KO](./adr/0001-consolidate-narwhal-portal-into-monorepo-ko.md)) | narwhal-portal을 narwhal 모노레포로 통합 |
| [0002-agent-execution-evidence-chain.md](./adr/0002-agent-execution-evidence-chain.md) ([KO](./adr/0002-agent-execution-evidence-chain-ko.md)) | 에이전트 실행 증적 체인 |

## evaluation/ — 단계별 검증

| 문서 | 내용 |
|------|------|
| [README.md](./evaluation/README.md) | 검증 로드맵 — 0단계(정적)부터 4단계(카오스)까지 |
| [00-static-validation.md](./evaluation/00-static-validation.md) | 0단계 정적 검증 가이드 (VM 불필요, CI와 동일 검사) |
| [smolvm-tool-sandbox-poc.md](./evaluation/smolvm-tool-sandbox-poc.md) | smolvm 도구 샌드박스 PoC |

---

## 자주 찾는 것

| 알고 싶은 것 | 어디 |
|--------------|------|
| 서비스 URL과 로그인 계정 | Vagrant → [dns-access.md](./vagrant/dns-access.md) · Kakao → [service-domains.md](./kakao/service-domains.md) |
| SSO 로그인 계정 확인 | [sso-accounts.md](./common/sso-accounts.md) |
| 비밀번호 실제 값 | `scripts/test/show-credentials.sh` (문서에 적지 않는다 — 클러스터가 생성한다) |
| kubectl 붙이기 | [kubeconfig.md](./common/kubeconfig.md) · Kakao는 `scripts/cloud/set-config-kakao.sh` |
| 변경을 클러스터에 반영 | [gitops-push.md](./common/gitops-push.md) — `kubectl apply`는 selfHeal이 되돌린다 |
| 과거에 뭐가 터졌나 | [lessons-log.md](./common/lessons-log.md) |

`archive/`는 더 이상 유지하지 않는 문서, `images/`는 문서용 스크린샷이다.
