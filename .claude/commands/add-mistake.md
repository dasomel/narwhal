---
name: add-mistake
description: Record a mistake pattern to CLAUDE.md Mistakes Log
disable-model-invocation: true
---

# Add Mistake Pattern - Manual Mistake Recording

Adds a discovered mistake pattern to the Mistakes Log table in CLAUDE.md.

## Input

Extract the following from $ARGUMENTS:

1. **Date**: Today's date (YYYY-MM-DD)
2. **Mistake**: What went wrong
3. **Fix**: How to fix/prevent it

## Target Location

Add to the appropriate category table in CLAUDE.md:
- Shell Script mistakes
- Kubernetes/Helm mistakes
- GitOps/ArgoCD mistakes
- Vagrant/Infrastructure mistakes

## Format

```markdown
| YYYY-MM-DD | mistake description | fix/prevention |
```
