"""Páginas do painel central: escopo e filtros, componentes (KPI, tabela, série) e uma página por módulo."""

from __future__ import annotations

from nexo.central.metrics.index import build, drill, options, project_names
from nexo.central.metrics.scope import Filters, Scope, make_scope
from nexo.central.metrics.widgets import kpi, page, table

__all__ = ["Filters", "Scope", "build", "drill", "kpi", "make_scope", "options", "page", "project_names", "table"]
