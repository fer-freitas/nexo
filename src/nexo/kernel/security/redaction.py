"""Redação determinística de segredos e dados pessoais.

Aplicada a tudo que é persistido em logs/auditoria e a tudo que é enviado a uma rota.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MASK = "[REDACTED:{kind}]"


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def valid_cpf(value: str) -> bool:
    d = _digits(value)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(d[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10) % 11 % 10
        if check != int(d[size]):
            return False
    return True


def valid_cnpj(value: str) -> bool:
    d = _digits(value)
    if len(d) != 14 or d == d[0] * 14:
        return False
    weights = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for size in (12, 13):
        w = weights if size == 12 else [6, *weights]
        total = sum(int(d[i]) * w[i] for i in range(size))
        rest = total % 11
        check = 0 if rest < 2 else 11 - rest
        if check != int(d[size]):
            return False
    return True


def valid_luhn(value: str) -> bool:
    d = _digits(value)
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net", "exemplo.com", "exemplo.com.br")
EXAMPLE_TLDS = (".example", ".test", ".invalid", ".localhost")


def real_email(value: str) -> bool:
    """E-mail em domínio reservado para exemplo (RFC 2606) não é dado pessoal: fica no texto, para a IA ver o
    exemplo (achado no uso real: o pedido trazia exemplos de máscara e eles sumiam da spec)."""
    domain = value.rsplit("@", 1)[-1].lower()
    if domain in EXAMPLE_DOMAINS or any(domain.endswith("." + d) for d in EXAMPLE_DOMAINS):
        return False
    return not domain.endswith(EXAMPLE_TLDS)


@dataclass(frozen=True)
class Rule:
    kind: str
    pattern: re.Pattern[str]
    validator: Callable[[str], bool] | None = None
    group: int = 0


RULES: tuple[Rule, ...] = (
    Rule(
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    Rule("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    Rule("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    Rule("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    Rule("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    Rule("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b")),
    Rule("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    Rule("bearer", re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{20,})"), group=1),
    Rule(
        "connection_secret",
        re.compile(
            r"(?i)\b(?:password|pwd|passwd|secret|senha|segredo|api[_-]?key|access[_-]?token|token|client[_-]?secret)\s*[=:]\s*([^\s;,'\"]{6,})"
        ),
        group=1,
    ),
    Rule("azure_pat", re.compile(r"\b[a-z2-7]{52}\b")),
    Rule("credit_card", re.compile(r"\b(?:\d[ -]?){13,19}\b"), validator=valid_luhn),
    Rule("cnpj", re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), validator=valid_cnpj),
    Rule("cpf", re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), validator=valid_cpf),
    Rule("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), validator=real_email),
    Rule("phone_br", re.compile(r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9\d{4}-?\d{4}(?!\d)")),
)


@dataclass(frozen=True)
class Finding:
    kind: str
    start: int
    end: int


def find(text: str, rules: tuple[Rule, ...] = RULES) -> list[Finding]:
    """Retorna trechos sensíveis sem sobreposição, na ordem de prioridade das regras."""
    taken: list[Finding] = []
    for rule in rules:
        for m in rule.pattern.finditer(text):
            start, end = m.span(rule.group)
            if rule.validator is not None and not rule.validator(m.group(rule.group)):
                continue
            if any(start < f.end and f.start < end for f in taken):
                continue
            taken.append(Finding(rule.kind, start, end))
    return sorted(taken, key=lambda f: f.start)


def redact(text: str, rules: tuple[Rule, ...] = RULES) -> str:
    out: list[str] = []
    pos = 0
    for f in find(text, rules):
        out.append(text[pos : f.start])
        out.append(MASK.format(kind=f.kind))
        pos = f.end
    out.append(text[pos:])
    return "".join(out)


def contains_sensitive(text: str) -> bool:
    return bool(find(text))


# Identidade de quem responde por uma decisão (aprovar, reprovar, decidir) fica legível na trilha de auditoria:
# é a finalidade da trilha. Só vale para esses campos e para valores no formato `provedor:login`.
ACCOUNTABLE_KEYS = frozenset({"approver", "by", "decided_by", "requester", "author"})
IDENTITY = re.compile(r"^(github|sso|os):[^\s]{1,200}$")


def redact_obj(value: Any, key: str = "") -> Any:
    """Redige recursivamente strings dentro de estruturas JSON-like."""
    if isinstance(value, str):
        if key in ACCOUNTABLE_KEYS and IDENTITY.match(value):
            return value
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_obj(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_obj(v) for v in value]
    return value
