"""Preços, câmbio, custo de uso, rateio de assinaturas e orçamento."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Pesos de rateio: a unidade que mede "quanto da assinatura" cada passo consumiu. A assinatura pode trocar
# pela chave `unit`.
WEIGHT_UNIT = {"claude": "api_equiv_usd", "copilot": "premium_request", "devin": "acu"}


@dataclass(frozen=True)
class FxRate:
    usd_brl: float
    valid_from: date


@dataclass(frozen=True)
class Subscription:
    tool: str
    usd_month: float
    valid_from: date
    included_units: float | None = None  # ex.: ACUs inclusos no plano do Devin
    unit_price_usd: float = 0.0  # preço de tabela por unidade (ex.: premium request)
    overage_brl: float = 0.0  # custo por unidade além do incluso em R$ (contratos antigos; prefira overage_usd)
    credit_price_usd: float = 0.0  # Copilot cobrado em AI Credits: US$ por crédito (0 = cobra por premium request)
    overage_usd: float = 0.0  # custo por unidade além do incluso em US$ (convertido pelo câmbio do dia)
    unit: str = ""  # unidade de cobrança/rateio (vazio = padrão da ferramenta)


@dataclass(frozen=True)
class Pricing:
    fx: tuple[FxRate, ...]
    subscriptions: tuple[Subscription, ...]
    dev_overrides: dict[str, dict[str, float]] = field(default_factory=dict)  # dev → ferramenta → usd_month

    @staticmethod
    def from_mapping(data: dict[str, Any]) -> Pricing:
        fx = tuple(
            sorted(
                (FxRate(float(f["usd_brl"]), _date(f.get("valid_from", "2000-01-01"))) for f in data.get("fx") or []),
                key=lambda f: f.valid_from,
            )
        )
        subs: list[Subscription] = []
        for tool, raw in (data.get("subscriptions") or {}).items():
            versions = raw if isinstance(raw, list) else [raw]
            for v in versions:
                subs.append(
                    Subscription(
                        str(tool),
                        float(v.get("usd_month", 0)),
                        _date(v.get("valid_from", "2000-01-01")),
                        float(v["included_units"]) if v.get("included_units") is not None else None,
                        float(v.get("unit_price_usd", 0)),
                        float(v.get("overage_brl", 0)),
                        float(v.get("credit_price_usd", 0)),
                        float(v.get("overage_usd", 0)),
                        str(v.get("unit") or ""),
                    )
                )
        overrides = {
            str(d): {str(t): float(x) for t, x in (tools or {}).items()}
            for d, tools in (data.get("devs") or {}).items()
        }
        return Pricing(fx, tuple(sorted(subs, key=lambda s: s.valid_from)), overrides)

    def rate(self, day: date) -> float:
        valid = [f for f in self.fx if f.valid_from <= day]
        if not valid:
            raise ValueError(f"sem cotação USD→BRL vigente em {day}")
        return valid[-1].usd_brl

    def subscription(self, tool: str, day: date) -> Subscription | None:
        valid = [s for s in self.subscriptions if s.tool == tool and s.valid_from <= day]
        return valid[-1] if valid else None

    def monthly_fixed_brl(self, dev: str, tool: str, day: date) -> float:
        sub = self.subscription(tool, day)
        if sub is None:
            return 0.0
        usd = self.dev_overrides.get(dev, {}).get(tool, sub.usd_month)
        return round(usd * self.rate(day), 2)

    def overage_brl(self, tool: str, day: date) -> float:
        """Custo em R$ de uma unidade além do incluso: `overage_usd` × câmbio do dia, ou `overage_brl` fixo."""
        sub = self.subscription(tool, day)
        if sub is None:
            return 0.0
        if sub.overage_usd:
            return round(sub.overage_usd * self.rate(day), 4)
        return sub.overage_brl

    def included_units(self, dev: str, tool: str, day: date) -> float | None:
        """Unidades inclusas no mês. Sem valor explícito, deriva da assinatura: US$ × câmbio ÷ R$ por unidade
        (ex.: Devin US$ 180 × 5,40 ÷ R$ 6 = 162 ACUs) — acompanha câmbio e contrato vigentes (A7)."""
        sub = self.subscription(tool, day)
        if sub is None:
            return None
        if sub.included_units is not None:
            return sub.included_units
        over = self.overage_brl(tool, day)
        if over <= 0:
            return None
        return round(self.monthly_fixed_brl(dev, tool, day) / over, 2)


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


@dataclass
class UsageLine:
    """Consumo agregado de um passo de agente."""

    step_id: str
    dev: str
    tool: str
    day: date
    target: str  # tarefa/história
    units: dict[str, float]  # tokens_in, tokens_out, premium_request, acu…
    api_equiv_usd: float = 0.0
    estimated: bool = False
    project: str = ""


def billing_unit(tool: str, sub: Subscription | None) -> str | None:
    return (sub.unit if sub is not None and sub.unit else None) or WEIGHT_UNIT.get(tool)


def usage_cost_brl(line: UsageLine, pricing: Pricing) -> float:
    """Custo de uso a preço de tabela (base do orçamento)."""
    fx = pricing.rate(line.day)
    sub = pricing.subscription(line.tool, line.day)
    unit = billing_unit(line.tool, sub)
    if line.tool == "claude" or line.api_equiv_usd:
        return round(line.api_equiv_usd * fx, 4)
    over = pricing.overage_brl(line.tool, line.day)
    if over and unit and unit in line.units:  # ex.: Devin, ACU a preço de excedente
        return round(line.units[unit] * over, 4)
    if line.tool == "devin":
        return round(line.units.get("acu", 0.0) * over, 4)
    if sub is not None and sub.credit_price_usd and "ai_credits" in line.units:
        return round(line.units["ai_credits"] * sub.credit_price_usd * fx, 4)
    unit_price = sub.unit_price_usd if sub else 0.0
    # só a unidade de cobrança entra no preço; tokens são informativos (sem unidade conhecida, soma as unidades)
    quantity = line.units.get(unit, 0.0) if unit and unit in line.units else sum(line.units.values())
    return round(quantity * unit_price * fx, 4)


@dataclass
class Allocation:
    step_id: str
    fixed_brl: float
    variable_brl: float

    @property
    def total(self) -> float:
        return round(self.fixed_brl + self.variable_brl, 4)


def weight(line: UsageLine, unit: str | None = None) -> float:
    unit = unit or WEIGHT_UNIT.get(line.tool, "")
    if unit == "api_equiv_usd" or (not unit and line.api_equiv_usd):
        return line.api_equiv_usd
    return line.units.get(unit, sum(line.units.values()))


def allocate(lines: list[UsageLine], pricing: Pricing) -> list[Allocation]:
    """Rateio mensal por dev × ferramenta: fixo proporcional ao peso + excedente (ACU além do incluso)."""
    out: list[Allocation] = []
    groups: dict[tuple[str, str, str], list[UsageLine]] = {}
    for line in lines:
        groups.setdefault((line.dev, line.tool, line.day.strftime("%Y-%m")), []).append(line)
    for (dev, tool, _), items in groups.items():
        items.sort(key=lambda x: (x.day, x.step_id))
        first_day = items[0].day
        fixed = pricing.monthly_fixed_brl(dev, tool, first_day)
        sub = pricing.subscription(tool, first_day)
        unit = billing_unit(tool, sub)
        total_w = sum(weight(i, unit) for i in items)
        included = pricing.included_units(dev, tool, first_day)
        overage = pricing.overage_brl(tool, first_day)
        used = 0.0
        for item in items:
            share = (weight(item, unit) / total_w) if total_w else 1 / len(items)
            variable = 0.0
            if included is not None and sub is not None:
                units = weight(item, unit)
                over = max(0.0, used + units - included) - max(0.0, used - included)
                variable = over * overage
                used += units
            out.append(Allocation(item.step_id, round(fixed * share, 4), round(variable, 4)))
    return out


@dataclass(frozen=True)
class BudgetStatus:
    limit_brl: float | None
    spent_brl: float
    by_tool: dict[str, float]
    tool_limits: dict[str, float]
    units: dict[str, dict[str, float]] = field(default_factory=dict)  # ex.: devin → {used, included} em ACU

    @property
    def ratio(self) -> float:
        return self.spent_brl / self.limit_brl if self.limit_brl else 0.0

    def tool_ok(self, tool: str) -> bool:
        if self.limit_brl is not None and self.spent_brl >= self.limit_brl:
            return False
        limit = self.tool_limits.get(tool)
        return limit is None or self.by_tool.get(tool, 0.0) < limit

    def crossed(self, levels: tuple[float, ...], previous_ratio: float) -> list[float]:
        return [lvl for lvl in levels if previous_ratio < lvl <= self.ratio]
