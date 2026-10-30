"""Tarefa de usuário em cada sistema, sem administrador: roda, termina e não fica residente."""

from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from nexo.kernel.errors import NexoError

LABEL = "com.nexo.lineage"
TASK = "NexoLinhagem"
MINUTES = 30
MARK = "# nexo-lineage"


@dataclass(frozen=True)
class Plan:
    system: str
    files: dict[str, str]
    install: tuple[tuple[str, ...], ...]
    remove: tuple[tuple[str, ...], ...]
    delete: tuple[str, ...]


def plan(system: str, nexo: str, home: Path, minutes: int = MINUTES) -> Plan:
    """O que instalar, como dado: dá para testar em qualquer sistema, sem rodar nada."""
    if system == "Darwin":
        path = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{nexo}</string><string>lineage</string><string>agent</string><string>run</string></array>
  <key>StartInterval</key><integer>{minutes * 60}</integer>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
</dict></plist>
"""
        return Plan(
            system,
            {str(path): body},
            (("launchctl", "load", "-w", str(path)),),
            (("launchctl", "unload", "-w", str(path)),),
            (str(path),),
        )
    if system == "Linux":
        folder = home / ".config" / "systemd" / "user"
        service = folder / "nexo-lineage.service"
        timer = folder / "nexo-lineage.timer"
        return Plan(
            system,
            {
                str(
                    service
                ): f"[Unit]\nDescription=Linhagem do Nexo\n\n[Service]\nType=oneshot\nNice=10\nExecStart={nexo} lineage agent run\n",  # noqa: E501
                str(
                    timer
                ): f"[Unit]\nDescription=Linhagem do Nexo\n\n[Timer]\nOnBootSec=5min\nOnUnitActiveSec={minutes}min\n\n[Install]\nWantedBy=timers.target\n",  # noqa: E501
            },
            (
                ("systemctl", "--user", "daemon-reload"),
                ("systemctl", "--user", "enable", "--now", "nexo-lineage.timer"),
            ),
            (("systemctl", "--user", "disable", "--now", "nexo-lineage.timer"),),
            (str(service), str(timer)),
        )
    if system == "Windows":
        command = f'"{nexo}" lineage agent run'
        return Plan(
            system,
            {},
            (("schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(minutes), "/TN", TASK, "/TR", command),),
            (("schtasks", "/Delete", "/F", "/TN", TASK),),
            (),
        )
    raise NexoError(f"sistema não suportado: {system}")


def install(nexo: str, home: Path | None = None, system: str | None = None) -> dict[str, object]:
    p = plan(system or platform.system(), nexo, home or Path.home())
    for name, body in p.files.items():
        Path(name).parent.mkdir(parents=True, exist_ok=True)
        Path(name).write_text(body, encoding="utf-8", newline="\n")
    for cmd in p.install:
        done = subprocess.run(
            list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        )
        if done.returncode != 0:
            raise NexoError(f"o sistema recusou o agendamento ({cmd[0]}): {done.stderr.strip()[:200]}")
    return {"system": p.system, "installed": True}


def uninstall(home: Path | None = None, system: str | None = None) -> dict[str, object]:
    p = plan(system or platform.system(), "nexo", home or Path.home())
    for cmd in p.remove:
        subprocess.run(list(cmd), capture_output=True, check=False)
    for name in p.delete:
        Path(name).unlink(missing_ok=True)
    return {"system": p.system, "installed": False}


def install_hook(repo: Path, nexo: str) -> str:
    """Hook `post-merge`, em segundo plano: nunca bloqueia nem falha o Git. Preserva o que já existe."""
    hooks = repo / ".git" / "hooks"
    if not hooks.is_dir():
        return ""
    path = hooks / "post-merge"
    line = f'"{nexo.replace(os.sep, "/")}" lineage agent run --repo . >/dev/null 2>&1 &  {MARK}\n'
    current = path.read_text(encoding="utf-8") if path.is_file() else "#!/bin/sh\n"
    if MARK in current:
        return str(path)
    path.write_text(current.rstrip("\n") + "\n" + line, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return str(path)


def remove_hook(repo: Path) -> None:
    path = repo / ".git" / "hooks" / "post-merge"
    if path.is_file():
        kept = [ln for ln in path.read_text(encoding="utf-8").splitlines(keepends=True) if MARK not in ln]
        path.write_text("".join(kept), encoding="utf-8", newline="\n")
