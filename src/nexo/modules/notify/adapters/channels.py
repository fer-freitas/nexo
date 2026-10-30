"""Canais de notificação: Teams (Workflows/webhook), Slack (webhook), Google Chat (webhook) e e-mail (SMTP) — RF-47."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from nexo.kernel.runtime import http


def teams(webhook_url: str, title: str, message: str, link: str | None = None) -> None:
    card = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "body": [
                        {"type": "TextBlock", "text": title, "weight": "Bolder", "wrap": True},
                        {"type": "TextBlock", "text": message, "wrap": True},
                    ],
                    "actions": [{"type": "Action.OpenUrl", "title": "Abrir", "url": link}] if link else [],
                },
            }
        ],
    }
    http.request("POST", webhook_url, json_body=card, expect=(200, 202))


def slack(webhook_url: str, title: str, message: str, link: str | None = None) -> None:
    text = f"*{title}*\n{message}" + (f"\n<{link}|Abrir>" if link else "")
    http.request("POST", webhook_url, json_body={"text": text}, expect=(200,))


def google_chat(webhook_url: str, title: str, message: str, link: str | None = None) -> None:
    """Google Chat: webhook de entrada do espaço."""
    text = f"*{title}*\n{message}" + (f"\n<{link}|Abrir>" if link else "")
    http.request("POST", webhook_url, json_body={"text": text}, expect=(200,))


def email(
    host: str,
    port: int,
    sender: str,
    recipients: list[str],
    title: str,
    message: str,
    user: str | None = None,
    password: str | None = None,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"[Nexo] {title}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(message)
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)
