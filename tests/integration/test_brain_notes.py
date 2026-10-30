"""Clone de trabalho x cópia verificada: o Obsidian nunca quebra a assinatura, e o lint vale sem CI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.conftest import Runner


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def make_brain(tmp_path: Path) -> Path:
    root = tmp_path / "nexo-brain"
    (root / "lessons").mkdir(parents=True)
    (root / "brain.yaml").write_text("schema_version: 2\n", encoding="utf-8")
    (root / ".gitignore").write_text(".obsidian/\n.trash/\n", encoding="utf-8")
    (root / "lessons" / "boa.md").write_text(
        "---\nid: boa\ntype: lesson\nscope: global\n---\n\nTexto.\n", encoding="utf-8"
    )
    git(root.parent, "init", "-q", str(root))
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    git(root, "checkout", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "i")
    return root


def test_sign_refuses_dirty_tree_and_broken_notes_but_ignores_obsidian(tmp_path: Path, nexo: Runner) -> None:
    brain = make_brain(tmp_path)
    key = tmp_path / "chave.pem"
    assert nexo("brain", "keygen", str(key))[0] == 0
    (brain / ".obsidian").mkdir()
    (brain / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    code, _, err = nexo("brain", "sign", str(brain), "--key", str(key), "--version", "2026.11.01-1")
    assert code == 0, err
    manifest = json.loads((brain / ".brain" / "manifest.json").read_text(encoding="utf-8"))
    assert ".obsidian/app.json" not in manifest["files"] and "lessons/boa.md" in manifest["files"]
    git(brain, "add", "-A")
    git(brain, "commit", "-qm", "manifesto")
    (brain / "lessons" / "ruim.md").write_text("sem frontmatter", encoding="utf-8")
    code, _, err = nexo("brain", "sign", str(brain), "--key", str(key), "--version", "2026.11.01-2")
    assert code != 0 and "suja" in err
    git(brain, "add", "-A")
    git(brain, "commit", "-qm", "ruim")
    code, _, err = nexo("brain", "sign", str(brain), "--key", str(key), "--version", "2026.11.01-2")
    assert code != 0 and "lint" in err


def test_lint_index_and_propose_by_branch(tmp_path: Path, nexo: Runner) -> None:
    brain = make_brain(tmp_path)
    code, out, _ = nexo("--json", "brain", "lint", str(brain))
    assert code == 0 and json.loads(out)["errors"] == 0
    data = json.loads(nexo("--json", "brain", "index", str(brain))[1])
    assert data["nodes"] == 1
    source = tmp_path / "nova.md"
    source.write_text("---\nid: nova\ntype: lesson\nscope: global\n---\n\nVeja [[boa]].\n", encoding="utf-8")
    code, out, err = nexo(
        "--json", "brain", "propose", str(source), "--clone", str(brain), "--target", "lessons/nova.md"
    )
    assert code == 0, err
    result = json.loads(out)
    assert result["branch"] == "nexo/proposta/nova"
    assert "lessons/nova.md" in git(brain, "ls-files")
    git(brain, "checkout", "-q", "main")
    bad = tmp_path / "ruim.md"
    bad.write_text("---\nid: outra\ntype: lesson\nscope: global\n---\n\nVeja [[nada]].\n", encoding="utf-8")
    code, _, err = nexo("--json", "brain", "propose", str(bad), "--clone", str(brain), "--target", "lessons/outra.md")
    assert code != 0 and "link quebrado" in err
    assert not (brain / "lessons" / "outra.md").exists()


def test_promote_moves_to_global_in_a_branch(tmp_path: Path, nexo: Runner) -> None:
    brain = make_brain(tmp_path)
    folder = brain / "projects" / "loja" / "lessons"
    folder.mkdir(parents=True)
    (folder / "util.md").write_text(
        "---\nid: util\ntype: lesson\nscope: project:loja\n---\n\nTexto.\n", encoding="utf-8"
    )
    git(brain, "add", "-A")
    git(brain, "commit", "-qm", "nota")
    code, out, err = nexo("--json", "brain", "promote", str(brain), "util")
    assert code == 0, err
    assert json.loads(out)["target"] == "lessons/util.md"
    assert "scope: global" in (brain / "lessons" / "util.md").read_text(encoding="utf-8")
    assert not (folder / "util.md").exists()
