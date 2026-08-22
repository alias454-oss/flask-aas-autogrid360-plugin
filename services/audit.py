# app/plugins/autogrid360/services/audit.py
"""Shared AutoGrid360 listing audit helpers."""

from flask_login import current_user

from app.core.trackers import audit_activity_enabled, log_action, log_action_isolated


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


def audit_listing_image_read(listing, image, *, variant: str) -> None:
    """Persist one successful authenticated listing-image read audit event."""

    if not audit_activity_enabled() or not current_user.is_authenticated:
        return

    log_action_isolated(
        user_id=current_user.id,
        action="autogrid360_listing_image_read",
        target=f"listing:{listing.id}",
        extra_data={
            "listing_id": listing.id,
            "seller_id": listing.seller_id,
            "image_id": image.id,
            "variant": variant,
        },
    )
