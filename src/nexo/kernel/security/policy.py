"""Política de ações dos agentes: caminhos, comandos, rede e MCP.

Pura: recebe a ação normalizada e o contexto; devolve decisão com motivo.
"""

from __future__ import annotations

import fnmatch
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

PROFILES = ("read-only", "test-writer", "code-writer", "doc-writer")
DOC_GLOBS = ("docs/**", "README.md", "**/README.md")  # o agente writer só escreve documentação
TEST_GLOBS = (
    "tests/**",
    "test/**",
    "**/*.Tests/**",
    "**/*.test.ts",
    "**/*.test.tsx",
    "**/*.spec.ts",
    "**/test_*.py",
    # F49 (achado no e2e Java): Maven/Gradle em src/test, pacotes Dart em test/ e integration_test/
    "**/test/**",
    "integration_test/**",
    "**/*_test.dart",
    "**/*Test.java",
    "**/*IT.java",
    ".nexo/tmp/**",  # rascunho dos agentes, fora do git (achado no e2e Flutter)
)


@dataclass(frozen=True)
class Action:
    kind: str  # read | write | shell | fetch | mcp | harmless | deny | unknown
    tool: str
    path: str | None = None
    command: str | None = None
    url: str | None = None
    mcp_server: str | None = None


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str
    rule: str

    @staticmethod
    def ok(reason: str = "permitido") -> Decision:
        return Decision(True, reason, "allow")


@dataclass(frozen=True)
class Policy:
    tool_kinds: dict[str, str]
    forbidden_paths: tuple[str, ...]
    shell_allow: tuple[str, ...]
    shell_deny: tuple[tuple[re.Pattern[str], str], ...]
    allowed_hosts: tuple[str, ...] = ()
    allowed_mcp: tuple[str, ...] = ("nexo",)
    locked_paths: frozenset[str] = field(default_factory=frozenset)
    env_allow: tuple[str, ...] = ()
    env_allow_tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    guard_deny: tuple[tuple[re.Pattern[str], str], ...] = ()  # bloqueios da guarda fora do Nexo
    mcp_write_allowed: tuple[str, ...] = ()  # MCPs externos que podem escrever (aprovação da Segurança)

    def agent_env_allow(self, tool: str) -> tuple[str, ...]:
        """Variáveis extras permitidas ao agente: as da org + a credencial da própria ferramenta."""
        return (*self.env_allow, *self.env_allow_tools.get(tool, ()))

    @staticmethod
    def from_mapping(data: dict[str, Any]) -> Policy:
        return Policy(
            tool_kinds={str(k): str(v) for k, v in (data.get("tool_kinds") or {}).items()},
            forbidden_paths=tuple(data.get("forbidden_paths") or ()),
            shell_allow=tuple(data.get("shell_allow") or ()),
            shell_deny=tuple((re.compile(d["pattern"]), str(d["reason"])) for d in data.get("shell_deny") or ()),
            allowed_hosts=tuple((data.get("network") or {}).get("allowed_hosts") or ()),
            allowed_mcp=tuple((data.get("mcp") or {}).get("allowed_servers") or ("nexo",)),
            env_allow=tuple(str(v) for v in (data.get("agent_env") or {}).get("allow") or ()),
            env_allow_tools={
                str(t): tuple(str(v) for v in names or ())
                for t, names in ((data.get("agent_env") or {}).get("tools") or {}).items()
            },
            guard_deny=tuple((re.compile(d["pattern"]), str(d["reason"])) for d in data.get("guard_deny") or ()),
            mcp_write_allowed=tuple(str(s) for s in (data.get("mcp") or {}).get("write_allowed") or ()),
        )

    def with_locked(self, paths: frozenset[str]) -> Policy:
        return Policy(
            self.tool_kinds,
            self.forbidden_paths,
            self.shell_allow,
            self.shell_deny,
            self.allowed_hosts,
            self.allowed_mcp,
            paths,
            self.env_allow,
            self.env_allow_tools,
            self.guard_deny,
            self.mcp_write_allowed,
        )


def merge_floor(floor: dict[str, Any], org: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Piso de segurança: a política da org só RESTRINGE a embutida.

    Aceito: novas negações e caminhos proibidos, remover comandos permitidos, classificar ferramentas novas,
    liberar variáveis de ambiente sem cara de segredo. Ignorado (e devolvido como violação): remover negação ou
    caminho proibido do piso, permitir comando/host/servidor MCP novo, reclassificar ferramenta do piso,
    liberar variável sensível ou curinga."""
    from nexo.kernel.security.environment import ALWAYS_DENY, SENSITIVE

    merged: dict[str, Any] = {**floor}
    violations: list[str] = []

    kinds = dict(floor.get("tool_kinds") or {})
    for tool, kind in (org.get("tool_kinds") or {}).items():
        if tool in kinds and kinds[tool] != kind:
            violations.append(f"tool_kinds.{tool}: reclassificar ferramenta do piso ({kinds[tool]} → {kind})")
        else:
            kinds[tool] = kind
    merged["tool_kinds"] = kinds

    for key in ("forbidden_paths",):
        base = list(floor.get(key) or [])
        if key in org:
            violations += [f"{key}: remover '{p}' do piso" for p in base if p not in (org.get(key) or [])]
        merged[key] = base + [p for p in org.get(key) or [] if p not in base]

    base_deny = list(floor.get("shell_deny") or [])
    if "shell_deny" in org:
        org_patterns = {d.get("pattern") for d in org.get("shell_deny") or []}
        violations += [
            f"shell_deny: remover '{d['pattern']}' do piso" for d in base_deny if d["pattern"] not in org_patterns
        ]
    merged["shell_deny"] = base_deny + [
        d for d in org.get("shell_deny") or [] if d.get("pattern") not in {b["pattern"] for b in base_deny}
    ]

    base_guard = list(floor.get("guard_deny") or [])
    if "guard_deny" in org:
        org_guard = {d.get("pattern") for d in org.get("guard_deny") or []}
        violations += [
            f"guard_deny: remover '{d['pattern']}' do piso" for d in base_guard if d["pattern"] not in org_guard
        ]
    merged["guard_deny"] = base_guard + [
        d for d in org.get("guard_deny") or [] if d.get("pattern") not in {b["pattern"] for b in base_guard}
    ]

    base_allow = list(floor.get("shell_allow") or [])
    if "shell_allow" in org:
        wanted = list(org.get("shell_allow") or [])
        violations += [f"shell_allow: permitir '{c}' além do piso" for c in wanted if c not in base_allow]
        merged["shell_allow"] = [c for c in base_allow if c in wanted]

    for section, key in (("network", "allowed_hosts"), ("mcp", "allowed_servers")):
        base = list((floor.get(section) or {}).get(key) or [])
        if section in org and key in (org.get(section) or {}):
            wanted = list((org.get(section) or {}).get(key) or [])
            violations += [f"{section}.{key}: liberar '{h}' além do piso" for h in wanted if h not in base]
            merged[section] = {**(floor.get(section) or {}), key: [h for h in base if h in wanted]}

    env = dict(floor.get("agent_env") or {})
    org_env = dict(org.get("agent_env") or {})

    def _safe(name: str) -> bool:
        return name.strip("*") != "" and name.upper() not in ALWAYS_DENY and not SENSITIVE.search(name)

    allow = list(env.get("allow") or [])
    for name in org_env.get("allow") or []:
        if _safe(str(name)):
            allow.append(str(name))
        else:
            violations.append(f"agent_env.allow: liberar '{name}' (sensível ou curinga)")
    tools = {t: list(v or []) for t, v in (env.get("tools") or {}).items()}
    for tool, names in (org_env.get("tools") or {}).items():
        keep = [n for n in names or [] if n in tools.get(tool, []) or _safe(str(n))]
        violations += [f"agent_env.tools.{tool}: liberar '{n}'" for n in names or [] if n not in keep]
        tools[tool] = keep
    merged["agent_env"] = {"allow": allow, "tools": tools}
    return merged, violations


def normalize(tool: str, tool_input: dict[str, Any], policy: Policy) -> Action:
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        return Action("mcp", tool, mcp_server=parts[1] if len(parts) > 1 else "")
    kind = policy.tool_kinds.get(tool, "unknown")
    path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
    return Action(
        kind=kind,
        tool=tool,
        path=str(path) if path is not None else None,
        command=str(tool_input["command"]) if "command" in tool_input else None,
        url=str(tool_input["url"]) if "url" in tool_input else None,
    )


def _relative(path: str, workdir: str) -> str | None:
    """Caminho relativo ao worktree, ou None se escapar dele."""
    p = path.replace("\\", "/")
    w = workdir.replace("\\", "/").rstrip("/")
    if re.match(r"^[A-Za-z]:/", p) or p.startswith("/"):
        full = posixpath.normpath(p)
    else:
        full = posixpath.normpath(posixpath.join(w, p))
    if full == w:
        return "."
    if full.lower().startswith(w.lower() + "/"):
        return full[len(w) + 1 :]
    return None


def _matches(rel: str, patterns: tuple[str, ...] | frozenset[str]) -> str | None:
    for pattern in patterns:
        candidates = {pattern, pattern.removeprefix("**/")}
        if any(fnmatch.fnmatch(rel, c) for c in candidates):
            return pattern
    return None


def _check_path(action: Action, workdir: str, policy: Policy, write: bool, profile: str) -> Decision:
    if not action.path:
        return Decision.ok("sem caminho")
    rel = _relative(action.path, workdir)
    if rel is None:
        return Decision(False, f"caminho fora do diretório de trabalho: {action.path}", "SEC-22")
    hit = _matches(rel, policy.forbidden_paths)
    if hit:
        return Decision(False, f"caminho proibido ({hit})", "SEC-13")
    if write:
        if rel in policy.locked_paths:
            return Decision(False, f"arquivo bloqueado para o agente: {rel}", "SEC-40")
        if profile == "test-writer" and not _matches(rel, TEST_GLOBS):
            return Decision(False, "perfil test-writer só escreve testes", "SEC-20")
        if profile == "doc-writer" and not _matches(rel, DOC_GLOBS):
            return Decision(False, "perfil doc-writer só escreve documentação (docs/ e README)", "SEC-20")
    return Decision.ok()


def _mask_quotes(command: str) -> str:
    """Mesma string com o conteúdo entre aspas trocado por `_` (mesmo tamanho): operadores e redirecionamentos dentro
    de aspas não são estrutura do comando (achado no teste real: `grep "a\\|b"` e `sed 's#</x>#...#'`)."""
    out: list[str] = []
    quote = ""
    escaped = False
    for ch in command:
        if escaped:
            out.append("_" if quote else ch)
            escaped = False
        elif ch == "\\" and quote != "'":
            out.append(ch)
            escaped = True
        elif quote:
            out.append(ch if ch == quote else "_")
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def _segments(command: str) -> list[str]:
    masked = _mask_quotes(command)
    parts: list[str] = []
    start = 0
    for m in re.finditer(r"&&|\|\||;|\||\n", masked):
        parts.append(command[start : m.start()])
        start = m.end()
    parts.append(command[start:])
    return [p.strip() for p in parts if p.strip()]


_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
DATA_SINKS = ("cat", "tee")  # heredoc só é dado quando vira arquivo; para bash/python/... é código


def _strip_data_heredocs(command: str) -> tuple[str, list[str]]:
    """Tira o conteúdo de `cat > arq <<EOF ... EOF` / `tee arq <<EOF` (texto gravado, não comandos) e devolve os
    destinos de `tee` para conferência. Heredoc para interpretador ou com `|` depois continua sendo verificado."""
    lines = command.split("\n")
    out: list[str] = []
    tee_targets: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        m = _HEREDOC.search(line)
        if not m:
            continue
        before = re.split(r"&&|\|\||;|\|", line[: m.start()])[-1].strip()
        words = re.sub(r"^(?:[A-Z_][A-Z0-9_]*=\S*\s+)+", "", before).split()
        rest = line[m.end() :]
        if not words or words[0] not in DATA_SINKS or "|" in rest:
            continue
        if words[0] == "tee":
            tee_targets += [w for w in [*words[1:], *rest.split()] if not w.startswith("-")]
        delim, dash = m.group(3), bool(m.group(1))
        j = i
        while j < len(lines) and (lines[j].strip() if dash else lines[j]) != delim:
            j += 1
        i = j + 1  # pula o conteúdo e a linha do delimitador
    return "\n".join(out), tee_targets


def _check_shell(command: str, workdir: str, policy: Policy) -> Decision:
    command, tee_targets = _strip_data_heredocs(command)
    for target in tee_targets:
        rel = _relative(target, workdir)
        if rel is None or _matches(rel, policy.forbidden_paths):
            return Decision(False, f"gravação em caminho não permitido: {target}", "SEC-22")
    for pattern, reason in policy.shell_deny:
        if pattern.search(command):
            return Decision(False, f"comando bloqueado: {reason}", "SEC-21")
    masked = _mask_quotes(command)
    for m in re.finditer(r">>?\s*([^\s;&|]+)", masked):
        redirect = command[m.start(1) : m.end(1)].strip("'\"")
        if redirect.startswith("&") or redirect == "/dev/null":
            continue
        rel = _relative(redirect, workdir)
        if rel is None or _matches(rel, policy.forbidden_paths):
            return Decision(False, f"redirecionamento para caminho não permitido: {redirect}", "SEC-22")
    for segment in _segments(command):
        for token in segment.split()[1:]:
            if token.startswith("-"):
                continue
            if token.startswith(("/", "~", "\\")) or ".." in token or re.match(r"^[A-Za-z]:[\\/]", token):
                rel = _relative(token, workdir)
                if rel is None or _matches(rel, policy.forbidden_paths):
                    return Decision(False, f"argumento fora do diretório de trabalho: {token}", "SEC-22")
        env_stripped = re.sub(r"^(?:[A-Z_][A-Z0-9_]*=\S*\s+)+", "", segment)
        if not any(env_stripped == p or env_stripped.startswith(p + " ") for p in policy.shell_allow):
            head = env_stripped.split(" ", 1)[0]
            return Decision(False, f"comando fora da allowlist: {head}", "SEC-21")
    return Decision.ok()


_READ_VERBS = frozenset(
    [
        "get",
        "list",
        "search",
        "read",
        "query",
        "fetch",
        "describe",
        "find",
        "show",
        "lookup",
        "view",
        "retrieve",
        "browse",
        "count",
        "explain",
        "analyze",
    ]
)
_WRITE_VERBS = frozenset(
    [
        "create",
        "update",
        "delete",
        "remove",
        "add",
        "edit",
        "merge",
        "close",
        "post",
        "send",
        "set",
        "transition",
        "assign",
        "write",
        "push",
        "run",
        "execute",
        "trigger",
        "approve",
        "reject",
        "publish",
        "upload",
        "move",
        "rename",
        "archive",
        "comment",
        "reply",
        "start",
        "stop",
        "restart",
        "deploy",
        "rotate",
        "revoke",
        "grant",
        "patch",
        "put",
        "modify",
    ]
)


def _words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w]


def is_read_tool(tool: str) -> bool:
    """ferramenta de MCP externo só de leitura pelo nome (`mcp__srv__getJiraIssue`, `search_issues`).
    Conservador: verbo de leitura nas duas primeiras palavras e nenhum verbo de escrita no nome."""
    name = tool.split("__", 2)[-1] if tool.startswith("mcp__") else tool
    words = _words(name)
    return bool(words) and any(w in _READ_VERBS for w in words[:2]) and not any(w in _WRITE_VERBS for w in words)


def evaluate(action: Action, workdir: str, policy: Policy, profile: str = "code-writer") -> Decision:
    if profile not in PROFILES:
        return Decision(False, f"perfil desconhecido: {profile}", "SEC-20")
    if action.kind == "harmless":
        return Decision.ok()
    if action.kind in ("deny", "unknown"):
        return Decision(False, f"ferramenta não permitida: {action.tool}", "SEC-20")
    if action.kind == "mcp":
        if action.mcp_server not in policy.allowed_mcp:
            return Decision(False, f"servidor MCP não aprovado: {action.mcp_server}", "SEC-34")
        external = action.mcp_server != "nexo" and action.mcp_server not in policy.mcp_write_allowed
        if external and not is_read_tool(action.tool):
            return Decision(False, f"MCP externo é somente leitura: {action.tool}", "SEC-35")
        return Decision.ok()
    if action.kind == "read":
        return _check_path(action, workdir, policy, write=False, profile=profile)
    if action.kind == "write":
        if profile == "read-only":
            return Decision(False, "perfil somente leitura", "SEC-20")
        return _check_path(action, workdir, policy, write=True, profile=profile)
    if action.kind == "shell":
        if profile == "read-only":
            return Decision(False, "perfil somente leitura não executa comandos", "SEC-20")
        return _check_shell(action.command or "", workdir, policy)
    if action.kind == "fetch":
        host = urlparse(action.url or "").hostname or ""
        if host and host in policy.allowed_hosts:
            return Decision.ok()
        return Decision(False, f"acesso de rede não autorizado: {host or action.tool}", "SEC-23")
    return Decision(False, f"ação desconhecida: {action.kind}", "SEC-20")


def _forbidden_anywhere(path: str, workdir: str, policy: Policy) -> str | None:
    """Caminho proibido dentro ou fora da pasta de trabalho (a guarda não restringe a pasta, só os segredos)."""
    import os.path
    from fnmatch import fnmatch
    from pathlib import Path, PurePath

    given = PurePath(Path(path).expanduser()) if path.startswith("~") else PurePath(path)
    absolute = given if given.is_absolute() else PurePath(workdir) / given
    full = os.path.normpath(str(absolute)).replace("\\", "/")
    name = PurePath(full).name
    for pattern in policy.forbidden_paths:
        tail = pattern[3:] if pattern.startswith("**/") else pattern
        if fnmatch(full, pattern) or fnmatch(full, "*/" + tail) or fnmatch(name, tail):
            return pattern
    return None


def evaluate_guard(action: Action, workdir: str, policy: Policy) -> Decision:
    """guarda para sessões da ferramenta de IA abertas fora do Nexo. Só os bloqueios duros da empresa —
    segredos, elevação de privilégio, destruição, script baixado executado, push forçado —, sem a allowlist das
    execuções do Nexo (o dev segue usando a ferramenta normalmente)."""
    if action.kind in ("read", "write") and action.path:
        hit = _forbidden_anywhere(action.path, workdir, policy)
        if hit:
            return Decision(False, f"arquivo protegido ({hit})", "SEC-13")
        return Decision.ok()
    if action.kind == "shell":
        command = action.command or ""
        for pattern, reason in policy.guard_deny:
            if pattern.search(command):
                return Decision(False, f"comando bloqueado: {reason}", "SEC-21")
        for segment in _segments(command):
            for token in segment.split()[1:]:
                if token.startswith("-"):
                    continue
                hit = _forbidden_anywhere(token.strip("'\""), workdir, policy)
                if hit:
                    return Decision(False, f"acesso a arquivo protegido ({hit})", "SEC-13")
        return Decision.ok()
    return Decision.ok()
