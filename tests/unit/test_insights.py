"""motor de insights — cada detector com caso positivo e negativo, estatística, estados e silêncio."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nexo.central import insights
from nexo.central.common import pid_of
from nexo.central.db import CollectorDB
from nexo.central.visibility import Principal
from nexo.kernel.finops.insights import linear_projection, parse_detector, percentile, two_proportion_p, wilson

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
CUR = "2026-09"


@pytest.fixture
def d(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[CollectorDB]:
    from nexo.central import common

    monkeypatch.setenv("NX_COLLECTOR_DB", str(tmp_path / "c.db"))
    monkeypatch.setenv("NX_COLLECTOR_PSEUDO_KEY", "k")
    common._dbs.clear()
    db = common.db()
    yield db
    db.close()
    common._dbs.clear()


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def insert(d: CollectorDB, table: str, row: dict[str, Any]) -> None:
    cols = ", ".join(row)
    d.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))  # noqa: S608


def story(d: CollectorDB, **kw: Any) -> None:
    done = kw.pop("done", NOW - timedelta(days=1))
    row = {
        "project": "p1",
        "story": f"US-{uuid.uuid4().hex[:6]}",
        "event_id": uuid.uuid4().hex,
        "at": iso(NOW),
        "squad": "sq-a",
        "stack": "python",
        "dev_pid": pid_of("bia"),
        "feature": "F-1",
        "points": 1,
        "state": "Concluido" if done else "EmDev",
        "human_gate": 0,
        "escalated": 0,
        "bugs": 0,
        "attempts": 0,
        "spec_returns": 0,
        "clarifications": 0,
        "handoffs": 0,
        "waiting_human_hours": 0,
        "cost_brl": 10.0,
        "estimate_brl": None,
        "started_at": iso((done or NOW) - timedelta(days=2)),
        "done_at": iso(done) if done else None,
        "month": (done or NOW).strftime("%Y-%m"),
    }
    row.update(kw)
    insert(d, "fact_story", row)


def step(d: CollectorDB, **kw: Any) -> None:
    at = kw.pop("when", NOW - timedelta(days=1))
    row = {
        "event_id": uuid.uuid4().hex,
        "dev_pid": pid_of("bia"),
        "project": "p1",
        "month": at.strftime("%Y-%m"),
        "squad": "sq-a",
        "stack": "python",
        "step": uuid.uuid4().hex,
        "run": "r1",
        "target": "T-1-1",
        "role": "developer",
        "tool": "claude",
        "model": "sonnet",
        "started_at": iso(at),
        "outcome": "success",
        "usage_brl": 1.0,
        "brain_version": "1.0.0",
    }
    row.update(kw)
    insert(d, "fact_step", row)


def event(d: CollectorDB, type_: str, **kw: Any) -> None:
    at = kw.pop("when", NOW - timedelta(days=1))
    row = {
        "event_id": uuid.uuid4().hex,
        "at": iso(at),
        "month": at.strftime("%Y-%m"),
        "type": type_,
        "project": "p1",
        "squad": "sq-a",
        "dev_pid": pid_of("bia"),
    }
    row.update(kw)
    insert(d, "fact_event", row)


def detector(id_: str, **params: Any) -> list[Any]:
    found = [x for x in insights.load_detectors(Path("/nao-existe")) if x.id == id_]
    assert found, id_
    det = found[0]
    det.params = {**det.params, **params}
    return [det]


def run(d: CollectorDB, id_: str, **params: Any) -> list[dict[str, Any]]:
    insights.run(d, NOW, detector(id_, **params))
    return [dict(r, evidence=json.loads(r["evidence"])) for r in d.query("SELECT * FROM insights")]


def month(delta: int) -> datetime:
    return (NOW.replace(day=10) - timedelta(days=31 * delta)).replace(day=10)


# ---------------------------------------------------------------- estatística


def test_statistics() -> None:
    assert percentile([1, 2, 3, 4, 5], 50) == 3 and percentile([10, 10, 10, 10, 10, 100], 90) == 55
    assert two_proportion_p(9, 10, 3, 10) < 0.01 and two_proportion_p(5, 10, 5, 10) == pytest.approx(0.5)
    low, high = wilson(8, 10)
    assert 0.4 < low < 0.8 < high < 1
    assert linear_projection(8, 15, 30) == 16


def test_detector_definitions_baseline_and_brain_override(tmp_path: Path) -> None:
    base = insights.load_detectors(tmp_path)
    assert [x.id for x in base] == [f"I-{n:02d}" for n in range(1, 17)]
    (tmp_path / "I-06.yaml").write_text("id: I-06\nparams: {p: 0.05}\n", encoding="utf-8")
    (tmp_path / "custom.yaml").write_text(
        "id: X-01\ntitle: Custo alto\nalgorithm: threshold\nparams: {metric: step_cost_brl, group_by: squad, op: '>',"
        " value: 5}\nmin_sample: 1\n",
        encoding="utf-8",
    )
    loaded = {x.id: x for x in insights.load_detectors(tmp_path)}
    assert loaded["I-06"].params == {"p": 0.05, "days": 30} and loaded["I-06"].systemic
    assert loaded["X-01"].algorithm == "threshold"
    with pytest.raises(ValueError, match="algoritmo desconhecido"):
        parse_detector({"id": "X", "title": "t", "algorithm": "sql_livre"}, frozenset(insights.ALGORITHMS))
    with pytest.raises(ValueError, match="parâmetros"):
        parse_detector(
            {"id": "X", "title": "t", "algorithm": "threshold", "params": {"q": {"a": 1}}},
            frozenset(insights.ALGORITHMS),
        )


# ---------------------------------------------------------------- detectores (positivo e negativo)


@pytest.mark.parametrize("cur_cost,expected", [(20.0, 1), (11.0, 0)])
def test_i01_efficiency_trend(d: CollectorDB, cur_cost: float, expected: int) -> None:
    for back in (1, 2, 3):
        for _ in range(2):
            story(d, done=month(back), cost_brl=10.0)
    for _ in range(5):
        story(d, cost_brl=cur_cost)
    found = run(d, "I-01")
    assert len(found) == expected
    if expected:
        assert found[0]["squad"] == "sq-a" and found[0]["evidence"]["brl_per_point_prev"] == 10


@pytest.mark.parametrize("outlier,expected", [(100.0, 1), (10.0, 0)])
def test_i02_outlier_story(d: CollectorDB, outlier: float, expected: int) -> None:
    for _ in range(5):
        story(d, cost_brl=10.0)
    story(d, story="US-9", cost_brl=outlier)
    found = run(d, "I-02")
    assert len(found) == expected
    if expected:
        assert found[0]["scope"] == "p1:US-9" and "p90" in found[0]["title"]


def test_i02_attempts_and_spec_returns_without_sample(d: CollectorDB) -> None:
    story(d, story="US-1", attempts=3)
    story(d, story="US-2", spec_returns=2)
    story(d, story="US-3", attempts=1)
    assert sorted(r["scope"] for r in run(d, "I-02")) == ["p1:US-1", "p1:US-2"]


@pytest.mark.parametrize("reals,expected", [([20, 20, 20, 20, 20], 1), ([11, 9, 11, 9, 10], 0)])
def test_i03_estimate_calibration(d: CollectorDB, reals: list[float], expected: int) -> None:
    for n, real in enumerate(reals):
        story(d, feature=f"F-{n}", estimate_brl=10.0, cost_brl=real)
    found = run(d, "I-03")
    assert len(found) == expected
    if expected:
        assert found[0]["systemic"] == 1 and found[0]["evidence"]["bias"] is True


@pytest.mark.parametrize("used,expected", [(8.0, 2), (2.0, 0)])
def test_i04_budget_overrun(d: CollectorDB, used: float, expected: int) -> None:
    insert(d, "budgets", {"dev": "bia", "month": CUR, "tool": "", "limit_brl": 10.0})
    step(d, usage_brl=used)
    found = run(d, "I-04")
    assert len(found) == expected
    if expected:
        dev = next(r for r in found if r["scope_kind"] == "dev")
        assert dev["dev_pid"] == pid_of("bia") and dev["purpose"] == "budget"
        assert dev["evidence"]["projection_brl"] == 16.0
        assert any(r["scope_kind"] == "squad" for r in found)


def test_i04_silent_after_day_20(d: CollectorDB) -> None:
    insert(d, "budgets", {"dev": "bia", "month": CUR, "tool": "", "limit_brl": 1.0})
    step(d, usage_brl=8.0)
    insights.run(d, NOW.replace(day=25), detector("I-04"))
    assert d.query("SELECT * FROM insights") == []


@pytest.mark.parametrize("prev_use,expected", [(10.0, 1), (50.0, 0)])
def test_i05_idle_license(d: CollectorDB, prev_use: float, expected: int) -> None:
    insert(d, "licenses", {"dev": "bia", "tool": "claude", "monthly_brl": 100.0})
    step(d, when=month(1), usage_brl=prev_use)
    step(d, usage_brl=1.0)  # usou nos últimos 30 dias
    found = run(d, "I-05")
    assert len(found) == expected
    if expected:
        assert found[0]["purpose"] == "license" and found[0]["period"] == "2026-08"


@pytest.mark.parametrize("worse_ok,expected", [(3, 1), (8, 0)])
def test_i06_bad_route(d: CollectorDB, worse_ok: int, expected: int) -> None:
    for i in range(10):
        step(d, tool="claude", model="sonnet", outcome="success" if i < 9 else "error")
        step(d, tool="copilot", model="gpt", outcome="success" if i < worse_ok else "error")
    found = run(d, "I-06")
    assert len(found) == expected
    if expected:
        assert found[0]["scope"] == "copilot/gpt|-|python" and found[0]["evidence"]["best"] == "claude/sonnet"


@pytest.mark.parametrize("wait,expected", [(10.0, 1), (2.0, 0)])
def test_i07_human_wait(d: CollectorDB, wait: float, expected: int) -> None:
    for _ in range(5):
        story(d, human_gate=1, waiting_human_hours=wait)
    assert len(run(d, "I-07")) == expected


@pytest.mark.parametrize("score,expected", [(0.88, 1), (0.5, 0)])
def test_i07_threshold_calibration(d: CollectorDB, score: float, expected: int) -> None:
    for n in range(5):
        for k, decision in enumerate(("human", "approved")):
            insert(
                d,
                "fact_gate",
                {
                    "event_id": uuid.uuid4().hex,
                    "at": iso(NOW - timedelta(days=1, minutes=-k)),
                    "month": CUR,
                    "project": "p1",
                    "story": f"US-{n}",
                    "gate": "code",
                    "decision": decision,
                    "score": score,
                    "threshold": 0.9,
                },
            )
    found = run(d, "I-07")
    assert len(found) == expected
    if expected:
        assert found[0]["scope"] == "limiar" and found[0]["systemic"] == 1


@pytest.mark.parametrize("days_after,expected", [(5, 1), (-1, 0)])
def test_i08_escape(d: CollectorDB, days_after: int, expected: int) -> None:
    done = NOW - timedelta(days=10)
    story(d, story="US-1", done=done)
    event(d, "bug.created", story="US-1", bug="BUG-1", source="tracker", when=done + timedelta(days=days_after))
    assert len(run(d, "I-08")) == expected


@pytest.mark.parametrize("high,expected", [(2, 1), (0, 0)])
def test_i09_quality_drop(d: CollectorDB, high: int, expected: int) -> None:
    for days in (1, 2, 3):
        insert(
            d,
            "fact_quality",
            {
                "event_id": uuid.uuid4().hex,
                "at": iso(NOW - timedelta(days=days)),
                "month": CUR,
                "project": "p1",
                "squad": "sq-a",
                "findings_high": high,
                "findings_critical": 0,
                "coverage": 85.0,
                "failed": 0,
            },
        )
    insert(
        d,
        "fact_quality",
        {
            "event_id": uuid.uuid4().hex,
            "at": iso(NOW - timedelta(days=10)),
            "month": CUR,
            "project": "p1",
            "squad": "sq-a",
            "findings_high": 0,
            "findings_critical": 0,
            "coverage": 85.0,
            "failed": 0,
        },
    )
    assert len(run(d, "I-09")) == expected


@pytest.mark.parametrize("after_ok,expected", [(1, 1), (5, 0)])
def test_i10_brain_change(d: CollectorDB, after_ok: int, expected: int) -> None:
    for i in range(5):
        step(d, brain_version="1.0.0", when=NOW - timedelta(days=20, minutes=i))
        step(
            d,
            brain_version="1.1.0",
            when=NOW - timedelta(days=2, minutes=i),
            outcome="success" if i < after_ok else "error",
        )
    found = run(d, "I-10")
    assert len(found) == expected
    if expected:
        assert found[0]["evidence"]["before"] == "1.0.0" and found[0]["evidence"]["after"] == "1.1.0"


@pytest.mark.parametrize("handoffs,expected", [(3, 1), (2, 0)])
def test_i11_tight_quota(d: CollectorDB, handoffs: int, expected: int) -> None:
    for _ in range(handoffs):
        event(d, "run.handoff", tool="claude", kind="quota")
    found = run(d, "I-11")
    assert len(found) == expected
    if expected:  # RN-105: agregado por ferramenta, sem pessoa
        assert found[0]["scope"] == "claude" and found[0]["dev_pid"] is None
        assert found[0]["evidence"] == {"devs_affected": 1, "handoffs": 3}


@pytest.mark.parametrize("clarifications,expected", [(3, 1), (1, 0)])
def test_i12_fragile_spec(d: CollectorDB, clarifications: int, expected: int) -> None:
    story(d, feature="F-7", clarifications=clarifications, done=None)
    story(d, feature="F-7", done=None)
    assert len(run(d, "I-12")) == expected


@pytest.mark.parametrize("nexo_stories,expected", [(2, 1), (5, 0)])
def test_i13_adoption(d: CollectorDB, nexo_stories: int, expected: int) -> None:
    insert(d, "tracker_totals", {"squad": "sq-a", "month": "2026-08", "stories": 10})
    for _ in range(nexo_stories):
        story(d, done=month(1) + timedelta(days=2))
    assert len(run(d, "I-13")) == expected


@pytest.mark.parametrize("diff,expected", [(10.0, 1), (3.0, 0)])
def test_i14_invoice_divergence(d: CollectorDB, diff: float, expected: int) -> None:
    insert(
        d,
        "invoices",
        {
            "tool": "claude",
            "month": "2026-08",
            "amount_brl": 100,
            "measured_brl": 100 + diff,
            "diff_pct": diff,
            "at": time.time(),
        },
    )
    assert len(run(d, "I-14")) == expected


@pytest.mark.parametrize("lag,expected", [(80.0, 1), (10.0, 0)])
def test_i15_telemetry_delay(d: CollectorDB, lag: float, expected: int) -> None:
    insert(
        d,
        "device_lag",
        {"device_id": "dev-1", "dev_pid": "x", "lag_hours": lag, "max_lag_hours": lag, "last_seen": time.time()},
    )
    assert len(run(d, "I-15")) == expected


def test_i15_divergent_anchor(d: CollectorDB) -> None:
    insert(
        d,
        "audit",
        {
            "id": "a1",
            "at": time.time(),
            "actor": "bia",
            "action": "anchor.divergent",
            "detail": json.dumps({"device_id": "dev-9", "seq": 4}),
        },
    )
    found = run(d, "I-15")
    assert len(found) == 1 and "âncora divergente" in found[0]["title"]


def test_i16_token_hogs(d: CollectorDB) -> None:
    now = datetime.now(UTC).isoformat()
    for i in range(6):
        big = i == 5
        insert(
            d,
            "fact_step",
            {
                "event_id": f"s{i}",
                "project": "p",
                "story": f"US-{i}",
                "squad": "sq",
                "started_at": now,
                "tokens_in": 90_000 if big else 10_000,
                "tokens_out": 5_000,
                "cache_read": 1_000 if big else 20_000,
                "cache_write": 0,
            },
        )
    found = run(d, "I-16")
    assert len(found) == 1 and "US-5" in found[0]["title"] and "acerto do cache 1%" in found[0]["title"]


def test_threshold_detector_from_brain(d: CollectorDB, tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text(
        "id: X-01\ntitle: Custo alto\nalgorithm: threshold\nparams: {metric: step_cost_brl, group_by: squad, op: '>',"
        " value: 5}\nmin_sample: 1\n",
        encoding="utf-8",
    )
    for _ in range(6):
        step(d, usage_brl=1.0)
    step(d, usage_brl=1.0, squad="sq-b")
    det = [x for x in insights.load_detectors(tmp_path) if x.id == "X-01"]
    insights.run(d, NOW, det)
    rows = d.query("SELECT scope, evidence FROM insights")
    assert [r["scope"] for r in rows] == ["sq-a"] and json.loads(rows[0]["evidence"])["value"] == 6
    bad = parse_detector(
        {"id": "X-02", "title": "t", "algorithm": "threshold", "params": {"metric": "dev_names"}},
        frozenset(insights.ALGORITHMS),
    )
    assert "métrica fora do catálogo" in insights.run(d, NOW, [bad])["errors"]["X-02"]


# ---------------------------------------------------------------- estados, silêncio e visibilidade


def test_states_reason_and_silence(d: CollectorDB) -> None:
    lead = Principal("lia", "lead", leads={"sq-a"})
    other = Principal("rui", "lead", leads={"sq-b"})
    for _ in range(5):
        story(d, human_gate=1, waiting_human_hours=10)
    insights.run(d, NOW, detector("I-07"))
    item = insights.list_for(d, lead)[0]
    assert item["state"] == "aberto" and insights.list_for(d, other) == []
    with pytest.raises(Exception, match="motivo"):
        insights.set_state(d, lead, item["id"], "descartado", "curto")
    assert insights.set_state(d, lead, item["id"], "reconhecido", "")["state"] == "reconhecido"
    with pytest.raises(Exception, match="transição inválida"):
        insights.set_state(d, lead, item["id"], "aberto", "")
    insights.set_state(d, lead, item["id"], "descartado", "sazonal: feriado prolongado")
    history = d.query("SELECT from_state, to_state FROM insight_history ORDER BY at")
    assert [(h["from_state"], h["to_state"]) for h in history] == [
        ("aberto", "reconhecido"),
        ("reconhecido", "descartado"),
    ]
    dismissed = datetime(2026, 9, 28, 12, tzinfo=UTC)
    d.execute("UPDATE insights SET updated_at = ?", (dismissed.timestamp(),))
    assert insights.run(d, NOW + timedelta(days=3), detector("I-07", days=90))["silenced"] == 1  # mesma chave
    muted = insights.run(d, datetime(2026, 10, 2, 12, tzinfo=UTC), detector("I-07", days=90))
    assert muted["silenced"] == 1  # outubro (outra chave), mas dentro dos 7 dias do descarte
    later = insights.run(d, datetime(2026, 10, 10, 12, tzinfo=UTC), detector("I-07", days=90))
    assert later["opened"] == 1  # passados 7 dias, volta a avisar


def test_individual_insights_follow_the_matrix(d: CollectorDB) -> None:
    insert(d, "budgets", {"dev": "bia", "month": CUR, "tool": "", "limit_brl": 10.0})
    insert(d, "org_members", {"dev": "bia", "squad": "sq-a", "relation": "member"})
    step(d, usage_brl=8.0)
    insights.run(d, NOW, detector("I-04"))

    def dev_items(p: Principal) -> list[str]:
        return [i["scope_kind"] for i in insights.list_for(d, p) if i["individual"]]

    assert dev_items(Principal("bia", "dev", member={"sq-a"})) == ["dev"]  # o próprio dev
    assert dev_items(Principal("gil", "manager", manages={"sq-a"})) == ["dev"]  # gestor do squad (orçamento)
    assert dev_items(Principal("caio", "dev", member={"sq-a"})) == []  # colega não vê
    assert dev_items(Principal("dir", "director")) == []  # diretoria: só agregado
    assert dev_items(Principal("nexo-cli", "nexo")) == []
    with pytest.raises(Exception, match="dev só atualiza"):
        squad_item = next(i for i in insights.list_for(d, Principal("caio", "dev", member={"sq-a"})))
        insights.set_state(d, Principal("caio", "dev", member={"sq-a"}), squad_item["id"], "reconhecido", "")
