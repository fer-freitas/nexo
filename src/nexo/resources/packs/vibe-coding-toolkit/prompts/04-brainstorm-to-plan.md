# Brainstorm to plan

## When to use

Use this prompt whenever a request is open or vague enough that an agent
could reasonably build three different things from it — for example, "add
a notification system", without saying which channel (email? push? in-app?),
for whom, or when to trigger it. It works both for a raw request from a
stakeholder (the person or group interested in the outcome, inside or
outside the technical team) and an idea you sketched too quickly and have
not yet fleshed out.

## Why it works

The failure this prompt prevents is concrete: the agent reads an ambiguous
request, silently chooses the most plausible interpretation, and confidently
builds on it — the mismatch with what you really wanted appears only after
the work is finished, when undoing it costs far more than asking beforehand
would have. Separating "understanding intent", "planning" and "implementing"
into distinct phases, each with its own checkpoint, prevents ambiguity from
silently leaking from one phase into another. The first phase uses focused
Socratic questions (questions designed to help you uncover your own answer,
instead of the agent simply guessing) to establish what "done" means, what
is in and out of scope, and constraints you did not mention but probably
have; when the request allows more than one reasonable reading, the agent
presents the interpretations side by side instead of choosing one alone
and hoping to get it right. Requiring an executable verification check — a
command, a test, an observable behavior — for every plan step, instead of
"should work", prevents the agent from marking a step complete before
actually proving it.

## How to adapt the placeholders

- **`[DESCRIBE THE REQUEST HERE — as short or open-ended as it actually
  is]`** — replace with the request exactly as it reached you: a loose
  sentence, a requirement pasted straight from a conversation, or an idea
  that has not yet become a formal requirement. Do not refine the request
  before pasting it here — the more raw and ambiguous it is, the more value
  Step 1 (clarification questions) provides. If the request is already well
  defined, that is fine: the prompt itself tells the agent not to ask
  questions for their own sake when there is no real ambiguity.

## The prompt

```
Before writing any code for this request: [DESCRIBE THE REQUEST HERE — as
short or open-ended as it actually is].

Do not start implementing. Follow this sequence:

## 1. Clarify intent first
Ask a short set of pointed questions to pin down:
- What "done" actually looks like for this request.
- What's explicitly in scope vs. explicitly out of scope.
- Any constraints I haven't stated but probably have — existing patterns
  to follow, things not to touch, performance or compatibility needs.

If the request is genuinely ambiguous — more than one reasonable reading —
say so directly and lay out the interpretations side by side instead of
silently picking one and hoping it's right. Keep this round tight; don't
interrogate a request that's already clear.

## 2. Turn the clarified intent into a plan
Once scope is settled, write a step-by-step plan. Every step gets an
explicit verification check attached — a command to run, a test to pass, a
behavior to observe — never "should work." A step with no way to verify it
is a sign that step is too vague; break it down further.

## 3. Get a go-ahead
Show the plan and wait for a lightweight confirmation, or corrections,
before touching any code. A plan that changes after feedback is expected,
not a failure — re-confirm only the parts that changed.

## 4. Implement against the plan
Work through the plan in order. For each step, actually run its
verification check and show the result before checking it off — never
mark a step done because it "should" pass. If a step's verification
fails, stop and fix it before moving to the next step; don't build on an
unverified foundation.
```

## Usage example

Imagine someone casually made the following request — without saying
whether the coupon gives a fixed amount or percentage, whether multiple
coupons can be combined, or when it expires:

- `[DESCRIBE THE REQUEST HERE...]` → "add a discount coupon system to checkout."

In Step 1, the agent writes nothing — it responds with questions: "does the
coupon give a fixed amount, a percentage, or both?", "can multiple coupons
apply to the same order?", "does the coupon expire by date, usage count,
or both?" Since "fixed amount vs. percentage" is genuinely ambiguous, it
lays out both readings side by side instead of choosing on its own.

After you answer ("percentage, cannot be combined, expires only by date"),
the agent writes a plan in Step 2 with steps such as "create a coupons
table" → verification: the migration runs without errors; "validate the
coupon in the checkout endpoint" → verification: tests covering valid,
expired and nonexistent coupons pass; "apply the discount to the order
total" → verification: the calculation test passes.

In Step 3, it shows the plan and waits for your go-ahead — a simple "okay"
or a specific adjustment ("forget usage-based expiry, just the date") is
enough. Only in Step 4 does implementation begin, running and showing each
verification result before marking the step done. If the expired-coupon
test fails, it stops there, fixes it, and only then proceeds to the next
step — never building on a step it has not proven works.

## Tips

- Skip Step 1 questions when the request is already small and unambiguous —
  the prompt itself says this; do not force the ritual on a one-line fix.
- The Step 3 go-ahead should be lightweight — "go ahead" or a specific
  correction, not a full design review. Save the real scrutiny for the
  plan's verification checks.
- Combines directly with the brainstorm → plan → implementation → review
  skills in [superpowers](../tools/01-superpowers.md); this prompt is a
  portable, tool-independent version of the same discipline.
