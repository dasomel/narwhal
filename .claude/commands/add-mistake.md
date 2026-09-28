---
name: add-mistake
description: Record a mistake pattern to the Mistakes Log in docs/common/lessons-log.md
disable-model-invocation: true
---

# Add Mistake Pattern - Manual Mistake Recording

Adds a row to the Mistakes Log tables in `docs/common/lessons-log.md`.

## Scope

Cluster incidents and **mistakes made while fixing them** both belong here. The second kind is
easy to skip and usually more valuable: a success marker written unconditionally, a setting put
in a file nothing reads, a workaround documented before it was run. Each of those hid the very
failure it was added to surface.

## Input

Extract the following from $ARGUMENTS:

1. **Date**: Today's date (YYYY-MM-DD)
2. **Mistake**: What went wrong — the symptom as it actually presented
3. **Fix**: How it was fixed, and what makes this cause distinguishable from the ones it
   resembles

## Before writing: check for a near-match

Grep `docs/common/lessons-log.md` for the symptom (error string, component, failing
command). If a row already covers it, **sharpen that row instead of appending a new one** —
two entries for one failure are worse than none, because the next reader trusts whichever
they hit first.

## Target Location

Pick the `###` section whose name matches the cause (`grep '^### ' docs/common/lessons-log.md`
lists them), and insert **newest first** at the top of its table.

## Format

```markdown
| YYYY-MM-DD | mistake description | fix/prevention |
```

Write the discriminator, not the conclusion. "Loki CrashLoopBackOff → deleted the WAL" helps
nobody; what earns a row is how to tell this cause from its look-alikes and which obvious fix
is wrong. Escape any literal `|` inside a cell as `\|`, or the table silently gains a column.

A rule graduates into `docs/common/agent-operational-rules.md` only once it has bitten across
more than one incident. One-off narratives stay here.
