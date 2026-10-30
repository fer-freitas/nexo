"""Kit de contrato de módulo: o que todo módulo cumpre, de fábrica ou novo, testado da mesma forma."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nexo.kernel import modules

# Sem estes não há como ligar nem diagnosticar nada: `init`, `doctor`, `flags`, `module`.
CORE = frozenset({"setup"})


def _resolves(root: Any, path: str) -> bool:
    node = root
    for word in path.split():
        if word.startswith("{"):
            return True
        commands = getattr(node, "commands", None)
        if commands is None:
            return True  # daqui em diante são argumentos do comando
        node = commands.get(word)
        if node is None:
            return False
    return True


@dataclass(frozen=True)
class ModuleContract:
    manifest: modules.Manifest

    def manifest_problems(self) -> list[str]:
        out = []
        if not self.manifest.title:
            out.append("sem título")
        out += [f"{c.path}: sem resumo" for c in self.manifest.commands if not c.summary]
        out += [f"{f.key}: flag sem título" for f in self.manifest.flags if not f.title]
        return out

    def missing_commands(self, cli: Any) -> list[str]:
        """Todo comando declarado existe, e todo atalho de papel leva a um comando real."""
        out = []
        for command in self.manifest.commands:
            if command.target:
                if not _resolves(cli, command.target):
                    out.append(f"{command.path} → {command.target}")
            elif not _resolves(cli, command.path):
                out.append(command.path)
        return out

    def commands_without_flag(self, flag_for: Any) -> list[str]:
        """Comando sem flag não dá para desligar sem release. Atalho de papel vale também pelo comando de destino."""
        if self.manifest.id in CORE:
            return []
        grouped = {c for m in modules.builtin() for f in m.flags for c in f.commands}
        out = []
        for command in self.manifest.commands:
            words = command.path.split()
            target = command.target.split() if command.target else []
            group = any(c.startswith(command.path + " ") for c in grouped)
            if not (flag_for(words) or (target and flag_for(target)) or group):
                out.append(command.path)
        return out
