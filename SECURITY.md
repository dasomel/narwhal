# Security Policy

This file covers **reporting a vulnerability in Narwhal itself**. For how a deployed cluster is
hardened — pod security, kernel settings, SSH, mTLS, the local-dev exceptions — see
[`docs/common/security.md`](docs/common/security.md).

## Reporting a vulnerability

**Use [GitHub private vulnerability reporting](https://github.com/dasomel/narwhal/security/advisories/new).**
It is enabled on this repository and keeps the report private until a fix is published.

Please do **not** open a public issue for a security problem. Public issues are the right place for
everything else, including hardening suggestions that do not describe an exploitable weakness.

A useful report contains:

- which component and version (`VERSIONS.md` has the exact pins)
- the deployment target — Vagrant, Kakao Cloud, or air-gapped — since network exposure differs
  substantially between them
- what an attacker can actually reach, and from where: this platform's components are mostly
  cluster-internal behind an SSO gateway, so reachability is usually the deciding factor
- reproduction steps, ideally against a clean install

You will get an acknowledgement within a week. This is a single-maintainer project, so please read
the honest expectations below before relying on a particular response time.

## Supported versions

| Version | Status |
|---|---|
| 1.2.x | Supported — fixes land here |
| < 1.2 | Not supported; upgrade first |

Only the latest minor is supported. Narwhal is installed by provisioning a cluster rather than by
upgrading a package, so "patched" in practice means a clean install or a GitOps sync from a fixed
revision, not a version bump on a running node.

## Scope

**In scope** — defects in what this repository controls:

- provisioning and operations scripts under `scripts/`
- GitOps manifests and Helm values under `gitops/`
- credential handling: generation, storage, distribution to nodes and workloads
- the air-gapped bundle and its delivery path (registry mirrors, APT bundle, chart registry)
- default configuration that is insecure as shipped
- CI workflows under `.github/`

**Out of scope** — report these upstream, though telling us is still welcome so the pin can move:

- vulnerabilities in the integrated components themselves (Kubernetes, Istio, Keycloak, Harbor,
  ArgoCD and the rest). `VERSIONS.md` lists what is pinned and why.
- issues that require an attacker to already have cluster-admin, or root on a node

**Known and accepted**, so no report is needed:

- the Vagrant profile ships development conveniences that are documented as unsuitable for
  production in [`docs/common/security.md`](docs/common/security.md#local-dev-exceptions)
- certificates are issued by a private CA that clients must trust explicitly; browser warnings
  before that trust is installed are expected behaviour, not a defect

## What this project already does

Stated so a reporter can tell a gap from a deliberate choice:

- **Secret scanning and push protection** are enabled on this repository
- **No credential is committed.** Passwords, tokens and keys are generated during provisioning and
  read back with `scripts/test/show-credentials.sh`; the CA private key stays in a cluster Secret
  and is never distributed to nodes
- **The portal image ships an SBOM and SLSA provenance.** Verify with:
  ```bash
  docker buildx imagetools inspect --raw ghcr.io/dasomel/narwhal-portal:1.0.17
  ```
  The `unknown/unknown` entries are the attestation manifests, carrying an SPDX document and a
  `slsa.dev/provenance/v1` predicate per architecture.
- **Every fix is written down.** [`docs/common/lessons-log.md`](docs/common/lessons-log.md) records
  each incident with the discriminator that separates it from causes it resembles — including
  security-relevant ones, such as the 2026-08 finding that a security group silently dropped
  metrics traffic and presented as a mesh problem.

## Honest limitations

- **Single maintainer.** There is no on-call rotation and no guaranteed fix window.
- **No CodeQL.** This repository is shell and YAML; CodeQL does not analyse Bash, so a code-scanning
  workflow here would report on almost nothing while implying coverage. ShellCheck runs on every
  push instead, and workflow-level analysis is on the roadmap.
- **No signed releases yet.** Release artifacts are not currently signed.

If any of these matters for your use, please say so in an issue — knowing who depends on what is
what moves them up the list.
