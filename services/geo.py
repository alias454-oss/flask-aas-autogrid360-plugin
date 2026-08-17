# app/plugins/autogrid360/services/geo.py
"""Postal reference synchronization and radius-search helpers."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
import re

from flask import current_app, has_app_context

from app.core.extensions import db
from app.core.locations import country_name
from app.plugins.autogrid360.models.postal import PostalLocation
from app.plugins.autogrid360.models.settings import (
    DEFAULT_DISTANCE_UNIT,
    DISTANCE_UNIT_AUTO,
    DISTANCE_UNIT_KILOMETERS,
    DISTANCE_UNIT_MILES,
)


DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "geography"
DEFAULT_COUNTRY = "US"
COUNTRY_ALIASES = {
    "US": "US",
    "USA": "US",
    "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "GB": "GB",
    "UK": "GB",
    "UNITED KINGDOM": "GB",
    "GREAT BRITAIN": "GB",
}
RADIUS_OPTIONS = (10, 25, 50, 100, 250)
AUTO_MILES_COUNTRIES = frozenset({"US", "GB"})
KM_PER_MILE = 1.609344
EARTH_RADIUS_KM = 6371.0088

_US_POSTAL_RE = re.compile(r"^(\d{5})(?:-\d{4})?$")
_GB_OUTWARD_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?$")


class PostalDataError(RuntimeError):
    """Raised when generated postal reference data is invalid or unavailable."""


@dataclass(frozen=True)
class PostalSyncResult:
    """Summary of one caller-owned postal dataset synchronization."""

    inserted: int
    updated: int
    reactivated: int
    deactivated: int
    total_active: int


def postal_data_root() -> Path:
    """Return the generated postal-artifact root, honoring test/deployment override."""

    if has_app_context():
        configured = current_app.config.get("AUTOGRID360_POSTAL_DATA_ROOT")
        if configured:
            return Path(configured)
    return DATA_ROOT


def normalize_country_code(value: object) -> str | None:
    """Normalize supported country labels/codes to ISO alpha-2 identifiers."""

    normalized = " ".join(str(value or "").strip().upper().split())
    if not normalized:
        return None
    known = COUNTRY_ALIASES.get(normalized)
    if known:
        return known
    if re.fullmatch(r"[A-Z]{2}", normalized):
        return normalized
    return None


def normalize_postal_code(country_code: object, value: object) -> str | None:
    """Normalize a searchable US ZIP or UK outward postcode."""

    country = normalize_country_code(country_code)
    raw_value = " ".join(str(value or "").strip().upper().split())
    if not country or not raw_value:
        return None

    if country == "US":
        compact = raw_value.replace(" ", "")
        match = _US_POSTAL_RE.fullmatch(compact)
        return match.group(1) if match else None

    if country == "GB":
        compact = raw_value.replace(" ", "")
        if len(compact) >= 5:
            compact = compact[:-3]
        if _GB_OUTWARD_RE.fullmatch(compact):
            return compact
        return None

    compact = "".join(raw_value.split())
    if not compact or len(compact) > 20:
        return None
    if not re.fullmatch(r"[A-Z0-9-]+", compact):
        return None
    return compact


def postal_artifact_filename(country_code: object) -> str:
    """Return the normalized artifact filename for one ISO alpha-2 country."""

    country = normalize_country_code(country_code)
    if not country:
        raise PostalDataError(f"Invalid postal country code: {country_code!r}")
    return f"{country.lower()}_postal_codes.csv"


def available_postal_countries() -> tuple[str, ...]:
    """Return canonical country codes with packaged postal artifacts on disk."""

    root = postal_data_root()
    countries: list[str] = []
    if not root.is_dir():
        return ()

    for path in sorted(root.glob("*_postal_codes.csv")):
        raw_country = path.name.removesuffix("_postal_codes.csv").upper()
        country = normalize_country_code(raw_country)
        if (
            country
            and len(country) == 2
            and path.name == postal_artifact_filename(country)
            and country not in countries
        ):
            countries.append(country)
    return tuple(countries)


def postal_country_choices() -> list[tuple[str, str]]:
    """Return countries that currently have active runtime postal data."""

    countries = [
        row[0]
        for row in (
            db.session.query(PostalLocation.country_code)
            .filter(PostalLocation.active.is_(True))
            .distinct()
            .order_by(PostalLocation.country_code.asc())
            .all()
        )
    ]
    return [
        (country, country_name(country) or country)
        for country in countries
    ]


def normalize_distance_unit(value: object, *, allow_auto: bool = True) -> str | None:
    """Normalize a supported distance unit token."""

    normalized = str(value or "").strip().lower()
    allowed = {DISTANCE_UNIT_MILES, DISTANCE_UNIT_KILOMETERS}
    if allow_auto:
        allowed.add(DISTANCE_UNIT_AUTO)
    return normalized if normalized in allowed else None


def resolve_distance_unit(country_code: object, configured_unit: object = DEFAULT_DISTANCE_UNIT) -> str:
    """Resolve Auto against the search country; explicit settings win globally."""

    configured = normalize_distance_unit(configured_unit) or DEFAULT_DISTANCE_UNIT
    if configured != DISTANCE_UNIT_AUTO:
        return configured
    country = normalize_country_code(country_code)
    return (
        DISTANCE_UNIT_MILES
        if country in AUTO_MILES_COUNTRIES
        else DISTANCE_UNIT_KILOMETERS
    )


def distance_to_kilometers(value: float, unit: object) -> float:
    """Convert a supported user-facing distance to canonical kilometers."""

    normalized = normalize_distance_unit(unit, allow_auto=False)
    if normalized == DISTANCE_UNIT_MILES:
        return float(value) * KM_PER_MILE
    if normalized == DISTANCE_UNIT_KILOMETERS:
        return float(value)
    raise ValueError(f"Unsupported distance unit: {unit!r}")


def distance_from_kilometers(value: float, unit: object) -> float:
    """Convert canonical kilometers to one supported display unit."""

    normalized = normalize_distance_unit(unit, allow_auto=False)
    if normalized == DISTANCE_UNIT_MILES:
        return float(value) / KM_PER_MILE
    if normalized == DISTANCE_UNIT_KILOMETERS:
        return float(value)
    raise ValueError(f"Unsupported distance unit: {unit!r}")


def postal_location_by_code(
    country_code: object,
    postal_code: object,
    *,
    active_only: bool = True,
) -> PostalLocation | None:
    """Resolve one normalized postal centroid."""

    country = normalize_country_code(country_code)
    code = normalize_postal_code(country, postal_code)
    if not country or not code:
        return None

    query = PostalLocation.query.filter_by(country_code=country, postal_code=code)
    if active_only:
        query = query.filter_by(active=True)
    return query.one_or_none()


def synchronize_listing_postal_location(listing) -> PostalLocation | None:
    """Attach the current centroid and fill missing locality metadata when available."""

    from app.plugins.autogrid360.services.location import postal_zone_code

    location = postal_location_by_code(listing.country_code, listing.postal_code)
    listing.postal_location = location
    if location is None:
        return None

    if not getattr(listing, "city", None) and location.locality:
        listing.city = location.locality
    if not getattr(listing, "zone_code", None):
        listing.zone_code = postal_zone_code(
            location.country_code,
            location.region_code,
            location.region,
        )
    return location


def apply_listing_form_location(listing, form) -> bool:
    """Apply seller-entered locality and resolve an optional postal centroid.

    A known postal record may fill a blank city/subdivision, but never overwrites
    seller-selected ISO reference data. City remains required when no lookup can supply it.
    """

    listing.country_code = form.country_code.data or None
    listing.city = form.city.data
    listing.zone_code = form.zone_code.data or None
    listing.postal_code = form.postal_code.data
    synchronize_listing_postal_location(listing)

    if listing.city:
        form.city.data = listing.city
        form.zone_code.data = listing.zone_code or ""
        return True

    form.city.errors.append(
        "Enter a city/locality, or provide a postal code that resolves to one."
    )
    return False


def haversine_kilometers(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Return great-circle distance in kilometers between WGS84 coordinates."""

    lat1 = radians(float(latitude_a))
    lon1 = radians(float(longitude_a))
    lat2 = radians(float(latitude_b))
    lon2 = radians(float(longitude_b))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    a = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * asin(min(1.0, sqrt(a)))


def radius_bounding_box_kilometers(
    latitude: float,
    longitude: float,
    radius_kilometers: float,
) -> tuple[float, float, float, float]:
    """Return a conservative coordinate box for a radius expressed in kilometers."""

    latitude = float(latitude)
    longitude = float(longitude)
    radius_kilometers = max(float(radius_kilometers), 0.0)
    latitude_delta = radius_kilometers / 111.045
    longitude_scale = max(abs(cos(radians(latitude))), 0.01)
    longitude_delta = radius_kilometers / (111.320 * longitude_scale)
    return (
        max(-90.0, latitude - latitude_delta),
        min(90.0, latitude + latitude_delta),
        max(-180.0, longitude - longitude_delta),
        min(180.0, longitude + longitude_delta),
    )


def _text(value: object, *, maximum: int) -> str | None:
    cleaned = " ".join(str(value or "").replace("\x00", "").split())
    if not cleaned:
        return None
    if len(cleaned) > maximum:
        raise PostalDataError("Postal reference text exceeds the supported length.")
    return cleaned


def _load_country_rows(path: Path, expected_country: str) -> dict[str, dict]:
    if not path.is_file():
        raise PostalDataError(
            f"Postal artifact is missing: {path}. Run scripts/update_postal_codes.py first."
        )

    rows: dict[str, dict] = {}
    with path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        required = {
            "country_code",
            "postal_code",
            "locality",
            "region",
            "region_code",
            "county",
            "latitude",
            "longitude",
            "accuracy",
            "source",
        }
        if set(reader.fieldnames or ()) != required:
            raise PostalDataError(f"Unexpected postal artifact columns in {path}.")

        for line_number, row in enumerate(reader, start=2):
            country = normalize_country_code(row.get("country_code"))
            code = normalize_postal_code(country, row.get("postal_code"))
            if country != expected_country or not code:
                raise PostalDataError(
                    f"Invalid postal identity at {path}:{line_number}."
                )
            if code in rows:
                raise PostalDataError(
                    f"Duplicate postal code {country}:{code} in {path}."
                )
            try:
                latitude = float(row["latitude"])
                longitude = float(row["longitude"])
                accuracy = int(row["accuracy"]) if row["accuracy"].strip() else None
            except (TypeError, ValueError) as exc:
                raise PostalDataError(
                    f"Invalid postal coordinate at {path}:{line_number}."
                ) from exc
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise PostalDataError(
                    f"Postal coordinate out of range at {path}:{line_number}."
                )
            rows[code] = {
                "country_code": country,
                "postal_code": code,
                "locality": _text(row.get("locality"), maximum=180),
                "region": _text(row.get("region"), maximum=100),
                "region_code": _text(row.get("region_code"), maximum=20),
                "county": _text(row.get("county"), maximum=100),
                "latitude": latitude,
                "longitude": longitude,
                "accuracy": accuracy,
                "source": _text(row.get("source"), maximum=40) or "geonames",
            }
    return rows


def sync_postal_data(
    *,
    countries: tuple[str, ...] | list[str] | None = None,
    data_root: Path | str | None = None,
) -> PostalSyncResult:
    """Synchronize normalized postal artifacts without deleting referenced history.

    Existing rows are updated from the generated artifact. Rows absent from a refreshed
    country artifact are marked inactive instead of deleted so historical listing foreign
    keys remain valid. The caller owns transaction commit/rollback.
    """

    selected = tuple(countries or (DEFAULT_COUNTRY,))
    normalized_countries: list[str] = []
    for value in selected:
        country = normalize_country_code(value)
        if not country:
            raise PostalDataError(f"Invalid postal country code: {value!r}")
        if country not in normalized_countries:
            normalized_countries.append(country)

    root = Path(data_root) if data_root is not None else postal_data_root()
    inserted = 0
    updated = 0
    reactivated = 0
    deactivated = 0

    for country in normalized_countries:
        incoming = _load_country_rows(root / postal_artifact_filename(country), country)
        existing = {
            row.postal_code: row
            for row in PostalLocation.query.filter_by(country_code=country).all()
        }

        for code, payload in incoming.items():
            current = existing.get(code)
            if current is None:
                db.session.add(PostalLocation(**payload, active=True))
                inserted += 1
                continue

            changed = False
            for field in (
                "locality",
                "region",
                "region_code",
                "county",
                "latitude",
                "longitude",
                "accuracy",
                "source",
            ):
                value = payload[field]
                if getattr(current, field) != value:
                    setattr(current, field, value)
                    changed = True
            if not current.active:
                current.active = True
                reactivated += 1
                changed = True
            if changed:
                updated += 1

        incoming_codes = set(incoming)
        for code, current in existing.items():
            if code not in incoming_codes and current.active:
                current.active = False
                deactivated += 1

    db.session.flush()
    total_active = PostalLocation.query.filter(
        PostalLocation.country_code.in_(normalized_countries),
        PostalLocation.active.is_(True),
    ).count()
    return PostalSyncResult(
        inserted=inserted,
        updated=updated,
        reactivated=reactivated,
        deactivated=deactivated,
        total_active=total_active,
    )
