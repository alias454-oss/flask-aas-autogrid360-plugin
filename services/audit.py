# app/plugins/autogrid360/services/audit.py
"""Shared AutoGrid360 listing audit helpers."""

from flask_login import current_user

from app.core.trackers import audit_activity_enabled, log_action


def audit_listing_action(listing, *, action: str, extra_data: dict | None = None) -> None:
    """Queue one metadata-only listing audit event in the caller transaction."""

    if not audit_activity_enabled():
        return

    payload = {
        "listing_id": listing.id,
        "seller_id": listing.seller_id,
    }
    if extra_data:
        payload.update(extra_data)

    log_action(
        user_id=current_user.id,
        action=action,
        target=f"listing:{listing.id}",
        extra_data=payload,
    )
