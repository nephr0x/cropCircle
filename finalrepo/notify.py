"""Send a message to an account by text or email. Every message is also saved so it shows in the app."""
import smtplib
from email.message import EmailMessage

import httpx

import config, db


def _sms(to, body):
    if not (to and config.TWILIO_ACCOUNT_SID and config.TWILIO_FROM_NUMBER):
        return False
    digits = "".join(ch for ch in to if ch.isdigit())
    to = "+" + digits if len(digits) > 10 else "+1" + digits
    r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{config.TWILIO_ACCOUNT_SID}/Messages.json",
                   auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN),
                   data={"From": config.TWILIO_FROM_NUMBER, "To": to, "Body": body[:1500]}, timeout=10)
    return r.status_code < 300


def _email(to, subject, body):
    if not (to and config.SMTP_HOST and config.SMTP_FROM):
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, to, subject
    msg.set_content(body)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as s:
        s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(msg)
    return True


def send(account, subject, body, channel=None, sender=None):
    """channel: 'text' or 'email'. Defaults to the account's preference, then whatever it has.
    sender: the account that caused the message, so it shows under their Sent messages."""
    channel = channel or account.get("contact_pref") or "text"
    if channel == "text" and not account.get("phone"):
        channel = "email"
    if channel == "email" and not account.get("email"):
        channel = "text"
    to = account.get("phone") if channel == "text" else account.get("email")
    try:
        sent = _sms(to, f"{subject}\n{body}") if channel == "text" else _email(to, subject, body)
    except Exception:
        sent = False
    db.insert("outbox", {"account_id": account["id"], "sender_id": sender and sender["id"],
                         "to_name": account.get("name"), "channel": channel, "to_addr": to or "",
                         "subject": subject, "body": body, "sent": int(sent)})
    return {"channel": channel, "to": to, "sent": sent}
