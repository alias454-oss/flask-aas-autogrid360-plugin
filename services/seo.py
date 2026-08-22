# app/plugins/autogrid360/services/seo.py
"""SEO and public publishing helpers owned by AutoGrid360."""

from __future__ import annotations

from datetime import timezone
import re
import unicodedata

from flask import url_for

from app.plugins.autogrid360.models import STATUS_ACTIVE, STATUS_SALE_PENDING
from app.plugins.autogrid360.services.settings import currency_policy


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")


def slugify(value: str | None, *, fallback: str = "listing") -> str:
    """Return one conservative ASCII URL slug."""

    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_RE.sub("-", ascii_value).strip("-")
    return slug[:96].rstrip("-") or fallback


def listing_slug_from_parts(
    *,
    year=None,
    make: str | None = None,
    model: str | None = None,
    trim: str | None = None,
    title: str | None = None,
) -> str:
    """Return the canonical listing slug from scalar vehicle/listing values."""

    vehicle_name = " ".join(
        part
        for part in (
            str(year) if year else "",
            make or "",
            model or "",
            trim or "",
        )
        if part
    )
    return slugify(vehicle_name or title)


def listing_slug(listing) -> str:
    """Return the current canonical slug for one listing."""

    vehicle = listing.vehicle
    return listing_slug_from_parts(
        year=vehicle.year,
        make=vehicle.make,
        model=vehicle.model,
        trim=vehicle.trim,
        title=listing.title,
    )


def listing_url(listing, *, external: bool = False) -> str:
    """Build the canonical public URL for one listing."""

    return url_for(
        "autogrid360.listing_public",
        listing_id=listing.id,
        slug=listing_slug(listing),
        _external=external,
    )


def compact_text(value: str | None, *, limit: int) -> str:
    """Collapse whitespace and bound one metadata string."""

    text = _WHITESPACE_RE.sub(" ", (value or "").strip())
    if len(text) <= limit:
        return text
    shortened = text[: max(1, limit - 1)].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return f"{shortened or text[: limit - 1].rstrip()}…"


def listing_vehicle_name(listing) -> str:
    """Return a concise year/make/model/trim label."""

    vehicle = listing.vehicle
    return " ".join(
        part
        for part in (
            str(vehicle.year) if vehicle.year else "",
            vehicle.make or "",
            vehicle.model or "",
            vehicle.trim or "",
        )
        if part
    )


def listing_meta_title(listing) -> str:
    """Return a useful listing-specific HTML/OG title."""

    vehicle_name = listing_vehicle_name(listing)
    if vehicle_name:
        return compact_text(f"{vehicle_name} for Sale - {listing.title}", limit=70)
    return compact_text(listing.title, limit=70)


def listing_meta_description(listing) -> str:
    """Return one concise public description suitable for search/social metadata."""

    if listing.description and listing.description.strip():
        return compact_text(listing.description, limit=160)

    parts = []
    vehicle_name = listing_vehicle_name(listing)
    if vehicle_name:
        parts.append(f"{vehicle_name} for sale")
    if listing.price is not None:
        from app.plugins.autogrid360.services.formatting import format_currency

        parts.append(f"listed at {format_currency(listing.price)}")
    location = listing.public_location
    if location:
        parts.append(f"in {location}")
    return compact_text(" ".join(parts) or listing.title, limit=160)


def listing_structured_data(listing, *, canonical_url: str, image_url: str | None):
    """Build Schema.org Product/Vehicle JSON-LD from visible listing data."""

    vehicle = listing.vehicle
    payload = {
        "@context": "https://schema.org",
        "@type": ["Product", "Vehicle"],
        "name": listing_meta_title(listing),
        "description": listing_meta_description(listing),
        "url": canonical_url,
        "sku": listing.portable_id,
    }
    if image_url:
        payload["image"] = [image_url]
    if vehicle.make:
        payload["brand"] = {"@type": "Brand", "name": vehicle.make}
    if vehicle.model:
        payload["model"] = vehicle.model
    if vehicle.year:
        payload["vehicleModelDate"] = str(vehicle.year)
    if vehicle.vin:
        payload["vehicleIdentificationNumber"] = vehicle.vin
    if vehicle.exterior_color:
        payload["color"] = vehicle.exterior_color
    if vehicle.fuel_type:
        payload["fuelType"] = vehicle.fuel_type
    if vehicle.transmission:
        payload["vehicleTransmission"] = vehicle.transmission

    if listing.price is not None:
        condition = (vehicle.condition or "").strip().lower()
        item_condition = {
            "new": "https://schema.org/NewCondition",
            "refurbished": "https://schema.org/RefurbishedCondition",
            "reconditioned": "https://schema.org/RefurbishedCondition",
            "used": "https://schema.org/UsedCondition",
            "pre-owned": "https://schema.org/UsedCondition",
            "preowned": "https://schema.org/UsedCondition",
        }.get(condition)

        offer = {
            "@type": "Offer",
            "url": canonical_url,
            "price": str(listing.price),
            "priceCurrency": currency_policy().code,
            "availability": (
                "https://schema.org/InStock"
                if listing.status == STATUS_ACTIVE
                else (
                    "https://schema.org/LimitedAvailability"
                    if listing.status == STATUS_SALE_PENDING
                    else "https://schema.org/SoldOut"
                )
            ),
        }
        if item_condition:
            offer["itemCondition"] = item_condition
        payload["offers"] = offer

    return payload


def sitemap_lastmod_from_values(updated_at, published_at, created_at) -> str:
    """Return sitemap lastmod from scalar listing timestamp values."""

    value = updated_at or published_at or created_at
    return value.date().isoformat()


def sitemap_lastmod(listing) -> str:
    """Return the actual listing-content modification date for sitemap output."""

    return sitemap_lastmod_from_values(
        listing.updated_at,
        listing.published_at,
        listing.created_at,
    )


def rss_datetime(value) -> str:
    """Return an RFC 2822 timestamp suitable for RSS 2.0."""

    from email.utils import format_datetime

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return format_datetime(value)


def listing_is_indexable(listing) -> bool:
    """Return whether the listing belongs in active search-engine publishing surfaces."""

    return listing.status == STATUS_ACTIVE


def listing_robots_meta(listing) -> str:
    """Return crawler policy for one public listing detail page."""

    return "index,follow" if listing_is_indexable(listing) else "noindex,follow"
