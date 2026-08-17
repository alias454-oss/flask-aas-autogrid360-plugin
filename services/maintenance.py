# app/plugins/autogrid360/services/maintenance.py
"""Scheduled listing maintenance owned by AutoGrid360."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging

from app.core.mailer import send_email
from app.core.trackers import audit_activity_enabled, log_action
from app.plugins.autogrid360.services.lifecycle import (
    age_out_expired_listing,
    age_out_sold_listing,
    expire_due_listings,
)
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_REMOVED,
    STATUS_SALE_PENDING,
    STATUS_SOLD,
    Listing,
)
from app.plugins.autogrid360.services.settings import ListingPolicy, listing_policy


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MaintenanceResult:
    """One deterministic AutoGrid360 maintenance-run summary."""

    expiration_enabled: bool
    warnings_queued: int = 0
    warnings_disabled: int = 0
    warnings_failed: int = 0
    expired: int = 0
    removal_warnings_queued: int = 0
    removal_warnings_disabled: int = 0
    removal_warnings_failed: int = 0
    removed: int = 0
    removal_notices_queued: int = 0
    removal_notices_disabled: int = 0
    removal_notices_failed: int = 0


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now(timezone.utc)


def _utc_label(value: datetime) -> str:
    """Return one stable UTC timestamp label for seller mail and audit metadata."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M UTC")


def expiring_listings(
    *,
    now: datetime | None = None,
    warning_days: int,
) -> list[Listing]:
    """Return Active/Sale Pending listings entering the expiration warning window once."""

    if warning_days < 0:
        raise ValueError("warning_days cannot be negative.")
    if warning_days == 0:
        return []

    cutoff = _now(now)
    warning_cutoff = cutoff + timedelta(days=warning_days)
    return (
        Listing.query.filter(
            Listing.status.in_((STATUS_ACTIVE, STATUS_SALE_PENDING)),
            Listing.expires_at.is_not(None),
            Listing.expires_at > cutoff,
            Listing.expires_at <= warning_cutoff,
            Listing.expiration_warning_sent_at.is_(None),
        )
        .order_by(Listing.id.asc())
        .all()
    )


def expired_listings_nearing_removal(
    *,
    now: datetime | None = None,
    retention_days: int,
    warning_days: int,
) -> list[Listing]:
    """Return expired listings entering their one-time removal-warning window."""

    if retention_days < 1:
        raise ValueError("retention_days must be at least 1.")
    if warning_days < 0:
        raise ValueError("warning_days cannot be negative.")
    if warning_days >= retention_days:
        raise ValueError("warning_days must be less than retention_days.")
    if warning_days == 0:
        return []

    cutoff = _now(now)
    due_cutoff = cutoff - timedelta(days=retention_days)
    warning_cutoff = cutoff - timedelta(days=retention_days - warning_days)
    return (
        Listing.query.filter(
            Listing.status == STATUS_EXPIRED,
            Listing.expired_at.is_not(None),
            Listing.expired_at > due_cutoff,
            Listing.expired_at <= warning_cutoff,
            Listing.expired_removal_warning_sent_at.is_(None),
        )
        .order_by(Listing.id.asc())
        .all()
    )


def expired_listings_due_removal(
    *,
    now: datetime | None = None,
    retention_days: int,
) -> list[Listing]:
    """Return expired listings whose retention deadline has elapsed."""

    if retention_days < 1:
        raise ValueError("retention_days must be at least 1.")

    cutoff = _now(now)
    due_cutoff = cutoff - timedelta(days=retention_days)
    return (
        Listing.query.filter(
            Listing.status == STATUS_EXPIRED,
            Listing.expired_at.is_not(None),
            Listing.expired_at <= due_cutoff,
        )
        .order_by(Listing.id.asc())
        .all()
    )


def sold_listings_due_removal(
    *,
    now: datetime | None = None,
    retention_days: int,
) -> list[Listing]:
    """Return sold listings whose sold-retention deadline has elapsed."""

    if retention_days < 0:
        raise ValueError("retention_days cannot be negative.")
    if retention_days == 0:
        return []

    cutoff = _now(now)
    due_cutoff = cutoff - timedelta(days=retention_days)
    return (
        Listing.query.filter(
            Listing.status == STATUS_SOLD,
            Listing.sold_at.is_not(None),
            Listing.sold_at <= due_cutoff,
        )
        .order_by(Listing.id.asc())
        .all()
    )


def aged_out_listings_needing_notice() -> list[Listing]:
    """Return automatically removed listings whose seller notice is still retryable."""

    return (
        Listing.query.filter(
            Listing.status == STATUS_REMOVED,
            Listing.aged_out_at.is_not(None),
            Listing.aged_out_notice_sent_at.is_(None),
        )
        .order_by(Listing.id.asc())
        .all()
    )


def _expiration_warning_subject(listing: Listing) -> str:
    title = " ".join((listing.title or "").split()) or f"Listing {listing.id}"
    return f"AutoGrid360 listing expires soon: {title}"[:150]


def _expiration_warning_body(listing: Listing) -> str:
    return "\n".join(
        [
            "AutoGrid360 listing expiration notice",
            "",
            f"Listing: {listing.title}",
            f"Listing ID: {listing.id}",
            f"Scheduled expiration: {_utc_label(listing.expires_at)}",
            "",
            "This listing will leave public inventory when its expiration deadline is reached.",
        ]
    )


def _removal_deadline(listing: Listing, *, retention_days: int) -> datetime:
    return listing.expired_at + timedelta(days=retention_days)


def _removal_warning_subject(listing: Listing) -> str:
    title = " ".join((listing.title or "").split()) or f"Listing {listing.id}"
    return f"AutoGrid360 expired listing will be removed soon: {title}"[:150]


def _removal_warning_body(listing: Listing, *, retention_days: int) -> str:
    return "\n".join(
        [
            "AutoGrid360 expired listing removal notice",
            "",
            f"Listing: {listing.title}",
            f"Listing ID: {listing.id}",
            f"Scheduled removal: {_utc_label(_removal_deadline(listing, retention_days=retention_days))}",
            "",
            "This listing has aged out without being sold.",
            "Relist it before the removal deadline to return it to inventory; otherwise it will be archived from your active AutoGrid360 listings.",
        ]
    )


def _aged_out_subject(listing: Listing) -> str:
    title = " ".join((listing.title or "").split()) or f"Listing {listing.id}"
    return f"AutoGrid360 expired listing removed: {title}"[:150]


def _aged_out_body(listing: Listing) -> str:
    return "\n".join(
        [
            "AutoGrid360 expired listing removed",
            "",
            f"Listing: {listing.title}",
            f"Listing ID: {listing.id}",
            f"Removed: {_utc_label(listing.aged_out_at)}",
            "",
            "This listing remained expired beyond the site's retention period and has been moved to the removed archive state.",
        ]
    )


def _audit_expiration_warning(listing: Listing, *, source: str) -> None:
    log_action(
        user_id=None,
        action="autogrid360_listing_expiration_warning_queued",
        target=f"listing:{listing.id}",
        extra_data={
            "listing_id": listing.id,
            "seller_id": listing.seller_id,
            "expires_at": _utc_label(listing.expires_at),
            "source": source,
        },
    )


def _audit_status_change(
    listing: Listing,
    *,
    previous_status: str,
    new_status: str,
    source: str,
) -> None:
    log_action(
        user_id=None,
        action="autogrid360_listing_status_changed",
        target=f"listing:{listing.id}",
        extra_data={
            "listing_id": listing.id,
            "seller_id": listing.seller_id,
            "previous_status": previous_status,
            "new_status": new_status,
            "source": source,
        },
    )


def _audit_removal_warning(listing: Listing, *, source: str, retention_days: int) -> None:
    log_action(
        user_id=None,
        action="autogrid360_listing_removal_warning_queued",
        target=f"listing:{listing.id}",
        extra_data={
            "listing_id": listing.id,
            "seller_id": listing.seller_id,
            "removal_at": _utc_label(
                _removal_deadline(listing, retention_days=retention_days)
            ),
            "source": source,
        },
    )


def _audit_aged_out_notice(listing: Listing, *, source: str) -> None:
    log_action(
        user_id=None,
        action="autogrid360_listing_aged_out_notice_queued",
        target=f"listing:{listing.id}",
        extra_data={
            "listing_id": listing.id,
            "seller_id": listing.seller_id,
            "aged_out_at": _utc_label(listing.aged_out_at),
            "source": source,
        },
    )


@dataclass
class _DeliveryCounts:
    """Mail delivery outcomes for one maintenance phase."""

    queued: int = 0
    disabled: int = 0
    failed: int = 0


def _record_delivery(
    status: str,
    counts: _DeliveryCounts,
    *,
    listing: Listing,
    label: str,
) -> bool:
    """Record one maintenance mail result and return whether it was queued."""

    if status == "queued":
        counts.queued += 1
        return True
    if status == "disabled":
        counts.disabled += 1
        logger.info(
            "AutoGrid360 %s unavailable listing_id=%s seller_id=%s",
            label,
            listing.id,
            listing.seller_id,
        )
        return False

    counts.failed += 1
    logger.warning(
        "AutoGrid360 %s failed listing_id=%s seller_id=%s",
        label,
        listing.id,
        listing.seller_id,
    )
    return False


def _queue_expiration_warnings(
    *,
    policy: ListingPolicy,
    cutoff: datetime,
    source: str,
) -> _DeliveryCounts:
    """Queue expiration warnings for inventory entering the warning window."""

    counts = _DeliveryCounts()
    for listing in expiring_listings(
        now=cutoff,
        warning_days=policy.expiration_warning_days,
    ):
        status = send_email(
            _expiration_warning_subject(listing),
            listing.seller.email,
            _expiration_warning_body(listing),
        )
        if _record_delivery(
            status,
            counts,
            listing=listing,
            label="expiration warning",
        ):
            listing.expiration_warning_sent_at = cutoff
            if audit_activity_enabled():
                _audit_expiration_warning(listing, source=source)

    return counts


def _expire_due_inventory(
    *,
    cutoff: datetime,
    source: str,
) -> int:
    """Expire due Active/Sale Pending inventory and queue status audits."""

    previous_statuses = dict(
        Listing.query.with_entities(Listing.id, Listing.status)
        .filter(
            Listing.status.in_((STATUS_ACTIVE, STATUS_SALE_PENDING)),
            Listing.expires_at.is_not(None),
            Listing.expires_at <= cutoff,
        )
        .all()
    )
    expired = expire_due_listings(now=cutoff)
    if expired and audit_activity_enabled():
        for listing in expired:
            _audit_status_change(
                listing,
                previous_status=previous_statuses.get(listing.id, STATUS_ACTIVE),
                new_status=STATUS_EXPIRED,
                source=source,
            )
    return len(expired)


def _queue_removal_warnings(
    *,
    policy: ListingPolicy,
    cutoff: datetime,
    source: str,
) -> _DeliveryCounts:
    """Queue warnings for expired inventory approaching retention removal."""

    counts = _DeliveryCounts()
    for listing in expired_listings_nearing_removal(
        now=cutoff,
        retention_days=policy.expired_retention_days,
        warning_days=policy.expired_removal_warning_days,
    ):
        status = send_email(
            _removal_warning_subject(listing),
            listing.seller.email,
            _removal_warning_body(
                listing,
                retention_days=policy.expired_retention_days,
            ),
        )
        if _record_delivery(
            status,
            counts,
            listing=listing,
            label="removal warning",
        ):
            listing.expired_removal_warning_sent_at = cutoff
            if audit_activity_enabled():
                _audit_removal_warning(
                    listing,
                    source=source,
                    retention_days=policy.expired_retention_days,
                )

    return counts


def _age_out_retained_inventory(
    *,
    policy: ListingPolicy,
    cutoff: datetime,
    source: str,
) -> int:
    """Age expired and sold inventory into Removed under retention policy."""

    expired_due = expired_listings_due_removal(
        now=cutoff,
        retention_days=policy.expired_retention_days,
    )
    for listing in expired_due:
        age_out_expired_listing(listing, now=cutoff)
        if audit_activity_enabled():
            _audit_status_change(
                listing,
                previous_status=STATUS_EXPIRED,
                new_status=STATUS_REMOVED,
                source=source,
            )

    sold_due = sold_listings_due_removal(
        now=cutoff,
        retention_days=policy.sold_retention_days,
    )
    for listing in sold_due:
        age_out_sold_listing(listing, now=cutoff)
        if audit_activity_enabled():
            _audit_status_change(
                listing,
                previous_status=STATUS_SOLD,
                new_status=STATUS_REMOVED,
                source=source,
            )

    return len(expired_due) + len(sold_due)


def _queue_aged_out_notices(
    *,
    cutoff: datetime,
    source: str,
) -> _DeliveryCounts:
    """Queue final notices for retention-removed inventory that has not been notified."""

    counts = _DeliveryCounts()
    for listing in aged_out_listings_needing_notice():
        status = send_email(
            _aged_out_subject(listing),
            listing.seller.email,
            _aged_out_body(listing),
        )
        if _record_delivery(
            status,
            counts,
            listing=listing,
            label="aged-out notice",
        ):
            listing.aged_out_notice_sent_at = cutoff
            if audit_activity_enabled():
                _audit_aged_out_notice(listing, source=source)

    return counts


def run_scheduled_maintenance(
    *,
    now: datetime | None = None,
    source: str = "scheduled_maintenance",
) -> MaintenanceResult:
    """Run one caller-owned warning, expiration, and retention-aging pass.

    Automatic expiration covers Active and Sale Pending inventory and obeys
    ``enable_listing_expiration``. Expired and Sold retention are independent of
    that switch: expired rows age into ``removed`` after expired retention, and
    sold rows age into ``removed`` after sold retention unless that value is 0.

    Warning/notice markers are persisted only when Flask-AAS reports that mail
    was queued. Disabled or failed mail therefore remains retryable while state
    transitions themselves remain deterministic and idempotent.
    """

    policy = listing_policy()
    cutoff = _now(now)
    expiration_warnings = _DeliveryCounts()
    expired_count = 0
    if policy.expiration_enabled:
        expiration_warnings = _queue_expiration_warnings(
            policy=policy,
            cutoff=cutoff,
            source=source,
        )
        expired_count = _expire_due_inventory(
            cutoff=cutoff,
            source=source,
        )

    removal_warnings = _queue_removal_warnings(
        policy=policy,
        cutoff=cutoff,
        source=source,
    )
    removed_count = _age_out_retained_inventory(
        policy=policy,
        cutoff=cutoff,
        source=source,
    )
    removal_notices = _queue_aged_out_notices(
        cutoff=cutoff,
        source=source,
    )

    return MaintenanceResult(
        expiration_enabled=policy.expiration_enabled,
        warnings_queued=expiration_warnings.queued,
        warnings_disabled=expiration_warnings.disabled,
        warnings_failed=expiration_warnings.failed,
        expired=expired_count,
        removal_warnings_queued=removal_warnings.queued,
        removal_warnings_disabled=removal_warnings.disabled,
        removal_warnings_failed=removal_warnings.failed,
        removed=removed_count,
        removal_notices_queued=removal_notices.queued,
        removal_notices_disabled=removal_notices.disabled,
        removal_notices_failed=removal_notices.failed,
    )
