"""console: frases do dia a dia entendidas sem IA, foco da conversa e o que pede confirmação."""

from __future__ import annotations

import pytest

from nexo.cli.registry import top_levels
from nexo.modules.console.domain.intents import Focus, needs_confirmation, normalize, parse

COMMANDS = set(top_levels())


@pytest.mark.parametrize(
    ("text", "argv"),
    [
        ("status", ("status",)),
        ("nexo status", ("status",)),  # hábito antigo continua valendo
        ("/run story US-1-2", ("run", "story", "US-1-2")),
        ('plan "login com biometria"', ("plan", "login com biometria")),
        ("como está a US-1-2?", ("status", "US-1-2")),
        ("COMO ESTA A us-1-2", ("status", "US-1-2")),
        ("roda a US-1-3", ("run", "story", "US-1-3")),
        ("executar a feature F-2 inteira", ("run", "feature", "F-2")),
        ("aprova a US-1-2", ("approve", "US-1-2")),
        ("aprovar o plano da F-1", ("plan", "approve", "F-1")),
        ("responder as dúvidas da F-1", ("plan", "answer", "F-1")),
        ("retomar a US-1-1", ("resume", "US-1-1")),
        ("quanto gastei?", ("cost",)),
        ("quanto custou a US-1-1", ("cost", "--story", "US-1-1")),
        ("rodar os testes", ("quality", "run")),
        ("ver o log da US-1-1", ("logs", "US-1-1")),
        ("como está o PR?", ("pr", "sync")),
        ("verificar o ambiente", ("doctor",)),
        ("quero criar uma feature de login com biometria no app", ("plan", "login com biometria no app")),
        ("planejar a partir do pedido.md", ("plan", "pedido.md")),
        ("reprova a US-1-2 porque o total está errado", ("reject", "US-1-2", "--reason", "o total está errado")),
    ],
)
def test_common_phrases_without_ai(text: str, argv: tuple[str, ...]) -> None:
    intent = parse(text, COMMANDS)
    assert intent.kind == "command" and intent.argv == argv


def test_focus_resolves_ela_and_asks_when_missing() -> None:
    focus = Focus()
    assert parse("aprova ela", COMMANDS, focus).kind == "ask_slot"
    focus.see("US-1-2")
    intent = parse("aprova ela", COMMANDS, focus)
    assert intent.argv == ("approve", "US-1-2")
    assert parse("roda de novo", COMMANDS, focus).argv == ("run", "story", "US-1-2")
    assert parse("reprova ela", COMMANDS, focus).slot == "reason"


def test_exit_help_next_menu_and_unknown() -> None:
    assert parse("sair", COMMANDS).kind == "exit"
    assert parse("ajuda", COMMANDS).kind == "help"
    assert parse("e agora?", COMMANDS).kind == "next"
    assert parse("o que espera por mim?", COMMANDS).kind == "next"
    assert parse("?", COMMANDS).kind == "next"
    assert parse("2", COMMANDS, menu_size=3).choice == 2
    assert parse("7", COMMANDS, menu_size=3).kind == "none"
    assert parse("qual a diferença entre ready e approve?", COMMANDS).kind == "none"  # vai para a IA
    assert parse("quero planejar", COMMANDS).slot == "file_or_text"


def test_normalize_is_the_same_on_every_os() -> None:
    assert normalize("  Como  ESTÁ a\tUS-1-2? ") == "como esta a us-1-2?"


@pytest.mark.parametrize(
    ("argv", "confirm"),
    [
        (("status",), False),
        (("logs", "US-1-1"), False),
        (("plan", "status", "F-1"), False),
        (("cost",), False),
        (("cost", "close", "--month", "2026-10"), True),
        (("ready", "US-1-1"), False),
        (("ready", "US-1-1", "--init"), True),
        (("quality", "run"), False),
        (("plan", "nova ideia"), True),
        (("run", "story", "US-1-1"), True),
        (("approve", "US-1-1"), True),
        (("pr", "sync"), True),
        (("config", "set", "x", "y"), True),
        (("cost", "budget", "show"), False),
        (("cost", "budget", "set", "--dev", "x", "100"), True),
        (("cost", "pricing", "set", "dolar", "5.4"), True),
        (("admin", "flags", "list"), False),
        (("admin", "flags", "set", "plan", "off"), True),
        (("extensions", "skill", "list"), False),
        (("extensions", "skill", "add", "x"), True),
        (("inbox",), False),
    ],
)
def test_what_needs_confirmation(argv: tuple[str, ...], confirm: bool) -> None:
    assert bool(needs_confirmation(argv)) is confirm


@pytest.mark.parametrize(
    ("new", "old"),
    [
        (["cost", "budget", "watch"], ["budget", "watch"]),
        (["cost", "usage", "scan"], ["usage", "scan"]),
        (["metrics", "baseline"], ["baseline"]),
        (["quality", "perf", "script", "US-1"], ["perf", "script", "US-1"]),
        (["audit", "evidence", "build", "F-1"], ["evidence", "build", "F-1"]),
        (["admin", "autonomy", "set", "L2"], ["autonomy", "set", "L2"]),
        (["extensions", "skill", "list"], ["skill", "list"]),
    ],
)
def test_merged_commands_are_governed_by_the_same_feature_flag(new: list[str], old: list[str]) -> None:
    """fundir comandos não pode abrir brecha na feature flag que bloqueia o nome antigo."""
    from nexo.kernel.config.flags import flag_for_command

    assert flag_for_command(new) == flag_for_command(old)
