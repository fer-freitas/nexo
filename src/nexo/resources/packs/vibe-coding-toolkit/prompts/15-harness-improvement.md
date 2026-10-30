# Set up a harness improvement workflow

Use this in the target project's agent session during every new-project bootstrap and existing-project setup, or for a recurring harness failure. The default adapts Harness2's evidence-driven improvement method to the current client, with initial evaluation and a reusable procedure. The official CLI is an optional isolated lab, not a generic project installer. Read the [guide](../tools/23-harness2.md) for requirements and distinctions.

## Shortcut

```text
Read https://raw.githubusercontent.com/soumatheusgomes/vibe-coding-toolkit/main/docs/prompts/15-harness-improvement.md and execute its Prompt section to set up a bounded harness improvement workflow in this project.
```

Use a local versioned toolkit copy if the remote revision is unavailable. Keep the guide, evaluation fixtures, and evaluator from the same toolkit revision. The remote shortcut becomes available after this version is published.

## Prompt

```text
Set up the default harness improvement workflow for this project using the Vibe Coding Toolkit's docs/tools/23-harness2.md. Include this workflow in both new-project bootstrap and existing-project setup without requiring a separate component selection. Inspect local instructions, existing setup/evaluation, current changes, client capabilities, and canonical documents before editing. Preserve the application stack, customizations, authorized client/model/authentication, permissions, and unrelated work. Reuse an existing workflow rather than duplicating instructions or reinstalling tools.

Select and report one mode. If none was specified, use the native method adaptation: a bounded evidence-based comparison in the current agent, inspired by Harness2. Clearly state that it does not execute the Harness2 framework or reproduce its algorithm or benchmark results. The official CLI is used only when explicitly selected, in a dedicated environment outside this repository. Read the versioned guide; use current official sources and Context7 when available, without sending secrets in queries.

Native method adaptation:
Run relevant initial harness smoke cases using docs/07-harness-evaluation.md and prompt 14 in disposable copies. Record actual results and unavailable observations. On unchanged setup reruns, reuse applicable evidence; material changes require relevant checks again. Make the procedure reusable even when no improvement trial is warranted.

1. Identify one observed instruction, memory, context-routing, or tool-use failure. Define the task, unchanged initial inputs, expected behavior, independent checks, writable files, resources, and stop condition. Reuse docs/07-harness-evaluation.md and prompt 14, existing project tests, and disposable cases. If no failure justifies a trial, document the reusable invocation and evaluation procedure without inventing a problem or changing working instructions.
2. Capture the baseline in a disposable copy outside the toolkit and original project. Do not blindly copy credentials, private caches, or production data. Keep original inputs and the evaluator outside the trial agent's writable scope. Use native independent contexts when available; record unavailable observations as not run. A fresh subagent does not prove a client restart. Do not change provider, authentication, or model to enable a trial.
3. Propose one minimal change to project-owned harness files and test it from the same initial state with comparable settings. Keep acceptance criteria and evaluator unchanged. Do not weaken guardrails, permissions, security, or existing quality gates. Run the same checks, inspect the diff, and distinguish observed actions from agent self-reports. Do not add dependencies, an orchestrator, global tools, automatic hooks, or a new memory system for this comparison.
4. Adopt a supported improvement only within the requested setup scope; otherwise retain the original harness. Apply only the chosen relevant diff, preserve custom content, and verify it in the target project with appropriate checks. Confirm that setup reapplication will not duplicate sections, skills, MCPs, or scripts. Default budget is one candidate comparison; report after it. Further rounds need a concrete reason and an agreed limit.

Official CLI lab, only if selected:
1. Verify the guide's OS/runtime requirements and review the selected upstream revision, package metadata, and demo code. Reuse a reviewed lab, or create a new dedicated checkout and virtual environment outside both this project and the toolkit. Never install Harness2 into the application's dependencies or copy its .env, dependencies, or runs into the application.
2. Run only the credential-free deterministic demo first, using a fresh evaluation-owned output directory. Check exit status, report counts, task outputs, and retained lessons. Report this as a local demo, not proof of project behavior, a model-backed run, or authentication compatibility.
3. Benchmark installation and model calls need explicit authorization for the selected benchmark, dependencies, model/provider/authentication, costs, limits, and isolated execution environment. The documented Codex route uses a Responses API endpoint; do not assume it inherits ChatGPT login. Do not enable the optional LiteLLM bridge or API billing automatically. Upstream Codex runs with its sandbox disabled and executor networking is unrestricted; do not weaken this project's permissions. Missing access or isolation blocks the benchmark, not independent setup work.
4. Do not invent support for arbitrary project tasks. A project adapter or custom executor is a separate implementation scope. Keep demo, benchmark, and native-method results distinct.

For either mode, make the workflow reusable by copying only this self-contained Prompt block into the project's existing prompt location when appropriate, adapting paths without weakening its boundaries, or recording its versioned source and invocation in docs/quality.md. Update docs/setup.md or the canonical equivalent with mode, rationale, toolkit/upstream revision where applicable, actual client/model and sanitized authentication mode, runtime, cases, inputs, commands/results, adopted or rejected changes, elapsed time, limitations, and next action. Store durable lessons with evidence, scope, verification date, and invalidation trigger in the responsible canonical document. Keep sensitive traces private; record tokens/cost only if observable.

Separate installation, discovery, demo, independent checks, and observed project behavior. Mark each selected check pass/fail/not run; do not present one passing task as universal reliability or promise measured savings. Reapplying setup must preserve the chosen mode and working configuration, without restarting trials or paid runs automatically. Keep useful evidence until reviewed, then remove only temporary resources created by this workflow.

This prompt authorizes the bounded local setup described above. It does not authorize production access, external uploads, global/client permission changes, API billing, proxy configuration, benchmark runs, publishing, commits, pushes, or messages. Complete independent authorized work and report specific blockers without inventing success.
```
