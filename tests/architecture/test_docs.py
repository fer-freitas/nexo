"""A documentação não pode prometer comando que não existe nem citar arquivo que sumiu."""

from __future__ import annotations

import html
import re
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
