"""Ambiente mínimo dos agentes: lista de permissão; segredos nunca herdados por acidente."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase

# Sistema, locale, terminal, temporários, proxy e certificados corporativos (macOS, Linux e Windows).
BASE_ALLOW = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LANGUAGE",
    "LC_*",
    "TERM",
    "COLORTERM",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "XDG_*",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "PYTHONDONTWRITEBYTECODE",
    "JAVA_HOME",
    "DOTNET_ROOT",
    "NVM_DIR",
    "PYENV_ROOT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "USERPROFILE",
    "USERNAME",
    "USERDOMAIN",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "COMMONPROGRAMFILES",
    "HOMEDRIVE",
    "HOMEPATH",
    "COMPUTERNAME",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "NX_*",
)
# Nunca repassados, nem por padrão amplo: identidade do aprovador.
ALWAYS_DENY = ("NX_ACTOR",)
SENSITIVE = re.compile(
    r"TOKEN|SECRET|(^|_)PAT(_|$)|PASS(WORD|WD)?|PWD$|KEY|CREDENTIAL|AUTH|COOKIE|SESSION|CONN(ECTION)?_?STR", re.I
)


def _match(name: str, patterns: Iterable[str]) -> bool:
    upper = name.upper()
    return any(fnmatchcase(upper, p.upper()) for p in patterns)


def filter_env(environ: Mapping[str, str], allow: Iterable[str] = ()) -> tuple[dict[str, str], list[str]]:
    """Mantém só o permitido. Nome com cara de segredo só passa se estiver em `allow` (permissão explícita
    da ferramenta ou da org), nunca pela lista base. Devolve (ambiente, nomes removidos) — valores nunca saem."""
    explicit = tuple(allow)
    kept: dict[str, str] = {}
    removed: list[str] = []
    for name, value in environ.items():
        if _match(name, ALWAYS_DENY):
            removed.append(name)
        elif _match(name, explicit) or (_match(name, BASE_ALLOW) and not SENSITIVE.search(name)):
            kept[name] = value
        else:
            removed.append(name)
    return kept, sorted(removed)
