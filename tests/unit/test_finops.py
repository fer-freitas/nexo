from __future__ import annotations

from datetime import date

import pytest

from nexo.kernel.finops.costs import BudgetStatus, Pricing, UsageLine, allocate, usage_cost_brl, weight
from nexo.kernel.finops.metrics import StoryFacts, estimate, success_rate, summarize

PRICING = Pricing.from_mapping(
    {
        "fx": [{"usd_brl": 5.0, "valid_from": "2026-01-01"}, {"usd_brl": 6.0, "valid_from": "2026-09-15"}],
        "subscriptions": {
            "claude": [{"usd_month": 100, "valid_from": "2026-01-01"}, {"usd_month": 200, "valid_from": "2026-10-01"}],
            "copilot": {"usd_month": 75, "valid_from": "2026-01-01", "unit_price_usd": 0.04},
            "devin": {"usd_month": 180, "valid_from": "2026-01-01", "overage_brl": 6.0, "included_units": 10},
        },
        "devs": {"ana": {"claude": 50}},
    }
)


def line(step: str, tool: str, day: str, dev: str = "bia", api: float = 0.0, **units: float) -> UsageLine:
    return UsageLine(step, dev, tool, date.fromisoformat(day), "T-1", dict(units), api)


def test_fx_and_subscription_validity() -> None:
    assert PRICING.rate(date(2026, 9, 1)) == 5.0 and PRICING.rate(date(2026, 9, 20)) == 6.0
    assert PRICING.monthly_fixed_brl("bia", "claude", date(2026, 9, 1)) == 500.0
    assert PRICING.monthly_fixed_brl("bia", "claude", date(2026, 10, 2)) == 1200.0
    assert PRICING.monthly_fixed_brl("ana", "claude", date(2026, 9, 1)) == 250.0  # contrato individual
    assert PRICING.monthly_fixed_brl("bia", "cursor", date(2026, 9, 1)) == 0.0
    with pytest.raises(ValueError):
        PRICING.rate(date(2025, 1, 1))


def test_usage_cost_at_list_price() -> None:
    assert usage_cost_brl(line("a", "claude", "2026-09-01", api=2.0), PRICING) == 10.0
    assert usage_cost_brl(line("b", "copilot", "2026-09-01", premium_request=10), PRICING) == 2.0
    assert usage_cost_brl(line("c", "devin", "2026-09-01", acu=3), PRICING) == 18.0


def test_allocation_shares_and_devin_overage() -> None:
    lines = [
        line("a", "claude", "2026-09-01", api=1.0),
        line("b", "claude", "2026-09-02", api=3.0),
        line("c", "devin", "2026-09-01", acu=8),
        line("d", "devin", "2026-09-03", acu=5),
    ]
    alloc = {a.step_id: a for a in allocate(lines, PRICING)}
    assert alloc["a"].fixed_brl == 125.0 and alloc["b"].fixed_brl == 375.0
    assert alloc["c"].variable_brl == 0.0 and alloc["d"].variable_brl == 18.0  # 3 ACU acima dos 10 inclusos
    assert round(alloc["c"].fixed_brl + alloc["d"].fixed_brl, 2) == 900.0
    assert weight(line("x", "copilot", "2026-09-01", premium_request=4)) == 4
    empty = allocate([line("z", "claude", "2026-09-01")], PRICING)
    assert empty[0].fixed_brl == 500.0


def test_devin_included_acus_derived_from_subscription() -> None:
    derived = Pricing.from_mapping(
        {
            "fx": [{"usd_brl": 5.4, "valid_from": "2026-01-01"}, {"usd_brl": 6.0, "valid_from": "2026-10-01"}],
            "subscriptions": {"devin": {"usd_month": 180, "valid_from": "2026-01-01", "overage_brl": 6.0}},
            "devs": {"ana": {"devin": 90}},
        }
    )
    assert derived.included_units("bia", "devin", date(2026, 9, 1)) == 162.0  # 180 × 5,40 ÷ 6
    assert derived.included_units("bia", "devin", date(2026, 10, 1)) == 180.0  # acompanha o câmbio
    assert derived.included_units("ana", "devin", date(2026, 9, 1)) == 81.0  # contrato individual
    assert PRICING.included_units("bia", "devin", date(2026, 9, 1)) == 10  # valor explícito prevalece
    assert PRICING.included_units("bia", "claude", date(2026, 9, 1)) is None
    allocs = allocate([line("a", "devin", "2026-09-01", acu=150), line("b", "devin", "2026-09-02", acu=20)], derived)
    assert allocs[0].variable_brl == 0.0 and allocs[1].variable_brl == 48.0  # 8 ACUs além de 162 × R$ 6


def test_budget_status() -> None:
    st = BudgetStatus(100.0, 75.0, {"claude": 60.0, "devin": 15.0}, {"devin": 15.0})
    assert st.ratio == 0.75 and st.tool_ok("claude") and not st.tool_ok("devin")
    assert st.crossed((0.7, 0.9), 0.5) == [0.7]
    assert not BudgetStatus(100.0, 100.0, {}, {}).tool_ok("claude")
    assert BudgetStatus(None, 999.0, {}, {}).tool_ok("claude")


def test_metrics_summary() -> None:
    facts = [
        StoryFacts("US-1", "python", 3, 30.0, "Concluido", lead_hours=10, cycle_hours=4, tokens=3000),
        StoryFacts("US-2", "python", 5, 70.0, "Concluido", human_gate=True, bugs=1, attempts=1, waiting_human_hours=2),
        StoryFacts("US-3", "dotnet", 2, 10.0, "Concluido", clarifications=1),
        StoryFacts("US-4", "python", 3, 5.0, "Escalado", escalated=True),
    ]
    r = summarize(facts)
    assert r.done == 3 and r.e2e_without_human == round(2 / 3, 4) and r.first_pass_yield == round(1 / 3, 4)
    assert r.brl_per_point == 11.0 and r.brl_per_story == 36.67 and r.escalation_rate == 0.25
    assert r.planning_accuracy == round(2 / 3, 4) and r.waiting_human_h == 2.0
    assert r.by_stack["python"]["stories"] == 2 and r.by_stack["dotnet"]["brl_per_point"] == 5.0
    assert summarize([]).e2e_without_human is None


def test_success_rate_and_estimate() -> None:
    assert success_rate(0, 0) == 0.7 and success_rate(10, 10) == 0.85
    prior = estimate(10, [12.0], [], 15.0)
    assert prior.confidence == "baixa" and prior.brl == 150.0 and prior.brl_low == 75.0
    est = estimate(10, [10.0, 12.0, 11.0, 13.0, 9.0], [0.5, 0.7], 15.0)
    assert est.confidence == "média" and est.brl == 110.0 and est.brl_low <= 110.0 <= est.brl_high
    assert est.hours == 6.0
