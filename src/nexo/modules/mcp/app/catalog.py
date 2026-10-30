"""MCPs oficiais dos produtos como ferramentas extras dos agentes.

Um servidor do catálogo (`resources/mcp/catalog.yaml`) entra na execução quando está ligado
(`mcp.servers.<id>.enabled`), aprovado pela Segurança (`org/tools.yaml › external_mcp`), com o fornecedor liberado
na `data_policy` da classe de dados da tarefa e com o que precisa configurado (URL, variáveis, credencial).
A política deixa os MCPs externos somente leitura, salvo os aprovados em `external_mcp_write`.

A credencial nunca vai na linha de comando: o JSON do servidor referencia `${VARIÁVEL}` e o valor entra só no
ambiente do processo do agente.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.runtime import http
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.secrets import credentials
from nexo.modules.knowledge.app import content
from nexo.modules.mcp.domain import rules


def catalog() -> dict[str, dict[str, Any]]:
    return {str(k): dict(v) for k, v in (dict(load_yaml("mcp/catalog.yaml") or {}).get("servers") or {}).items()}


def _settings(ctx: AppContext) -> dict[str, dict[str, Any]]:
    servers = dict((config.resolved(ctx).values.get("mcp") or {}).get("servers") or {})
    return {str(k): dict(v or {}) for k, v in servers.items()}


def secret_name(server: str) -> str:
    return "mcp_" + server.replace("-", "_")


def _secret(server: str, entry: dict[str, Any]) -> str | None:
    env = str(entry.get("token_env") or "")
    return credentials.get_secret(secret_name(server)) or (os.environ.get(env) if env else None) or None


def _fill(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


STATE_FILE = "mcp-state.json"
REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"


def _state_path(ctx: AppContext) -> Path:
    return ctx.home / STATE_FILE


def evidence(ctx: AppContext) -> dict[str, dict[str, str]]:
    path = _state_path(ctx)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _record(ctx: AppContext, server: str, state: str, detail: str) -> None:
    """Estado honesto: cada passo só vale com evidência e data."""
    data = evidence(ctx)
    data.setdefault(server, {})[state] = f"{datetime.now(UTC).isoformat(timespec='seconds')} · {detail}"
    _state_path(ctx).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    ctx.audit("mcp.state", {"server": server, "state": state})


def state_of(ctx: AppContext, server: str) -> str:
    seen = evidence(ctx).get(server, {})
    return next((s for s in reversed(rules.STATES) if s in seen), "listed")


def search(ctx: AppContext, query: str) -> list[dict[str, Any]]:
    """Catálogo aprovado primeiro; depois o registro oficial, que só descobre e não aprova."""
    low = query.lower()
    rows = [
        {"id": k, "source": "catálogo", "product": v.get("product"), "approved": k in set(content.external_mcp(ctx))}
        for k, v in catalog().items()
        if low in k.lower() or low in str(v.get("product", "")).lower()
    ]
    try:
        body = http.request("GET", f"{REGISTRY}?search={urllib.parse.quote(query)}&limit=20", {}).body or {}
    except (NexoError, OSError):
        return rows
    for item in body.get("servers") or []:
        server = item.get("server", item)
        rows.append(
            {"id": str(server.get("name")), "source": "registro oficial", "product": server.get("description"), "approved": False}
        )
    return rows


def add(ctx: AppContext, server: str, source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Liga no projeto. Ainda sem efeito: a execução só usa depois de aprovado no cérebro, por PR."""
    entry = source or catalog().get(server)
    if entry is None:
        raise NexoError(f"{server} não está no catálogo; use `nexo mcp search`")
    if ctx.project_root is None:
        raise NexoError("rode dentro de um projeto (`nexo init`)")
    config.set_value(ctx, f"mcp.servers.{server}.enabled", "true", "project")
    _record(ctx, server, "configured", "ligado no projeto")
    problems, values = _problems(server, entry, _settings(ctx).get(server, {}))
    return {
        "id": server,
        "state": "configured",
        "identity": rules.identity(entry, values).key,
        "pending": problems,
        "approve": allowlist_entry(server, entry, values),
    }


def allowlist_entry(server: str, entry: dict[str, Any], values: dict[str, str] | None = None) -> dict[str, Any]:
    """Trecho de `org/mcp-allowlist.yaml`, que entra no cérebro por PR."""
    return {
        "id": server,
        "match": rules.identity(entry, values).key,
        "tools": {"allow": [], "write": []},
    }


def allowlist(ctx: AppContext) -> dict[str, dict[str, Any]]:
    path = ctx.brain_dir / "org" / "mcp-allowlist.yaml"
    from nexo.kernel.runtime import yamlfile

    return {str(e["id"]): dict(e) for e in (yamlfile.load(path).get("servers") or [])} if path.is_file() else {}


def identity_problem(ctx: AppContext, server: str, entry: dict[str, Any], values: dict[str, str]) -> str:
    """A aprovação vale para o destino exato. Trocou a URL ou a versão, precisa aprovar de novo."""
    approved = allowlist(ctx).get(server)
    if approved is None or not approved.get("match"):
        return ""
    current = rules.identity(entry, values).key
    return "" if approved["match"] == current else f"destino diferente do aprovado ({current})"


def probe(ctx: AppContext, server: str, tool: str = "", arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Conecta de verdade. `tool` chama uma ferramenta: só com ela o estado vira `used`."""
    from nexo.modules.mcp.adapters.client import HttpClient, StdioClient

    entry = catalog().get(server)
    if entry is None:
        raise NexoError(f"{server} não está no catálogo")
    cfg = _settings(ctx).get(server, {})
    problems, values = _problems(server, entry, cfg)
    if problems:
        raise NexoError("; ".join(problems))
    conf = _server_config(server, entry, cfg, values)
    if conf.get("type") == "http":
        secret = _secret(server, entry) or ""
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        client: Any = HttpClient(str(conf["url"]), headers)
    else:
        client = StdioClient(str(conf["command"]), list(conf["args"]), {**os.environ, **dict(conf.get("env") or {})})
    result = client.exchange(tool, arguments)
    _record(ctx, server, "connected", f"{len(result['tools'])} ferramenta(s)")
    if tool:
        if tool not in result["tools"]:
            raise NexoError(f"{server} não oferece {tool}")
        allowed = list((allowlist(ctx).get(server, {}).get("tools") or {}).get("allow") or [])
        if allowed and not rules.tool_allowed(allowed, tool):
            raise NexoError(f"{tool} fora da lista aprovada")
        _record(ctx, server, "used", f"chamou {tool}")
    return {"id": server, "state": state_of(ctx, server), "tools": result["tools"], "evidence": evidence(ctx).get(server, {})}


def _problems(server: str, entry: dict[str, Any], cfg: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    values = {**{str(k): str(v) for k, v in (entry.get("defaults") or {}).items()}}
    values.update({str(k): str(v) for k, v in (cfg.get("vars") or {}).items()})
    problems = [f"falta mcp.servers.{server}.vars.{v}" for v in entry.get("vars") or [] if not values.get(v)]
    if entry.get("transport") == "http" and not (cfg.get("url") or entry.get("url")):
        problems.append(f"falta mcp.servers.{server}.url")
    pin = rules.pin_problem(entry, values)
    if pin:
        problems.append(f"versão não fixada: {pin}")
    if entry.get("auth") == "bearer" and not _secret(server, entry):
        problems.append(f"sem credencial: nexo login {secret_name(server)} (ou {entry.get('token_env')})")
    return problems, values


def status(ctx: AppContext) -> list[dict[str, Any]]:
    approved = set(content.external_mcp(ctx))
    writers = set(content.external_mcp_write(ctx))
    settings = _settings(ctx)
    rows = []
    for server, entry in sorted(catalog().items()):
        cfg = settings.get(server, {})
        problems, _ = _problems(server, entry, cfg)
        rows.append(
            {
                "id": server,
                "product": entry.get("product"),
                "vendor": entry.get("vendor"),
                "status": entry.get("status"),
                "transport": entry.get("transport"),
                "auth": entry.get("auth"),
                "enabled": bool(cfg.get("enabled")),
                "approved": server in approved,
                "write": server in writers,
                "problems": problems,
                "verified": bool(entry.get("verified")),
                "state": state_of(ctx, server),
                "notes": entry.get("notes") or "",
                "docs": entry.get("docs") or "",
            }
        )
    return rows


def check(ctx: AppContext) -> list[dict[str, str]]:
    """Servidores ligados que não vão entrar nas execuções, com o motivo."""
    issues = []
    for row in status(ctx):
        if not row["enabled"]:
            continue
        if not row["approved"]:
            issues.append({"id": row["id"], "reason": "ligado mas não aprovado pela Segurança (org/tools.yaml)"})
        issues += [{"id": row["id"], "reason": p} for p in row["problems"]]
    unknown = sorted(s for s, cfg in _settings(ctx).items() if cfg.get("enabled") and s not in catalog())
    issues += [{"id": s, "reason": "não existe no catálogo de MCPs"} for s in unknown]
    return issues


def _server_config(server: str, entry: dict[str, Any], cfg: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    token = str(entry.get("token_env") or "")
    if entry.get("transport") == "http":
        out: dict[str, Any] = {"type": "http", "url": _fill(str(cfg.get("url") or entry.get("url")), values)}
        if entry.get("auth") == "bearer":
            out["headers"] = {"Authorization": "Bearer ${" + token + "}"}
        return out
    return {
        "command": str(entry.get("command")),
        "args": [_fill(str(a), values) for a in entry.get("args") or []],
        "env": {str(k): _fill(str(v), values) for k, v in (entry.get("env") or {}).items()},
    }


def servers_for_run(
    ctx: AppContext, allowed_vendors: tuple[str, ...] | None
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """(servidores no formato `mcpServers`, variáveis com as credenciais, servidores ligados que ficaram de fora)."""
    approved = set(content.external_mcp(ctx))
    servers: dict[str, Any] = {}
    env: dict[str, str] = {}
    skipped: dict[str, str] = {}
    entries = catalog()
    for server, cfg in _settings(ctx).items():
        entry = entries.get(server)
        if not cfg.get("enabled") or entry is None:
            continue
        if server not in approved:
            skipped[server] = "não aprovado pela Segurança"
            continue
        if allowed_vendors is not None and str(entry.get("vendor")) not in allowed_vendors:
            skipped[server] = f"fornecedor {entry.get('vendor')} fora da data_policy desta classe de dados"
            continue
        problems, values = _problems(server, entry, cfg)
        wrong = identity_problem(ctx, server, entry, values)
        if wrong:
            problems.append(wrong)
        if problems:
            skipped[server] = "; ".join(problems)
            continue
        servers[server] = _server_config(server, entry, cfg, values)
        secret = _secret(server, entry) if entry.get("token_env") else None
        if secret:
            env[str(entry["token_env"])] = secret
    return servers, env, skipped
