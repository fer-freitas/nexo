"""Travas de arquitetura: o que nunca pode acontecer, verificado em todo teste."""

from __future__ import annotations

import ast
import importlib
import re
from collections import Counter
from pathlib import Path

import pytest

from nexo.kernel import modules

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "nexo"


def _unit(mod: str) -> str:
    p = mod.split(".")
    if p[1:2] == ["modules"] and len(p) > 2:
        return "modules." + p[2]
    return p[1] if len(p) > 1 else "nexo"


def _edges() -> Counter[tuple[str, str]]:
    found: Counter[tuple[str, str]] = Counter()
    for f in SRC.rglob("*.py"):
        name = ".".join(f.relative_to(SRC.parent).with_suffix("").parts).replace(".__init__", "")
        unit = _unit(name)
        seen: set[str] = set()
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("nexo"):
                targets = [f"{n.module}.{a.name}" if _unit(n.module) == "nexo" else n.module for a in n.names]
                seen |= {_unit(t) for t in targets if _unit(t) != unit}
        found.update((unit, t) for t in seen)
    return found


def test_kernel_and_central_never_depend_on_modules_or_cli() -> None:
    bad = [
        e
        for e in _edges()
        if e[0] in ("kernel", "store") and (e[1].startswith("modules") or e[1] in ("cli", "central"))
    ]
    bad += [e for e in _edges() if e[0] == "central" and (e[1].startswith("modules") or e[1] == "cli")]
    assert bad == []


def test_module_never_imports_another_module() -> None:
    """Módulo fala com módulo por evento ou por `kernel.services`, nunca por import."""
    edges = [f"{a} -> {b}" for a, b in _edges() if a.startswith("modules") and b.startswith("modules")]
    assert edges == [], "use evento ou kernel.services"


def test_every_service_points_to_something_that_exists() -> None:
    for name, target in {n: t for m in modules.builtin() for n, t in m.services.items()}.items():
        assert importlib.import_module(target), name


FORBIDDEN = (
    (r"\.nexo[\"'/ ]+[/ ]*[\"']?config\.yaml", ".nexo/config.yaml"),
    (r"[\"']NEXO_[A-Z]", "variável NEXO_*"),
    (r"Path\.home\(\) */ *[\"']\.nexo[\"']", "~/.nexo"),
    (r"\.nexo-brain", ".nexo-brain"),
)


@pytest.mark.parametrize("pattern,label", FORBIDDEN)
def test_reserved_names_are_not_used(pattern: str, label: str) -> None:
    hits = [str(f.relative_to(ROOT)) for f in SRC.rglob("*.py") if re.search(pattern, f.read_text(encoding="utf-8"))]
    assert hits == [], label


def test_keyring_service_is_reserved() -> None:
    from nexo.kernel.secrets import credentials

    assert credentials.SERVICE == "nexo-cli"


def test_floor_has_no_flag() -> None:
    declared = {f.key.split(".")[-1] for m in modules.builtin() for f in m.flags}
    assert not declared & modules.FLOOR


def test_floor_flag_is_refused_by_the_loader() -> None:
    from nexo.kernel.errors import NexoError

    bad = modules.parse({"id": "x", "title": "x", "flags": [{"key": "audit", "title": "desliga"}]}, "teste")
    with pytest.raises(NexoError, match="piso"):
        modules.validate([bad])


def test_loader_refuses_cycle_duplicates_and_unknown_dependency() -> None:
    from nexo.kernel.errors import NexoError

    a = modules.parse({"id": "a", "title": "a", "requires": ["b"]}, "t")
    b = modules.parse({"id": "b", "title": "b", "requires": ["a"]}, "t")
    with pytest.raises(NexoError, match="circular"):
        modules.validate([a, b])
    with pytest.raises(NexoError, match="repetido"):
        modules.validate([modules.parse({"id": "a", "title": "a"}, "t")] * 2)
    with pytest.raises(NexoError, match="inexistente"):
        modules.validate([modules.parse({"id": "c", "title": "c", "requires": ["z"]}, "t")])


def test_every_manifest_is_valid_and_declares_real_commands() -> None:
    import typer

    from nexo.cli.main import app
    from nexo.cli.surface import aliases, translate

    root = typer.main.get_command(app)
    missing = []
    for m in modules.builtin():
        for c in m.commands:
            words = translate([*c.path.split(), "X"])[:-1] if c.target else c.path.split()
            words = [w for w in words if not w.startswith("-") and w not in ("X", "{0}")]
            node = root
            for w in words:
                subs = getattr(node, "commands", {})
                if w not in subs:
                    break
                node = subs[w]
            else:
                continue
            missing.append(c.path)
    assert missing == [], "comando declarado no manifesto sem implementação"
    assert aliases()


def test_no_ci_is_generated_and_no_workflow_shipped() -> None:
    assert not list((ROOT / ".github").glob("**/*")) if (ROOT / ".github").exists() else True
    assert not (SRC / "resources" / "ci").exists()


def test_new_module_needs_only_a_folder(tmp_path: Path) -> None:
    """Módulo novo entra só com a pasta: flags e comandos vêm do manifesto, sem tabela central."""
    data = {
        "id": "hello",
        "title": "Olá",
        "flags": [{"key": "hello.world", "title": "Olá", "stage": "experimental", "default": False}],
        "commands": [{"path": "hello", "summary": "Olá", "roles": ["all"]}],
    }
    manifest = modules.parse(data, "teste")
    modules.validate([*modules.builtin(), manifest])
    assert manifest.flags[0].default is False
