"""Tarefas agendadas do central: rodam no próprio serviço da AWS, sem depender do GitHub Actions.

Uso: `python -m nexo.central.jobs dora` e `first-admin <pessoa>`. Os repositórios vêm de `NX_DORA_REPOS`, separados por vírgula.
"""

from __future__ import annotations

import json
import os
import sys

from nexo.central import auth
from nexo.central.common import db
from nexo.central.dora_github import collect


def dora() -> dict[str, object]:
    repos = [r.strip() for r in os.environ.get("NX_DORA_REPOS", "").split(",") if r.strip()]
    return collect(db(), repos)


def main(argv: list[str]) -> int:
    if argv[:1] == ["first-admin"] and len(argv) == 2:
        try:
            print(auth.first_admin(argv[1]))
        except PermissionError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if argv[:1] != ["dora"]:
        print("uso: python -m nexo.central.jobs dora | first-admin <pessoa>", file=sys.stderr)
        return 2
    print(json.dumps(dora(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
