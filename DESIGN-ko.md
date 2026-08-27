# DESIGN-ko.md

[English](DESIGN.md) | 한국어

## 제품 아키타입 (Product archetype)

`archetype: Developer Tool`

Narwhal은 Kubernetes, Istio, Keycloak SSO, Harbor를 통합하는 내부 개발자 플랫폼(IDP) 청사진이자 클러스터 자동화 스위트입니다.

## 제품 성격 (Personality)

- **밀도 (Density):** 높음 (High — 터미널 CLI 출력 및 구조화된 운영 로그)
- **시각적 비중:** ANSI 컬러 기반의 심각도(INFO/WARN/FATAL) 구분이 명확한 CLI 테마
- **강조 색상:** 시 블루 (`#0284c7`) 및 인프라 상태 지표

## 시맨틱 토큰 매핑 (Token mapping)

```yaml
tokens:
  bgCanvas: var(--of-color-bg-canvas, #020617)
  bgSurface: var(--of-color-bg-surface, #0f172a)
  bgSurfaceRaised: var(--of-color-bg-surface-raised, #1e293b)
  textPrimary: var(--of-color-text-primary, #f8fafc)
  textSecondary: var(--of-color-text-secondary, #94a3b8)
  textMuted: var(--of-color-text-muted, #64748b)
  borderDefault: var(--of-color-border-default, #334155)
  accentPrimary: var(--of-color-accent-primary, #38bdf8)
  danger: var(--of-color-status-danger, #ef4444)
  success: var(--of-color-status-success, #22c55e)
```
