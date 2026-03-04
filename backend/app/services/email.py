"""Email sending service via ZeptoMail SMTP.

Uses aiosmtplib if installed, otherwise falls back to smtplib in a thread
executor. When email is not configured (has_email=False), emits a WARNING log
with the token URL so developers can test the flow locally without SMTP.

All functions return bool: True = sent, False = not configured or failed.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

import structlog

from app.config import get_settings

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()


def _build_reset_message(to_email: str, reset_url: str, sender: str) -> MIMEMultipart:
    """Build the password reset MIME email."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Reset your NexSidi password"
    msg["From"] = sender
    msg["To"] = to_email

    text_body = (
        "You requested a password reset for your NexSidi account.\n\n"
        f"Click the link below to reset your password (valid for 1 hour):\n\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can safely ignore this email.\n"
        "Your password will not change until you click the link above and create a new one.\n"
    )
    html_body = f"""\
<html>
  <body style="font-family:Arial,sans-serif;color:#222;">
    <h2>Reset your NexSidi password</h2>
    <p>You requested a password reset. Click the button below to set a new password.
       This link is valid for <strong>1 hour</strong>.</p>
    <p style="margin:24px 0;">
      <a href="{reset_url}"
         style="background:#2563eb;color:#fff;padding:12px 24px;border-radius:6px;
                text-decoration:none;font-weight:bold;">
        Reset Password
      </a>
    </p>
    <p>Or copy and paste this URL into your browser:<br>
       <a href="{reset_url}">{reset_url}</a></p>
    <hr style="margin-top:32px;border:none;border-top:1px solid #eee;">
    <p style="color:#888;font-size:12px;">
      If you did not request a password reset, you can safely ignore this email.
    </p>
  </body>
</html>
"""
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    return msg


def _build_verification_message(to_email: str, verify_url: str, sender: str) -> MIMEMultipart:
    """Build the email verification MIME email."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Verify your NexSidi email address"
    msg["From"] = sender
    msg["To"] = to_email

    text_body = (
        "Welcome to NexSidi! Please verify your email address.\n\n"
        f"Click the link below to verify your email (valid for 24 hours):\n\n"
        f"{verify_url}\n\n"
        "If you did not create a NexSidi account, you can safely ignore this email.\n"
    )
    html_body = f"""\
<html>
  <body style="font-family:Arial,sans-serif;color:#222;">
    <h2>Verify your NexSidi email address</h2>
    <p>Welcome to NexSidi! Click the button below to verify your email address.
       This link is valid for <strong>24 hours</strong>.</p>
    <p style="margin:24px 0;">
      <a href="{verify_url}"
         style="background:#16a34a;color:#fff;padding:12px 24px;border-radius:6px;
                text-decoration:none;font-weight:bold;">
        Verify Email
      </a>
    </p>
    <p>Or copy and paste this URL into your browser:<br>
       <a href="{verify_url}">{verify_url}</a></p>
    <hr style="margin-top:32px;border:none;border-top:1px solid #eee;">
    <p style="color:#888;font-size:12px;">
      If you did not create a NexSidi account, you can safely ignore this email.
    </p>
  </body>
</html>
"""
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    return msg


def _send_smtp_sync(host: str, port: int, username: str, password: str, msg: MIMEMultipart) -> None:
    """Send email synchronously via smtplib with STARTTLS."""
    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=15) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(username, password)
        server.send_message(msg)


async def _send_via_aiosmtplib(
    host: str, port: int, username: str, password: str, msg: MIMEMultipart
) -> None:
    """Send email via aiosmtplib (async SMTP). Preferred when available."""
    import aiosmtplib  # type: ignore[import]
    await aiosmtplib.send(
        msg,
        hostname=host,
        port=port,
        username=username,
        password=password,
        start_tls=True,
    )


async def _send_email(msg: MIMEMultipart) -> bool:
    """Low-level email dispatch: tries aiosmtplib, falls back to smtplib in executor."""
    settings = get_settings()
    if not settings.has_email:
        return False

    host = settings.zeptomail_smtp_server
    port = settings.zeptomail_smtp_port
    username = settings.zeptomail_username
    password = settings.zeptomail_password

    try:
        # Prefer async SMTP if aiosmtplib is installed
        try:
            await _send_via_aiosmtplib(host, port, username, password, msg)
        except ImportError:
            # Fallback to sync smtplib in a thread executor to avoid blocking
            await asyncio.to_thread(_send_smtp_sync, host, port, username, password, msg)
        return True
    except Exception as exc:
        logger.error("email_send_failed", error=str(exc)[:300])
        return False


async def send_password_reset_email(to_email: str, reset_url: str) -> bool:
    """Send password reset email via ZeptoMail SMTP. Returns True if sent.

    When email is not configured (has_email=False), logs the reset URL at
    WARNING level so developers can test locally without SMTP setup.
    """
    settings = get_settings()
    if not settings.has_email:
        # Dev mode: log the token so developers can test without SMTP
        logger.warning(
            "password_reset_dev_mode",
            hint="Email not configured — use this URL to complete password reset",
            reset_url=reset_url,
        )
        return False

    sender = settings.zeptomail_sender_email or settings.zeptomail_username
    msg = _build_reset_message(to_email, reset_url, sender)
    sent = await _send_email(msg)
    if sent:
        logger.info("password_reset_email_sent")
    return sent


async def send_verification_email(to_email: str, verify_url: str) -> bool:
    """Send email verification link via ZeptoMail SMTP. Returns True if sent.

    When email is not configured (has_email=False), logs the verify URL at
    WARNING level so developers can test locally without SMTP setup.
    """
    settings = get_settings()
    if not settings.has_email:
        logger.warning(
            "email_verification_dev_mode",
            hint="Email not configured — use this URL to verify email",
            verify_url=verify_url,
        )
        return False

    sender = settings.zeptomail_sender_email or settings.zeptomail_username
    msg = _build_verification_message(to_email, verify_url, sender)
    sent = await _send_email(msg)
    if sent:
        logger.info("verification_email_sent")
    return sent


async def send_invite_email(to_email: str, invite_url: str, org_name: str, role: str) -> bool:
    """Send organization invite email. Returns True if sent."""
    settings = get_settings()
    if not settings.has_email:
        logger.warning(
            "invite_email_dev_mode",
            hint="Email not configured — use this URL to complete invite",
            invite_url=invite_url,
            role=role,
        )
        return False

    sender = settings.zeptomail_sender_email or settings.zeptomail_username
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"You've been invited to join {org_name} on NexSidi"
    msg["From"] = sender
    msg["To"] = to_email

    text_body = (
        f"You have been invited to join {org_name} on NexSidi as a {role}.\n\n"
        f"Click the link below to accept the invitation:\n\n{invite_url}\n\n"
        "This invitation is valid for 7 days.\n"
    )
    html_body = f"""\
<html>
  <body style="font-family:Arial,sans-serif;color:#222;">
    <h2>You've been invited to join {org_name}</h2>
    <p>You have been invited to join <strong>{org_name}</strong> on NexSidi
       as a <strong>{role}</strong>.</p>
    <p style="margin:24px 0;">
      <a href="{invite_url}"
         style="background:#7c3aed;color:#fff;padding:12px 24px;border-radius:6px;
                text-decoration:none;font-weight:bold;">
        Accept Invitation
      </a>
    </p>
    <p>Or copy and paste this URL:<br><a href="{invite_url}">{invite_url}</a></p>
    <hr style="margin-top:32px;border:none;border-top:1px solid #eee;">
    <p style="color:#888;font-size:12px;">This invitation expires in 7 days.</p>
  </body>
</html>
"""
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    sent = await _send_email(msg)
    if sent:
        logger.info("invite_email_sent")
    return sent
