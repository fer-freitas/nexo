from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nexo.kernel.backlog import Backlog, normalize, overlaps, validate, waves
from nexo.kernel.spec import questions as q
from nexo.kernel.spec.gherkin import lint_feature, render_feature
from nexo.kernel.spec.model import TEST_TYPES, Scenario, Spec, apply_patch, readiness

FULL = {
    "objective": "Permitir consultar versão",
    "problem": "Não sabemos a versão em produção",
    "personas": ["SRE"],
    "scope_in": ["função"],
    "scope_out": ["UI"],
    "acceptance": [{"id": "AC-1", "title": "retorna", "given": ["x"], "when": ["chamo"], "then": ["ok"]}],
    "contracts_not_applicable": "sem API",
    "nfr": {"performance": "< 1 ms", "security": "nada sensível", "availability": "n/a lib"},
    "data_class": "internal",
    "dependencies_none": True,
    "test_strategy": {t: "sim" for t in TEST_TYPES},
    "risks_reviewed": True,
}


def spec(**kw) -> Spec:  # type: ignore[no-untyped-def]
    return Spec(id="F-1", title="t", **kw)


def test_readiness_empty_and_full() -> None:
    r = readiness(spec(), 0)
    assert r.score == 0 and not r.ready and len(r.missing) == 10
    full = readiness(spec(**FULL), 0)
    assert full.score == 100 and full.ready
    blocked = readiness(spec(**FULL), 1)
    assert blocked.score == 100 and not blocked.ready


def test_readiness_partial_never_reports_100() -> None:
    data = {**FULL, "risks_reviewed": False}
    r = readiness(spec(**data), 0)
    assert r.score < 100 and [i.key for i in r.missing] == ["risks"]


def test_incomplete_scenario_and_test_strategy() -> None:
    data = {**FULL, "acceptance": [{"id": "AC-1", "title": "x", "when": ["a"]}]}
    assert "acceptance" in [i.key for i in readiness(spec(**data), 0).missing]
    data = {**FULL, "test_strategy": {"unit": "x"}}
    assert "test_strategy" in [i.key for i in readiness(spec(**data), 0).missing]


def test_apply_patch_validates() -> None:
    new, changed = apply_patch(spec(), {"objective": "abc", "title": "t"})
    assert new.objective == "abc" and changed == ["objective"]
    with pytest.raises(ValueError, match="desconhecidas"):
        apply_patch(spec(), {"id": "X"})
    with pytest.raises(ValueError, match="inválido"):
        apply_patch(spec(), {"data_class": "top-secret"})
    with pytest.raises(ValueError):
        apply_patch(spec(), {"acceptance": [{"id": "X-1", "title": "a"}]})
    with pytest.raises(ValueError):
        apply_patch(spec(), {"risks": [{"area": "space", "description": "x"}]})


def test_questions_merge_dedup_limit_and_answer() -> None:
    qs = q.merge_new(
        [],
        [
            {"text": "A?", "severity": "optional"},
            {"text": "B?", "severity": "blocking"},
            {"text": "a?"},
            {"text": ""},
            {"text": "C?", "severity": "weird"},
        ],
    )
    assert [x.id for x in qs] == ["Q-1", "Q-2", "Q-3"] and qs[0].text == "B?"
    assert q.blocking_open(qs) == 1
    qs = q.answer(qs, "Q-1", "sim")
    assert q.blocking_open(qs) == 0 and len(q.open_questions(qs)) == 2
    with pytest.raises(ValueError):
        q.answer(qs, "Q-9", "x")
    many = q.merge_new(qs, [{"text": f"Nova {i}?"} for i in range(20)])
    assert len(many) == 3 + q.MAX_PER_ROUND and many[-1].id == "Q-13"
    again = q.merge_new(many, [{"text": "B?"}])
    assert len(again) == len(many)


def backlog(tasks: list[dict], stories: list[dict] | None = None) -> Backlog:  # type: ignore[type-arg]
    return Backlog.model_validate(
        {"feature": "F-1", "stories": stories or [{"id": "US-1-1", "title": "s", "ac_refs": ["AC-1"]}], "tasks": tasks}
    )


def t(i: str, **kw) -> dict:  # type: ignore[no-untyped-def,type-arg]
    return {"id": i, "story": "US-1-1", "kind": "backend", "title": i, "files_hint": [f"{i}.py"], **kw}


def test_backlog_valid_and_waves() -> None:
    b = backlog(
        [
            t("T-1", kind="contract"),
            t("T-2", depends_on=["T-1"]),
            t("T-3", depends_on=["T-1"]),
            t("T-4", depends_on=["T-2", "T-3"]),
        ]
    )
    assert validate(b, ["AC-1"]).ok
    assert waves(b) == [["T-1"], ["T-2", "T-3"], ["T-4"]]


def test_waves_split_file_overlap() -> None:
    b = backlog(
        [
            t("T-1", files_hint=["a.py"]),
            t("T-2", files_hint=["a.py"]),
            t("T-3", files_hint=["src/*.py"]),
            t("T-4", files_hint=["src/x.py"]),
        ]
    )
    assert waves(b) == [["T-1", "T-3"], ["T-2", "T-4"]]
    assert overlaps(["src/*.py"], ["src/x.py"]) and not overlaps(["a"], ["b"])


def test_backlog_errors() -> None:
    b = backlog(
        [
            t("T-1", depends_on=["T-2"]),
            t("T-2", depends_on=["T-1"]),
            t("T-3", size="L"),
            t("T-3"),
            t("T-4", story="US-9", depends_on=["T-9"], kind="weird", files_hint=[str(i) for i in range(9)]),
        ],
        stories=[{"id": "US-1-1", "title": "s", "ac_refs": []}, {"id": "US-1-2", "title": "vazia"}],
    )
    errors = " | ".join(validate(b, ["AC-1"]).errors)
    for fragment in (
        "duplicado",
        "ciclo",
        "grande demais",
        "não existe",
        "inexistente",
        "tipo inválido",
        "arquivos previstos",
        "critérios sem história",
        "história sem tarefas",
    ):
        assert fragment in errors, fragment
    with pytest.raises(ValueError):
        waves(backlog([t("T-1", depends_on=["T-2"]), t("T-2", depends_on=["T-1"])]))


def test_contract_first_rule_and_human_gate() -> None:
    b = backlog([t("T-1", kind="contract"), t("T-2"), t("T-3", kind="migration", depends_on=["T-1"])])
    check = validate(b, ["AC-1"])
    assert any("T-2" in e and "contrato" in e for e in check.errors)
    n = normalize(b)
    assert [x.human_gate for x in n.tasks] == [False, False, True]
    assert validate(backlog([t("T-1", files_hint=[])]), ["AC-1"]).warnings
    task = n.tasks[2].to_task("python", "internal")
    assert task.human_gate and task.story == "US-1-1"


def test_gherkin_render_and_lint() -> None:
    sc = Scenario(
        id="AC-1", title="Estorno", given=["pagamento aprovado", "saldo"], when=["peço estorno"], then=["recebo 200"]
    )
    text = render_feature("US-1-1", "Estornar", [sc])
    assert "@AC-1" in text and "    E saldo" in text and "Então recebo 200" in text and "# language: pt" in text
    assert lint_feature(text) == []
    broken = text.replace("    Então recebo 200\n", "")
    assert lint_feature(broken) == ["@AC-1: sem passo 'Então'"]
    assert "sem 'Funcionalidade:'" in lint_feature("nada")


@given(st.lists(st.tuples(st.integers(1, 6), st.lists(st.integers(1, 6), max_size=3)), min_size=1, max_size=6))
def test_property_waves_respect_dependencies(spec_list: list[tuple[int, list[int]]]) -> None:
    ids = sorted({i for i, _ in spec_list})
    deps = {i: sorted({d for j, ds in spec_list if j == i for d in ds if d < i}) for i in ids}
    b = backlog([t(f"T-{i}", depends_on=[f"T-{d}" for d in deps[i] if d in ids]) for i in ids])
    order = {task: n for n, wave in enumerate(waves(b)) for task in wave}
    for task in b.tasks:
        for d in task.depends_on:
            assert order[d] < order[task.id]


def test_story_without_acceptance_criteria_is_an_error() -> None:
    """F49 (achado no e2e Angular): o planejador devolveu história sem critério e ela só parava na DoR."""
    stories = [
        {"id": "US-1-1", "title": "a", "ac_refs": ["AC-1"]},
        {"id": "US-1-2", "title": "b", "ac_refs": []},
        {"id": "US-1-3", "title": "c", "ac_refs": ["AC-7"]},
    ]
    tasks = [t("T-1"), t("T-2", story="US-1-2"), t("T-3", story="US-1-3")]
    errors = " | ".join(validate(backlog(tasks, stories), ["AC-1"]).errors)
    assert "US-1-2: história sem critérios de aceite" in errors
    assert "US-1-3: critérios inexistentes na spec: AC-7" in errors
