"""Stripe integration — checkout sessions and fulfilment.

Environment variables (set these before running):
    STRIPE_SECRET_KEY       — sk_test_... or sk_live_...
    STRIPE_WEBHOOK_SECRET   — whsec_... (from Stripe Dashboard → Webhooks)
    STRIPE_PRICE_ID         — price_... (the $5 / 500 images credit-pack price ID)
    STRIPE_DESKTOP_PRICE_ID — price_... (the desktop license price ID)
    IMAGIC_BASE_URL         — https://yourdomain.com (for redirect URLs)

How to set up in Stripe Dashboard:
    1. Create a Product called "imagic Web Credits" with a one-time $5 price.
    2. Create a Product called "imagic Desktop" with a one-time desktop license price.
    3. Copy the Price IDs → set as STRIPE_PRICE_ID and STRIPE_DESKTOP_PRICE_ID.
    4. Add a webhook endpoint pointing to {IMAGIC_BASE_URL}/api/stripe/webhook
         listening for: checkout.session.completed.
    5. Copy the webhook signing secret → set as STRIPE_WEBHOOK_SECRET.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
import stripe

from .account_store import account_store
from .desktop_delivery import (
    build_download_link,
    resolve_download_target,
    send_desktop_purchase_email,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (load .env then read from environment)
# ---------------------------------------------------------------------------

_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_ENV_PATH)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
DESKTOP_PRICE_ID = os.environ.get("STRIPE_DESKTOP_PRICE_ID", "")
BASE_URL = os.environ.get("IMAGIC_BASE_URL", "http://localhost:8000")


def is_configured() -> bool:
    """Return True if Stripe keys are set."""
    return bool(stripe.api_key and PRICE_ID)


def desktop_is_configured() -> bool:
    """Return True if desktop checkout keys are set."""
    return bool(stripe.api_key and DESKTOP_PRICE_ID)


def create_checkout_session(client_ip: str, user_id: int) -> str:
    """Create a Stripe Checkout session for a 500-image credit pack.

    Args:
        client_ip: Client IP for audit metadata.
        user_id: Account receiving the purchased credits.

    Returns:
        The Checkout Session URL to redirect the user to.

    Raises:
        RuntimeError: If Stripe is not configured.
    """
    if not is_configured():
        raise RuntimeError(
            "Stripe is not configured. Set STRIPE_SECRET_KEY and STRIPE_PRICE_ID."
        )

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": PRICE_ID, "quantity": 1}],
        success_url=f"{BASE_URL}/app?credits=1&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{BASE_URL}/app?cancelled=1",
        metadata={"client_ip": client_ip, "user_id": str(user_id), "credits": "500"},
    )
    return session.url


def create_desktop_checkout_session(client_ip: str, email: str) -> str:
    """Create a Stripe Checkout session for an imagic Desktop purchase."""
    if not desktop_is_configured():
        raise RuntimeError(
            "Desktop checkout is not configured. Set STRIPE_SECRET_KEY and STRIPE_DESKTOP_PRICE_ID."
        )

    clean_email = email.strip().lower()
    if not clean_email or "@" not in clean_email:
        raise RuntimeError("A valid delivery email is required.")

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": DESKTOP_PRICE_ID, "quantity": 1}],
        success_url=f"{BASE_URL}/desktop/thanks?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{BASE_URL}/desktop?cancelled=1",
        customer_email=clean_email,
        metadata={
            "client_ip": client_ip,
            "purchase_type": "desktop",
            "delivery_email": clean_email,
        },
    )
    return session.url


# ---------------------------------------------------------------------------
# Webhook processing
# ---------------------------------------------------------------------------


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify and process a Stripe webhook event.

    Args:
        payload: Raw request body bytes.
        sig_header: The Stripe-Signature header value.

    Returns:
        A dict with ``{"status": "ok"}`` or error info.
    """
    if not WEBHOOK_SECRET:
        logger.warning("Stripe webhook secret not configured; skipping verification.")
        return {"status": "error", "message": "Webhook secret not configured"}

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook signature verification failed.")
        return {"status": "error", "message": "Invalid signature"}
    except Exception as exc:
        logger.error("Stripe webhook error: %s", exc)
        return {"status": "error", "message": str(exc)}

    event_type = event["type"]
    data = event["data"]["object"]

    logger.info("Stripe webhook: %s", event_type)

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(data)
    else:
        logger.debug("Unhandled Stripe event: %s", event_type)

    return {"status": "ok"}


def _handle_checkout_completed(session: dict) -> None:
    """Fulfil web credits or desktop sales when checkout completes."""
    metadata = session.get("metadata", {}) or {}
    purchase_type = metadata.get("purchase_type", "")
    if purchase_type == "desktop":
        _handle_desktop_checkout_completed(session)
        return

    user_id = metadata.get("user_id", "")
    credits = metadata.get("credits", "500")
    if not user_id:
        logger.warning("Checkout completed without user_id metadata.")
        return
    try:
        new_balance = account_store.add_credits(int(user_id), int(credits))
        logger.info("Added %s credits to user %s. New balance=%s", credits, user_id, new_balance)
    except Exception as exc:
        logger.exception("Failed to credit account after checkout: %s", exc)


def _handle_desktop_checkout_completed(session: dict) -> None:
    metadata = session.get("metadata", {}) or {}
    session_id = str(session.get("id", "")).strip()
    delivery_email = str(
        metadata.get("delivery_email")
        or (session.get("customer_details") or {}).get("email")
        or session.get("customer_email")
        or ""
    ).strip().lower()

    if not session_id or not delivery_email:
        logger.warning("Desktop checkout completed without a usable session id or email.")
        return

    try:
        purchase = account_store.fulfill_desktop_purchase(session_id, delivery_email)
        if purchase.get("email_sent_at"):
            logger.info("Desktop order email already sent for Stripe session %s", session_id)
            return

        if resolve_download_target("standard") is None:
            raise RuntimeError("No desktop installer is configured for delivery.")

        standard = account_store.issue_desktop_download(session_id, "standard")
        bundle_link = None
        if resolve_download_target("rawtherapee") is not None:
            bundle = account_store.issue_desktop_download(session_id, "rawtherapee")
            bundle_link = build_download_link(str(bundle["token"]))
        order_url = f"{BASE_URL}/desktop/thanks?session_id={session_id}"
        send_desktop_purchase_email(
            recipient_email=delivery_email,
            license_key=str(purchase["license_key"]),
            standard_download_link=build_download_link(str(standard["token"])),
            bundle_download_link=bundle_link,
            order_status_link=order_url,
        )
        account_store.mark_desktop_purchase_email_result(session_id, sent=True)
        logger.info("Desktop purchase fulfilled for %s", delivery_email)
    except Exception as exc:
        account_store.mark_desktop_purchase_email_result(
            session_id,
            sent=False,
            error_message=str(exc),
        )
        logger.exception("Failed to fulfil desktop purchase: %s", exc)
