# Improvement Roadmap Decisions

## Secret Naming Convention (Phase 1)

모든 팀이 동일한 Secret 이름/네임스페이스를 사용해야 함.

| Secret Name | Namespace | Keys | 생성 위치 |
|---|---|---|---|
| `narwhal-db-credentials` | database | `keycloak-password`, `harbor-password`, `gitea-password` | 07-cnpg.sh |
| `keycloak-admin` | iam | (Operator 자동 생성) | 11-keycloak.sh |
| `oidc-client-secrets` | iam | `argocd`, `grafana`, `gitea`, `harbor`, `headlamp`, `oauth2-proxy` | 11-keycloak.sh |
| `oauth2-proxy-secrets` | iam | `cookie-secret`, `client-secret` | 11-keycloak.sh |
| `harbor-secrets` | devtools | `admin-password`, `oidc-secret` | 08-platform-apps.sh |
| `grafana-secrets` | monitoring | `admin-password`, `oidc-secret` | 08-platform-apps.sh |
| `gitea-admin` | devtools | `admin-password` | 12-gitea.sh |
| `velero-s3-credentials` | storage | `cloud` (INI format) | 08-platform-apps.sh |

### 패턴

```bash
# 스크립트에서 Secret 생성
generate_password() { openssl rand -base64 16 | tr -d '=/+' | head -c 24; }

PASSWORD=$(generate_password)
kubectl create secret generic <name> \
  --from-literal=<key>="${PASSWORD}" \
  -n <namespace> --dry-run=client -o yaml | kubectl apply -f -
```

```yaml
# Helm values에서 existingSecret 참조
existingSecret: "<secret-name>"
existingSecretKey: "<key>"
```

## Phase Execution Plan

- Phase 1: 시크릿 외부화 (4 teams)
- Phase 2: CI/CD 파이프라인 (3 teams)
- Phase 3: 모니터링 알림 (3 teams)
- Phase 4: 백업/복구 검증 (3 teams)
- Phase 5: 보안 강화 (4 teams)
- Phase 6: 개발자 경험 (3 teams)
