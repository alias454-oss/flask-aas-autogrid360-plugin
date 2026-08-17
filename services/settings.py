# app/plugins/autogrid360/services/settings.py
"""Effective AutoGrid360 application policy helpers."""

from dataclasses import dataclass

from flask import current_app

from app.core.extensions import db
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_SALE_PENDING,
    STATUS_SOLD,
)
from app.plugins.autogrid360.models.settings import (
    DEFAULT_CURRENCY_CODE,
    DEFAULT_CURRENCY_DECIMAL_SEPARATOR,
    DEFAULT_CURRENCY_SYMBOL,
    DEFAULT_CURRENCY_THOUSANDS_SEPARATOR,
    DEFAULT_DISTANCE_UNIT,
    DEFAULT_EXPIRATION_WARNING_DAYS,
    DEFAULT_EXPIRED_REMOVAL_WARNING_DAYS,
    DEFAULT_EXPIRED_RETENTION_DAYS,
    DEFAULT_LISTING_EXPIRATION_DAYS,
    DEFAULT_LISTING_IMAGES_PATH,
    DEFAULT_ALLOW_SELLER_INVENTORY_IMPORT,
    DEFAULT_SOLD_RETENTION_DAYS,
    AutoGrid360Settings,
)


SETTINGS_ROW_ID = 1


@dataclass(frozen=True)
class CurrencyPolicy:
    """Effective AutoGrid360 currency display and publishing settings."""

    code: str = DEFAULT_CURRENCY_CODE
    symbol: str = DEFAULT_CURRENCY_SYMBOL
    decimal_separator: str = DEFAULT_CURRENCY_DECIMAL_SEPARATOR
    thousands_separator: str = DEFAULT_CURRENCY_THOUSANDS_SEPARATOR


@dataclass(frozen=True)
class DistancePolicy:
    """Effective AutoGrid360 distance-search presentation settings."""

    default_unit: str = DEFAULT_DISTANCE_UNIT


@dataclass(frozen=True)
class ListingPolicy:
    """Effective listing publication and lifecycle policy for this installation."""

    require_approval: bool = True
    require_rereview: bool = True
    expiration_enabled: bool = False
    expiration_days: int = DEFAULT_LISTING_EXPIRATION_DAYS
    expiration_warning_days: int = DEFAULT_EXPIRATION_WARNING_DAYS
    expired_retention_days: int = DEFAULT_EXPIRED_RETENTION_DAYS
    expired_removal_warning_days: int = DEFAULT_EXPIRED_REMOVAL_WARNING_DAYS
    show_sale_pending_publicly: bool = True
    show_sold_publicly: bool = True
    sold_retention_days: int = DEFAULT_SOLD_RETENTION_DAYS

    @property
    def rereview_active_edits(self) -> bool:
        """Return whether published seller edits must return to moderation."""

        return self.require_approval and self.require_rereview

    @property
    def active_expiration_days(self) -> int | None:
        """Return the publication lifetime when automatic expiration is enabled."""

        return self.expiration_days if self.expiration_enabled else None

    @property
    def public_statuses(self) -> tuple[str, ...]:
        """Return lifecycle states exposed on buyer-facing inventory surfaces."""

        statuses = [STATUS_ACTIVE]
        if self.show_sale_pending_publicly:
            statuses.append(STATUS_SALE_PENDING)
        if self.show_sold_publicly:
            statuses.append(STATUS_SOLD)
        return tuple(statuses)


def get_settings_row() -> AutoGrid360Settings | None:
    """Return the persisted singleton settings row without creating it."""

    return db.session.get(AutoGrid360Settings, SETTINGS_ROW_ID)


def listing_images_path() -> str:
    """Return the configured listing-image storage directory.

    The persisted AutoGrid360 setting is authoritative once the singleton row
    exists. Before that first persisted settings row exists, the legacy
    ``AUTOGRID360_IMAGE_ROOT`` deployment value acts only as the installation
    seed/fallback. Relative paths are resolved by the media service from the
    Flask-AAS project root.
    """

    settings = get_settings_row()
    if settings is not None:
        return str(settings.listing_images_path or "").strip()

    configured = str(current_app.config.get("AUTOGRID360_IMAGE_ROOT") or "").strip()
    return configured or DEFAULT_LISTING_IMAGES_PATH


def listing_policy() -> ListingPolicy:
    """Return the effective listing moderation policy with secure defaults."""

    settings = get_settings_row()
    if settings is None:
        return ListingPolicy()

    return ListingPolicy(
        require_approval=bool(settings.require_listing_approval),
        require_rereview=bool(settings.require_rereview_on_edit),
        expiration_enabled=bool(settings.enable_listing_expiration),
        expiration_days=int(settings.listing_expiration_days),
        expiration_warning_days=int(settings.expiration_warning_days),
        expired_retention_days=int(settings.expired_retention_days),
        expired_removal_warning_days=int(settings.expired_removal_warning_days),
        show_sale_pending_publicly=bool(settings.show_sale_pending_listings_publicly),
        show_sold_publicly=bool(settings.show_sold_listings_publicly),
        sold_retention_days=int(settings.sold_retention_days),
    )


def currency_policy() -> CurrencyPolicy:
    """Return effective currency presentation settings with stable defaults."""

    settings = get_settings_row()
    if settings is None:
        return CurrencyPolicy()

    return CurrencyPolicy(
        code=settings.currency_code,
        symbol=settings.currency_symbol,
        decimal_separator=settings.currency_decimal_separator,
        thousands_separator=settings.currency_thousands_separator,
    )


def distance_policy() -> DistancePolicy:
    """Return effective distance-search settings with a country-aware default."""

    settings = get_settings_row()
    if settings is None:
        return DistancePolicy()

    return DistancePolicy(default_unit=settings.default_distance_unit)


def public_listing_statuses() -> tuple[str, ...]:
    """Return lifecycle states currently exposed to anonymous buyers."""

    return listing_policy().public_statuses


def listing_is_publicly_visible(listing) -> bool:
    """Return whether one listing is public under the current site policy."""

    return listing.status in public_listing_statuses()


def seller_inventory_import_allowed() -> bool:
    """Return whether ordinary sellers may restore portable inventory bundles."""

    settings = get_settings_row()
    if settings is None:
        return DEFAULT_ALLOW_SELLER_INVENTORY_IMPORT
    return bool(settings.allow_seller_inventory_import)
