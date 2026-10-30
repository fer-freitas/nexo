"""Modo isolado: quando o agente roda em contêiner e o que é exigido pela org.

`isolation.required_for` e a imagem vêm só da org/squad; o dev pode ligar o modo isolado por conta própria, nunca
desligar o que a org exige para a classe de dados.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.security.isolation import allowlist, compose, squid_conf

TRUSTED = ("default", "org", "squad")


def _cfg(ctx: AppContext) -> tuple[dict[str, Any], dict[str, Any]]:
    from nexo.kernel.config.cascade import resolve

    trusted = dict(resolve([x for x in config.load_layers(ctx) if x.level in TRUSTED]).values.get("isolation") or {})
    effective = dict(config.resolved(ctx).values.get("isolation") or {})
    return trusted, effective


def spec(ctx: AppContext, data_class: str) -> dict[str, Any] | None:
    """Parâmetros do contêiner para esta classe de dados, ou None (execução normal). Exigido e desligado → erro."""
    trusted, effective = _cfg(ctx)
    required = data_class in [str(x) for x in trusted.get("required_for") or []]
    mode = "container" if "container" in (trusted.get("mode"), effective.get("mode")) else "none"
    if mode != "container":
        if required:
            raise NexoError(
                f"dados '{data_class}' exigem o modo isolado (isolation.mode: container) — rode "
                "`nexo admin isolation setup`"
            )
        return None
    image = str(trusted.get("image") or effective.get("image") or "")
    if not image:
        raise NexoError("modo isolado sem imagem do agente (isolation.image na org)")
    return {
        "image": image,
        "engine": str(trusted.get("engine") or effective.get("engine") or "docker"),
        "network": str(trusted.get("network") or "nexo-egress"),
        "proxy": str(trusted.get("proxy") or "http://nexo-egress:3128"),
    }


def setup(ctx: AppContext) -> dict[str, Any]:
    """Gera o proxy de saída (squid) com a lista de permissões e a rede interna do Docker/Podman."""
    from nexo.modules.execution.app import agents as agents_app

    trusted, _ = _cfg(ctx)
    vendors = {v for r in agents_app.catalog(ctx).routes.values() for v in r.vendors}
    domains = allowlist(vendors, extra=tuple(str(x) for x in trusted.get("allow") or []))
    folder = ctx.home / "isolation"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "squid.conf").write_text(squid_conf(domains), encoding="utf-8")
    (folder / "egress.compose.yml").write_text(compose(), encoding="utf-8")
    ctx.audit("isolation.setup", {"domains": len(domains)})
    return {"folder": str(folder), "domains": domains}


def check(ctx: AppContext) -> list[str]:
    """Problemas para rodar isolado (doctor): motor de contêiner e rede `nexo-egress`."""
    trusted, effective = _cfg(ctx)
    if "container" not in (trusted.get("mode"), effective.get("mode")) and not trusted.get("required_for"):
        return []
    engine = str(trusted.get("engine") or effective.get("engine") or "docker")
    exe = shutil.which(engine)
    if exe is None:
        return [f"{engine} não encontrado (modo isolado)"]
    proc = subprocess.run(
        [exe, "network", "inspect", str(trusted.get("network") or "nexo-egress")],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return [
            f"rede nexo-egress ausente: `docker compose -f {Path(ctx.home / 'isolation' / 'egress.compose.yml')} up -d`"
        ]
    return []
