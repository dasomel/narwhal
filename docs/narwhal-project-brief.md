# Narwhal — Project Brief

> Application artifact for the OpenAI Codex for Open Source / Codex Open Source Fund forms.
> Deliberately NOT committed to the repository: it addresses one funder, dates quickly, and reads
> oddly to an ordinary repository visitor. The durable half of this content already lives in
> README.md; this file is the part that belongs in a form.
>
> Every figure below is measured, not estimated. Figures the maintainer could not verify at the
> time of writing are marked and can be filled in from a live cluster run.

**Repository:** https://github.com/dasomel/narwhal
**License:** Apache-2.0
**Maintainer:** dasomel (single maintainer)
**Activity:** 483 commits since 2026-02-08 · 4 tagged releases · latest v1.2.0

---

## What it is

Narwhal is an open-source, reproducible Kubernetes Internal Developer Platform that integrates
**35 cloud-native open-source components** — GitOps, IAM/SSO, service mesh, observability, registry,
storage, backup, security policy, API gateway and a management portal — into one platform that
installs, verifies and operates as a single unit.

It targets local development environments (Vagrant/ARM64), cloud infrastructure (Kakao Cloud/AMD64)
and **fully air-gapped networks**, from the same source.

**Narwhal is not a Kubernetes installer. It is an integration and operations layer across the
cloud-native ecosystem.**

## The problem it addresses

The difficult part of a Kubernetes platform is not installing Kubernetes. It is making
independently developed projects work together, and keeping them working:

- DNS and certificate dependencies
- identity and SSO wiring
- service-mesh interactions with plain-HTTP and cookie-based flows
- GitOps reconciliation and drift
- component startup ordering
- version compatibility across 35 moving projects
- offline software supply chains
- upgrade and recovery procedures

These failures occur **at the boundaries between projects**, not inside any one of them, which is
exactly where each project's own test suite cannot see them.

## Evidence of active maintenance

| | |
|---|---|
| Commits | 483 since 2026-02-08 (~6 months, single maintainer) |
| Releases | 4 tagged, latest v1.2.0, with a maintained CHANGELOG |
| CI | 3 workflows; a 51-check regression suite runs on every push |
| Documented incidents | 263 dated rows in `docs/common/lessons-log.md` |
| Integrated components | 35 GitOps-managed applications |
| Air-gapped bundle | 105 container images, 27 Helm charts, 149 OS packages, binaries and remote manifests, per architecture |
| Deployment targets | Vagrant (ARM64), Kakao Cloud (AMD64), fully isolated networks |
| Sibling projects | narwhal-portal (management UI), kube-ready-box (Packer base box), nfs-quota-agent |

*Not included above because they are runtime totals that require a live cluster to confirm: the
cluster verification script and the SSO test script each report their own check counts on
completion (`verify-cluster.sh`, `test-sso.sh`). Run them and quote the printed totals if the form
has room.*

### The maintenance mechanism, not just the numbers

The strongest evidence here is not the commit count. It is that integration failures are converted
into permanent tests:

```
Incident  →  Lesson  →  Discriminator  →  Regression test
```

Each of the 263 rows records the actual cause, **how to distinguish it from the causes it
resembles**, and which plausible-looking remedy is wrong. Checks in the regression suite map back
to specific dated incidents, so a solved integration bug cannot silently return at the next
upgrade.

A worked example from the log: Prometheus scraped node metrics on only one of six nodes. The first
diagnosis — service-mesh interception — was supported by real evidence in the mesh proxy logs, was
deployed, and did not fix it. The actual cause was a cloud security group that allowed the
pre-1.17 metrics ports but not the current ones, and which *drops* rather than rejects, so the
block presented as a timeout identical to the mesh symptom. The row records the discriminator that
separates the two, because both theories explain "only the local node works".

## Why the project matters to the ecosystem

Narwhal maintains no fork and adds no proprietary component. It contributes the layer the
individual projects do not own: version compatibility, an offline distribution of the whole stack,
end-to-end identity, upgrade and recovery procedure, and verification of the boundaries.

Its audience is platform engineers, SRE teams, private and hybrid cloud operators, and regulated or
air-gapped environments where managed Kubernetes is unavailable or unsuitable — the settings where
"just use the managed offering" is not an answer and the integration burden falls entirely on the
operator.

**Narwhal does not replace the cloud-native ecosystem. It makes the ecosystem work together.**

## Why Codex

The dominant maintenance cost is continuous revalidation. Every Kubernetes release, chart bump or
security update can affect configuration compatibility, API behaviour, authentication, networking,
security policy, storage, GitOps reconciliation, air-gapped packaging and regression behaviour —
across 35 components, carried by one maintainer.

Intended use of credits:

**Upgrade analysis** — impact analysis for Kubernetes and component upgrades; configuration and
compatibility review; changelog and release-note analysis.

**Quality** — regression test generation from the incident log; integration-test analysis; manifest
and configuration review.

**Maintenance** — issue triage; documentation updates; release validation; GitOps hygiene.

**Security** — configuration security review, dependency analysis, security regression detection.

```
Component / Kubernetes change
            ↓
      Codex analysis
            ↓
 Compatibility & security review
            ↓
      Automated tests
            ↓
      Maintainer review
            ↓
        Release
```

The objective is not to replace the maintainer. It is to reduce repetitive analysis and validation
so that limited maintainer capacity goes to architecture, reliability and community support.

## Expected open-source impact

Support would raise the frequency and depth of compatibility testing, dependency maintenance,
security review, issue triage, documentation and release validation. The resulting tests,
automation, documentation and integration fixes stay open source and are directly reusable by other
platform engineers and cloud-native projects — particularly the air-gapped packaging work, which
few open-source IDP projects attempt and which is generally re-solved from scratch by each team
that needs it.

## Honest assessment of weaknesses

State these rather than let a reviewer find them:

- **External adoption is currently minimal** — few stars, forks or outside issues. The case rests
  on ecosystem importance and maintenance rigour, which the programme explicitly allows for.
- **Single maintainer.** This is also the argument for the request: the integration surface is
  large relative to the maintainer capacity.
- **No external contributors yet**, so there is no PR-review history to point to. The contribution
  conventions are documented in the README and `CLAUDE.md`.

## Next steps that would strengthen a future application

Not README work — external usage evidence:

1. GitHub topics and repository description (discoverability; currently unset)
2. A published release artifact or container image with download/pull counts
3. A written install case study from a third party
4. External issues or PRs, however small
5. A short demo recording of a clean install and the verification run
