"""Email notifications for completed / failed experiments.

Mirrors the MATLAB ``sendEmail.m`` / ``sendError.m`` / ``setNILEmail.m``
workflow: at the end of each experiment the user gets a Gmail-relayed
email with the elapsed time and (optionally) the saved session as an
attachment. Errors during the run trigger a failure-flavoured message
with the exception text instead of the success summary.

Differences from the MATLAB version
-----------------------------------
* **Credentials are NOT hardcoded.** The MATLAB ``setNILEmail.m``
  embeds an app password in the source. That's a security gun pointed
  at your foot the moment the file is shared. Here, credentials come
  from one of:
    1. environment variables
       (``STIMTEST_SMTP_USER`` / ``STIMTEST_SMTP_PASSWORD`` /
       ``STIMTEST_SMTP_SERVER`` / ``STIMTEST_SMTP_PORT``), or
    2. a JSON config at ``~/.stimtest/email_config.json`` of the form
       ``{"smtp_user": "...", "smtp_password": "...",
          "smtp_server": "smtp.gmail.com", "smtp_port": 465}``
  Either path keeps the secrets out of version control. If neither
  is set, :func:`send_completion_email` and :func:`send_error_email`
  silently return without sending — so the GUI keeps working when
  email isn't configured.
* **SMS via email-to-SMS gateways** is supported the same way the
  MATLAB ``sendError.m`` does it (verizon → @vtext.com, att →
  @txt.att.net, etc.). Optional; only used when phone + carrier are
  populated.
* **Sign-off** is "-Christopher K. Nguyen, PhD" instead of the
  MATLAB code's "-The Neural Interfaces Lab".

The module is sync (``smtplib`` is sync). Callers should invoke it
from a worker thread or a fire-and-forget thread, not the GUI thread,
so a slow SMTP server doesn't block the UI.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, List, Optional


# Sign-off line on every email sent from this module. Match the
# MATLAB convention's leading dash so the signature reads as a
# clear close.
SIGN_OFF = "-Christopher K. Nguyen, PhD"


# Carrier → email-to-SMS-gateway domain. Direct port of the switch
# block in ``sendError.m`` for the same providers.
SMS_GATEWAYS = {
    "alltel":     "message.alltel.com",
    "att":        "txt.att.net",
    "boost":      "myboostmobile.com",
    "cingular":   "cingularme.com",
    "cingular2":  "mobile.mycingular.com",
    "cricket":    "sms.mycricket.com",
    "metropcs":   "mymetropcs.com",
    "nextel":     "messaging.nextel.com",
    "sprint":     "messaging.sprintpcs.com",
    "tmobile":    "tmomail.net",
    "tracfone":   "mmst5@tracfone.com",
    "uscellular": "email.uscc.net",
    "verizon":    "vtext.com",
    "virgin":     "vmobl.com",
}


@dataclass
class EmailConfig:
    """SMTP server + sender credentials, loaded from env or JSON."""
    smtp_user: str
    smtp_password: str
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 465      # SSL/TLS, matches the MATLAB Java config

    @property
    def configured(self) -> bool:
        return bool(self.smtp_user) and bool(self.smtp_password)


def _config_path() -> Path:
    """Path to the optional JSON config file."""
    return Path.home() / ".stimtest" / "email_config.json"


def load_config() -> EmailConfig:
    """Resolve credentials from env vars, falling back to the JSON file.

    Returns an ``EmailConfig`` whose ``configured`` attribute is False
    when nothing's set, so callers can decide whether to skip without
    raising. Callers that need email and find it unconfigured should
    log a clear message; the runner should NOT abort just because
    notifications can't go out.
    """
    user = os.environ.get("STIMTEST_SMTP_USER", "")
    password = os.environ.get("STIMTEST_SMTP_PASSWORD", "")
    server = os.environ.get("STIMTEST_SMTP_SERVER", "smtp.gmail.com")
    port = os.environ.get("STIMTEST_SMTP_PORT", "465")
    if not (user and password):
        # Fall back to the JSON file. Missing / unreadable file is
        # equivalent to "not configured" — never surface an exception
        # from a notifications path.
        p = _config_path()
        if p.is_file():
            try:
                blob = json.loads(p.read_text(encoding="utf-8"))
                user = user or str(blob.get("smtp_user", ""))
                password = password or str(blob.get("smtp_password", ""))
                server = blob.get("smtp_server", server) or server
                port = str(blob.get("smtp_port", port))
            except Exception:
                pass
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        port_i = 465
    return EmailConfig(smtp_user=user, smtp_password=password,
                       smtp_server=server, smtp_port=port_i)


def _format_elapsed(seconds: float) -> str:
    """MATLAB-style ``%.2f %s`` rendering of a duration."""
    if seconds < 60:
        return f"{seconds:.2f} s"
    if seconds < 3600:
        return f"{seconds / 60:.2f} min"
    return f"{seconds / 3600:.2f} h"


def _greeting_name(to_email: str, recipient_name: str) -> str:
    """Pick a salutation: first name when supplied, else local part of
    the email address before the @."""
    if recipient_name.strip():
        # Use the first space-delimited token (works for "First Last"
        # and "First Middle Last" alike). Falls back to the whole
        # string when there's no space.
        return recipient_name.strip().split()[0]
    if "@" in to_email:
        return to_email.split("@", 1)[0]
    return "there"


def _send(message: EmailMessage, config: EmailConfig) -> None:
    """Send a prepared :class:`EmailMessage` via SMTP_SSL.

    Mirrors the MATLAB ``setNILEmail.m`` Java configuration:
    ``smtp.gmail.com:465`` with SSL (not STARTTLS on 587). Raises on
    connection / auth failures so the caller can log a useful error;
    the caller is expected to wrap this in a try/except so an
    unreachable mail server doesn't break the run.
    """
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(config.smtp_server, config.smtp_port,
                          context=context) as server:
        server.login(config.smtp_user, config.smtp_password)
        server.send_message(message)


def _attach_files(msg: EmailMessage, attachments: Iterable) -> List[Path]:
    """Attach files; return the list of paths actually attached.

    Quietly drops missing files so a stale path doesn't fail the
    send. Skips files larger than 24 MB (typical Gmail per-attachment
    cap with margin) and logs them in the body via a return value
    the caller can mention.
    """
    attached: List[Path] = []
    for raw in attachments:
        if raw is None:
            continue
        p = Path(raw)
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > 24 * 1024 * 1024:
            continue
        try:
            with p.open("rb") as f:
                msg.add_attachment(f.read(),
                                   maintype="application",
                                   subtype="octet-stream",
                                   filename=p.name)
            attached.append(p)
        except Exception:
            # Skip individual attachment failures rather than aborting
            # the whole send.
            continue
    return attached


# ---------------------------------------------------------------------------
# Public entry points — match the MATLAB sendEmail.m / sendError.m calls
# ---------------------------------------------------------------------------
def send_completion_email(*,
                          to_email: str,
                          recipient_name: str = "",
                          subject_name: str = "",
                          test_name: str = "",
                          elapsed_seconds: Optional[float] = None,
                          attachments: Optional[Iterable] = None,
                          config: Optional[EmailConfig] = None) -> bool:
    """Send the "experiment completed" email.

    Returns True on success, False when skipped (no recipient or
    SMTP credentials missing) or on failure. Never raises — the
    runner shouldn't fail because mail couldn't go out.

    ``subject_name`` and ``test_name`` are folded into the email
    subject the same way ``sendEmail.m`` did:
    ``"Stimulation: <subject> <test>"``.
    """
    if not to_email:
        return False
    cfg = config or load_config()
    if not cfg.configured:
        return False

    elapsed_str = (_format_elapsed(elapsed_seconds)
                   if elapsed_seconds is not None else "")
    greeting_name = _greeting_name(to_email, recipient_name)

    subject = f"Stimulation: {subject_name} {test_name}".strip()
    body_lines = [f"Hello {greeting_name},", ""]
    if elapsed_str:
        body_lines.append(f"Experiment completed in {elapsed_str}.")
    else:
        body_lines.append("Experiment completed.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_user
    msg["To"] = to_email

    attached: List[Path] = []
    if attachments:
        attached = _attach_files(msg, attachments)
        if attached:
            body_lines.append("The file(s) from the experiment is "
                              "attached to this message.")
    body_lines.append("")
    body_lines.append(SIGN_OFF)
    msg.set_content("\n".join(body_lines))
    try:
        _send(msg, cfg)
        return True
    except Exception:
        return False


def send_error_email(*,
                     to_email: str,
                     recipient_name: str = "",
                     subject_name: str = "",
                     test_name: str = "",
                     error_message: str = "",
                     elapsed_seconds: Optional[float] = None,
                     attachments: Optional[Iterable] = None,
                     config: Optional[EmailConfig] = None) -> bool:
    """Send the "experiment failed" email — same shape as the success
    flavour but with an error body. Returns True on send success."""
    if not to_email:
        return False
    cfg = config or load_config()
    if not cfg.configured:
        return False

    greeting_name = _greeting_name(to_email, recipient_name)
    subject = f"Stimulation FAILED: {subject_name} {test_name}".strip()
    body_lines = [f"Hello {greeting_name},", ""]
    if elapsed_seconds is not None:
        body_lines.append(
            f"Experiment failed at {_format_elapsed(elapsed_seconds)}.")
    else:
        body_lines.append("Experiment failed.")
    if error_message:
        body_lines.append("")
        body_lines.append(error_message)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_user
    msg["To"] = to_email
    attached: List[Path] = []
    if attachments:
        attached = _attach_files(msg, attachments)
        if attached:
            body_lines.append("")
            body_lines.append(
                "The partial file from the experiment is attached.")
    body_lines.append("")
    body_lines.append(SIGN_OFF)
    msg.set_content("\n".join(body_lines))
    try:
        _send(msg, cfg)
        return True
    except Exception:
        return False


# Convenience: SMS via email-to-SMS gateway. The MATLAB code stores the
# user's phone + carrier in ``File.User``; the Python equivalent
# accepts them as kwargs. Optional — most labs have moved away from
# this since SMS gateways are unreliable, but keep the door open
# because the MATLAB version exposed it.
def send_sms_via_gateway(*, phone: str, carrier: str, body: str,
                         subject: str = "Stimulation",
                         config: Optional[EmailConfig] = None) -> bool:
    """Send a text via the email-to-SMS gateway for the given carrier.

    ``carrier`` matches one of :data:`SMS_GATEWAYS` (case-insensitive,
    dashes and ampersands stripped). Body is plain text; gateways
    typically truncate at 160 chars.
    """
    if not phone or not carrier:
        return False
    cfg = config or load_config()
    if not cfg.configured:
        return False
    key = carrier.lower().replace("-", "").replace("&", "")
    domain = SMS_GATEWAYS.get(key)
    if domain is None:
        return False
    to = f"{phone}@{domain}"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_user
    msg["To"] = to
    msg.set_content(body)
    try:
        _send(msg, cfg)
        return True
    except Exception:
        return False
