"""caches de desempenho: nunca devolvem dado velho nem compartilham objetos mutáveis."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from nexo.kernel.runtime import resources, yamlfile


def test_yaml_cache_sees_every_change_and_returns_copies(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    yamlfile.dump(path, {"gates": {"definition_of_ready": "warn"}})
    first = yamlfile.load(path)
    first["gates"]["definition_of_ready"] = "alterado em memória"
    assert yamlfile.load(path) == {"gates": {"definition_of_ready": "warn"}}  # cópia: mutação não vaza
    path.write_text("gates:\n  definition_of_ready: required\n", encoding="utf-8")  # mesmo tamanho? não importa
    assert yamlfile.load(path)["gates"]["definition_of_ready"] == "required"
    path.write_text("gates:\n  definition_of_ready: warn00\n", encoding="utf-8")  # mesmo tamanho do anterior
    assert yamlfile.load(path)["gates"]["definition_of_ready"] == "warn00"
    assert yamlfile.load(tmp_path / "nao-existe.yaml") == {}


def test_resource_cache_respects_brain_override(tmp_path: Path) -> None:
    base = resources.load_yaml("routing.yaml")
    base["defaults"] = {}
    assert resources.load_yaml("routing.yaml")["defaults"]  # cópia independente
    (tmp_path / "routing.yaml").write_text("defaults: {writer: [claude/opus]}\n", encoding="utf-8")
    assert resources.load_yaml("routing.yaml", tmp_path)["defaults"] == {"writer": ["claude/opus"]}
    (tmp_path / "routing.yaml").write_text("defaults: {writer: [copilot/gpt-5]}\n", encoding="utf-8")
    assert resources.load_yaml("routing.yaml", tmp_path)["defaults"] == {"writer": ["copilot/gpt-5"]}


def test_planning_does_not_import_jsonschema_upfront() -> None:
    """O jsonschema custa ~0,7 s para importar: só carrega quando há validação de esquema."""
    code = (
        "import sys, nexo.modules.planning.app.plan, nexo.modules.execution.app.llm; print('jsonschema' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, encoding="utf-8", errors="replace"
    ).stdout
    assert out.strip() == "False"
