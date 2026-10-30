"""Insights determinísticos: definição dos detectores e estatística simples.

Os detectores são declarativos (YAML no cérebro, `insights/*.yaml`): o algoritmo é um dos embutidos e o YAML só
ajusta limiares, amostra mínima, escopo, dono e canais. `threshold` permite detector novo sem release, sobre um
catálogo fechado de métricas (nenhuma consulta livre chega ao banco do coletor).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

STATES = ("aberto", "reconhecido", "resolvido", "descartado")
TRANSITIONS = {
    "aberto": ("reconhecido", "resolvido", "descartado"),
    "reconhecido": ("resolvido", "descartado"),
    "resolvido": (),
    "descartado": (),
}
SEVERITIES = ("baixa", "media", "alta")
SCOPES = ("org", "squad", "project", "tool", "story", "feature", "dev", "license", "route", "device")
CHANNELS = ("card", "weekly", "monthly_finops", "issue")
SILENCE_DAYS = 7  # RN-104: descartado com motivo → silêncio


@dataclass
class Detector:
    id: str
    title: str
    algorithm: str
    params: dict[str, Any] = field(default_factory=dict)
    min_sample: int = 5  # RN-104
    severity: str = "media"
    owner: str = ""
    action: str = ""
    channels: tuple[str, ...] = ("card",)
    systemic: bool = False  # abre issue no nexo-brain
    enabled: bool = True


def parse_detector(data: dict[str, Any], algorithms: frozenset[str]) -> Detector:
    """Valida a definição (vinda do cérebro): algoritmo conhecido, severidade e canais da lista."""
    missing = [k for k in ("id", "title", "algorithm") if not data.get(k)]
    if missing:
        raise ValueError(f"detector sem {', '.join(missing)}")
    algorithm = str(data["algorithm"])
    if algorithm not in algorithms:
        raise ValueError(f"{data['id']}: algoritmo desconhecido {algorithm}")
    severity = str(data.get("severity", "media"))
    if severity not in SEVERITIES:
        raise ValueError(f"{data['id']}: severidade inválida {severity}")
    channels = tuple(str(c) for c in data.get("channels") or ["card"])
    bad = [c for c in channels if c not in CHANNELS]
    if bad:
        raise ValueError(f"{data['id']}: canal inválido {', '.join(bad)}")
    params = dict(data.get("params") or {})
    if not all(isinstance(v, (int, float, str, bool, list)) for v in params.values()):
        raise ValueError(f"{data['id']}: parâmetros só aceitam números, textos e listas")
    return Detector(
        id=str(data["id"]),
        title=str(data["title"]),
        algorithm=algorithm,
        params=params,
        min_sample=max(1, int(data.get("min_sample", 5))),
        severity=severity,
        owner=str(data.get("owner", "")),
        action=str(data.get("action", "")),
        channels=channels,
        systemic=bool(data.get("systemic", False)),
        enabled=bool(data.get("enabled", True)),
    )


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, ())


# ---------------------------------------------------------------- estatística


def percentile(values: list[float], pct: float) -> float:
    """Percentil com interpolação linear (mesma definição do numpy padrão)."""
    if not values:
        raise ValueError("amostra vazia")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct / 100
    low = math.floor(pos)
    high = math.ceil(pos)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def two_proportion_p(better_hits: int, better_n: int, worse_hits: int, worse_n: int) -> float:
    """p unicaudal de "a proporção do primeiro grupo é maior" (teste z de duas proporções)."""
    if min(better_n, worse_n) == 0:
        return 1.0
    p1 = better_hits / better_n
    p2 = worse_hits / worse_n
    pooled = (better_hits + worse_hits) / (better_n + worse_n)
    se = math.sqrt(pooled * (1 - pooled) * (1 / better_n + 1 / worse_n))
    if se == 0:
        return 0.0 if p1 > p2 else 1.0
    z = (p1 - p2) / se
    return 0.5 * math.erfc(z / math.sqrt(2))


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de Wilson para uma proporção (evidência dos cards)."""
    if n == 0:
        return 0.0, 1.0
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def linear_projection(value: float, day: int, days_in_month: int) -> float:
    """Projeção linear de fim de mês a partir do acumulado até o dia `day`."""
    if day <= 0:
        return value
    return value / day * days_in_month
