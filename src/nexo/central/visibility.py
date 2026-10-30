"""Matriz de visibilidade — aplicada no servidor, nunca só na tela.

- Agregado: dev, tech lead e gestor veem o total da org e os próprios squads; diretoria, FinOps, time do Nexo,
  Segurança e admin veem todos os squads.
- Individual: só nos propósitos da matriz (orçamento, bloqueios, portões pendentes, licença, violações e o próprio
  painel); toda exibição de nome de pessoa é auditada; listas de pessoas ficam em ordem alfabética (sem ranking).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException

ROLES = ("dev", "lead", "manager", "director", "finops", "nexo", "security", "admin", "ci")  # ci: só integrações
ALL_SQUADS_ROLES = frozenset({"director", "finops", "nexo", "security", "admin"})

PAGES: dict[str, tuple[str, frozenset[str]]] = {
    "executive": ("Visão geral", frozenset({"director", "manager", "lead", "finops", "nexo", "admin"})),
    "finops": ("Custos e orçamento", frozenset({"finops", "manager", "director", "nexo", "admin"})),
    "squad": ("Squads", frozenset({"dev", "lead", "manager", "director", "nexo", "admin"})),
    "quality": ("Qualidade e segurança", frozenset({"lead", "manager", "director", "security", "nexo", "admin"})),
    "routing": ("Ferramentas e modelos", frozenset({"nexo", "admin"})),
    "brain": ("Conhecimento", frozenset({"nexo", "admin"})),
    "fleet": ("Frota de agentes", frozenset({"nexo", "security", "admin"})),  # F15
    "dora": ("DORA e ROI", frozenset({"director", "manager", "lead", "finops", "nexo", "admin"})),  # F15
    "insights": ("Insights", frozenset(ROLES) - {"ci"}),
    "flags": ("Feature flags", frozenset({"nexo", "admin"})),  # F17
    "me": ("Meu painel", frozenset(ROLES) - {"ci"}),
}
EXPORT_ROLES = frozenset({"lead", "manager", "director", "finops", "nexo", "security", "admin"})

# propósito individual → papéis e relação exigida com o squad da pessoa exibida
INDIVIDUAL: dict[str, dict[str, str]] = {
    "budget": {"lead": "leads", "manager": "manages"},
    "blocks": {"lead": "leads"},
    "pending_gates": {"lead": "leads"},
    "license": {"finops": "any"},
    "violations": {"security": "any"},
}


@dataclass
class Principal:
    dev: str
    role: str
    member: set[str] = field(default_factory=set)
    leads: set[str] = field(default_factory=set)
    manages: set[str] = field(default_factory=set)
    via: str = "token"  # token | session | device

    @property
    def squads(self) -> set[str] | None:
        """Squads cujo agregado a pessoa pode ver (None = todos)."""
        if self.role in ALL_SQUADS_ROLES:
            return None
        if self.role == "lead":
            return self.member | self.leads
        if self.role == "manager":
            return self.member | self.manages
        return set(self.member)

    def sees_squad(self, squad: str | None) -> bool:
        allowed = self.squads
        return allowed is None or (squad or "") in allowed

    def can_page(self, page: str) -> bool:
        return page in PAGES and self.role in PAGES[page][1]

    def individual(self, purpose: str, squad: str | None = None, dev: str | None = None) -> bool:
        """Pode ver dado individual para `purpose` de uma pessoa do `squad`? O próprio dev sempre se vê."""
        if dev is not None and dev == self.dev:
            return True
        relation = INDIVIDUAL.get(purpose, {}).get(self.role)
        if relation is None:
            return False
        if relation == "any":
            return True
        return (squad or "") in getattr(self, relation)

    def individual_squads(self, purpose: str) -> set[str] | None:
        """Squads cujas pessoas podem aparecer individualmente para `purpose` (None = todas; vazio = nenhuma)."""
        relation = INDIVIDUAL.get(purpose, {}).get(self.role)
        if relation is None:
            return set()
        if relation == "any":
            return None
        return set(getattr(self, relation))


def require_page(principal: Principal, page: str) -> None:
    if page not in PAGES:
        raise HTTPException(404, "página desconhecida")
    if not principal.can_page(page):
        raise HTTPException(403, f"seu perfil ({principal.role}) não acessa a página {PAGES[page][0]}")


def require_export(principal: Principal) -> None:
    if principal.role not in EXPORT_ROLES:
        raise HTTPException(403, f"seu perfil ({principal.role}) não exporta dados")


def check_filter_squads(principal: Principal, requested: list[str]) -> None:
    denied = [s for s in requested if not principal.sees_squad(s)]
    if denied:
        raise HTTPException(403, f"sem acesso ao squad {', '.join(denied)}")
