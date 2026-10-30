"""Notificações: sempre auditadas; enviadas aos canais configurados por evento (Teams primeiro)."""

from __future__ import annotations

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.secrets import credentials
from nexo.modules.notify.adapters import channels as channels

CHATS = ("teams", "slack", "google_chat")  # "chat" nos eventos = os chats ligados
DEFAULT_EVENTS = {
    "story": ["chat", "email"],
    "budget": ["email", "chat"],
    "gate": ["chat"],
    "monitor": ["chat", "email"],  # O-03: formato de CLI divergente
    "clarification": ["chat"],  # pergunta do agente com link para responder
    "planning": ["chat", "email"],  # passagem entre papéis (pacote, aprovações, pronto para o dev)
    "change": ["chat", "email"],  # requisição de mudança aberta, aprovada ou recusada
}


def notify(
    ctx: AppContext, event: str, title: str, message: str, ref: str | None = None, link: str | None = None
) -> list[str]:
    cfg = dict(config.resolved(ctx).values.get("notify") or {})
    enabled = {k for k, v in (cfg.get("channels") or {}).items() if v}
    listed = (cfg.get("events") or DEFAULT_EVENTS).get(event, [])
    expanded = [x for c in listed for x in (CHATS if c == "chat" else (c,))]
    wanted = [c for c in dict.fromkeys(expanded) if c in enabled]
    sent: list[str] = []
    errors: dict[str, str] = {}
    for channel in wanted:
        try:
            _send(channel, cfg, title, message, link)
            sent.append(channel)
        except (NexoError, OSError) as exc:  # notificação nunca interrompe o fluxo
            errors[channel] = exc.message if isinstance(exc, NexoError) else str(exc)
    ctx.audit(
        "notify",
        {"event": event, "title": title, "message": message, "channels": ["local", *sent], "errors": errors},
        ref=ref,
    )
    return sent


def _send(channel: str, cfg: dict[str, object], title: str, message: str, link: str | None) -> None:
    if channel == "teams":
        channels.teams(_secret("teams"), title, message, link)
    elif channel == "slack":
        channels.slack(_secret("slack"), title, message, link)
    elif channel == "google_chat":
        channels.google_chat(_secret("google_chat"), title, message, link)
    elif channel == "email":
        email = dict(cfg.get("email") or {})  # type: ignore[call-overload]
        recipients = [str(r) for r in email.get("to") or []]
        if not recipients or not email.get("host"):
            raise NexoError("e-mail sem host/destinatários (config notify.email)")
        channels.email(
            str(email["host"]),
            int(email.get("port", 587)),
            str(email.get("from", "nexo@localhost")),
            recipients,
            title,
            message + (f"\n\n{link}" if link else ""),
            str(email["user"]) if email.get("user") else None,
            credentials.get_secret("smtp") if email.get("user") else None,
        )
    else:
        raise NexoError(f"canal desconhecido: {channel}")


def _secret(name: str) -> str:
    value = credentials.get_secret(name)
    if not value:
        raise NexoError(f"credencial '{name}' ausente: rode `nexo login {name}`")
    return value
