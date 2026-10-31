"""Valores padrão embutidos (camada mais baixa da cascata)."""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "lang": "pt-BR",
    "profile": "balanceado",
    "data_class": "confidential",
    "max_parallel": 3,
    "bug_loop": {"max_attempts": 3, "max_spec_returns": 2},
    "handoff": {"max_per_task": 3},
    "gates": {"default_threshold": 0.95},
    # SEC-52: autoria e aprovação com identidade corporativa verificada; `os` só na org/squad.
    "identity": {"mode": "verified"},
    # O-03: monitor sintético das CLIs (rotas vazias = todas as do catálogo); falha de formato bloqueia a rota local
    "monitor": {"routes": [], "auto_block": True},
    # a org liga e trava (enabled/url só valem da org/squad); atraso: aviso e bloqueio em dias.
    # guarda do Nexo no Claude Code do dev: o piloto automático instala sozinho (auto) e a org pode exigir (required)
    "guard": {"auto": True, "required": False},
    "telemetry": {
        "enabled": False,
        "url": "",
        "background": True,
        "max_delay_days": {"warn": 3, "block": 7},
        # enviar ao central os totais de IA usados fora do Nexo (só org/squad liga; só agregados por dia)
        "external_usage": False,
    },
    "brain": {"repo": "", "auto_pull": True},
    "tracker": {"kind": "local"},
    "scm": {"provider": "none", "remote": "origin", "open_pr": True},
    "notify": {"channels": {"teams": False, "email": False}},
    # Preços em USD com vigência; câmbio configurável (contratos renovados não reescrevem o histórico).
    # os valores de fábrica são a tabela pública de cada ferramenta na data abaixo; cada cliente informa o
    # próprio contrato (`nexo cost pricing set`) e, enquanto não informar, o custo aparece como estimado.
    "pricing": {
        "list_prices_as_of": "2026-10-01",
        "fx": [{"usd_brl": 5.40, "valid_from": "2026-01-01"}],
        "subscriptions": {
            "claude": {"usd_month": 100, "valid_from": "2026-01-01"},  # Claude Max 5x
            "copilot": {"usd_month": 19, "valid_from": "2026-01-01", "unit_price_usd": 0.04},  # Business
            # Devin Team: 250 ACUs inclusos, US$ 2,00 por ACU excedente
            "devin": {"usd_month": 500, "valid_from": "2026-01-01", "overage_usd": 2.0, "included_units": 250},
        },
    },
    "budget": {
        "alerts": [0.7, 0.9],
        "source": "local",
        # teto por história = fixo, ou max(piso, estimativa alta × fator).
        "story_cap": {
            "fixed_brl": None,
            "floor_brl": 20.0,
            "factor": 1.5,
            "alert": 0.8,
            "self_raise_factor": 2.0,
            "economic_raise_pct": 25.0,
        },
        "feature_cap_brl": None,
        # limite vindo de fora (`source: external`): arquivo YAML ou URL HTTPS, relido a cada ttl.
        "external": {"file": "", "url": "", "ttl_seconds": 300},
    },
    # quem aprova cada portão (quality | cost | budget): o Nexo ou um processo externo que só devolve
    # o sinal continue/stop. `mode`/`gates` só valem da org ou do squad.
    # documentos cujas seções obrigatórias entram na DoR (sdd | functional-requirements |
    # technical-design | epic | story | task) e publicação (none | docusaurus).
    # fluxo por papéis (vale só da org/squad). joint_session: always | auto (risco e tamanho) | never.
    "planning": {
        "required_documents": ["sdd"],
        "role_flow": {
            "enabled": False,
            "joint_session": "auto",
            "min_points": 13,
            "max_stories": 5,
            "sensitive": ["auth", "payments", "personal_data", "migration", "infra"],
            "allow_same_person": False,
        },
    },
    # quem tem cada papel no fluxo de planejamento (identidades verificadas, ex.: github:ana).
    "roles": {"pm": [], "tech_lead": [], "dev": []},
    # código existente — caracterização abaixo de X% de cobertura, orçamento de contexto (caracteres).
    "brownfield": {
        "characterization": True,
        "characterization_below": 60,
        "context_chars": 3000,
        "repo_map_chars": 6000,
    },
    "docs": {"publish": {"target": "none", "path": "/Nexo"}},
    "approvals": {
        "mode": "nexo",
        "gates": {},
        "channel": "file",
        "file": {"dir": ""},
        "http": {"url": ""},
        "tracker": {
            "continue_states": ["Approved", "Aprovado"],
            "stop_states": ["Rejected", "Reprovado"],
            "continue_tags": ["nexo:continuar"],
            "stop_tags": ["nexo:parar"],
        },
        "on_continue": {"cost_raise_pct": 50.0, "budget_grant_pct": 20.0},
    },
    "estimate": {"prior_brl_per_point": 15.0},
    # `propose` gera proposta revisável; `auto` usa o histórico direto no roteamento.
    "routing": {"learning": "propose", "min_samples": 5},
    "learning": {"batch_size": 20, "sources": {}},
    "agents": {"timeout_minutes": 30, "max_steps": 8, "max_policy_denials": 3},
    # Fornecedores (ferramenta e modelo) permitidos por classe de dados. "*" = todos.
    "data_policy": {
        "public": ["*"],
        "internal": ["anthropic", "github", "openai", "cognition"],
        "confidential": ["anthropic", "github", "openai", "cognition"],
        "restricted": [],
    },
}

PROFILES = ("economico", "balanceado", "maxima-qualidade")
LANGS = ("pt-BR", "en")
DATA_CLASSES = ("public", "internal", "confidential", "restricted")
