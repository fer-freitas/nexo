"""Mensagens da CLI em pt-BR (padrão) e inglês."""

from __future__ import annotations

from nexo.kernel.i18n.messages import MESSAGES

_current = "pt-BR"


def set_lang(lang: str) -> None:
    global _current
    _current = lang if lang in MESSAGES else "pt-BR"


def get_lang() -> str:
    return _current


def t(msg_key: str, /, **kwargs: object) -> str:
    table = MESSAGES.get(_current, MESSAGES["pt-BR"])
    template = table.get(msg_key) or MESSAGES["pt-BR"].get(msg_key) or msg_key
    return template.format(**kwargs) if kwargs else template
