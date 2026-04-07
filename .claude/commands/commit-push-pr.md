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

## Steps

### 1. Check Changes
```bash
git status --short
git diff --stat
```

### 2. Run Validation
```bash
ruby -c Vagrantfile 2>/dev/null || true
shellcheck scripts/**/*.sh 2>/dev/null || true
```

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
