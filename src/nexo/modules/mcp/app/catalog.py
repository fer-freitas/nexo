"""MCPs oficiais dos produtos como ferramentas extras dos agentes.

Um servidor do catálogo (`resources/mcp/catalog.yaml`) entra na execução quando está ligado
(`mcp.servers.<id>.enabled`), aprovado pela Segurança (`org/tools.yaml › external_mcp`), com o fornecedor liberado
na `data_policy` da classe de dados da tarefa e com o que precisa configurado (URL, variáveis, credencial).
A política deixa os MCPs externos somente leitura, salvo os aprovados em `external_mcp_write`.

A credencial nunca vai na linha de comando: o JSON do servidor referencia `${VARIÁVEL}` e o valor entra só no
ambiente do processo do agente.
"""

from __future__ import annotations

import os
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.platform import config
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.secrets import credentials
from nexo.modules.knowledge.app import content


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


def _problems(server: str, entry: dict[str, Any], cfg: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    values = {**{str(k): str(v) for k, v in (entry.get("defaults") or {}).items()}}
    values.update({str(k): str(v) for k, v in (cfg.get("vars") or {}).items()})
    problems = [f"falta mcp.servers.{server}.vars.{v}" for v in entry.get("vars") or [] if not values.get(v)]
    if entry.get("transport") == "http" and not (cfg.get("url") or entry.get("url")):
        problems.append(f"falta mcp.servers.{server}.url")
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
        if problems:
            skipped[server] = "; ".join(problems)
            continue
        servers[server] = _server_config(server, entry, cfg, values)
        secret = _secret(server, entry) if entry.get("token_env") else None
        if secret:
            env[str(entry["token_env"])] = secret
    return servers, env, skipped
