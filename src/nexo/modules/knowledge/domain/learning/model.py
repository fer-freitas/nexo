"""Aprendizado contínuo: eventos brutos, lições, mudança de padrão e estatística de avaliação."""

from __future__ import annotations

import math
import random
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from nexo.kernel.extensions.model import similarity

SOURCES = ("pr_comments", "bugs", "human_fixes", "adrs", "incidents", "routing")
LESSON_TYPES = ("standard", "anti_pattern", "pitfall", "domain", "routing_hint", "dor_dod")


@dataclass
class RawEvent:
    source: str
    ref: str  # id estável na origem (evita reprocessar)
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Lesson:
    type: str
    title: str
    body: str
    evidence_refs: list[str]
    proposed_target: str = ""

    def text(self) -> str:
        return f"{self.title}\n{self.body}"


def dedup_lessons(
    candidates: list[Lesson], existing: list[str], threshold: float = 0.5
) -> tuple[list[Lesson], list[Lesson]]:
    """Separa lições novas das repetidas (contra o cérebro e entre si)."""
    new: list[Lesson] = []
    dup: list[Lesson] = []
    seen = list(existing)
    for lesson in candidates:
        text = lesson.text()
        if any(similarity(text, other) >= threshold for other in seen) or lesson.title.lower() in (
            s.splitlines()[0].lower() for s in seen if s
        ):
            dup.append(lesson)
            continue
        new.append(lesson)
        seen.append(text)
    return new, dup


# ---------------- mudança de padrão (7.3)

DEP_PATTERNS = (
    ("npm", re.compile(r'^\+\s*"(@?[a-z0-9._/-]+)"\s*:\s*"[\^~]?\d', re.M)),
    ("pypi", re.compile(r'^\+\s*"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*(?:[<>=~!]=?[^"]*)?",?\s*$', re.M)),
    ("nuget", re.compile(r'^\+\s*<PackageReference\s+Include="([^"]+)"', re.M)),
    # pubspec.yaml (`  dio: ^5.4.0`) e Maven (`<artifactId>` novo) / Gradle (`implementation("g:a:v")`)
    ("pub", re.compile(r"^\+\s{2,4}([a-z][a-z0-9_]*):\s*['\"]?[\^~>=<]*\d", re.M)),
    ("maven", re.compile(r"^\+\s*<artifactId>([\w.-]+)</artifactId>", re.M)),
    (
        "maven",
        re.compile(
            r"^\+\s*(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*\(?\s*['\"]([\w.-]+:[\w.-]+):",
            re.M,
        ),
    ),
)


# o padrão desses ecossistemas só vale dentro do manifesto (YAML/XML genérico daria falso positivo)
DEP_MANIFESTS = {
    "pub": re.compile(r"(^|/)pubspec\.yaml$"),
    "maven": re.compile(r"(^|/)(pom\.xml|build\.gradle(\.kts)?)$"),
}
PUB_NOT_PACKAGES = {"sdk", "flutter", "version"}


def _diff_by_file(diff: str) -> dict[str, str]:
    files: dict[str, list[str]] = {}
    current = ""
    for line in diff.splitlines(keepends=True):
        if line.startswith("+++ "):
            current = line[4:].strip().removeprefix("b/")
            continue
        files.setdefault(current, []).append(line)
    return {k: "".join(v) for k, v in files.items()}


def added_dependencies(diff: str) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    by_file = _diff_by_file(diff)
    for eco, pattern in DEP_PATTERNS:
        manifest = DEP_MANIFESTS.get(eco)
        text = "".join(t for f, t in by_file.items() if manifest.search(f)) if manifest else diff
        for m in pattern.finditer(text):
            name = m.group(1)
            if eco == "pypi" and (name.startswith(("python", "requires")) or "." in name[:1]):
                continue
            if eco == "pub" and name in PUB_NOT_PACKAGES:
                continue
            found.add((eco, name.lower()))
    return found


@dataclass(frozen=True)
class Adoption:
    ecosystem: str
    package: str
    commits: tuple[str, ...]


def adoptions(commit_diffs: dict[str, str], min_commits: int = 2) -> list[Adoption]:
    """Dependência adicionada em ≥ N commits distintos = padrão emergente a documentar/avaliar."""
    by_pkg: dict[tuple[str, str], list[str]] = defaultdict(list)
    for sha, diff in commit_diffs.items():
        for dep in added_dependencies(diff):
            by_pkg[dep].append(sha)
    return sorted(
        (Adoption(eco, pkg, tuple(shas)) for (eco, pkg), shas in by_pkg.items() if len(shas) >= min_commits),
        key=lambda a: (-len(a.commits), a.package),
    )


# ---------------- avaliação (7.5)


@dataclass
class VariantResult:
    variant: str
    runs: int
    successes: int
    cost_brl: list[float] = field(default_factory=list)
    seconds: list[float] = field(default_factory=list)

    @property
    def rate(self) -> float:
        return self.successes / self.runs if self.runs else 0.0


def wilson(successes: int, n: int, z: float = 1.645) -> tuple[float, float]:
    """Intervalo de Wilson (90%) para taxa de sucesso — robusto para amostras pequenas."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, center - half), 3), round(min(1.0, center + half), 3)


def compare(a: VariantResult, b: VariantResult) -> dict[str, Any]:
    """Diferença de taxas com teste de duas proporções; só recomenda com significância e amostra mínima."""
    diff = a.rate - b.rate
    pooled = (a.successes + b.successes) / (a.runs + b.runs) if a.runs + b.runs else 0.0
    se = math.sqrt(pooled * (1 - pooled) * (1 / max(a.runs, 1) + 1 / max(b.runs, 1)))
    z = diff / se if se else 0.0
    p_value = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    cost_a = statistics.fmean(a.cost_brl) if a.cost_brl else 0.0
    cost_b = statistics.fmean(b.cost_brl) if b.cost_brl else 0.0
    if min(a.runs, b.runs) < 5:
        verdict = "amostra insuficiente (mínimo 5 execuções por variante)"
        winner = None
    elif p_value < 0.1:
        winner = a.variant if diff > 0 else b.variant
        verdict = f"{winner} é melhor (p={p_value:.3f})"
    elif abs(cost_a - cost_b) > 0.01:
        winner = a.variant if cost_a < cost_b else b.variant
        verdict = f"sem diferença de sucesso (p={p_value:.2f}); {winner} é mais barato"
    else:
        winner = None
        verdict = f"sem diferença significativa (p={p_value:.2f})"
    return {
        "diff": round(diff, 3),
        "p_value": round(p_value, 4),
        "winner": winner,
        "verdict": verdict,
        "cost_a": round(cost_a, 2),
        "cost_b": round(cost_b, 2),
    }


# ---------------- roteador que aprende (7.6)


@dataclass
class RouteStats:
    successes: int = 0
    failures: int = 0


def posterior(prior: float, stats: RouteStats, strength: float = 10.0) -> float:
    """Média a posteriori Beta com o prior do catálogo como pseudo-contagem."""
    alpha = prior * strength + stats.successes
    beta = (1 - prior) * strength + stats.failures
    return round(alpha / (alpha + beta), 4)


def thompson(prior: float, stats: RouteStats, rng: random.Random, strength: float = 10.0) -> float:
    alpha = prior * strength + stats.successes
    beta = (1 - prior) * strength + stats.failures
    return rng.betavariate(alpha, beta)
