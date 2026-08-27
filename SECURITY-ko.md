# 보안 정책 (Security Policy)

[English](SECURITY.md) | 한국어

본 문서는 **Narwhal 자체의 보안 취약점 보고 절차**를 다룹니다. 배포된 클러스터의 보안 강화 설정(파드 보안, 커널 파라미터, SSH, mTLS 등)은 [`docs/common/security-ko.md`](docs/common/security-ko.md)를 참고하십시오.

## 취약점 보고 절차 (Reporting a Vulnerability)

**[GitHub Private Vulnerability Reporting](https://github.com/dasomel/narwhal/security/advisories/new)을 사용해 주십시오.**
취약점 패치가 릴리스될 때까지 비공개로 안전하게 처리됩니다. 보안 문제를 공개 이슈로 등록하지 마십시오.

유효한 보안 보고서 항목:
- 대상 컴포넌트 및 버전 (`VERSIONS.md`에 정의된 핀)
- 배포 환경 (Vagrant, Kakao Cloud, 또는 Air-gapped 오프라인 환경)
- 공격자가 실제로 도달 가능한 네트워크 경로 및 권한
- 재현 단계 (Clean Install 환경 권장)

단일 유지관리자 프로젝트로 1주일 이내 접수 확인 및 대응 계획을 안내합니다.

## 지원 버전 (Supported Versions)

| 버전 | 상태 |
|---|---|
| 1.2.x | 지원 대상 (Fix 릴리스 적용) |
| < 1.2 | 지원 종료 (최신 버전 업그레이드 권장) |

참조: [OpenForge Security Standard](https://github.com/dasomel/openforge/blob/main/docs/security.md)
