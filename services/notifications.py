# app/plugins/autogrid360/services/notifications.py
"""Best-effort AutoGrid360 workflow notifications."""

from __future__ import annotations

import logging

from flask import url_for

from app.core.mailer import MailStatus, get_mail_env_settings, send_email
from app.plugins.autogrid360.models import Listing


logger = logging.getLogger(__name__)


def notify_admin_listing_pending(
    listing: Listing,
    *,
    reason: str,
) -> MailStatus:
    """Notify the configured site administrator that a listing needs review.

    The listing transition must already be committed before this helper is
    called. Notification delivery is intentionally best-effort and never owns
    or rolls back the listing transaction.
    """

    try:
        env = get_mail_env_settings()
        recipient = str(getattr(env, "admin_email", "") or "").strip()
        if not recipient:
            logger.info(
                "AutoGrid360 pending-review notification skipped: admin email is not configured listing_id=%s",
                listing.id,
            )
            return "disabled"

        seller_name = str(getattr(listing.seller, "username", "") or "").strip()
        review_url = url_for("autogrid360_admin.pending", _external=True)
        listing_url = url_for(
            "autogrid360_listings.detail",
            listing_id=listing.id,
            _external=True,
        )
        subject = f"AutoGrid360 listing requires review: {listing.title}"
        body = "\n".join(
            [
                "An AutoGrid360 listing requires administrator review.",
                "",
                f"Listing: {listing.title}",
                f"Listing ID: {listing.id}",
                f"Seller: {seller_name or listing.seller_id}",
                f"Reason: {reason}",
                f"Listing: {listing_url}",
                f"Pending review queue: {review_url}",
            ]
        )
        status = send_email(subject, recipient, body)
    except Exception:
        logger.exception(
            "AutoGrid360 pending-review notification failed listing_id=%s reason=%s",
            listing.id,
            reason,
        )
        return "failed"

    logger.info(
        "AutoGrid360 pending-review notification status=%s listing_id=%s seller_id=%s reason=%s",
        status,
        listing.id,
        listing.seller_id,
        reason,
    )
    return status
