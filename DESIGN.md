# DESIGN.md

English | [한국어](DESIGN-ko.md)

## Product archetype

`archetype: Developer Tool`

Narwhal is an internal developer platform (IDP) blueprint and cluster automation suite integrating Kubernetes, Istio, Keycloak SSO, and Harbor.

## Product personality

- **Density:** High (compact CLI output and structured operational logging)
- **Visual weight:** Terminal-native ANSI palette with clear severity levels (INFO/WARN/FATAL)
- **Accent:** Sea blue (`#0284c7`) and infrastructure status indicators

## Token mapping

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
