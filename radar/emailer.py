"""Send the digest over SMTP (e.g. Gmail with an app password)."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def smtp_configured() -> bool:
    return all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"))


def send(subject: str, html_body: str, text_body: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT") or 587)
    user, pw = os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"]
    to = [a.strip() for a in os.environ["EMAIL_TO"].split(",") if a.strip()]
    sender = os.getenv("EMAIL_FROM") or user

    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(to)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, pw)
            s.sendmail(sender, to, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, pw)
            s.sendmail(sender, to, msg.as_string())
