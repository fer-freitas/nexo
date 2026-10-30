"""Preços por cliente: ver o que vale, informar o contrato e conferir o que ainda é preço de tabela.

Os valores de fábrica são a tabela pública de cada ferramenta (`pricing.list_prices_as_of`). Cada cliente informa o
próprio contrato na camada da org (no clone do cérebro, que depois é assinado e vai por PR) ou do projeto. Mudança
com `valid_from` cria uma nova vigência e preserva o histórico: meses fechados não mudam de valor.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from nexo.kernel.config.cascade import ensure_can_set
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError, PolicyViolation
from nexo.kernel.platform import config
from nexo.kernel.runtime import yamlfile
from nexo.modules.finops.app import costs

FIELDS = ("usd_month", "unit_price_usd", "overage_usd", "overage_brl", "included_units", "credit_price_usd", "unit")
FACTORY = "default"


def _pricing(ctx: AppContext) -> tuple[dict[str, Any], dict[str, str]]:
    res = config.resolved(ctx)
    return dict(res.values.get("pricing") or {}), res.origin


def _origins(origin: dict[str, str], prefix: str) -> set[str]:
    return {lvl for key, lvl in origin.items() if key == prefix or key.startswith(prefix + ".")}


def _versions(raw: Any) -> list[dict[str, Any]]:
    items = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    return sorted((dict(v) for v in items), key=lambda v: str(v.get("valid_from", "2000-01-01")))


def _current(versions: list[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    day = (today or date.today()).isoformat()
    valid = [v for v in versions if str(v.get("valid_from", "2000-01-01")) <= day]
    return valid[-1] if valid else (versions[0] if versions else {})


def _tools_in_use(ctx: AppContext) -> dict[str, bool]:
    """Ferramentas com consumo registrado → se o custo veio da API (US$ medido, não da assinatura)."""
    out: dict[str, bool] = {}
    for line in costs.usage_lines(ctx):
        out[line.tool] = out.get(line.tool, False) or bool(line.api_equiv_usd)
    return out


def show(ctx: AppContext) -> dict[str, Any]:
    pricing, origin = _pricing(ctx)
    in_use = _tools_in_use(ctx)
    fx = _versions(pricing.get("fx"))
    subs = []
    for tool, raw in sorted((pricing.get("subscriptions") or {}).items()):
        versions = _versions(raw)
        levels = _origins(origin, f"pricing.subscriptions.{tool}")
        subs.append(
            {
                "tool": tool,
                "current": _current(versions),
                "versions": len(versions),
                "origin": sorted(levels),
                "factory": levels <= {FACTORY},
                "in_use": tool in in_use,
            }
        )
    return {
        "list_prices_as_of": pricing.get("list_prices_as_of"),
        "fx": {"current": _current(fx), "versions": len(fx), "factory": _origins(origin, "pricing.fx") <= {FACTORY}},
        "subscriptions": subs,
        "issues": check(ctx),
    }


def check(ctx: AppContext) -> list[dict[str, str]]:
    """O que ainda deixa o custo estimado: câmbio e assinaturas de fábrica, ou ferramenta usada sem preço."""
    pricing, origin = _pricing(ctx)
    in_use = _tools_in_use(ctx)
    subs = dict(pricing.get("subscriptions") or {})
    issues: list[dict[str, str]] = []
    if in_use and _origins(origin, "pricing.fx") <= {FACTORY}:
        issues.append({"item": "fx", "reason": "câmbio de fábrica; informe o do contrato (nexo cost pricing set fx …)"})
    for tool, via_api in sorted(in_use.items()):
        if tool not in subs:
            if not via_api:
                issues.append({"item": tool, "reason": "ferramenta usada sem preço; o custo aparece como zero"})
            continue
        if _origins(origin, f"pricing.subscriptions.{tool}") <= {FACTORY}:
            as_of = pricing.get("list_prices_as_of") or "—"
            issues.append({"item": tool, "reason": f"preço de tabela pública de {as_of}; informe o contrato"})
    return issues


def estimated_tools(ctx: AppContext) -> set[str]:
    """Ferramentas cujo custo ainda sai de preço de fábrica (o câmbio de fábrica marca todas)."""
    issues = check(ctx)
    if any(i["item"] == "fx" for i in issues):
        return set(_tools_in_use(ctx))
    return {i["item"] for i in issues}


def _parse(field: str, value: str) -> Any:
    if field == "unit":
        if not value.replace("_", "").isalnum():
            raise NexoError(f"unidade inválida: {value}")
        return value
    if field == "included_units" and value.lower() in ("none", "null", "-"):
        return None
    try:
        number = float(value.replace(",", "."))
    except ValueError:
        raise NexoError(f"valor numérico inválido para {field}: {value}") from None
    if number < 0:
        raise NexoError(f"{field} não pode ser negativo")
    return number


def _target(ctx: AppContext, brain: Path | None, squad: str | None) -> tuple[Path, str]:
    if brain is not None:
        if not (brain / "org").is_dir():
            raise NexoError(f"{brain} não parece um clone do cérebro (falta a pasta org/)")
        if squad:
            return brain / "squads" / squad / "config.yaml", "squad"
        return brain / "org" / "config.yaml", "org"
    path = ctx.project_config()
    if path is None:
        raise NexoError("informe --brain <clone do cérebro> (preço da org) ou rode dentro de um projeto")
    return path, "project"


def _base(ctx: AppContext, data: dict[str, Any], level: str, tool: str) -> Any:
    """Vigências de partida: as do próprio arquivo; senão, no cérebro, as de fábrica; no projeto, as resolvidas
    (herda o contrato da org)."""
    from nexo.kernel.config.defaults import DEFAULTS

    own = dict(data.get("pricing") or {})
    if tool == "fx" and own.get("fx"):
        return own["fx"]
    if tool != "fx" and tool in (own.get("subscriptions") or {}):
        return own["subscriptions"][tool]
    pricing = dict(DEFAULTS["pricing"]) if level in ("org", "squad") else _pricing(ctx)[0]
    return pricing.get("fx") if tool == "fx" else (pricing.get("subscriptions") or {}).get(tool)


def set_price(
    ctx: AppContext,
    item: str,
    value: str,
    valid_from: str | None = None,
    brain: Path | None = None,
    squad: str | None = None,
) -> dict[str, Any]:
    """`item` = `fx` (R$ por US$) ou `<ferramenta>.<campo>`; com `valid_from`, abre nova vigência."""
    if valid_from is not None:
        try:
            date.fromisoformat(valid_from)
        except ValueError:
            raise NexoError(f"data inválida: {valid_from} (use AAAA-MM-DD)") from None
    tool, _, field = item.partition(".")
    if tool == "fx":
        key, field, parsed = "pricing.fx", "usd_brl", _parse("usd_brl", value)
        if not parsed:
            raise NexoError("câmbio precisa ser maior que zero")
    else:
        if field not in FIELDS:
            raise NexoError(f"campo desconhecido: {field or '—'} (use {', '.join(FIELDS)})")
        key, parsed = f"pricing.subscriptions.{tool}", _parse(field, value)
    path, level = _target(ctx, brain, squad)
    if level == "project":
        try:
            ensure_can_set(config.load_layers(ctx), key, level)
        except PolicyViolation:
            ctx.audit("policy.violation", {"rule": "locked-key", "key": key, "level": level})
            raise
    data = yamlfile.load(path) if path.is_file() else {}
    current = _base(ctx, data, level, tool)
    versions = _versions(current)
    stamp = valid_from or (str(_current(versions).get("valid_from")) if versions else "2000-01-01")
    target = next((v for v in versions if str(v.get("valid_from")) == stamp), None)
    if target is None:  # nova vigência copia a anterior e muda só o campo pedido
        target = {**_current(versions, date.fromisoformat(stamp)), "valid_from": stamp}
        versions = sorted([*versions, target], key=lambda v: str(v.get("valid_from")))
    old = target.get(field)
    target[field] = parsed
    section = data.setdefault("pricing", {})
    if tool == "fx":
        section["fx"] = versions
    else:
        section.setdefault("subscriptions", {})[tool] = versions if len(versions) > 1 else versions[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    yamlfile.dump(path, data)
    ctx.audit(
        "pricing.set",
        {"item": item, "field": field, "old": old, "new": parsed, "valid_from": stamp, "level": level},
    )
    result: dict[str, Any] = {
        "item": item,
        "field": field,
        "value": parsed,
        "valid_from": stamp,
        "level": level,
        "path": str(path),
    }
    if level in ("org", "squad"):
        result["next"] = [
            f"nexo brain sign {brain} --key <chave de assinatura> --version <AAAA.MM.DD-n>",
            "abrir PR no repositório do cérebro e aprovar",
        ]
    return result
