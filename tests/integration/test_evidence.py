"""pacote de evidências por release: dossiê verificável da feature e exportação da trilha de auditoria."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import Runner
from tests.integration.test_story import OK_REVIEWS, QA, impl, run_json


def test_evidence_package_for_delivered_feature(  # type: ignore[no-untyped-def]
    planned, nexo: Runner, scenario, tmp_path: Path, nexo_home: Path
) -> None:
    root = planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md"), *OK_REVIEWS])
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert code == 0 and rep["state"] == "Concluido"
    code, out, err = nexo("--json", "evidence", "build", "F-1", "--out", str(tmp_path / "ev"))
    assert code == 0, err
    result = json.loads(out)
    folder = Path(result["folder"])
    assert result["audit"]["chain_ok"] and result["audit"]["events"] > 0 and not result["signed"]
    for name in ("index.md", "dados.json", "trilha.jsonl", "documentos/spec.md", ".evidencia/manifest.json"):
        assert (folder / name).is_file(), name
    assert list((folder / "qualidade" / "US-1-1").rglob("quality-report.json"))
    data = json.loads((folder / "dados.json").read_text(encoding="utf-8"))
    story = data["stories"][0]
    assert story["id"] == "US-1-1" and story["state"] == "Concluido" and story["quality"]["passed"]
    assert any(g["gate"] == "spec" for g in data["gates"]) and data["cost_brl"] >= 0
    trail = [json.loads(line) for line in (folder / "trilha.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {e["ref"] for e in trail} >= {"F-1", "US-1-1", "T-1-1"} and all(e["hash"] for e in trail)
    index = (folder / "index.md").read_text(encoding="utf-8")
    assert "Evidências da entrega F-1" in index and "Trilha de auditoria íntegra: **sim**" in index

    # integridade: pacote intacto passa; arquivo adulterado é detectado
    code, out, _ = nexo("--json", "evidence", "verify", str(folder))
    assert code == 0 and json.loads(out)["integrity"] and not json.loads(out)["signed"]
    (folder / "documentos" / "spec.md").write_text("adulterado", encoding="utf-8")
    code, _, err = nexo("evidence", "verify", str(folder))
    assert code != 0 and "spec.md" in err
    assert root.is_dir()


def test_signed_evidence_and_audit_export(  # type: ignore[no-untyped-def]
    planned, nexo: Runner, scenario, tmp_path: Path, nexo_home: Path
) -> None:
    planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md"), *OK_REVIEWS])
    assert run_json(nexo, "run", "story", "US-1-1")[0] == 0
    key = tmp_path / "evidencias.key"
    code, out, err = nexo("--json", "brain", "keygen", str(key))
    assert code == 0, err
    public = json.loads(nexo("--json", "brain", "pubkey", "--key", str(key))[1])["public_key"]
    code, out, err = nexo("--json", "evidence", "build", "F-1", "--out", str(tmp_path / "ev"), "--key", str(key))
    assert code == 0 and json.loads(out)["signed"], err
    folder = Path(json.loads(out)["folder"])
    # sem a chave pública entre as confiáveis: íntegro, mas a assinatura não é aceita
    code, out, _ = nexo("--json", "evidence", "verify", str(folder))
    assert code != 0 and json.loads(out)["integrity"] and json.loads(out)["signed"]
    nexo("config", "set", "evidence.trusted_keys", public)  # config pessoal não vale para confiar em chave
    assert nexo("evidence", "verify", str(folder))[0] != 0
    from nexo.kernel.runtime import yamlfile
    from tests.conftest import dump_yaml

    org = nexo_home / "brain" / "org" / "config.yaml"
    dump_yaml(org, {**yamlfile.load(org), "evidence": {"trusted_keys": [public]}})
    code, out, err = nexo("--json", "evidence", "verify", str(folder))
    assert code == 0 and json.loads(out)["ok"], err

    # exportação da trilha: por item (com derivados) e por período, com a cadeia de hashes
    code, out, _ = nexo("--json", "audit", "export", "--story", "US-1-1")
    rows = json.loads(out)
    assert rows and all(r["ref"].startswith("US-1-1") for r in rows) and all(r["prev_hash"] for r in rows)
    target = tmp_path / "trilha.jsonl"
    code, out, _ = nexo("audit", "export", "--from", "2000-01-01", "--out", str(target))
    assert code == 0 and len(target.read_text(encoding="utf-8").splitlines()) >= len(rows)


def test_every_advertised_command_exists() -> None:
    """Todo comando listado na ajuda precisa existir (o `audit export` estava anunciado e faltava)."""
    import typer

    from nexo.cli.main import app
    from nexo.cli.registry import COMMANDS

    root = typer.main.get_command(app)
    missing = []
    for spec in COMMANDS:
        cmd = root
        for part in spec.path.split():
            subs = getattr(cmd, "commands", None)
            if subs is None:
                break
            if part not in subs:
                missing.append(spec.path)
                break
            cmd = subs[part]
    assert missing == []
