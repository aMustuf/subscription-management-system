"""Sending email.

Every email is stored in the email_logs table (the Email outbox page). If SMTP
settings exist in .env, the email is also really sent; otherwise it is only
logged, which keeps the demo working without any email account.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app

from extensions import db
from models import EmailLog


def email_enabled() -> bool:
    return bool(current_app.config.get("MAIL_SERVER"))


def send_email(to: str, subject: str, body: str, kind: str = "general") -> EmailLog:
    log = EmailLog(to_email=to, subject=subject, body=body, kind=kind, status="logged")
    if email_enabled():
        cfg = current_app.config
        msg = EmailMessage()
        msg["From"] = cfg["MAIL_FROM"]
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if cfg.get("MAIL_USERNAME"):
                    smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
                smtp.send_message(msg)
            log.status = "sent"
        except Exception as exc:  # noqa: BLE001 - we want the app to keep working
            log.status = "failed"
            log.error = str(exc)[:250]
            current_app.logger.warning("Email to %s failed: %s", to, exc)
    db.session.add(log)
    return log
