# ADR-0001: Consolidate narwhal-portal into the narwhal monorepo

- Status: Proposed (becomes Accepted when the import PR merges)
- Date: 2026-10-15
- Supersedes: —
- Superseded by: —
- Korean: [0001-consolidate-narwhal-portal-into-monorepo-ko.md](0001-consolidate-narwhal-portal-into-monorepo-ko.md)

Decision IDs: D-MONO-1 (history-preserving import), D-MONO-2 (seam checks read the
in-repo tree), D-MONO-3 (CI), D-MONO-4 (release and image identity), D-MONO-5 (this ADR).
The import and its seam/docs wiring are in the `feat/import-portal-seam` branch.

## Context

`narwhal` (Kubernetes IDP cluster: Bash/Helm/GitOps) and `narwhal-portal` (Next.js 16 /
React 19 management UI) lived in two repositories coordinated from an umbrella workspace
that is not itself a Git repository.

- **The portal is Narwhal's UI, not a standalone product.** It reads 53 of the 57
  environment variables narwhal injects into its Deployment, its README defines it as
  Narwhal's UI, and the `.narwhal.internal` domain is hardcoded.
- **The work is shared.** 40 of the portal's 64 issues derive from narwhal issues, and 15
  incidents crossed the repository boundary.
- **The seam was held together by a pin, and the pin went stale.** narwhal pins
  `ghcr.io/dasomel/narwhal-portal:1.0.19`, while portal `main` already requires Valkey TLS
  and ACL environment that `scripts/cluster/13-2-narwhal-portal-bindings.sh` does not inject
  yet (the Valkey change is pending in PR #343). A portal bump and the matching cluster
  change cannot land atomically. This is the same stale-pin failure beluga hit with a pin
  118 commits behind (beluga ADR-0005).
- **Seam checks were optional.** R117, R55, R216 and R211 compared against a sibling
  checkout (`../narwhal-portal`) or a pinned tag tree, and silently skipped without it
  (lessons-log 2026-09-07: R117 reported PASS without comparing anything).

## Decision

1. **D-MONO-1:** import `narwhal-portal` with full history into `portal/`
   (`git-filter-repo` path rewrite of 415 commits). PR references in commit messages are
   qualified as `dasomel/narwhal-portal#N` so they do not resolve to narwhal's numbers.
   Tags are namespaced `portal/vX.Y.Z`. The imported tree was verified identical to
   `portal@39c0079`: at import time the `portal/` tree of the rewritten head `e8e8229d`
   (second parent of the import merge `498a3ae`; the first parent is main's `0935ac1`) equals
   the original tree. Both commands print `8a50ade5f710b14732b1ce12f05ebddd16d67862`:

   ```bash
   git rev-parse e8e8229d:portal '39c0079^{tree}'
   ```

   Later commits on main change `portal/` on purpose (`portal/.github` removed, seam fixes), so
   `origin/main:portal` no longer equals that hash; the check applies to the import only.
2. **D-MONO-2:** seam checks read the in-repo `portal/` working tree: no pinned tag, no
   sibling checkout, no network. `check-oidc-rbac-portal-contract.sh` and
   `check-kaniko-tag-portal-contract.sh` default to `portal/` and now FAIL, not SKIP, when
   the files are missing. R117/R117b, R55, R216 and R211 always run; R211 gained a negative
   test (R211b). What each check asserts is unchanged. The image pin
   `ghcr.io/dasomel/narwhal-portal:1.0.19` is a published artifact and is kept; R210
   still requires every active pin to agree.
3. **D-MONO-3:** CI is path-filtered through `portal-*.yml` workflows, with an
   always-running `Portal gate` aggregate job so a required check exists on every PR.
   Dependabot gets a `/portal` entry. (Workflow files are owned by the CI lane of this
   migration, not by this ADR's change set.)
4. **D-MONO-4:** the image name stays `ghcr.io/dasomel/narwhal-portal`; only the Git tag
   changes (`portal/vX.Y.Z`). Portal still releases independently of the cluster.
5. **D-MONO-5:** portal keeps its own `AGENTS.md`/`CLAUDE.md`, LICENSE, SECURITY and
   CODE_OF_CONDUCT under `portal/`; none are moved or duplicated at the root.

## Consequences

- A portal change and the cluster change it needs (env, RBAC, routes, Valkey) land in one
  reviewed PR, and the seam gates fail in that PR instead of in a later clean install.
- Repository-wide checks (lint, validate, docs, SAST) now also see `portal/`; a check that
  duplicates a portal-owned equivalent is scoped out, a security scan never is.
- The portal pin (`1.0.19`) can still lag `portal/` HEAD, because the pin is a release
  artifact. R211 therefore asserts the probe routes in the working tree, not in the
  released tag.
- Lessons rule: a fix's lessons-log row goes in the log of the area the fix touches
  (`portal/docs/lessons-log.md` or `docs/common/lessons-log.md`); a fix spanning both goes
  in narwhal's.

- **The rewritten historical tags are deliberately not pushed.** The import carries
  `portal/v1.0.0` .. `portal/v1.0.19` locally, but `portal-docker-publish.yml` triggers on
  `portal/v*.*.*`: pushing them would rebuild and re-sign image tags that are already
  published. Releases restart from the next new `portal/vX.Y.Z`; the old tags stay in
  `dasomel/narwhal-portal` (archived, never deleted).
- The root `.gitignore` patterns for `logs/`, `secrets/` and `kubeconfig` matched portal
  API route directories of the same names; `/portal/src/**/` negations keep that source
  tracked, and R35 fails if a portal source file is ignored again.
- **Markdown gate (D-MONO-CI-MD):** portal never linted Markdown, so against the PR base every
  imported portal line is "new". `scripts/ci/markdownlint-changed.py` therefore measures `portal/`
  files against the import baseline commit `e8e8229d8adf350f57feb218f6c19233e2685df4` (when it is an
  ancestor of HEAD; otherwise the PR base). Legacy debt is not excluded and the docs are not edited:
  only lines changed since the import must be clean. Honest limit: that legacy debt is never reported
  by the gate. At the import it was 1686 findings (MD034 687, MD040 664, MD032 137, MD022 121,
  MD031 42, MD058 11, 15 other), so cleaning it is a separate, deliberate change.

## Pending (not done yet)

- Transfer portal's 27 open issues to this repository, with an old-to-new number-mapping
  comment.
- Open PRs cannot move. Dependabot PRs regenerate; drafts #187 and #77 must be re-opened.
- Archive `dasomel/narwhal-portal` with a pointer to `portal/`, after the PR merges.
- Change branch protection required checks to include `Portal gate`.
- Sequence the Valkey change (PR #343) with a portal v1.1.0 release, then bump the pin.
- Retire the separate `narwhal-portal` entry in the OpenForge registry.

## Alternatives considered

- **Keep two repositories and make the contract checks hard** — rejected: keeps the
  non-atomic cross-repo change and a manual pin bump that was already stale.
- **New umbrella repository holding both** — rejected: a third repository to maintain,
  with the same history-rewrite cost.
- **Git submodule** — rejected: still a pin, and awkward for contributors and CI.
- **Squashed import** — rejected: loses 415 commits of blame and incident history that the
  mistakes log refers to.
