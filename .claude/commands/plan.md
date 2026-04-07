---
name: plan
description: Plan mode guide - task planning workflow
user-invocable: false
---

# Plan - Planning Mode Guide

> "A good plan is 90% of success"

## Entering Plan Mode

Press **Shift + Tab** twice to enter Plan mode.

## When to Use

### Always use for:
- Adding new IDP components
- Major script modifications
- GitOps app structure changes
- Version upgrades (especially with breaking changes)
- Network/storage architecture changes

### Optional for:
- Simple bug fixes
- Documentation updates
- Configuration value changes

## Plan Template

```markdown
## Goal
[1-2 line goal description]

## Impact Scope
- Files to modify:
- Dependencies:

## Execution Steps
1. [ ] Step 1
2. [ ] Step 2
3. [ ] Verification

## Verification Method
- [ ] Syntax check
- [ ] Cluster test

## Rollback Plan
[Recovery method if issues arise]
```
