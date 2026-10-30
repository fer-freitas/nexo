"""Rastreabilidade critério → teste → resultado."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from nexo.kernel.quality.model import FAILED, PASSED, TestCase

AC_IN_TEXT = re.compile(r"(?<![A-Za-z0-9])AC[-_ ]?(\d+)(?!\d)", re.IGNORECASE)


@dataclass
class AcTrace:
    ac: str
    tests: list[str] = field(default_factory=list)
    status: str = "missing"  # missing | passed | failed


def ac_ids_in(text: str) -> set[str]:
    return {f"AC-{int(m.group(1))}" for m in AC_IN_TEXT.finditer(text)}


def trace(ac_ids: list[str], tests: list[TestCase], file_tags: dict[str, set[str]]) -> list[AcTrace]:
    """Um teste cobre um AC se o nome do caso cita o AC ou se o arquivo do teste declara a tag."""
    out: list[AcTrace] = []
    for ac in ac_ids:
        covering = []
        for t in tests:
            tags = ac_ids_in(t.name) | ac_ids_in(t.classname)
            for path, file_ac in file_tags.items():
                same_file = bool(t.file) and (t.file.endswith(path) or path.endswith(t.file))
                same_file = same_file or (bool(t.classname) and t.classname.endswith(path))  # Vitest, Flutter
                same_module = bool(t.classname) and path.replace("/", ".").removesuffix(".py").endswith(t.classname)
                if same_file or same_module:
                    tags |= file_ac
            if ac in tags:
                covering.append(t)
        if not covering:
            out.append(AcTrace(ac))
            continue
        status = FAILED if any(t.status != PASSED and t.status != "skipped" for t in covering) else PASSED
        out.append(AcTrace(ac, [f"{t.classname}::{t.name}".strip(":") for t in covering], status))
    return out
