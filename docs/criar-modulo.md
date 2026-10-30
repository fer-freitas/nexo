# Como criar um módulo

Um módulo novo entra só com uma pasta. Nenhum arquivo central precisa mudar.

## 1. Crie a pasta e o manifesto

`src/nexo/modules/exemplo/module.yaml`:

```yaml
id: exemplo
version: 1.0.0
title: "Exemplo"
flags:
  - key: exemplo.usar
    title: "Comandos do exemplo"
    area: "Integrações"
    stage: experimental   # experimental começa desligada
    default: false
    commands: ["exemplo"]
cli: "nexo.modules.exemplo.cli:register"
services:
  exemplo.app: "nexo.modules.exemplo.app"
commands:
  - {path: "exemplo", summary: "O que o comando faz", roles: [dev]}
```

## 2. Registre os comandos

`src/nexo/modules/exemplo/cli.py`:

```python
"""Comandos `nexo exemplo`."""

from typing import Any

import typer


def register(root: typer.Typer, tools: Any) -> None:
    @root.command("exemplo")
    def exemplo() -> None:
        """O que o comando faz."""
        tools.emit({"ok": True})
```

## 3. Fale com outros módulos sem importá-los

```python
from nexo.kernel import services

if services.available("demands.core"):
    demandas = services.load("demands.core").load(ctx)
```

Para reagir a algo que aconteceu, declare `subscribe` e `events.consumes` no manifesto.

## 4. Confira

```bash
.venv/bin/python -m pytest tests/architecture -q
```

O kit de contrato roda sozinho em todo módulo e recusa:

- manifesto inválido ou sem título;
- comando declarado que não existe;
- comando sem flag;
- importação direta de outro módulo.

Depois de mudar comandos, gere de novo os snapshots da ajuda:

```bash
NX_HOME=/tmp/s COLUMNS=200 NO_COLOR=1 PYTHONIOENCODING=utf-8 .venv/bin/python -m nexo help > tests/snapshots/help.pt-BR.txt
NX_HOME=/tmp/s COLUMNS=200 NO_COLOR=1 PYTHONIOENCODING=utf-8 .venv/bin/python -m nexo --lang en help > tests/snapshots/help.en.txt
```
