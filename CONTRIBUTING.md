# Contributing to Narwhal

Narwhal provisions a full Kubernetes IDP stack (GitOps, SSO, monitoring, storage, backup) onto
Vagrant VMs, and separately onto Kakao Cloud. This document is the human-facing entrypoint;
`CLAUDE.md` carries the same rules in more detail for AI coding agents working in this repo — both
describe the same conventions, so a change to one that generalizes past a single incident should be
reflected in the other.

## Before you start

- Read `README.md` for the project overview and `CLAUDE.md`'s "Recurring Rules" section for
  conventions that aren't obvious from the code (image/registry policy, GitOps push path, shell
  pitfalls, Kyverno/Keycloak gotchas).
- For anything that touches cluster architecture, GitOps app structure, or a version upgrade, open
  an issue first — these are exactly the changes `CLAUDE.md` calls out for Plan Mode.

## Reporting a security issue

**Do not open a public issue.** Use
[GitHub private vulnerability reporting](https://github.com/dasomel/narwhal/security/advisories/new)
as documented in `SECURITY.md`.

## Development workflow

- Standard `vagrant up/halt/destroy/ssh`. Phase 2 (platform apps) does not run automatically from a
  bare `vagrant up` — see `CLAUDE.md` → Development Commands.
- `make lint` (shellcheck + yamllint), `make validate` (Vagrantfile syntax + YAML parse), and
  `./scripts/test/regression-check-kakao.sh --static` reproduce the checks CI runs on every PR —
  run them locally before pushing. `make test` prints the commands for the runtime half, which
  needs a live cluster.
- Edit YAML with `yq`, never `sed` — see `CLAUDE.md`.

## Code conventions

- Every shell script keeps `set -euo pipefail`; 2-space indentation (shell and YAML both — CI
  blocks the shell case).
- `ENV_VAR` for environment names, `local_var` for locals.
- Never hardcode a password, token, or kubeconfig credential into a script or manifest — nothing in
  CI catches one.
- Bitnami images and charts are banned. Registry preference: `ghcr.io` > `registry.k8s.io` >
  `quay.io` > `docker.io`.

## Recording incidents

If your change fixes a bug that cost real debugging time, add a row to
[`docs/common/lessons-log.md`](docs/common/lessons-log.md) in the section matching the cause.
Grep for the symptom first — sharpen a near-match row instead of adding a duplicate — and record the
**discriminator** (how to tell this cause apart from ones it resembles), not just the conclusion.
Mistakes made while fixing the original bug count too.

## Commit / PR conventions

- Conventional Commits, scoped to the files the change actually touched.
- Stage specific paths, not `git add -A` — concurrent work in the same tree is common, and a broad
  add can sweep in someone else's uncommitted edit.
- CI (`.github/workflows/lint.yml`) runs shellcheck, the 2-space indent check, the static
  clean-install regression suite, YAML/Vagrantfile/kubeconform validation, and the Mistakes Log
  format check on every PR — all of it must pass before merge.

## License

Apache-2.0 — see `LICENSE`. By contributing, you agree your contribution is licensed under the same
terms.
