"""SMTP email delivery."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

from aurora.config import EmailDeliveryConfig
from aurora.models import DeliveryResult, RenderedDigest


def send_email(rendered: RenderedDigest, config: EmailDeliveryConfig) -> DeliveryResult:
    """Send a rendered digest through SMTP."""
    recipients = _recipients(config.recipients_env)
    username = os.getenv(config.smtp_username_env)
    password = os.getenv(config.password_env)
    if not recipients:
        return DeliveryResult(channel="email", ok=False, error="missing email recipients")
    if not username or not password:
        return DeliveryResult(channel="email", ok=False, error="missing SMTP credentials")

    message = EmailMessage()
    message["Subject"] = rendered.title
    message["From"] = f"{config.sender_name} <{username}>"
    message["To"] = ", ".join(recipients)
    message.set_content(rendered.markdown)
    if rendered.html:
        message.add_alternative(rendered.html, subtype="html")

    with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)
    return DeliveryResult(channel="email", destination=config.recipients_env)


def _recipients(env_name: str) -> list[str]:
    raw = os.getenv(env_name, "")
    return [recipient.strip() for recipient in raw.split(",") if recipient.strip()]
