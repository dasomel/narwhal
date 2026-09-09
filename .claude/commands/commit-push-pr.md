---
name: commit-push-pr
description: Commit, push, and create PR in one step
disable-model-invocation: true
---

# Commit-Push-PR - One-Step Commit, Push, and PR Creation

Commits changes, pushes to remote, and creates a PR.

## Prerequisites

- Changes must exist
- Current branch should not be main/master (for PR creation)
- Follow `AGENTS.md` and the matching canonical project skill before choosing validation scope

## Steps

### 1. Check Changes
```bash
git status --short
git diff --stat
```

### 2. Run Validation

Use repository-owned checks and fail closed. Do not append `|| true` to validators.

For changes touching the Vagrantfile or shell provisioning/scripts, the minimum checks are:

```bash
ruby -c Vagrantfile
find scripts/ -name '*.sh' -print0 | xargs -0 shellcheck --severity=warning
```

For broader platform/GitOps changes, follow `.agents/skills/narwhal-verification/SKILL.md` and run the relevant deterministic CI-equivalent checks. If a required validator fails, stop before commit/push and report the failure instead of claiming validation succeeded.

### 3. Commit
```bash
# Stage changed files
git add -A

# Commit message format: type(scope): description
```

### 4. Push
```bash
git push -u origin HEAD
```

### 5. Create PR (optional)
```bash
gh pr create --title "PR title" --body "description"
```

## Commit Message Convention

| Type | Description |
|------|-------------|
| feat | New feature |
| fix | Bug fix |
| docs | Documentation change |
| refactor | Code refactoring |
| chore | Build/config change |
