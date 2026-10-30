# Complete development environment setup

This prompt installs, configures, updates and validates the global development environment. It works for a new machine or for fixing one that already has tools installed. It includes Node.js, Docker, Codex, Claude Code and all mandatory extensions listed below.

The scope is the computer, including configuration outside the project. Existing local dependencies, lockfiles, credentials and data must be preserved. Read the [usage guide](../03-environment-setup.md) to choose an agent, track required interventions, resume a run and reproduce the result on another machine.

## How to use

In an agent with a terminal and access to the computer's files, paste **the entire block in the “Prompt” section**. Do not paste it into the terminal as a shell command. A conversation without command execution can explain the setup, but cannot perform installations.

If the repository is available locally, paste into the agent:

```text
Read docs/prompts/10-development-environment-setup.md in this repository and execute the entire block in the “Prompt” section on this computer.
Use templates/environment-manifest.json.example as the initial manifest format, filling in observed values and adding other components found.
Save the report and manifest outside projects, in a user setup directory, and report the final paths.
```

To use without cloning, after this version is published on the `main` branch:

```text
Read https://raw.githubusercontent.com/soumatheusgomes/vibe-coding-toolkit/main/docs/prompts/10-development-environment-setup.md
and execute the entire block in the “Prompt” section on this computer. Save the report and manifest in a user setup directory, outside projects, and report the final paths.
```

## Prompt

```markdown
# Install, configure, update and optimize the entire development environment

Prepare this computer for development, primarily with Node.js, and for using Codex and Claude Code.

This prompt must work in two scenarios:

- **New environment, no tools:** install and configure the entire mandatory foundation, including plugins, skills, MCPs and hooks.
- **Existing environment:** inventory, update, fix, remove duplicates and improve integrations.

Do the work. Do not deliver only a tutorial or recommendations.

Adapt commands, paths and methods to the operating system, architecture, shell and execution date. Consult current official documentation.

## 1. Authorization and rules

The following are authorized:

- Installation of every component in the mandatory list.
- Updating all existing global development components.
- Configuration and activation of mandatory plugins, skills, MCPs and hooks.
- Removal of confirmed duplicates and unnecessary leftovers.
- Fixes to PATH, shell initialization and integrations.
- Running commands and reading or changing files outside the project when needed for setup.

Preserve projects, local dependencies, lockfiles, credentials, sessions, histories and custom settings.

Back up before changing configuration or removing components with relevant data. Record how to restore it.

Do not repeat requests for authorization already granted. Request my intervention only for authentication, system requirements, costs, new extras or migrations with incompatible impact.

Do not publish content, send messages, purchase services or change projects as a consequence of setup.

The process must be repeatable: subsequent runs must check, update and fix without duplicating installations or configuration.

## 2. Mandatory foundation

**Every item below must be installed when absent, updated when needed and configured for use. Merely checking its existence does not meet the goal.**

### System and development

- Required prerequisites: certificates, downloading, extraction and compilation.
- Git.
- GitHub CLI.
- A package manager appropriate for the system.
- A Node.js version manager, preferably `fnm` when compatible and appropriate.
- Node.js LTS and npm.
- pnpm.
- ripgrep, fd and jq, or justified equivalents when incompatible.

### Containers

- Docker, including a working client and engine.
- Docker Compose available through `docker compose`.
- Components required for operation on the operating system.

### Agents

- Codex CLI.
- Claude Code CLI.

### Agent productivity and extensions

- **RTK:** installation and integration for supported commands.
- **Ponytail:** plugin or skills, with `full` intensity as the default for programming tasks.
- **Caveman:** skills and concise communication by default, preserving clarity and normal wording in code, documentation and commits.
- **Superpowers:** skills installation and configuration in compatible agents.
- **Context7:** MCP server installation or connection and configuration in compatible clients.
- **Graphify:** installation and configuration through the officially supported mechanism.

Resolve these tools' correct identities and sources before installing. Do not install a package merely because its name is similar.

Use each project's and platform's official mechanisms. When a component is unsupported, record the incompatibility, continue with the others and present an appropriate alternative.

**Mandatory plugins are not optional extras and must not be skipped because they are not yet on the machine.**

### Project capability prerequisites

This toolkit configures machines and individual projects. In this machine flow, evaluate docs/tools/README.md and its scope matrix against the intended development work; project skills and application configuration remain local to each codebase.

- Next.js: for next-dev-loop, prepare compatible agent-browser and browser dependencies when Next.js development is selected. Read docs/tools/17-nextjs-runtime-and-performance.md for the current Next.js/Turbopack and browser version requirements. Do not upgrade applications or change their bundler here. next-bundle-optimizer uses the project's installed analyzer.
- PostgreSQL: supabase-postgres-best-practices is local guidance for PostgreSQL anywhere; it does not require a Supabase account, SDK or new database service. Install database tools only for the selected workflow.
- GitHub Actions: when CI investigation is selected, prepare GitHub CLI and authorized login for gh-fix-ci. Record supported clients and missing permissions without copying credentials.
- Security review: check Git/history and compatible tools for differential-review; preserve the native session model/provider/authentication.
- UI: shadcn instructions belong only to projects that adopt shadcn/ui. Do not globally initialize a design system.
- Sentry: check client MCP/OAuth support when this service is selected. Configure the intended scope only with an authorized organization/project; missing access remains pending. Do not create accounts, install application SDKs or enable paid services as a side effect.
- Harness2: docs/tools/23-harness2.md defines the default native harness workflow for new and existing project setup. It reuses the current agent and needs no additional machine installation. Prepare Git, uv, and compatible Python only for a selected official CLI lab outside projects; benchmark dependencies, model access, API costs, the optional bridge, and execution isolation require their own explicit selection. Do not install the CLI globally or change client permissions by default.

Inventory optional capabilities with required: false until a selected workflow makes their prerequisites necessary. Record source/revision, scope, client, installation, discovery, live evidence and update method. Evaluate every item, without installing every project-specific option globally. Continue independent machine work when a capability is unavailable.

## 3. Diagnosis and inventory

Before changing the environment, identify:

- Operating system, version, distribution and architecture.
- Native environment, WSL, virtual machine, container or remote environment.
- Shell, terminal, PATH, startup files and privileges.
- Package managers and runtimes.
- Global tools by user, system, runtime and environment.
- Plugins, skills, MCPs, hooks and extensions for agents and editors.
- Global and project configuration, including precedence.
- Active, disabled, duplicate, incompatible or discontinued components.
- Relevant projects' requirements.

Record each component's name, purpose, version or revision, path, source, update method and integrations.

Use each ecosystem's inventory mechanisms. Do not rely only on executables found in PATH.

Distinguish real duplicates from symbolic links, shared references and installations needed in different environments.

If an inventory or manifest from the source environment exists, include its components when reproducing the new environment. Without that inventory, install the entire mandatory foundation and state that unidentified tools from the previous machine cannot be reproduced automatically.

## 4. Installation and updates

Execute in the appropriate dependency order:

1. Diagnosis and backups.
2. Prerequisites and package manager.
3. Git, GitHub CLI and utilities.
4. Version manager, Node.js, npm and pnpm.
5. Codex CLI and Claude Code CLI.
6. Mandatory plugins, skills, MCPs and hooks.
7. Docker and Docker Compose.
8. Updates to other existing global components.
9. Cleanup, validation and report.

For each component:

- Consult current official sources.
- Use a compatible stable version.
- Reuse installations that are already suitable.
- Preserve the existing installation method when supported.
- Avoid installing the same tool through different managers.
- Confirm the version, path and executable actually used.

Also update existing global components absent from the mandatory list: packages, CLIs, plugins, skills, local MCPs, extensions and tools used by hooks.

Preserve local changes in custom plugins or skills. Do not discard the user's work to force an update.

For remote MCP services, check compatibility and connectivity; do not treat the service as a local package.

Updating a previously disabled component does not authorize enabling it. This restriction does not apply to mandatory components that need to be configured in this setup.

Do not update projects' local dependencies or applications unrelated to development.

## 5. Shell, Node.js and projects

- Configure PATH and initialization in the correct shell files.
- Ensure operation in new sessions and the terminals agents use.
- Configure the latest compatible Node.js LTS version as the default outside projects.
- Configure automatic version selection per project when supported.
- Respect `.node-version`, `.nvmrc`, `engines`, `packageManager` and lockfiles.
- Preserve specific versions projects require.
- Identify conflicting requirements and explain the resolution.
- Configure pnpm through the officially supported mechanism.
- Preserve the package managers chosen by projects.
- Do not change dependencies, lockfiles or declared versions as part of setup.
- Fix permission issues at their cause, without indiscriminate permissions or administrator use by default.

On Windows, distinguish native and WSL environments. Do not mix their executables or dependencies.

## 6. Plugins, skills, MCPs and hooks

Install and configure each mandatory component in agents with official support.

- Verify installation, discovery, loading and operation.
- Use each client's documented formats and paths.
- Preserve custom instructions and existing preferences.
- Identify duplicate hooks, repeated command execution and competing integrations.
- Do not duplicate installations when a distribution already includes the same skills.
- Do not assume configuring Codex also configures Claude Code, or vice versa.

Configure preferences:

- **Ponytail:** `full` intensity for programming, preserving security, accessibility and explicit requirements.
- **Caveman:** concise responses in the user's language; complete explanations when requested; normal wording in code, documentation and commits.
- **Context7:** current documentation for libraries, APIs and tools when relevant; never send secrets in queries.
- **RTK:** integration with supported execution mechanisms, avoiding duplicate wrappers or hooks.
- **Superpowers:** skills available and loaded in compatible clients.
- **Graphify:** tool and integration ready for use through the supported mechanism.

For Graphify and Caveman LLM work, preserve the native client, model and authentication authorized in the session. In Codex, retain ChatGPT login when used; in Claude, preserve the current authentication mode. Pass the same model explicitly when supported delegation is available. Do not silently switch provider/model or enable API billing or a proxy. The standalone CLI does not automatically inherit the agent's authentication; record limitations instead of inventing support.

This prompt's instructions and my explicit preferences take precedence over skill workflows that would repeat authorizations already granted.

## 7. Codex, Claude Code and permissions

- Ensure both CLIs work in new terminals.
- Preserve existing models, providers, sessions and configuration.
- In a new environment, use official authentication flows.
- Check authentication without revealing credentials.
- Open the login flow when needed and specify exactly what intervention I must provide.
- Do not enable API billing or change authentication mode without my choice.

### Codex

Configure full access, networking and no routine approval prompts through officially supported mechanisms, with persistence when available.

Distinguish CLI, desktop app, operating system and organization permissions. Verify every context used.

Do not assume changing a file automatically changes the current session's permissions. Confirm in a new session.

### Operating system

- Check permissions required by the features used.
- Confirm application and component identities.
- Open the relevant screens when possible.
- Provide guidance on elevation, system password entry, biometric authentication or restarting.
- Never request passwords, tokens or authentication codes in chat.
- Do not bypass protections or administrative policies.
- If a policy prevents configuration, record the restriction and continue independent steps.

## 8. Docker and Docker Compose

Install and configure a working Docker solution, including engine, client and Compose.

- On macOS and Windows, use Docker Desktop when appropriate and configure official prerequisites.
- On Linux, use Docker Engine and the Docker Compose plugin compatible with the distribution.
- Check architecture, virtualization, system requirements and licensing.
- If costs apply, ask for my choice before purchasing.
- Reuse a working installation and avoid competing engines or services.
- Configure the correct context, user access and persistent startup when supported.
- Preserve existing containers, images, volumes, networks and configuration.
- Report required interventions and restarts.

Docker is ready only when the client can access the engine and run containers and Compose projects.

## 9. Cleanup and duplicate removal

- Identify which installation is in use before removing another.
- Check references in PATH, shell, agents, editors and hooks.
- Remove confirmed duplicates through their responsible manager.
- Preserve or consolidate necessary configuration.
- Eliminate repeated entries, broken links and obsolete references.
- Remove demonstrably unnecessary leftovers.
- Clear caches through official mechanisms when there is a concrete benefit.
- Do not remove tools merely because they are old or similar.
- Do not treat versions required by projects as duplicates.
- Preserve files with unknown purposes.
- Do not indiscriminately clean Docker volumes or other data.
- Confirm operation after every removal.

When dependency or impact is uncertain, preserve the component and record the outstanding issue.

## 10. Additional improvements

After completing the foundation, evaluate relevant improvements for:

- Terminal and navigation.
- Git and collaboration.
- Documentation and agent context.
- Browser automation.
- Testing, formatting, code analysis and accessibility.
- Containers and reproducible environments.
- Diagnosis, performance and resource consumption.

Consider current tools and better alternatives, without installing for novelty or popularity.

For each recommendation, state the problem solved, benefit, compatibility, maintenance, overlap, costs and installation scope.

Prioritize a few high-impact improvements. Prefer native features or existing solutions when sufficient.

Install new extras after my choice. The mandatory foundation and updates to existing components do not depend on that choice.

## 11. Validation

Test in a new session, without relying on temporary variables:

- Git, GitHub CLI and the managers used.
- Default Node.js LTS, npm and pnpm.
- HTTPS access to the configured registry.
- Per-project version switching and returning to the default.
- Codex and Claude Code startup and authentication.
- RTK installation and operation.
- Ponytail, Caveman and Superpowers discovery and loading.
- Context7 and other enabled MCP connections.
- Graphify operation.
- Hook execution without duplication.
- Persistence of configuration and verifiable permissions.
- Absence of relevant conflicts and broken references.

For Docker:

1. Run `docker version` and confirm client and server.
2. Run `docker info`.
3. Run `docker compose version`.
4. Run `docker run --rm hello-world`.
5. Create a minimal Compose project in a temporary directory.
6. Run `docker compose up -d` and confirm the service works.
7. Stop it with `docker compose down` and remove only resources created by the test.

Use temporary directories for tests. Preserve existing projects.

Run relevant official diagnostics, such as `brew doctor` when applicable. Investigate relevant warnings.

Do not make paid calls merely for validation without authorization.

## 12. Delivery and reproduction

Deliver a Markdown report with:

- The identified environment.
- A checklist of the entire mandatory foundation.
- Components installed, updated or reused.
- Previous and final versions when applicable.
- Sources, paths and installation methods.
- Configured plugins, skills, MCPs and hooks.
- Duplicates removed and cleanup performed.
- Configuration changes, backups and restoration.
- Tests and results.
- Outstanding issues, incompatibilities and blockers.
- Recommended additional improvements.

Also generate a reusable manifest of the final environment, with components, sources, versions or revisions, scopes and required configuration. Do not include secrets or private data.

This manifest must enable reproducing the environment on another machine, adapting paths and requirements to the target system.

Do not consider the work complete merely because installations finished. Confirm every mandatory component's configuration and operation, and make clear what still depends on me.
```
