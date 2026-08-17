# app/plugins/autogrid360/services/lifecycle.py
"""Listing lifecycle transitions owned by AutoGrid360."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.plugins.autogrid360.models import (
    LISTING_STATUSES,
    STATUS_ACTIVE,
    STATUS_DRAFT,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REMOVED,
    STATUS_SALE_PENDING,
    STATUS_SOLD,
    Listing,
)


class ListingTransitionError(ValueError):
    """Raised when a requested listing lifecycle transition is invalid."""


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now(timezone.utc)


def _normalize_datetime(value: datetime | None) -> datetime | None:
    """Return a timezone-aware UTC datetime for lifecycle comparisons."""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clear_expired_cycle(listing: Listing) -> None:
    listing.expired_at = None
    listing.expired_edited_at = None
    listing.expired_removal_warning_sent_at = None
    listing.aged_out_at = None
    listing.aged_out_notice_sent_at = None


def _activate_listing(
    listing: Listing,
    *,
    now: datetime | None = None,
    expiration_days: int | None = None,
) -> Listing:
    """Activate one listing and assign its publication deadline without committing."""

    published_at = _now(now)
    if expiration_days is not None and expiration_days < 1:
        raise ValueError("expiration_days must be at least 1 when provided.")

    listing.status = STATUS_ACTIVE
    listing.sold_at = None
    if listing.first_published_at is None:
        listing.first_published_at = published_at
    listing.published_at = published_at
    listing.expiration_warning_sent_at = None
    _clear_expired_cycle(listing)
    listing.expires_at = (
        published_at + timedelta(days=expiration_days)
        if expiration_days is not None
        else None
    )
    return listing


def _restore_active_listing(
    listing: Listing,
    *,
    now: datetime | None = None,
    expiration_days: int | None = None,
) -> Listing:
    """Restore published inventory to available status without needless republishing."""

    current = _now(now)
    expires_at = _normalize_datetime(listing.expires_at)
    if listing.published_at is None or (expires_at is not None and expires_at <= current):
        return _activate_listing(
            listing,
            now=current,
            expiration_days=expiration_days,
        )

    listing.status = STATUS_ACTIVE
    listing.sold_at = None
    _clear_expired_cycle(listing)
    return listing


def _set_pending_review(listing: Listing) -> Listing:
    """Move one listing to moderation review while preserving publication history."""

    listing.status = STATUS_PENDING
    listing.published_at = None
    listing.expires_at = None
    listing.expiration_warning_sent_at = None
    listing.sold_at = None
    _clear_expired_cycle(listing)
    return listing


def submit_listing(
    listing: Listing,
    *,
    require_approval: bool,
    now: datetime | None = None,
    expiration_days: int | None = None,
) -> Listing:
    """Submit or directly publish one draft listing without committing."""

    if listing.status != STATUS_DRAFT:
        raise ListingTransitionError(
            f"Cannot submit listing from status {listing.status!r}."
        )

    if require_approval:
        _set_pending_review(listing)
    else:
        _activate_listing(
            listing,
            now=now,
            expiration_days=expiration_days,
        )
    return listing


def approve_listing(
    listing: Listing,
    *,
    now: datetime | None = None,
    expiration_days: int | None = None,
) -> Listing:
    """Approve one pending listing for publication without committing."""

    if listing.status != STATUS_PENDING:
        raise ListingTransitionError(
            f"Cannot approve listing from status {listing.status!r}."
        )

    return _activate_listing(
        listing,
        now=now,
        expiration_days=expiration_days,
    )


def return_public_listing_to_pending(
    listing: Listing,
    *,
    require_rereview: bool,
) -> bool:
    """Return changed Active/Sale Pending inventory to moderation when required."""

    if listing.status not in {STATUS_ACTIVE, STATUS_SALE_PENDING} or not require_rereview:
        return False

    _set_pending_review(listing)
    return True


def relist_listing(
    listing: Listing,
    *,
    require_approval: bool,
    now: datetime | None = None,
    expiration_days: int | None = None,
) -> Listing:
    """Relist one expired listing without committing.

    An expired listing that has not changed in its current expired cycle is
    reactivated directly because it already passed the destination site's
    publication policy. Changed expired inventory follows the ordinary approval
    policy before returning to public inventory.
    """

    if listing.status != STATUS_EXPIRED:
        raise ListingTransitionError(
            f"Cannot relist listing from status {listing.status!r}."
        )

    if listing.changed_since_expiration and require_approval:
        return _set_pending_review(listing)

    return _activate_listing(
        listing,
        now=now,
        expiration_days=expiration_days,
    )


def mark_expired_listing_edited(
    listing: Listing,
    *,
    now: datetime | None = None,
) -> bool:
    """Mark one expired cycle as seller-changed and requiring normal reapproval."""

    if listing.status != STATUS_EXPIRED:
        return False
    if listing.expired_edited_at is None:
        listing.expired_edited_at = _now(now)
    return True


def mark_sale_pending_listing(
    listing: Listing,
    *,
    now: datetime | None = None,
    expiration_days: int | None = None,
) -> Listing:
    """Mark available or sold inventory as Sale Pending without committing."""

    if listing.status == STATUS_ACTIVE:
        listing.status = STATUS_SALE_PENDING
        listing.sold_at = None
        return listing

    if listing.status == STATUS_SOLD:
        _restore_active_listing(
            listing,
            now=now,
            expiration_days=expiration_days,
        )
        listing.status = STATUS_SALE_PENDING
        return listing

    raise ListingTransitionError(
        f"Cannot mark listing sale pending from status {listing.status!r}."
    )


def mark_sold_listing(
    listing: Listing,
    *,
    now: datetime | None = None,
) -> Listing:
    """Move available or Sale Pending inventory to sold without committing."""

    if listing.status not in {STATUS_ACTIVE, STATUS_SALE_PENDING}:
        raise ListingTransitionError(
            f"Cannot mark listing sold from status {listing.status!r}."
        )

    listing.status = STATUS_SOLD
    listing.sold_at = _now(now)
    return listing


def make_listing_available(
    listing: Listing,
    *,
    now: datetime | None = None,
    expiration_days: int | None = None,
) -> Listing:
    """Return Sale Pending or sold inventory to available Active status."""

    if listing.status not in {STATUS_SALE_PENDING, STATUS_SOLD}:
        raise ListingTransitionError(
            f"Cannot make listing available from status {listing.status!r}."
        )

    return _restore_active_listing(
        listing,
        now=now,
        expiration_days=expiration_days,
    )


def expire_listing(
    listing: Listing,
    *,
    now: datetime | None = None,
) -> Listing:
    """Move one available or Sale Pending listing to expired without committing."""

    if listing.status not in {STATUS_ACTIVE, STATUS_SALE_PENDING}:
        raise ListingTransitionError(
            f"Cannot expire listing from status {listing.status!r}."
        )

    listing.status = STATUS_EXPIRED
    listing.sold_at = None
    listing.expired_at = _now(now)
    listing.expired_edited_at = None
    listing.expired_removal_warning_sent_at = None
    return listing


def remove_listing(listing: Listing) -> Listing:
    """Soft-remove one published/expired listing without committing."""

    if listing.status not in {
        STATUS_ACTIVE,
        STATUS_SALE_PENDING,
        STATUS_SOLD,
        STATUS_EXPIRED,
    }:
        raise ListingTransitionError(
            f"Cannot remove listing from status {listing.status!r}."
        )

    listing.status = STATUS_REMOVED
    return listing


def age_out_expired_listing(
    listing: Listing,
    *,
    now: datetime | None = None,
) -> Listing:
    """Soft-remove one expired listing because its retention period elapsed."""

    if listing.status != STATUS_EXPIRED:
        raise ListingTransitionError(
            f"Cannot age out listing from status {listing.status!r}."
        )

    listing.status = STATUS_REMOVED
    listing.aged_out_at = _now(now)
    listing.aged_out_notice_sent_at = None
    return listing


def age_out_sold_listing(
    listing: Listing,
    *,
    now: datetime | None = None,
) -> Listing:
    """Soft-remove one sold listing because sold retention elapsed."""

    if listing.status != STATUS_SOLD:
        raise ListingTransitionError(
            f"Cannot age out sold listing from status {listing.status!r}."
        )

    listing.status = STATUS_REMOVED
    return listing


def admin_set_listing_status(
    listing: Listing,
    target_status: str,
    *,
    now: datetime | None = None,
    expiration_days: int | None = None,
) -> Listing:
    """Apply one administrator-directed lifecycle status while preserving invariants.

    This is deliberately broader than seller transitions. It remains a lifecycle
    operation rather than a raw ``listing.status`` assignment so publication,
    expiration, sold, and archive timestamps stay internally consistent.
    """

    if target_status not in LISTING_STATUSES:
        raise ListingTransitionError(f"Unknown listing status {target_status!r}.")
    if target_status == listing.status:
        return listing

    current = _now(now)
    if target_status == STATUS_DRAFT:
        listing.status = STATUS_DRAFT
        listing.published_at = None
        listing.expires_at = None
        listing.expiration_warning_sent_at = None
        listing.sold_at = None
        _clear_expired_cycle(listing)
        return listing
    if target_status == STATUS_PENDING:
        return _set_pending_review(listing)
    if target_status == STATUS_ACTIVE:
        if listing.status in {STATUS_SALE_PENDING, STATUS_SOLD}:
            return _restore_active_listing(
                listing,
                now=current,
                expiration_days=expiration_days,
            )
        return _activate_listing(
            listing,
            now=current,
            expiration_days=expiration_days,
        )
    if target_status == STATUS_SALE_PENDING:
        if listing.status in {STATUS_ACTIVE, STATUS_SOLD}:
            return mark_sale_pending_listing(
                listing,
                now=current,
                expiration_days=expiration_days,
            )
        _activate_listing(
            listing,
            now=current,
            expiration_days=expiration_days,
        )
        listing.status = STATUS_SALE_PENDING
        return listing
    if target_status == STATUS_SOLD:
        if listing.status not in {STATUS_ACTIVE, STATUS_SALE_PENDING}:
            _activate_listing(
                listing,
                now=current,
                expiration_days=expiration_days,
            )
        return mark_sold_listing(listing, now=current)
    if target_status == STATUS_EXPIRED:
        if listing.status not in {STATUS_ACTIVE, STATUS_SALE_PENDING}:
            _activate_listing(
                listing,
                now=current,
                expiration_days=expiration_days,
            )
        return expire_listing(listing, now=current)
    if target_status == STATUS_REMOVED:
        listing.status = STATUS_REMOVED
        return listing



def expire_due_listings(*, now: datetime | None = None) -> list[Listing]:
    """Expire due Active/Sale Pending listings with explicit deadlines.

    The caller owns the transaction and any audit/notification behavior. This
    helper only identifies due rows and applies the lifecycle transition.
    """

    cutoff = _now(now)
    due = (
        Listing.query.filter(
            Listing.status.in_((STATUS_ACTIVE, STATUS_SALE_PENDING)),
            Listing.expires_at.is_not(None),
            Listing.expires_at <= cutoff,
        )
        .order_by(Listing.id.asc())
        .all()
    )

    for listing in due:
        expire_listing(listing, now=cutoff)

    return due
