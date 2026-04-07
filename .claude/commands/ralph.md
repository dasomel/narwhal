---
name: ralph
description: Ralph autonomous dev loop - infinite loop execution based on PROMPT.md
disable-model-invocation: true
---

# Ralph - Autonomous Development Loop

> "Ralph is a Bash loop" - Geoffrey Huntley
> https://ghuntley.com/ralph/

Runs Claude in an infinite loop based on a PROMPT.md file for autonomous task execution.

## Usage

### 1. Create PROMPT.md

Copy template:
```bash
cp .claude/templates/PROMPT.md ./PROMPT.md
```

### 2. Edit PROMPT.md

Clearly define goals, task list, and completion criteria.

### 3. Run Ralph

```bash
.claude/scripts/ralph.sh                          # default (unlimited)
.claude/scripts/ralph.sh --max-iterations 10      # max 10 iterations
.claude/scripts/ralph.sh --safe                    # with permission prompts
.claude/scripts/ralph.sh --dry-run                 # preview prompt only
```

## Cautions

1. Clearly limit task scope in PROMPT.md
2. Completion criteria are mandatory (prevents infinite loop)
3. Periodically check `git diff`
4. Git commit before important tasks

## Stopping

Press `Ctrl+C` to stop at any time.
