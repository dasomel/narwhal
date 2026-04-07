---
name: compact
description: Context cleanup - generate and save session summary
---

# Compact - Session Context Cleanup

Summarizes current session work to clean up context.

## Procedure

### 1. Generate Session Summary

Summarize all session work in the following format:

```markdown
# Session State - [today's date]

## Goal
- Final goal of this session

## Tech Stack & Environment
- Platform, K8s version, key architecture decisions

## Completed Work
- [x] Resolved issues and implemented features
- [x] Important code patterns (with snippets)

## Changed Files
- file path: change summary

## Remaining Work
- [ ] Remaining tasks (priority order)

## Current State
- What was being worked on just before summary
- Immediate next step
```

### 2. Save

Save summary to `.claude/cache/SESSION_STATE.md`.

### 3. Check Git Changes

```bash
git diff --stat
git status --short
```

### 4. Notify User

```
=== Context Compact Complete ===
Session summary saved to .claude/cache/SESSION_STATE.md
Changed files: [N], Completed: [N], Remaining: [N]
Will be automatically restored in next session.
===
```

## Notes

- Do not include sensitive information (secrets, tokens) in summary
- Include only key pattern snippets (not full files)
- If there are patterns worth preserving permanently, update MEMORY.md as well
