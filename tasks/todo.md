# Narwhal TODO

## 완료
- [x] 사용자 4명 체계 (admin/dev/view/guest)
- [x] 그룹명 단수 변경 + guest 그룹 추가
- [x] 네임스페이스 기능별 통합 (platform-system, iam, devtools, storage, dev)
- [x] Gateway-Level SSO (Traefik ForwardAuth + OAuth2-Proxy)
- [x] 앱별 SSO 리다이렉트 URL 설정 (appRedirects 맵)
- [x] emailVerified=true 설정 (Keycloak 사용자 + OAuth2-Proxy 방어)
- [x] 프로젝트 전체 점검 (6개 팀 병렬)
- [x] 점검 결과 수정 (HIGH 6건 + MEDIUM 12건)
- [x] CLAUDE.md 워크플로우 지침 추가

## 미완료
- [ ] SSO 통합 테스트 실행 (test-sso.sh --section=acl)
- [ ] Gitea 레포에 전체 변경사항 push (traefik-routes.yaml 최신본)
- [ ] 프로덕션 전환 시 시크릿 외부화 (OpenBao 연동)
