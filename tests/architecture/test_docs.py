"""A documentação não pode prometer comando que não existe nem citar arquivo que sumiu."""

from __future__ import annotations

import html
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import typer.main

from nexo.cli import surface
from nexo.cli.main import app

ROOT = Path(__file__).resolve().parents[2]
PAGES = sorted((ROOT / "docs" / "site").glob("*.html"))
COMMAND = re.compile(r"\bnexo ([a-z][a-z-]*(?: [a-z][a-z-]*)?)")


def exists(words: list[str]) -> bool:
    if " ".join(words[:2]) in surface.aliases():
        return True
    node = typer.main.get_command(app)
    for word in words:
        commands = getattr(node, "commands", None)
        if not commands:
            return True  # daqui em diante são argumentos
        node = commands.get(word)
        if node is None:
            return False
    return True


def commands_in_code(text: str) -> set[str]:
    blocks = re.findall(r"<code>(.*?)</code>", text, re.S) + re.findall(r"`([^`\n]+)`", text)
    fenced = re.findall(r"```[a-z]*\n(.*?)```", text, re.S)
    return {m for block in [*blocks, *fenced] for m in COMMAND.findall(html.unescape(block))}


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_command_in_the_docs_exists(page: Path) -> None:
    found = commands_in_code(page.read_text(encoding="utf-8"))
    assert sorted(m for m in found if not exists(m.split())) == []


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_links_point_to_real_files(page: Path) -> None:
    for link in re.findall(r'(?:href)="([^"#]+)"', page.read_text(encoding="utf-8")):
        assert (page.parent / link).exists(), link


def test_docs_do_not_load_anything_from_outside() -> None:
    for page in PAGES:
        assert "http://" not in page.read_text(encoding="utf-8")
        assert "https://" not in page.read_text(encoding="utf-8"), page.name


def test_readme_walkthrough_commands_exist() -> None:
    found = commands_in_code((ROOT / "README.md").read_text(encoding="utf-8"))
    assert found and sorted(m for m in found if not exists(m.split())) == []


def test_pages_were_generated_from_their_sources() -> None:
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_docs.py"), "--check"], check=False)
    assert done.returncode == 0, "rode `python scripts/build_docs.py`"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_anchors_point_to_real_sections(page: Path) -> None:
    for name, anchor in re.findall(r'href="([^"#]*)#([^"]+)"', page.read_text(encoding="utf-8")):
        target = (page.parent / name) if name else page
        assert f'id="{anchor}"' in target.read_text(encoding="utf-8"), f"{name}#{anchor}"


def test_placar_counts_every_feature() -> None:
    text = (ROOT / "docs" / "site" / "features.html").read_text(encoding="utf-8")
    rows = re.findall(r'<td><span class="selo (real|sim|falta)">', text)
    assert "{{" not in text and f"{rows.count('real')} itens provados" in text


def test_docs_are_written_from_inside_the_company() -> None:
    """Vale para o que as pessoas leem: nem ambiente de construção, nem pedido de máquina, nem questão jurídica."""
    forbidden = re.compile(
        r"(?i)\bmac ?os\b|\bmac\b|máquina windows|windows da empresa|máquina da empresa|conta aws"
        r"|jur[ií]dic|\blgpd\b|base legal"
    )
    readable = [*PAGES, *sorted((ROOT / "docs").glob("*.md")), ROOT / "README.md"]
    found = {p.name: forbidden.findall(re.sub(r"<[^>]+>", " ", p.read_text(encoding="utf-8"))) for p in readable}
    assert {k: v for k, v in found.items() if v} == {}


def test_commands_and_options_in_the_faq_are_real() -> None:
    """Quem copia um comando do FAQ não pode cair em opção que não existe."""
    text = (ROOT / "docs" / "site" / "faq.html").read_text(encoding="utf-8")
    assert text.count("<details") >= 50, "o FAQ precisa continuar robusto"
    root = typer.main.get_command(app)
    wrong = []
    for block in re.findall(r"<pre><code>(.*?)</code></pre>", text, re.S):
        for line in html.unescape(block).split("\n"):
            line = line.split("  #")[0].strip()
            if not line.startswith("nexo "):
                continue
            words = shlex.split(line)[1:]
            args = surface.translate(words)
            node = root
            used = 0
            while getattr(node, "commands", None) and used < len(args) and args[used] in node.commands:
                node = node.commands[args[used]]
                used += 1
            valid = {o for p in getattr(node, "params", []) for o in p.opts} | {"--tool", "--json", "--help"}
            if not used or [w for w in words if w.startswith("--") and w not in valid]:
                wrong.append(line)
    assert wrong == []
