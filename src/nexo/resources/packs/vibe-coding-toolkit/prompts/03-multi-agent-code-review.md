# Multi-agent code review

## When to use

Use this prompt before merging any nontrivial change, instead of relying
on a single review pass — which inevitably favors whichever perspective
that reviewer uses by default. A security-focused reviewer tends to miss a
React hook rule violation, and vice versa. It works both for an individual
PR (*pull request*, a proposed change to merge into the project) and for
reviewing an entire branch before opening the PR.

## Why it works

Running several narrowly focused specialists in parallel finds more real
problems than one generalist pass — each lens (security, general quality,
typing, framework) sees what the others were not designed to see. But this
only works if synthesis actually discards noise rather than concatenating
everyone's output: without deduplication and a concrete failure scenario
filter, four reviewers merely produce a list four times longer and less
reliable — not four times more signal.

## How to adapt the placeholders

- **`[DIFF/PR/BRANCH]`** — the review target: a literal diff (the line-by-line
  comparison between two versions of the code), a PR number/link, or a
  branch name compared with its base.
- **`[LANGUAGE]`** — the main language of the changed code. Determines which
  language-specific typing concerns enter the review (e.g. TypeScript,
  Python, Go, Rust).
- **`[FRAMEWORK]`** — the framework used in the changed code, when the diff
  touches framework-facing code (e.g. React, Next.js, Django). The prompt
  itself already says to drop this lens when the change is purely
  backend/CLI, without any framework surface.

## The prompt

```
Review [DIFF/PR/BRANCH] using a panel of independent specialist reviewers,
then synthesize their findings into one ranked report. Do not review it
yourself first — dispatch the panel after fixing the target and scope.
Record exact base/head commit IDs, or freeze an explicit working-tree snapshot
covering staged, unstaged, and relevant untracked files. Review the diff plus
connected callers, contracts, and tests; report missing context as a limitation.
Choose lenses by risk and unresolved evidence. Respect any user-requested round
count. Stop when that scope and count are covered and findings have evidence
and disposition; do not add rounds merely to meet a universal quota.
All reviewers are read-only: no fixes, commits, or publishing. Checks that mutate
build output, test databases, ports, or services need independent resources or
must be serialized by the coordinator.

## 1. Dispatch the panel — in parallel, one batch
Send the same diff to each of these reviewer lenses at once, dropping any
that don't apply to this change:
- **General code quality** — readability, structure, error handling, dead
  code, missing test coverage.
- **Security** — OWASP Top 10, hardcoded secrets, injection, broken auth,
  unsafe deserialization, dependency CVEs.
- **[LANGUAGE] type-safety** — unsafe type assertions/casts, escape hatches
  around the type system, async/concurrency correctness, injection risk
  from unchecked input.
- **[FRAMEWORK]-specific** — e.g. component/hook rules, render performance,
  accessibility, framework-specific footguns. Only if this diff touches
  framework-facing code.

Each reviewer works independently — no reviewer sees another's findings —
and reports every finding as: `file:line — severity — one-sentence claim —
concrete failure scenario (the input or state that actually makes this
break)`. A finding with no failure scenario isn't a finding, it's a hunch;
reviewers should drop those themselves instead of padding the list.

## 2. Synthesize
Once every reviewer has reported:
1. **Dedupe** — the same underlying issue flagged by more than one reviewer
   collapses into a single entry; keep the sharpest description and note
   which reviewers agreed.
2. **Filter** — drop anything without a concrete failure scenario, or
   anything already handled elsewhere in the code. Verify against the
   frozen diff and connected code before dropping a finding — don't take a reviewer's claim
   on faith in either direction.
3. **Rank** — CRITICAL (security/data-loss, must fix before merge) → HIGH
   (real bug or significant quality issue) → MEDIUM (maintainability) →
   LOW (style/optional).

## 3. Present
One report, most severe first. Each entry: file:line, one-sentence
summary, failure scenario, severity, which reviewer(s) raised it. State
plainly if nothing survived synthesis — an empty CRITICAL/HIGH list is a
valid, useful result, not a failure to find something. Include the target
identifiers, checks performed, unreviewed scope, and residual risks. Do not
apply fixes automatically; report them for the coordinator to handle within
existing or explicit implementation authorization.

Target: [DIFF/PR/BRANCH]. Stack: [LANGUAGE] / [FRAMEWORK].
```

## Usage example

Imagine you just finished a PR adding a new API endpoint (backend) and the
React form that consumes it. Fill in:

- `[DIFF/PR/BRANCH]` → `PR #482` (or `git diff main...feature/signup-form`)
- `[LANGUAGE]` → "TypeScript"
- `[FRAMEWORK]` → "React + Next.js"

The agent dispatches the four lenses in parallel — general quality,
security (OWASP, the "Open Web Application Security Project", the reference
for the most common web vulnerabilities), TypeScript typing, and the
React/Next.js-specific lens — each reviewing the diff independently, without
seeing what the others found. Each reviewer reports findings as
`file:line — severity — sentence — concrete failure scenario`. During
synthesis, two different reviewers identify the same problem (the endpoint
does not validate a field on the server, even though the form validates it
on the client) — this becomes one entry, marked as confirmed by two
reviewers. A vague comment such as "this component could be cleaner", with
no failure scenario, is discarded. The final report is ordered by severity
— for example: CRITICAL (missing server validation, bypassed by calling the
API directly), HIGH (a new dependency with a known vulnerability — CVE),
MEDIUM (a component without error-state handling), and so on.

## Tips

- Drop reviewer lenses that do not apply — no framework lens for a pure
  backend/CLI change — instead of forcing all four every time.
- Deduplication and filtering in part 2 matter as much as the panel itself;
  four reviewers filling the list with low-confidence findings only make
  a longer, less reliable report.
- See [subagent orchestration](../tools/02-subagent-orchestration.md) to
  actually dispatch this as parallel subagents instead of sequential turns.
