"""Etapa e "com quem está" são calculadas dos fatos; impacto na dúvida para tudo."""

from __future__ import annotations

from nexo.modules.demands.domain import impact as imp
from nexo.modules.demands.domain import model as m


def ev(i: int, type_: str, demand: str, actor: str = "gh:x", **data: object) -> dict[str, object]:
    return {
        "id": f"{i:04d}",
        "at": f"2026-11-0{1 + i % 8}T10:00:00+00:00",
        "actor": actor,
        "type": type_,
        "demand": demand,
        "data": data,
    }


def world() -> list[dict[str, object]]:
    return [
        ev(1, "demand.created", "IDEA-1", type="idea", title="Parcelas", responsible={"pm": "gh:bia"}),
        ev(2, "demand.decided", "IDEA-1", decision="build"),
        ev(
            3, "demand.created", "F-1", type="feature", title="Simulador", parent="IDEA-1", responsible={"pm": "gh:bia"}
        ),
        ev(4, "demand.created", "US-1-1", type="story", title="Ver parcelas", parent="F-1"),
    ]


def test_idea_goes_from_pm_to_split_when_it_has_features() -> None:
    demands = m.project(world()[:2])
    assert m.stage_of(demands["IDEA-1"], demands) == "idea"
    assert m.holders_of(demands["IDEA-1"], demands)[0]["who"] == "gh:bia"
    demands = m.project(world())
    assert m.stage_of(demands["IDEA-1"], demands) == "split"
    assert demands["IDEA-1"].children == ["F-1"]


def test_feature_passes_from_pm_to_tl_only_after_requisitos() -> None:
    events = world()
    demands = m.project(events)
    assert m.stage_of(demands["F-1"], demands) == "functional"
    events.append(ev(5, "demand.approved", "F-1", part="requisitos", digest="d"))
    demands = m.project(events)
    assert m.stage_of(demands["F-1"], demands) == "technical"
    assert m.holders_of(demands["F-1"], demands)[0]["role"] == "tl"


def test_story_goes_to_qa_and_dev_at_the_same_time_and_then_ready() -> None:
    events = [
        *world(),
        ev(5, "demand.assigned", "US-1-1", role="dev", who="gh:dani"),
        ev(6, "demand.assigned", "US-1-1", role="qa", who="gh:caio"),
    ]
    demands = m.project(events)
    story = demands["US-1-1"]
    assert m.stage_of(story, demands) == "refinement"
    assert {h["who"] for h in m.holders_of(story, demands)} == {"gh:dani", "gh:caio"}
    events += [ev(7, "demand.approved", "US-1-1", part="testes"), ev(8, "demand.approved", "US-1-1", part="plano")]
    demands = m.project(events)
    assert m.stage_of(demands["US-1-1"], demands) == "ready"
    events.append(ev(9, "demand.dor", "US-1-1", passed=True))
    assert m.stage_of(m.project(events)["US-1-1"]) == "build"


def test_story_path_after_build_validation_gate_pr_accept() -> None:
    events = [
        *world(),
        ev(5, "demand.assigned", "US-1-1", role="dev", who="gh:d"),
        ev(6, "demand.assigned", "US-1-1", role="qa", who="gh:q"),
    ]
    stages = []
    for i, (kind, data) in enumerate(
        [
            ("demand.state", {"state": "EmDev"}),
            ("demand.state", {"state": "AguardandoHumano"}),
            ("demand.validated", {"passed": True}),
            ("demand.state", {"state": "Concluido"}),
            ("demand.merged", {}),
            ("demand.accepted", {}),
        ]
    ):
        events.append(ev(10 + i, kind, "US-1-1", **data))
        stages.append(m.stage_of(m.project(events)["US-1-1"]))
    assert stages == ["build", "validation", "gate", "pr", "accept", "done"]


def test_proposal_blocks_and_shows_the_owner_as_holder() -> None:
    events = [
        *world(),
        ev(5, "proposal.opened", "US-1-1", id="P-1", section="funcional", text="AC-3 sem parcela zero", blocking=True),
    ]
    demands = m.project(events)
    story = demands["US-1-1"]
    assert m.blocked_by(story) == ["P-1"]
    assert any(h["role"] == "pm" and "P-1" in h["action"] for h in m.holders_of(story, demands))
    events.append(ev(6, "proposal.decided", "US-1-1", id="P-1", status="assumed"))
    demands = m.project(events)
    assert m.blocked_by(demands["US-1-1"]) == [] and [p.id for p in demands["US-1-1"].open_premises] == ["P-1"]


def test_invalidation_touches_only_that_part() -> None:
    events = [
        *world(),
        ev(5, "demand.approved", "US-1-1", part="testes"),
        ev(6, "demand.approved", "US-1-1", part="plano"),
        ev(7, "demand.invalidated", "US-1-1", part="plano"),
    ]
    story = m.project(events)["US-1-1"]
    assert story.approved("testes") and not story.approved("plano")


def test_returned_demand_goes_to_who_must_answer() -> None:
    events = [*world(), ev(5, "demand.returned", "F-1", to="pm", reason="E se recusar?")]
    demands = m.project(events)
    assert m.holders_of(demands["F-1"], demands)[0]["action"].endswith("E se recusar?")


def test_ai_finding_starts_in_triage_and_gate_bug_is_active() -> None:
    events = [
        *world(),
        ev(5, "demand.created", "FIND-1", type="finding", parent="US-1-1", triage=True),
        ev(6, "demand.created", "BUG-1", type="bug", parent="US-1-1", state="EmDev"),
    ]
    demands = m.project(events)
    assert m.stage_of(demands["FIND-1"], demands) == "triage"
    assert m.stage_of(demands["BUG-1"], demands) == "build"


def test_order_comes_from_event_id_not_from_arrival() -> None:
    events = world()
    assert m.project(list(reversed(events))).keys() == m.project(events).keys()


def task(i: str, **kw: object) -> imp.TaskRef:
    return imp.TaskRef(id=i, **kw)  # type: ignore[arg-type]


def test_impact_pauses_only_affected() -> None:
    tasks = [
        task("T-1", ac_refs=("AC-1",), done=True),
        task("T-2", ac_refs=("AC-2",), files=("src/a.py",)),
        task("T-3", ac_refs=("AC-3",), files=("src/b.py",)),
    ]
    out = imp.compute(tasks, ["AC-2"], [], [], {"AC-2": ["test_ac_2"]})
    assert out.affected == ["T-2"] and out.unaffected == ["T-3"] and out.done_kept == ["T-1"]
    assert out.tests == ["test_ac_2"] and not out.uncertain


def test_impact_overlaps_folders_and_windows_paths() -> None:
    tasks = [task("T-1", files=("src\\pag\\a.py",)), task("T-2", files=("docs/x.md",))]
    assert imp.compute(tasks, [], [], ["src/pag"]).affected == ["T-1"]


def test_impact_in_doubt_stops_everything() -> None:
    tasks = [task("T-1", ac_refs=("AC-1",)), task("T-2")]
    out = imp.compute(tasks, ["AC-9"], [], [])
    assert out.uncertain and set(out.affected) == {"T-1", "T-2"} and out.unaffected == []
    assert imp.compute([task("T-1", ac_refs=("AC-1",))], [], [], []).uncertain
