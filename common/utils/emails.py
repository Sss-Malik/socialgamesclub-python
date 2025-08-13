import json
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Iterable, Mapping, Union

from settings import (
    MAIL_FROM_ADDRESS,
    MAIL_FROM_NAME,
    MAIL_PASSWORD,
    MAIL_HOST,
    MAIL_PORT,

    MAIL_RECIPIENT
    # optionally: MAIL_ENCRYPTION = "tls" | "ssl" | "none"
)

BodyType = Union[str, Mapping[str, Any], Iterable[Any]]

def _format_body(body: BodyType) -> str:
    """Nicely format dicts/lists; pass strings through."""
    if isinstance(body, str):
        return body

    # Flat dict -> aligned "Key: Value" lines (nicer to read in plain text)
    if isinstance(body, Mapping):
        # decide if it's "flat" (no nested dict/list values)
        is_flat = all(not isinstance(v, (Mapping, list, tuple)) for v in body.values())
        if is_flat:
            # stable ordering by key for deterministic emails
            items = sorted(body.items(), key=lambda kv: str(kv[0]).lower())
            width = max((len(str(k)) for k, _ in items), default=0)
            lines = [f"{str(k).rjust(width)}: {v}" for k, v in items]
            return "\n".join(lines)
        # nested dict -> pretty JSON
        return json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False)

    # Lists/tuples -> bullet list
    if isinstance(body, (list, tuple)):
        if all(isinstance(x, (str, int, float, bool)) for x in body):
            return "\n".join(f"- {x}" for x in body)
        # complex sequences -> pretty JSON
        return json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False)

    # Fallback: stringify
    return str(body)


def send_email(
    subject: str,
    body: BodyType,
    *,
    reply_to: Union[str, None] = None,
    timeout: int = 20,
    to_email: Union[str, Iterable[str]] = MAIL_RECIPIENT,
) -> None:
    """
    Send a simple text email. SMTP settings come from `settings`.
    - `to_email` can be a single address or an iterable of addresses.
    - `body` can be str | dict | list/tuple (dicts/lists are pretty-formatted).
    """
    # --- Validate config from settings ---
    if not MAIL_HOST:
        raise ValueError("MAIL_HOST is not set.")
    if not MAIL_PORT:
        raise ValueError("MAIL_PORT is not set.")
    if not MAIL_PASSWORD:
        raise ValueError("SMTP password / SENDGRID_API_KEY not set.")
    if not MAIL_FROM_ADDRESS:
        raise ValueError("MAIL_FROM_ADDRESS is not set.")

    # --- Normalize recipients ---
    if isinstance(to_email, (list, tuple, set)):
        recipients = list(to_email)
    else:
        recipients = [to_email]

    # --- Build message ---
    msg = EmailMessage()
    msg["From"] = f"{MAIL_FROM_NAME} <{MAIL_FROM_ADDRESS}>" if MAIL_FROM_NAME else MAIL_FROM_ADDRESS
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to

    rendered = _format_body(body)
    msg.set_content(rendered)

    # --- Send (STARTTLS) ---
    context = ssl.create_default_context()
    with smtplib.SMTP(MAIL_HOST, int(MAIL_PORT), timeout=timeout) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        # Some providers (e.g., SendGrid) use 'apikey' as username with the API key as password
        server.login("apikey", MAIL_PASSWORD)
        server.send_message(msg)
