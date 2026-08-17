# app/plugins/autogrid360/services/location.py
"""Bridge AutoGrid360 location data to the canonical Flask-AAS ISO references."""

from __future__ import annotations

from flask import g, has_request_context

from app.core.cache import get_cached_env_settings
from sqlalchemy import select

from app.core.extensions import db
from app.core.locations import (
    configure_location_choices as configure_host_location_choices,
    country_name as host_country_name,
    normalize_country_code,
    normalize_zone_code,
    zone_name as host_zone_name,
    zone_records,
)
from app.models.country import Country
from app.models.zone import Zone


class LocationReferenceError(ValueError):
    """Raised when portable AutoGrid360 location codes do not match host references."""


def configure_location_form(form) -> None:
    """Populate one AutoGrid360 form from Flask-AAS Country/Zone references."""

    configure_host_location_choices(form)


def validate_location_codes(
    country_code: object,
    zone_code: object,
    *,
    active_only: bool = True,
) -> tuple[str | None, str | None]:
    """Normalize and validate one portable Country/Zone pair."""

    country = normalize_country_code(country_code)
    zone = normalize_zone_code(zone_code)

    if zone and not country:
        raise LocationReferenceError("A subdivision requires a country.")
    if not country:
        return None, None

    country_query = select(Country).where(Country.iso_code_2 == country)
    if active_only:
        country_query = country_query.where(Country.active.is_(True))
    country_row = db.session.scalar(country_query)
    if country_row is None:
        raise LocationReferenceError("Choose a valid country.")

    if not zone:
        return country, None

    zone_query = select(Zone).where(
        Zone.code == zone,
        Zone.country_id == country_row.country_id,
    )
    if active_only:
        zone_query = zone_query.where(Zone.active.is_(True))
    if db.session.scalar(zone_query) is None:
        raise LocationReferenceError("Choose a valid subdivision for the selected country.")

    return country, zone


def postal_zone_code(
    country_code: object,
    region_code: object = None,
    region_name: object = None,
) -> str | None:
    """Resolve postal region metadata to one active host ISO subdivision code."""

    country = normalize_country_code(country_code)
    if not country:
        return None

    records = zone_records(country)
    if not records:
        return None

    raw_region_code = str(region_code or "").strip().upper()
    if raw_region_code:
        candidate = (
            raw_region_code
            if raw_region_code.startswith(f"{country}-")
            else f"{country}-{raw_region_code}"
        )
        for record in records:
            if record["code"] == candidate:
                return candidate

    raw_region_name = " ".join(str(region_name or "").split()).casefold()
    if raw_region_name:
        matches = [
            record["code"]
            for record in records
            if record["name"].casefold() == raw_region_name
        ]
        if len(matches) == 1:
            return matches[0]

    return None


def _request_cached_label(kind: str, code: str, resolver) -> str:
    if not has_request_context():
        return resolver(code) or code

    cache = getattr(g, "_autogrid360_location_labels", None)
    if cache is None:
        cache = {}
        g._autogrid360_location_labels = cache
    key = (kind, code)
    if key not in cache:
        cache[key] = resolver(code) or code
    return cache[key]


def country_label(country_code: object) -> str | None:
    """Return the host display name for one stored ISO alpha-2 code."""

    code = normalize_country_code(country_code)
    if not code:
        return None
    return _request_cached_label("country", code, host_country_name)


def zone_label(zone_code: object) -> str | None:
    """Return a host display label for one stored ISO 3166-2 code."""

    code = normalize_zone_code(zone_code)
    if not code:
        return None

    if has_request_context() and "-" in code:
        country_code = code.split("-", 1)[0]
        cache = getattr(g, "_autogrid360_zone_labels", None)
        if cache is None:
            cache = {}
            g._autogrid360_zone_labels = cache
        if country_code not in cache:
            cache[country_code] = {
                record["code"]: record["label"]
                for record in zone_records(country_code)
            }
        label = cache[country_code].get(code)
        if label:
            return label

    return _request_cached_label("zone", code, host_zone_name)


def format_public_location(
    *,
    country_code: object = None,
    zone_code: object = None,
    city: object = None,
    postal_code: object = None,
) -> str:
    """Render public locality text without exposing a street address."""

    locality = " ".join(str(city or "").split())
    region = zone_label(zone_code) or ""
    postal = " ".join(str(postal_code or "").split())
    country = country_label(country_code) or ""

    parts: list[str] = []
    if locality and region:
        parts.append(f"{locality}, {region}")
    elif locality or region:
        parts.append(locality or region)
    if postal:
        parts.append(postal)

    rendered = " ".join(parts)
    if country:
        rendered = f"{rendered} · {country}" if rendered else country
    return rendered

def user_location_enabled() -> bool:
    """Return whether account location data is enabled for this installation."""

    settings = get_cached_env_settings()
    return bool(settings and settings.use_user_location)


def listing_profile_location(user) -> dict[str, str] | None:
    """Return saved account location values suitable for copying into a listing."""

    if not user_location_enabled():
        return None

    country = normalize_country_code(getattr(user, "country_code", None))
    if not country:
        return None

    zone = normalize_zone_code(getattr(user, "zone_code", None))
    city = " ".join(str(getattr(user, "city", None) or "").split())
    postal = " ".join(str(getattr(user, "postal_code", None) or "").split())
    return {
        "country_code": country,
        "zone_code": zone or "",
        "city": city,
        "postal_code": postal,
    }


def user_public_location(user) -> str:
    """Return coarse public account location when location sharing is enabled."""

    if not user_location_enabled():
        return ""

    return format_public_location(
        country_code=getattr(user, "country_code", None),
        zone_code=getattr(user, "zone_code", None),
        city=getattr(user, "city", None),
    )
