"""Email sending via Resend API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from anjo.core.logger import logger


def send_verification_email(to_email: str, username: str, token: str) -> bool:
    """Send email verification link. Returns True on success."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    base_url = os.environ.get("ANJO_BASE_URL", "https://your-domain.com")

    if not api_key:
        logger.warning("RESEND_API_KEY not set — skipping email send")
        return False

    verify_url = f"{base_url}/verify?token={token}"

    html = f"""
    <div style="font-family: system-ui, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
      <h2 style="font-size: 24px; font-weight: 700; margin-bottom: 8px;">Welcome to Anjo</h2>
      <p style="color: #666; margin-bottom: 28px;">Hi {username} — verify your email to start your first conversation.</p>
      <a href="{verify_url}"
         style="display: inline-block; background: #c9a96e; color: #0f0d0c; text-decoration: none;
                padding: 12px 28px; border-radius: 10px; font-weight: 600; font-size: 15px;">
        Verify email
      </a>
      <p style="color: #999; font-size: 12px; margin-top: 28px;">
        This link expires in 24 hours. If you didn't create an account, you can ignore this.
      </p>
    </div>
    """

    payload = json.dumps(
        {
            "from": "Anjo <noreply@your-domain.com>",
            "to": [to_email],
            "subject": "Verify your Anjo account",
            "html": html,
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Anjo/1.0 (Python urllib)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201)
    except urllib.error.HTTPError as e:
        logger.error(f"Resend error {e.code}: {e.read().decode()}")
        return False
    except Exception as e:
        logger.error(f"Send failed: {e}")
        return False


def send_reset_email(to_email: str, username: str, token: str) -> bool:
    """Send password reset link. Returns True on success."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    base_url = os.environ.get("ANJO_BASE_URL", "https://your-domain.com")

    if not api_key:
        logger.warning("RESEND_API_KEY not set — skipping reset email")
        return False

    reset_url = f"{base_url}/reset?token={token}"

    html = f"""
    <div style="font-family: system-ui, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
      <h2 style="font-size: 24px; font-weight: 700; margin-bottom: 8px;">Reset your password</h2>
      <p style="color: #666; margin-bottom: 28px;">Hi {username} — click below to set a new password. This link expires in 1 hour.</p>
      <a href="{reset_url}"
         style="display: inline-block; background: #c9a96e; color: #0f0d0c; text-decoration: none;
                padding: 12px 28px; border-radius: 10px; font-weight: 600; font-size: 15px;">
        Reset password
      </a>
      <p style="color: #999; font-size: 12px; margin-top: 28px;">
        If you didn't request this, you can safely ignore it.
      </p>
    </div>
    """

    payload = json.dumps(
        {
            "from": "Anjo <noreply@your-domain.com>",
            "to": [to_email],
            "subject": "Reset your Anjo password",
            "html": html,
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Anjo/1.0 (Python urllib)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 201)
    except urllib.error.HTTPError as e:
        logger.error(f"Resend error {e.code}: {e.read().decode()}")
        return False
    except Exception as e:
        logger.error(f"Reset email failed: {e}")
        return False
