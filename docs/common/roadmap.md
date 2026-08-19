# Narwhal Platform Roadmap — GitHub Project Specification

> **Source of truth for GitHub Project setup.** The Projects API is not available via the
> connected GitHub tools, so this file captures the complete specification for manual creation at
> `dasomel/narwhal` → Projects → New project.

## Project Name

`Narwhal Platform Roadmap`

---

## Views

| # | Name | Description |
|---|------|-------------|
| 1 | **Roadmap Board** | Status-based Kanban |
| 2 | **Execution Table** | Sorted by Wave / Priority / Area |
| 3 | **Architecture Tree** | Area → Issue relationship view |

---

## Custom Fields

| Field | Type | Values |
|-------|------|--------|
| Status | Single select | Backlog / Ready / In Progress / Blocked / Done |
| Priority | Single select | P0 / P1 / P2 |
| Area | Single select | Control Plane / Fleet / Platform / Developer / Security / Observability / AI / Ecosystem / Cross-cutting |
| Wave | Single select | W0 Architecture / W1 Control Plane / W2 Security / W3 Platform / W4 Developer / W5 Fleet-AI / W6 Ecosystem |
| Type | Single select | Epic / Feature / Spike / ADR / Integration |
| RFP Type | Single select | Public / Enterprise / Finance / AI / Reference |
| Repository | Single select | narwhal / nfs-quota-agent / kubemetal / kube-ready-box |
| Dependency | Text | `#<issue>` format |

---

## Operating Rules

- Issues are the source of truth for implementation detail; the Project is the source of truth for execution plan and status.
- #41 is the overall taxonomy/architecture anchor.
- #42 is the shared control-plane API foundation.
- When the same capability is spread across multiple issues, express the relationship with Area/Dependency — do not duplicate implementation.
- The Portal is an API consumer; it must not become the source of truth for business logic.

---

## Issue Mapping

### W0 Architecture

| Issue | Area | Priority | Type | RFP | Notes |
|-------|------|----------|------|-----|-------|
| #41 | Cross-cutting | P0 | Epic | Enterprise/Public | Taxonomy/architecture anchor |
| #42 | Control Plane | P1 | Feature | — | Depends: #41 |

### W1 Control Plane

| Issue | Area | Priority | Type | Notes |
|-------|------|----------|------|-------|
| #30 | Control Plane | P0 | Feature | Blueprint definition layer for #7 |
| #26 | Control Plane | P0 | Feature | |
| #19 | Control Plane | P0 | Feature | |
| #6 | Control Plane | P0 | Feature | Depends: #30, #26, #19 |
| #7 | Control Plane | P0 | Feature | Depends: #30, #26, #19, #42 |
| #8 | Control Plane | P0 | Feature | Depends: #7, #26 |
| #27 | Fleet | P1 | Feature | Depends: #6, #7, #14 |
| #14 | Control Plane | P1 | ADR/Spike | Depends: #41 |

### W2 Security

| Issue | Area | Priority | Type | Notes |
|-------|------|----------|------|-------|
| #13 | Security | P1 | Epic | |
| #33 | Security | P1 | Feature | Depends: #13, #19 |
| #35 | Security | P1 | Feature | Depends: #8, #13, #19, #9 |
| #36 | Security | P1 | Feature | Depends: #13 |
| #25 | Platform | P1 | Feature | Depends: #7, #19 |

### W3 Platform

| Issue | Area | Priority | Type | Notes |
|-------|------|----------|------|-------|
| #11 | Platform | P1 | Feature | |
| #18 | Platform | P1 | Feature | Depends: #11 |
| #10 | Platform | P1 | Feature | Depends: #11, #26 |
| #12 | Observability | P1 | Feature | Depends: #10, #24 |
| #24 | Observability | P1 | Epic | Depends: #13, #19 |
| #20 | Cross-cutting | P2 | Feature | Child capability of #24/#23; not a standalone roadmap pillar |
| #39 | Platform | P1 | Feature | Depends: #11, #14, #30, #37 |

### W4 Developer

| Issue | Area | Priority | Type | Notes |
|-------|------|----------|------|-------|
| #9 | Developer | P1 | Feature | Depends: #35, #42 |
| #37 | Developer | P1 | Feature | Depends: #9, #30, #42 |
| #38 | Developer | P1 | Feature | Depends: #24, #37 |
| #31 | Developer | P2 | Feature | Depends: #6, #13, #42 |

### W5 Fleet / AI

| Issue | Area | Priority | Type | Notes |
|-------|------|----------|------|-------|
| #40 | Fleet | P1 | Feature | Depends: #27, #28 |
| #28 | Fleet | P1 | Feature | Depends: #6, #13, #27 |
| #29 | Fleet | P1 | Feature | Depends: #27, #10, #40 |
| #15 | AI | P2 | ADR/Spike | Depends: #41; architecture scope control for #22/#34 |
| #22 | AI | P1 | Epic | Depends: #24, #15 |
| #34 | AI | P1 | Feature | Depends: #22 |

### W6 Ecosystem

| Issue | Area | Priority | Type | Notes |
|-------|------|----------|------|-------|
| #16 | Ecosystem | P2 | ADR/Spike | Depends: #26, #42 |
| #21 | Cross-cutting | P1 | Integration | Depends: #11, #19, #15 |
| #23 | Cross-cutting | P1 | Epic | Applies to all waves |

---

## Recommended Initial Status

| Status | Issues |
|--------|--------|
| **Ready** | #41, #42, #30, #26, #19 |
| **Backlog** | All others unless active implementation has begun |
| **In Progress** | Set only after confirming actual implementation state |

---

## Priority Summary

| Priority | Issues |
|----------|--------|
| **P0** | #6, #7, #8, #19, #26, #30, #41 |
| **P1** | #9, #10, #11, #12, #13, #14, #17, #18, #21, #22, #23, #24, #25, #27, #28, #29, #33, #34, #35, #36, #37, #38, #39, #40, #42 |
| **P2** | #15, #16, #20, #31, #32 |

---

## Notes

- **#20** Platform SLA/SLO is a capability under #24/#23, not a standalone roadmap pillar.
- **#15** GPU/AI defines architecture scope; #22 and #34 carry the implementation work.
- **#27 / #28 / #40 / #29** are intentionally separate: Fleet inventory → GitOps targeting → placement decision → scheduling/DR.
- **#30** Blueprint is the definition layer for #7 Lifecycle.
- **#39** Infrastructure Claims is a later-stage self-service abstraction and must not block core cluster lifecycle.
- `k-paas` and `dasomel/harbor` fork are explicitly **excluded** from this roadmap.

---

## Manual Project Creation Steps

1. Go to `https://github.com/dasomel/narwhal` → **Projects** → **New project**
2. Name the project: `Narwhal Platform Roadmap`
3. Add the custom fields listed above
4. Add repository issues #5 – #42 (exclude any non-roadmap issues as appropriate)
5. Apply the Wave / Priority / Area / Type / Status mapping from this file
6. Create the three views described at the top of this document
