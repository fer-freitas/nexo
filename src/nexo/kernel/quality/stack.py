"""Detecção determinística de stack a partir dos arquivos do projeto."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath

STACKS = ("dotnet", "java", "react", "angular", "flutter", "python", "node")
JAVA_BUILD = {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
# pastas de plataforma de um app Flutter: o Gradle de `android/` é do Flutter, não um projeto Java
FLUTTER_PLATFORMS = {"android", "ios", "macos", "linux", "windows", "web"}


def detect_stacks(paths: Iterable[str], read: Callable[[str], str]) -> list[str]:
    """`paths` relativos (separador /); `read` devolve o conteúdo de um arquivo relativo."""
    all_paths = [PurePosixPath(raw) for raw in paths]
    flutter_roots = {p.parent for p in all_paths if p.name == "pubspec.yaml" and _is_flutter(read(p.as_posix()))}
    found: set[str] = set()
    for p in all_paths:
        name = p.name
        if p.suffix in {".csproj", ".sln", ".fsproj"} or name == "global.json":
            found.add("dotnet")
        elif name in JAVA_BUILD and not _inside_flutter_platform(p, flutter_roots):
            found.add("java")
        elif name in {"pyproject.toml", "requirements.txt", "setup.py", "Pipfile"}:
            found.add("python")
        elif name == "pubspec.yaml" and p.parent in flutter_roots:
            found.add("flutter")
        elif name == "package.json" and "node_modules" not in p.parts:
            found.add(_classify_package_json(read(p.as_posix())))
    return sorted(found, key=STACKS.index)


def _is_flutter(content: str) -> bool:
    """`pubspec.yaml` de app ou pacote Flutter (dependência do SDK `flutter`); Dart puro fica de fora."""
    return any(line.strip().startswith("flutter:") or "sdk: flutter" in line for line in content.splitlines())


def _inside_flutter_platform(path: PurePosixPath, flutter_roots: set[PurePosixPath]) -> bool:
    for root in flutter_roots:
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in FLUTTER_PLATFORMS:
            return True
    return False


def _classify_package_json(content: str) -> str:
    try:
        data = json.loads(content)
    except ValueError:
        return "node"
    deps: dict[str, str] = {}
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        value = data.get(section)
        if isinstance(value, dict):
            deps.update(value)
    if "@angular/core" in deps:
        return "angular"
    return "react" if "react" in deps else "node"
