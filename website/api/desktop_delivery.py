"""Desktop purchase email and download target helpers."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse


BASE_URL = os.environ.get("IMAGIC_BASE_URL", "http://localhost:8000").rstrip("/")
SMTP_HOST = os.environ.get("IMAGIC_SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("IMAGIC_SMTP_PORT", "587") or "587")
SMTP_USERNAME = os.environ.get("IMAGIC_SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.environ.get("IMAGIC_SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.environ.get("IMAGIC_SMTP_FROM_EMAIL", SMTP_USERNAME).strip()
SMTP_FROM_NAME = os.environ.get("IMAGIC_SMTP_FROM_NAME", "imagic")
SMTP_USE_TLS = os.environ.get("IMAGIC_SMTP_USE_TLS", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SMTP_USE_SSL = os.environ.get("IMAGIC_SMTP_USE_SSL", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_VARIANT_CONFIG = {
    "standard": {
        "path_env": "IMAGIC_DESKTOP_INSTALLER_PATH",
        "url_env": "IMAGIC_DESKTOP_INSTALLER_URL",
        "default_name": "imagic-desktop-setup.exe",
        "label": "Windows installer",
    },
    "rawtherapee": {
        "path_env": "IMAGIC_DESKTOP_BUNDLE_PATH",
        "url_env": "IMAGIC_DESKTOP_BUNDLE_URL",
        "default_name": "imagic-desktop-plus-rawtherapee-setup.exe",
        "label": "Windows installer + RawTherapee bundle",
    },
    "macos": {
        "path_env": "IMAGIC_DESKTOP_MACOS_INSTALLER_PATH",
        "url_env": "IMAGIC_DESKTOP_MACOS_INSTALLER_URL",
        "default_name": "imagic-desktop.dmg",
        "label": "macOS installer (includes RawTherapee)",
    },
}

VALID_VARIANTS = set(_VARIANT_CONFIG.keys())


def email_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM_EMAIL)


def build_download_link(token: str) -> str:
    return f"{BASE_URL}/desktop/download/{token}"


def resolve_download_target(variant: str) -> dict | None:
    config = _VARIANT_CONFIG.get(variant)
    if config is None:
        return None

    local_path = os.environ.get(config["path_env"], "").strip()
    if local_path:
        candidate = Path(local_path)
        if candidate.is_file():
            return {
                "kind": "file",
                "path": candidate,
                "filename": candidate.name,
                "label": config["label"],
            }

    remote_url = os.environ.get(config["url_env"], "").strip()
    if remote_url:
        parsed = urlparse(remote_url)
        filename = Path(parsed.path).name or config["default_name"]
        return {
            "kind": "redirect",
            "url": remote_url,
            "filename": filename,
            "label": config["label"],
        }

    return None


def send_desktop_purchase_email(
    recipient_email: str,
    license_key: str,
    standard_download_link: str,
    bundle_download_link: str | None,
    order_status_link: str,
    macos_download_link: str | None = None,
) -> None:
    if not email_configured():
        raise RuntimeError("Desktop purchase email is not configured.")

    message = EmailMessage()
    message["Subject"] = "Your imagic desktop download and product key"
    message["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    message["To"] = recipient_email

    plain_lines = [
        "Thanks for buying imagic Desktop.",
        "",
        f"Product key: {license_key}",
        "",
        "Downloads — Windows:",
        f"- Windows installer: {standard_download_link}",
    ]
    if bundle_download_link:
        plain_lines.append(f"- Windows installer + RawTherapee bundle: {bundle_download_link}")
    if macos_download_link:
        plain_lines.append("")
        plain_lines.append("Downloads — macOS:")
        plain_lines.append(f"- macOS installer (includes RawTherapee): {macos_download_link}")
    plain_lines.extend(
        [
            "",
            f"Order page: {order_status_link}",
            "",
            "Activation works by product key. When you move the key to another device, the old device activation is revoked automatically.",
        ]
    )
    message.set_content("\n".join(plain_lines))

    bundle_block = ""
    if bundle_download_link:
        bundle_block = (
            f"<p><strong>Windows installer + RawTherapee bundle:</strong> "
            f"<a href=\"{bundle_download_link}\">Download the bundle</a></p>"
        )

    macos_block = ""
    if macos_download_link:
        macos_block = (
            f"<p><strong>macOS installer (includes RawTherapee):</strong> "
            f"<a href=\"{macos_download_link}\">Download for macOS</a></p>"
        )

    message.add_alternative(
        f"""
        <html>
          <body style="font-family: Arial, sans-serif; color: #111; line-height: 1.5;">
            <h2>Your imagic Desktop order is ready</h2>
            <p>Thanks for buying imagic Desktop.</p>
            <p><strong>Product key:</strong> {license_key}</p>
            <h3 style="margin-top:1.5em;">Windows</h3>
            <p><strong>Windows installer:</strong> <a href="{standard_download_link}">Download imagic Desktop for Windows</a></p>
            {bundle_block}
            {f'<h3 style="margin-top:1.5em;">macOS</h3>' + macos_block if macos_block else ''}
            <p><strong>Order page:</strong> <a href="{order_status_link}">{order_status_link}</a></p>
            <p>After install, imagic asks for your product key once and stores the activation on that device. If you move the same key to another device later, the older activation becomes invalid automatically.</p>
          </body>
        </html>
        """,
        subtype="html",
    )

    if SMTP_USE_SSL:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            if SMTP_USERNAME:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
        return

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        if SMTP_USE_TLS:
            server.starttls()
        if SMTP_USERNAME:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(message)