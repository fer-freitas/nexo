"""Modo isolado do agente: contêiner com rede só pelo proxy de saída (lista de permissões).

O agente roda com o worktree montado em /work, sem capabilities, sem novos privilégios e com limites de processo e
memória. A rede do contêiner só alcança o proxy `nexo-egress`, que libera apenas os domínios do fornecedor do modelo
e dos registries aprovados. Hooks nativos e o servidor MCP do Nexo (que apontam para o host) ficam desligados dentro
do contêiner; a política continua garantida pela verificação do diff ao fim do passo.
"""

from __future__ import annotations

import re

VENDOR_DOMAINS: dict[str, tuple[str, ...]] = {
    "anthropic": ("api.anthropic.com", "statsig.anthropic.com"),
    "github": ("api.githubcopilot.com", "api.github.com", "copilot-proxy.githubusercontent.com"),
    "openai": ("api.openai.com",),
    "cognition": ("api.devin.ai", "app.devin.ai"),
    "google": ("generativelanguage.googleapis.com",),
}
REGISTRY_DOMAINS: tuple[str, ...] = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "api.nuget.org",
)


def allowlist(
    vendors: set[str], registries: tuple[str, ...] = REGISTRY_DOMAINS, extra: tuple[str, ...] = ()
) -> list[str]:
    domains: set[str] = set(registries) | set(extra)
    for vendor in vendors:
        domains |= set(VENDOR_DOMAINS.get(vendor, ()))
    return sorted(domains)


ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
IMAGE = re.compile(r"[a-z0-9][a-z0-9._/-]{0,200}(:[A-Za-z0-9._-]{1,128})?(@sha256:[a-f0-9]{64})?")


def container_command(
    cmd: list[str],
    workdir: str,
    image: str,
    env_names: list[str],
    engine: str = "docker",
    network: str = "nexo-egress",
    proxy: str = "http://nexo-egress:3128",
    memory: str = "4g",
) -> list[str]:
    if not IMAGE.fullmatch(image):
        raise ValueError(f"imagem do agente inválida: {image!r}")
    args = [
        engine,
        "run",
        "--rm",
        "-i",
        "--network",
        network,
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "512",
        "--memory",
        memory,
        "-v",
        f"{workdir}:/work",
        "-w",
        "/work",
    ]
    if proxy:
        args += ["-e", f"HTTPS_PROXY={proxy}", "-e", f"HTTP_PROXY={proxy}", "-e", "NO_PROXY=localhost,127.0.0.1"]
    for name in sorted({n for n in env_names if ENV_NAME.fullmatch(n)}):
        args += ["-e", name]  # valor vem do ambiente já filtrado do processo, nunca na linha de comando
    return [*args, image, *cmd]


def squid_conf(domains: list[str]) -> str:
    acl = "\n".join(f"acl permitido dstdomain {d}" for d in domains)
    return (
        "# Proxy de saída do modo isolado (gerado por `nexo admin isolation setup`): só os domínios abaixo.\n"
        "http_port 3128\n"
        f"{acl}\n"
        "http_access allow permitido\n"
        "http_access deny all\n"
        "cache deny all\n"
    )


def compose(proxy_image: str = "ubuntu/squid:6.6-24.04_beta") -> str:
    return (
        "# Rede isolada do Nexo: `docker compose -f egress.compose.yml up -d`\n"
        "services:\n"
        "  nexo-egress:\n"
        f"    image: {proxy_image}\n"
        "    container_name: nexo-egress\n"
        "    restart: unless-stopped\n"
        "    volumes:\n"
        "      - ./squid.conf:/etc/squid/squid.conf:ro\n"
        "    networks: [nexo-egress, saida]\n"
        "networks:\n"
        "  nexo-egress:\n"
        "    name: nexo-egress\n"
        "    internal: true  # contêineres do agente só alcançam o proxy\n"
        "  saida: {}\n"
    )
