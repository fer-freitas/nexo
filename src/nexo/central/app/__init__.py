"""Coletor central do Nexo: a aplicação FastAPI montada a partir das rotas por assunto.

Execução: `uvicorn nexo.collector.app:app` (detalhes em `core.py`). A ordem de importação dos módulos de rotas é a
ordem de registro (rota fixa antes da rota com parâmetro no mesmo prefixo).
"""

from __future__ import annotations

from nexo.central.app import admin, api, devices, finops, governance, ingest, portal, web  # noqa: F401
from nexo.central.app.core import app
from nexo.central.common import _dbs, db, pseudonym

__all__ = ["_dbs", "app", "db", "pseudonym"]
