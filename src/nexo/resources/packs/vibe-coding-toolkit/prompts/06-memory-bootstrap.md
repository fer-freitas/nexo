# Memory bootstrap

## When to use

Use this prompt when starting a project from scratch — or adopting
persistent memory in an existing project — that has no memory system
between agent sessions yet. Without one, every new session relearns the
same expensive lesson an earlier session already paid to discover: a
subtle bug, an architecture decision that only makes sense with knowledge
of what was tried and rejected, or a business rule the code does not make
obvious. This is the prompt version of the pattern described in the
[Claude memory system](../tools/09-claude-memory-system.md) (the fast tier)
and [Obsidian as memory](../tools/08-obsidian-memory.md) (the long-term tier).

## Why it works

The prompt builds two tiers with different purposes. The first is a small
index automatically loaded in every session — so it must stay lean: if it
grows too large, it stops being read properly and becomes another large
file nobody opens. The second is an unlimited long-term store, consulted
on demand instead of always loaded. The "worth saving" criterion (Step 2)
is deliberately restrictive — the question is not "is this true about the
project?", but "would a future session be surprised and grateful to know
this before starting, instead of learning it the hard way?" — because the
biggest threat to a memory system is not forgetting something important,
but accumulating so much irrelevant material that nobody reads the index
anymore. The growth policy (Step 3) never lets an entry disappear without
confirmation: it is deleted from the index only after being created **and**
successfully read back from the long-term store — deleting before that
confirmation is data loss, not cleanup.

## How to adapt the placeholders

- **`[MEMORY_DIR]`** — appears three times; the folder for the fast
  (always-loaded) tier. In Claude Code, a common convention is
  `.claude/memory/` can be a custom folder — verify loading explicitly;
  prefer the active client's native memory or existing repository docs.
- **`[CONFIG_FILE]`** — the file your agent automatically loads at the start
  of every session, where memory's `INSTRUCTIONS.md` must be referenced to
  be read without asking. In Claude Code, this is usually the project's
  `CLAUDE.md`.
- **`[LINE_CAP]`** — the maximum number of nonblank lines the index may have
  before triggering the cleanup routine in Step 3. The prompt suggests
  130 as a starting point — adjust to your situation.
- **The entire conditional block in Step 4** (the bracketed paragraph
  beginning "If this project has a long-term vault/wiki already…") — this
  is not a simple blank to fill in, but a conditional instruction for the
  agent to decide by inspecting the project. If a long-term store already
  exists — a vault (a long-term notes repository such as Obsidian or
  Notion), wiki, or docs folder — replace the entire block with a direct
  sentence naming its location and access method, filling in what the
  prompt calls `[VAULT_TOOL/LOCATION]` (the store's name or location) and
  `[HOW — MCP tool, CLI, direct file edit]` (the access mechanism — an MCP
  server, short for "Model Context Protocol", the standard way an agent
  calls external tools; a CLI; or direct file editing). If none exists,
  leave the block unchanged — it already tells the agent to acknowledge
  that and deliver only tier 1, without inventing a destination the growth
  policy will eventually need.
- **`[PROJECT NAME/STACK]`** — the project name and technical stack, just
  for context (e.g. "Task management SaaS, Next.js + PostgreSQL").

## The prompt

````
Reuse the active client's native memory or existing repository docs first.
Do not add a vault, MCP server, or dependency. Verify how the client loads
the index; a folder name alone does not enable automatic memory.

Set up a two-tier memory system for this project: a small always-loaded
index for facts a session needs before it starts working, and a place for
everything else that doesn't need to load every time.

## 1. Tier one — the always-loaded index
Reuse existing equivalents; create only missing files:
- `[MEMORY_DIR]/INSTRUCTIONS.md` — the rules below, wired into whatever
  this project auto-loads at session start (e.g. [CONFIG_FILE]), so every
  session reads them without being asked.
- `[MEMORY_DIR]/MEMORY.md` — the index itself: one line per saved fact,
  linking to its own topic file.
- `[MEMORY_DIR]/` as a folder for individual topic files, each with
  frontmatter:
  ```
  ---
  name: kebab-case-slug
  description: one-line summary — used to judge relevance in a future session
  metadata:
    type: feedback | architecture | business-rule | reference
  source: path, decision record, or approved reference
  evidence: observation or check supporting the lesson
  last_verified: YYYY-MM-DD
  invalidate_when: change that would make the fact stale
  ---
  ```

## 2. What's worth saving — the narrow test
Save a memory only when the answer to this is yes: **would a future
session be surprised and grateful to know this before starting, rather
than discovering it the hard way?**

Concretely:
- Derivable by reading the code or git history → don't save it.
- A deadline, a motivation, or anything temporary to right now → don't
  save it.
- A debugging recipe that belongs in a commit message → don't save it.
- A mistake a session made that had to be corrected, an architecture
  pattern found only after failed attempts, a business rule invisible in
  the code, or where to find something outside the repo (a dashboard, a
  wiki, a channel) → save it.

Err toward not saving. A memory nobody needed is clutter; a lesson
relearned the hard way is expensive — but a small, high-signal index beats
a large, ignored one every time.

Keep temporary handoffs (objective, changed files, checks, blockers, next
step) in the task record, separate from durable memory. Promote only verified
lessons. Before using a memory, check its source and invalidation condition
against the current state; reverify or mark stale entries superseded. Never
invent evidence or verified dates, or store credentials and private dumps.

## 3. Growth policy — keep the index small forever
Before adding a new entry, count the index's non-blank lines. Past
[LINE_CAP] (e.g. 130), sanitize first:
1. Score every existing entry: recency × specificity × likelihood of
   preventing a real future mistake.
2. For each low-scoring entry, migrate it out — never just delete it —
   in this exact order:
   1. **Dedup** — search the long-term store (below) for the same topic;
      extend an existing note instead of creating a duplicate.
   2. **Match the target's template** — long-term notes may have their
      own required structure; follow it exactly.
   3. **Create** the note in the long-term store.
   4. **Confirm** — read the note back. No successful read, no deletion.
   5. **Only then** delete the entry from the index and its topic file.
3. Rewrite the index with only what's left.
4. Then add the new entry.

Deleting before the read-back confirms is data loss, not cleanup. If
unsure whether an entry still earns its place, leave it — migrating later
costs nothing.

## 4. Tier two — the long-term store
[If this project has a long-term vault/wiki already: name it explicitly —
e.g. "migrate low-value entries to [VAULT_TOOL/LOCATION], accessed via
[HOW — MCP tool, CLI, direct file edit]." If nothing like that exists yet,
say so and stop after tier one; don't invent a destination for entries the
cap will eventually need to shed.]

Project: [PROJECT NAME/STACK].
````

## Usage example

Imagine starting a task management SaaS from scratch — Next.js + PostgreSQL —
and the team already uses an internal Notion wiki for longer-term documentation:

- `[MEMORY_DIR]` → `.claude/memory`
- `[CONFIG_FILE]` → `CLAUDE.md`
- `[LINE_CAP]` → `130`
- Step 4 conditional block → "migrate low-value entries to the internal
  Notion wiki, accessed through the official integration"
- `[PROJECT NAME/STACK]` → "Task Tracker SaaS, Next.js + PostgreSQL"

The agent creates `.claude/memory/INSTRUCTIONS.md` (linked in the project's
`CLAUDE.md`), an empty `.claude/memory/MEMORY.md`, and prepares the
`.claude/memory/` folder for topic files. Two weeks later, during a session,
the agent discovers — after two failed attempts — that a race condition
(two processes competing for the same data simultaneously) only happens
because two routes write to the same table without a lock; it saves this
as an `architecture` entry in `.claude/memory/tasks-table-race-condition.md`
and adds an index line. Months later, the index exceeds 130 lines: before
adding the next entry, the agent scores the existing ones, chooses the
lowest-value entries (for example, a configuration detail now readily found
in the code), migrates each to the Notion wiki following dedup → template →
create → confirm by reading back, and only then deletes the corresponding
index entry — never the other way around.

## Tips

- The saving criterion in Step 2 is deliberately restrictive — most of what
  an agent learns during a session does not deserve permanent memory.
  Resist recording everything "just in case".
- The migrate-before-deleting order in Step 3 exists because an unconfirmed
  migration is data loss, not relocation — never skip straight to deleting
  the index entry.
- If no long-term store exists yet, deliver tier 1 only instead of inventing
  a destination — see [Obsidian as memory](../tools/08-obsidian-memory.md) for
  how to build one from scratch.
