import type { KisaControl } from "@/types/kisa"

export const KISA_CATALOG: Omit<KisaControl, "status" | "live" | "detail" | "detailLocalized" | "reasonKey">[] = [
  {
    id: "KISA-CP-01",
    domain: "컨트롤플레인/API",
    title: "API 서버 감사 로그 활성화",
    severity: "Critical",
    standardRefs: ["CIS 1.2.x", "NSA/CISA §7", "ISMS-P 2.11"],
    evidence:
      "kube-apiserver 파드의 --audit-log-path/--audit-policy-file 인자를 실시간 점검. Falco 0.45.1은 syscall 소스만 사용하며 감사 webhook(k8saudit) 백엔드는 구성하지 않음.",
    remediation: "감사 로그의 Loki 출하 연계(현재 파일 로컬 보관만)와 감사 정책 커버리지 점검.",
  },
  {
    id: "KISA-ETCD-01",
    domain: "etcd",
    title: "etcd 저장 데이터 암호화",
    severity: "Critical",
    standardRefs: ["CIS 2.x", "ISMS-P 2.7.1"],
    evidence:
      "kube-apiserver 파드의 --encryption-provider-config 인자를 실시간 점검 (EncryptionConfiguration aescbc 프로바이더).",
    remediation: "암호화 키 주기 로테이션 절차 수립 및 기존 시크릿 재암호화(kubectl replace) 주기화.",
  },
  {
    id: "KISA-SEC-01",
    domain: "시크릿",
    title: "OpenBao unseal 키 격리",
    severity: "Critical",
    standardRefs: ["NIST SP 800-190 §4.5", "ISMS-P 2.7"],
    evidence:
      "CronJob storage/openbao-auto-unseal 사양(전용 SA·automountServiceAccountToken=false·BAO_SKIP_VERIFY 미사용)과 storage/openbao-init Secret 메타데이터(velero.io/exclude-from-backup·last-applied-configuration 미존재)를 실시간 점검. OpenBao 실제 seal 구성(HCL/Secret)은 읽기 전용 API로 검증할 수 없어 pass를 내지 않고 위생 확인 시 warn, CronJob 부재/중지 또는 조회 불가 시 manual(미평가)로 표시.",
    remediation:
      "외부 KMS/HSM auto-unseal 적용 시 OpenBao 설정에서 직접 확인하고, 온프렘 유지 시 CronJob 최소권한·Secret 백업 제외 라벨·last-applied 주석 제거 상태를 유지.",
  },
  {
    id: "KISA-ETCD-02",
    domain: "etcd",
    title: "APISIX etcd 접근 보호",
    severity: "High",
    standardRefs: ["CIS", "NSA/CISA"],
    evidence:
      "Deployment platform-system/apisix-etcd 인자(--listen-client-urls TLS·--client-cert-auth)와 platform-system NetworkPolicy의 APISIX 게이트웨이 인바운드 제한 여부를 실시간 점검.",
    remediation:
      "APISIX etcd 인바운드를 APISIX 게이트웨이로만 제한하는 NetworkPolicy 유지 및 클라이언트 TLS·mTLS 인증(--client-cert-auth) 전환.",
  },
  {
    id: "KISA-IMG-01",
    domain: "이미지/공급망",
    title: "레지스트리 TLS 인증서 검증",
    severity: "High",
    standardRefs: ["NIST SP 800-190 §3.1", "KISA Docker #26"],
    evidence:
      "노드 파일시스템(/etc/containerd/certs.d/harbor.*/hosts.toml)은 Kubernetes API로 직접 읽을 수 없고 #765(08-6b-harbor-containerd-tls-node.sh)가 클러스터 상태 객체를 게시하지 않아 수동 확인 대상입니다. 노드에서 grep -E 'ca =|skip_verify' /etc/containerd/certs.d/harbor.*/hosts.toml 또는 harbor_ca_check_local로 확인하세요.",
    remediation: "각 노드에서 Harbor containerd CA 신뢰(ca = ...) 및 skip_verify 미존재를 정기 검증.",
  },
  {
    id: "KISA-RBAC-01",
    domain: "RBAC/인증",
    title: "최소 권한·cluster-admin 통제",
    severity: "High",
    standardRefs: ["CIS 5.1", "NSA/CISA", "ISMS-P 2.6"],
    evidence:
      "검토된 정확 수용 규칙(compliance-acceptance-rules.json + 부트스트랩 메타데이터 검증) 외 cluster-admin 바인딩 존재 여부를 실시간 점검. headlamp(view), velero-ui(velero-ui-readonly), velero-server(velero-server-least-privilege), platform-admin은 최소권한으로 전환됨.",
    remediation:
      "신규 cluster-admin 직접 부여를 금지하고 최소권한 Role/ClusterRole 유지.",
  },
  {
    id: "KISA-TLS-01",
    domain: "TLS/mesh",
    title: "서비스 간 mTLS 적용 범위",
    severity: "High",
    standardRefs: ["NSA/CISA", "ISMS-P 2.7.1"],
    evidence:
      "security.istio.io PeerAuthentication 전체를 실시간 조회해 메시 전역 기본 모드(istio-system/default)와 모든 PERMISSIVE/DISABLE 예외(네임스페이스·셀렉터·포트 단위)를 점검.",
    remediation:
      "PERMISSIVE/DISABLE 예외별로 비-메시 트래픽 경로를 재확인해 최소 범위(워크로드·포트 단위)로 축소하고, 예외 구간은 NetworkPolicy로 보완.",
  },
  {
    id: "KISA-POD-01",
    domain: "Pod 보안",
    title: "Pod Security Admission 적용",
    severity: "Medium",
    standardRefs: ["CIS 5.2", "Pod Security Standards"],
    evidence:
      "대상 네임스페이스(kube-node-lease·kube-public 제외)의 pod-security.kubernetes.io enforce/audit/warn 라벨 적용 비율을 실시간 점검.",
    remediation: "audit 위반 로그 관찰 후 단계적 enforce 승격.",
  },
  {
    id: "KISA-OBS-01",
    domain: "관측",
    title: "보안 이벤트 실시간 통지",
    severity: "Medium",
    standardRefs: ["ISMS-P 2.11.1", "KISA 운영관리"],
    evidence:
      "AlertmanagerConfig(monitoring/narwhal-notification-channels)의 외부 알림 수신자(Slack/Email/Webhook 등) 라우팅 여부와 security-system의 Falco·falcosidekick 파드 Ready 상태를 실시간 점검.",
    remediation:
      "포털 설정에서 외부 알림 수신자(Slack/Email/Webhook)를 등록하고 Falco·falcosidekick 기동 상태를 유지.",
  },
  {
    id: "KISA-NET-01",
    domain: "네트워크",
    title: "Ingress default-deny",
    severity: "Medium",
    standardRefs: ["CIS 5.3", "NSA/CISA §4", "KISA 망분리"],
    evidence:
      "대상 네임스페이스(iam·devtools·monitoring·storage·database)의 ingress default-deny NetworkPolicy(podSelector: {}) 적용 여부를 실시간 점검. egress는 현재 전체 허용(egress:[{}]) 상태.",
    remediation: "미적용 네임스페이스에 ingress default-deny + 명시적 허용 정책을 단계 적용하고, egress 최소 허용목록 전환 검토.",
  },
  {
    id: "KISA-IMG-02",
    domain: "이미지/공급망",
    title: "이미지 태그·서명 관리",
    severity: "Low",
    standardRefs: ["NSA/CISA", "SLSA"],
    evidence:
      "실행 중 파드의 :latest/미지정 태그 여부, Kyverno ClusterPolicy(disallow-latest-tag·verify-image-signatures)의 기준선(kisa-policy-baseline.json) 정규화 SHA-256 일치·Ready=True·오버라이드 부재, 및 PolicyReport의 현재 유효 위반 건수(삭제 리소스·0-replica 구 ReplicaSet·구 정책 범위 외 잔재 제외)를 실시간 점검.",
    remediation: "disallow-latest-tag Enforce 유지, verify-image-signatures Enforce 전환 여부(에어갭 서명 미러링 요건 고려) 검토.",
  },
  {
    id: "KISA-IMG-03",
    domain: "이미지/공급망",
    title: "이미지 취약점 점검·조치",
    severity: "High",
    standardRefs: ["NIST SP 800-190", "KISA 패치관리"],
    evidence: "Trivy Operator로 이미지 취약점 스캔 수행.",
    remediation: "Critical/High CVE 신속 패치.",
  },
  {
    id: "KISA-ADM-01",
    domain: "Admission/정책",
    title: "정책 강제(Policy-as-Code)",
    severity: "Medium",
    standardRefs: ["CIS", "KISA"],
    evidence: "Kyverno 정책 가동 중.",
    remediation: "fail-closed 전환·제외 ns 축소·Audit→Enforce.",
  },
  {
    id: "KISA-TLS-02",
    domain: "TLS/mesh",
    title: "인증서 수명·상태 관리",
    severity: "Medium",
    standardRefs: ["ISMS-P 2.7.1"],
    evidence: "cert-manager 인증서 발급/갱신.",
    remediation:
      "만료 임박 인증서 갱신, 10년 CA 수명 단축, 와일드카드 축소.",
  },
  {
    id: "KISA-RBAC-02",
    domain: "RBAC/인증",
    title: "RBAC 위험 바인딩 점검",
    severity: "Medium",
    standardRefs: ["CIS 5.1"],
    evidence:
      "비-빌트인 RBAC 바인딩을 단일 rule 기준(apiGroups·resources·verbs)으로 실시간 평가하고 compliance-acceptance-rules.json 정확 수용 규칙과 일관되게 집계.",
    remediation: "조치가능한 wildcard·권한상승(bind/escalate/impersonate) 바인딩 제거.",
  },
  {
    id: "KISA-CFG-01",
    domain: "Admission/정책",
    title: "컴플라이언스 프레임워크 통과율",
    severity: "Medium",
    standardRefs: ["CIS", "NSA/CISA"],
    evidence: "Trivy ClusterComplianceReport(CIS/NSA) 및 Config/RBAC/Infra 감사 리포트 실시간 평가.",
    remediation: "미통과 컨트롤 조치.",
  },
]
