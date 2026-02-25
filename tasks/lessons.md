# Lessons Learned

> 사용자에게 수정을 받거나 실수를 발견할 때마다 여기에 기록.
> 세션 시작 시 반드시 검토.

## 인프라/K8s

### Traefik ForwardAuth 패턴
- Errors 미들웨어는 원본 HTTP 상태코드(401)를 보존함 → 브라우저가 자동 리다이렉트 안 함
- 해결: nginx + JS 페이지로 `window.location.href` 강제 리다이렉트
- ExternalName 서비스 사용 시 `allowExternalNameServices: true` 필수
- 크로스 네임스페이스: 미들웨어는 HTTPRoute와 같은 네임스페이스에 있어야 함

### PKCE 충돌 방지
- 여러 보호 앱이 동시 리다이렉트 → OAuth2 PKCE state 충돌
- 해결: sessionStorage 기반 5초 디바운스

### Keycloak emailVerified
- OAuth2-Proxy는 기본적으로 emailVerified=true 요구
- `kcadm.sh create users`에 항상 `-s emailVerified=true` 추가

### ArgoCD selfHeal
- kubectl로 직접 수정해도 ArgoCD가 Gitea 상태로 되돌림
- 항상 Gitea 레포에 push해야 영속됨
- Gitea 서비스가 headless → Pod IP 직접 사용

### MetalLB vs Cilium LB-IPAM
- `io.cilium/lb-ipam-ips` 어노테이션은 MetalLB에서 무시됨
- MetalLB: `metallb.universe.tf/loadBalancerIPs` 사용

## 워크플로우

### 사용자가 이미 알려준 정보를 다시 찾지 말 것
- 2026-02-26: 사용자가 ArgoCD 리다이렉트 URL을 알려줬는데 다시 검증하려 함
- 교훈: 사용자가 명시적으로 제공한 값은 바로 적용. 재확인 불필요.

### 점검 시 팀 병렬 투입
- 6개 영역(Shell, YAML, 문서, 보안, SSO, 테스트)을 병렬로 점검하면 ~5분
- 결과 종합 후 수정도 4개 팀 병렬 투입 가능
