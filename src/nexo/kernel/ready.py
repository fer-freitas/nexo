"""Definition of Ready: o modelo único de refinamento e as regras que o portão confere, sem IA.

O refinamento de cada história fica em `.nexo/specs/<feature>/refinamento/<US>.yaml` (skill `definition-of-ready`).
O portão confere presença, formato e números; o mérito técnico continua com o tech lead.

Performance é um teste do fluxo: os limites do refinamento viram os thresholds do teste de carga (k6), e o resultado
do teste é comparado com os mesmos limites.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MODEL_VERSION = 1
POINTS = (1, 2, 3, 5, 8)
DEPLOY_STRATEGIES = ("canario", "blue_green", "rolling", "feature_flag", "pacote_versionado")  # pacote: biblioteca
RISK_LEVELS = ("baixo", "medio", "alto")
BLOCK_LEVELS = ("high", "critical")
VERIFICATIONS = ("teste", "revisao", "metrica")
LOAD_TYPES = ("rps", "mensagens_por_segundo", "usuarios")
INTEGRATION_KINDS = frozenset({"backend", "integration", "contract", "migration"})
MIN_JUSTIFICATION = 20
DURATION = re.compile(r"^\d+(s|m|h)$")


@dataclass
class ReadyReport:
    story: str
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.problems


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def check(
    refinement: dict[str, Any] | None,
    story: str,
    ac_ids: list[str],
    task_kinds: list[str],
    floors: dict[str, float] | None = None,
) -> ReadyReport:
    """Confere o refinamento de uma história contra o modelo único. `floors`: pisos da org (cobertura, mutação)."""
    rep = ReadyReport(story)
    p = rep.problems.append
    if not refinement:
        p("refinamento ausente (nexo ready <história> --init cria o modelo)")
        return rep
    floors = floors or {}
    if refinement.get("versao_modelo") != MODEL_VERSION:
        p(f"versao_modelo deve ser {MODEL_VERSION}")
    if _text(refinement.get("historia")) != story:
        p(f"historia deve ser {story}")
    listed = [str(a) for a in refinement.get("criterios_de_aceite") or []]
    missing = sorted(set(ac_ids) - set(listed))
    unknown = sorted(set(listed) - set(ac_ids))
    if not ac_ids:
        p("a história não tem critérios de aceite")
    if missing:
        p(f"critérios de aceite da história fora do refinamento: {', '.join(missing)}")
    if unknown:
        p(f"critérios de aceite que não existem na história: {', '.join(unknown)}")

    tech = refinement.get("criterios_tecnicos") or []
    if not tech:
        p("pelo menos um critério técnico (CT-n)")
    for i, ct in enumerate(tech, 1):
        ct = ct if isinstance(ct, dict) else {}
        cid = _text(ct.get("id")) or f"#{i}"
        if not re.fullmatch(r"CT-\d+", cid):
            p(f"critério técnico {cid}: id no formato CT-n")
        if len(_text(ct.get("descricao"))) < 10:
            p(f"critério técnico {cid}: descrição")
        if ct.get("verificacao") not in VERIFICATIONS:
            p(f"critério técnico {cid}: verificacao deve ser {' | '.join(VERIFICATIONS)}")

    tests = refinement.get("testes") or {}
    unit = tests.get("unitario") or {}
    cov = _num(unit.get("cobertura_minima_linhas_alteradas"))
    if cov is None or not 0 < cov <= 100:
        p("testes.unitario.cobertura_minima_linhas_alteradas entre 1 e 100")
    elif cov < floors.get("coverage", 0):
        p(f"cobertura mínima {cov:g} abaixo do piso da org ({floors['coverage']:g})")

    kinds = set(task_kinds)
    integration = tests.get("integracao") or []
    if kinds & INTEGRATION_KINDS and not integration:
        p("testes.integracao: pelo menos um cenário (a história toca API, fila, banco ou contrato)")
    for i, ci in enumerate(integration, 1):
        ci = ci if isinstance(ci, dict) else {}
        if not re.fullmatch(r"CI-\d+", _text(ci.get("id"))) or len(_text(ci.get("cenario"))) < 10:
            p(f"testes.integracao #{i}: id CI-n e cenário")

    contract = tests.get("contrato") or {}
    if "contract" in kinds and not contract.get("aplica"):
        p("testes.contrato.aplica deve ser true (a história tem tarefa de contrato)")
    if contract.get("aplica") and not _text(contract.get("artefato")):
        p("testes.contrato.artefato: caminho do OpenAPI/AsyncAPI")

    _check_performance(tests.get("performance") or {}, p)

    mut = _num((tests.get("mutacao") or {}).get("score_minimo"))
    if mut is None or not 0 < mut <= 100:
        p("testes.mutacao.score_minimo entre 1 e 100")
    elif mut < floors.get("mutation", 0):
        p(f"score de mutação {mut:g} abaixo do piso da org ({floors['mutation']:g})")
    if (tests.get("seguranca") or {}).get("bloqueia_em") not in BLOCK_LEVELS:
        p(f"testes.seguranca.bloqueia_em deve ser {' | '.join(BLOCK_LEVELS)}")

    obs = refinement.get("observabilidade") or {}
    if obs.get("aplica", True) is False:  # ex.: biblioteca sem processo próprio; quem a usa observa
        if len(_text(obs.get("justificativa_nao_aplica"))) < 10:
            p("observabilidade.justificativa_nao_aplica: por que não se aplica")
    else:
        for key in ("logs", "metricas", "alarmes"):
            if not obs.get(key):
                p(f"observabilidade.{key}: pelo menos um item")

    for i, d in enumerate(refinement.get("dados") or [], 1):
        if not isinstance(d, dict) or not _text(d.get("campo")) or not _text(d.get("classe")):
            p(f"dados #{i}: campo e classe")

    deploy = refinement.get("deploy") or {}
    if deploy.get("estrategia") not in DEPLOY_STRATEGIES:
        p(f"deploy.estrategia deve ser {' | '.join(DEPLOY_STRATEGIES)}")
    if len(_text(deploy.get("rollback"))) < 10:
        p("deploy.rollback: como voltar")
    change = refinement.get("mudanca") or {}
    if change.get("risco") not in RISK_LEVELS:
        p(f"mudanca.risco deve ser {' | '.join(RISK_LEVELS)}")
    standard = change.get("candidata_mudanca_padrao", change.get("candidata_low_risk"))  # nome antigo aceito
    if standard and change.get("risco") != "baixo":
        p("mudanca.candidata_mudanca_padrao só com risco baixo")
    points = (refinement.get("estimativa") or {}).get("pontos")
    if points not in POINTS:
        p(f"estimativa.pontos deve ser um de {', '.join(map(str, POINTS))}")
    for dep in refinement.get("dependencias") or []:
        if isinstance(dep, str) and len(dep.strip()) < 10:
            rep.warnings.append(f"dependência vaga: {dep!r} (diga o quê, com quem e até quando)")
    return rep


def _check_performance(perf: dict[str, Any], p: Any) -> None:
    if "aplica" not in perf:
        p("testes.performance.aplica: true ou false")
        return
    if not perf.get("aplica"):
        if len(_text(perf.get("justificativa_nao_aplica"))) < MIN_JUSTIFICATION:
            p("testes.performance: sem teste de performance exige justificativa (justificativa_nao_aplica)")
        return
    if len(_text(perf.get("cenario"))) < 10:
        p("testes.performance.cenario")
    load = perf.get("carga") or {}
    if load.get("tipo") not in LOAD_TYPES:
        p(f"testes.performance.carga.tipo deve ser {' | '.join(LOAD_TYPES)}")
    value = _num(load.get("valor"))
    if value is None or value <= 0:
        p("testes.performance.carga.valor maior que zero")
    if not DURATION.fullmatch(_text(load.get("duracao"))):
        p("testes.performance.carga.duracao no formato 10m, 30s ou 1h")
    limits = perf.get("limites") or {}
    p95, p99, err = _num(limits.get("p95_ms")), _num(limits.get("p99_ms")), _num(limits.get("erro_max_pct"))
    if p95 is None or p95 <= 0:
        p("testes.performance.limites.p95_ms maior que zero")
    if p99 is None or p99 <= 0:
        p("testes.performance.limites.p99_ms maior que zero")
    if p95 and p99 and p99 < p95:
        p("testes.performance.limites.p99_ms não pode ser menor que p95_ms")
    if err is None or not 0 <= err <= 100:
        p("testes.performance.limites.erro_max_pct entre 0 e 100")


def skeleton(story: str, ac_ids: list[str], task_kinds: list[str]) -> dict[str, Any]:
    """Modelo a preencher no refinamento técnico, já com os critérios da história."""
    kinds = set(task_kinds)
    return {
        "versao_modelo": MODEL_VERSION,
        "historia": story,
        "criterios_de_aceite": list(ac_ids),
        "criterios_tecnicos": [{"id": "CT-1", "descricao": "", "verificacao": "teste"}],
        "testes": {
            "unitario": {"cobertura_minima_linhas_alteradas": 80},
            "integracao": [{"id": "CI-1", "cenario": ""}] if kinds & INTEGRATION_KINDS else [],
            "contrato": {"aplica": "contract" in kinds, "artefato": ""},
            "performance": {
                "aplica": True,
                "cenario": "",
                "carga": {"tipo": "rps", "valor": 0, "duracao": "10m"},
                "limites": {"p95_ms": 0, "p99_ms": 0, "erro_max_pct": 0.5},
                "justificativa_nao_aplica": "",
            },
            "mutacao": {"score_minimo": 70},
            "seguranca": {"bloqueia_em": "high", "itens": []},
        },
        "observabilidade": {"aplica": True, "logs": [], "metricas": [], "alarmes": [], "justificativa_nao_aplica": ""},
        "dados": [],
        "deploy": {"estrategia": "canario", "rollback": "", "feature_flag": ""},
        "mudanca": {"risco": "medio", "candidata_mudanca_padrao": False},
        "dependencias": [],
        "riscos": [],
        "estimativa": {"pontos": 0},
    }


# ---------------------------------------------------------------- performance como teste do fluxo


def k6_script(story: str, perf: dict[str, Any]) -> str:
    """Script k6 com os thresholds exatamente iguais aos limites do refinamento (o time completa a requisição)."""
    load, limits = perf["carga"], perf["limites"]
    rate = int(load["valor"]) if load["tipo"] != "usuarios" else 0
    scenario = (
        f"{{ executor: 'constant-arrival-rate', rate: {rate}, timeUnit: '1s', duration: '{load['duracao']}',"
        f" preAllocatedVUs: {max(10, rate // 2)}, maxVUs: {max(50, rate * 2)} }}"
        if load["tipo"] != "usuarios"
        else f"{{ executor: 'constant-vus', vus: {int(load['valor'])}, duration: '{load['duracao']}' }}"
    )
    err = float(limits["erro_max_pct"]) / 100
    return (
        f"// Teste de performance da {story} — gerado pelo Nexo a partir do refinamento (não altere os thresholds:\n"
        f"// eles são conferidos com os limites aprovados). Cenário: {_text(perf.get('cenario'))}\n"
        "import http from 'k6/http';\n"
        "import { check } from 'k6';\n\n"
        "export const options = {\n"
        f"  scenarios: {{ carga: {scenario} }},\n"
        "  thresholds: {\n"
        f"    http_req_duration: ['p(95)<{limits['p95_ms']:g}', 'p(99)<{limits['p99_ms']:g}'],\n"
        f"    http_req_failed: ['rate<{err:g}'],\n"
        "  },\n"
        "};\n\n"
        "export default function () {\n"
        "  // TODO do time: requisição real do cenário (use __ENV.BASE_URL e dados sintéticos)\n"
        "  const res = http.get(`${__ENV.BASE_URL}/health/ready`);\n"
        "  check(res, { 'status 2xx': (r) => r.status >= 200 && r.status < 300 });\n"
        "}\n"
    )


THRESHOLD = re.compile(r"p\((95|99)\)\s*<\s*([\d.]+)")
FAILED_RATE = re.compile(r"http_req_failed\s*:\s*\[\s*['\"]rate\s*<\s*([\d.]+)")


def script_limits(text: str) -> dict[str, float]:
    """Limites declarados num script k6 (para conferir com o refinamento)."""
    out = {f"p{m.group(1)}_ms": float(m.group(2)) for m in THRESHOLD.finditer(text)}
    m = FAILED_RATE.search(text)
    if m:
        out["erro_max_pct"] = round(float(m.group(1)) * 100, 6)
    return out


def script_matches(text: str, limits: dict[str, Any]) -> list[str]:
    found = script_limits(text)
    problems = []
    for key in ("p95_ms", "p99_ms", "erro_max_pct"):
        want = _num(limits.get(key))
        if want is None:
            continue
        if key not in found:
            problems.append(f"script sem threshold para {key}")
        elif abs(found[key] - want) > 1e-9:
            problems.append(f"threshold {key} do script ({found[key]:g}) diferente do refinamento ({want:g})")
    return problems


def evaluate_summary(summary: dict[str, Any], limits: dict[str, Any]) -> dict[str, Any]:
    """Resultado de um teste k6 (`--summary-export`) comparado com os limites do refinamento."""
    metrics = summary.get("metrics") or {}
    dur = metrics.get("http_req_duration") or {}
    dur = dur.get("values") or dur  # k6 ≥ 0.50 aninha em "values"
    failed = metrics.get("http_req_failed") or {}
    failed = failed.get("values") or failed
    measured = {
        "p95_ms": _num(dur.get("p(95)")),
        "p99_ms": _num(dur.get("p(99)")),
        "erro_max_pct": round(float(failed.get("rate", failed.get("value", 0)) or 0) * 100, 4),
        "requisicoes": _num((metrics.get("http_reqs") or {}).get("count")),
    }
    problems = []
    for key in ("p95_ms", "p99_ms", "erro_max_pct"):
        limit, got = _num(limits.get(key)), measured.get(key)
        if got is None:
            problems.append(f"{key} ausente no resultado (rode o k6 com percentis 95 e 99)")
        elif limit is not None and got > limit:
            problems.append(f"{key} {got:g} acima do limite {limit:g}")
    return {"passed": not problems, "measured": measured, "limits": limits, "problems": problems}
