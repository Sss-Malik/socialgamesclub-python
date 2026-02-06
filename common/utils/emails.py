import json
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Iterable, Mapping, Union
import traceback
import base64

from common.utils.db_actions import insert_log
from settings import (
    ACTIVATE_EMAILS,
    MAIL_FROM_ADDRESS,
    MAIL_FROM_NAME,
    MAIL_PASSWORD,
    MAIL_HOST,
    MAIL_PORT,
    MAIL_USERNAME,
    MAIL_ENCRYPTION,
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



def auth_login(server, username, password):
    server.docmd("AUTH", "LOGIN")
    server.docmd(base64.b64encode(username.encode()).decode())
    server.docmd(base64.b64encode(password.encode()).decode())


def send_email(
    subject: str,
    body: BodyType,
    *,
    reply_to: Union[str, None] = None,
    timeout: int = 20,
    to_email: Union[str, Iterable[str]] = MAIL_RECIPIENT,
) -> None:
    """
    Send a simple text email.
    Fails gracefully — exceptions are caught and logged.
    """

    if not ACTIVATE_EMAILS:
        print("Emails deactivated; skipping send.")
        return

    # --- Validate config (non-fatal) ---
    missing = []
    if not MAIL_HOST:
        missing.append("MAIL_HOST")
    if not MAIL_PORT:
        missing.append("MAIL_PORT")
    if not MAIL_USERNAME:
        missing.append("MAIL_USERNAME")
    if not MAIL_PASSWORD:
        missing.append("MAIL_PASSWORD")
    if not MAIL_FROM_ADDRESS:
        missing.append("MAIL_FROM_ADDRESS")

    if missing:
        print(f"[Email] Missing configuration: {', '.join(missing)}")
        return

    # --- Normalize recipients ---
    if isinstance(to_email, (list, tuple, set)):
        recipients = list(to_email)
    else:
        recipients = [to_email]

    if not recipients:
        print("[Email] No recipients provided.")
        return

    # --- Build message ---
    msg = EmailMessage()
    msg["From"] = (
        f"{MAIL_FROM_NAME} <{MAIL_FROM_ADDRESS}>"
        if MAIL_FROM_NAME
        else MAIL_FROM_ADDRESS
    )
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    if reply_to:
        msg["Reply-To"] = reply_to

    msg.set_content(_format_body(body))

    # --- Send email safely ---
    try:
        context = ssl.create_default_context()

        with smtplib.SMTP(MAIL_HOST, MAIL_PORT, timeout=20) as server:
            server.set_debuglevel(1)
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()

            auth_login(server, MAIL_USERNAME, MAIL_PASSWORD)
            server.send_message(msg)

    except Exception as exc:
        insert_log("error", f"Email sending failed: {exc}")
        # Swallow exception intentionally — execution continues
