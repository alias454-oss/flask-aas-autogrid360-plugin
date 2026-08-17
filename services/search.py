# app/plugins/autogrid360/services/search.py
"""Reusable public inventory search and URL semantics for AutoGrid360."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import url_for
from sqlalchemy import and_, case, false, func, or_
from sqlalchemy.orm import aliased

from app.core.extensions import db
from app.models import Country, User, Zone
from app.plugins.autogrid360.models import (
    CATEGORY_DRIVETRAIN,
    CATEGORY_FEATURE,
    CATEGORY_MAKE,
    CATEGORY_VEHICLE_TYPE,
    STATUS_ACTIVE,
    STATUS_SALE_PENDING,
    Listing,
    PostalLocation,
    ReferenceValue,
    Vehicle,
    VehicleModel,
    vehicle_features,
)
from app.plugins.autogrid360.services.geo import (
    RADIUS_OPTIONS,
    distance_from_kilometers,
    distance_to_kilometers,
    haversine_kilometers,
    normalize_country_code,
    normalize_distance_unit,
    normalize_postal_code,
    postal_location_by_code,
    radius_bounding_box_kilometers,
    resolve_distance_unit,
)
from app.plugins.autogrid360.services.reference import (
    normalize_reference_key,
    parse_model_choice,
    reference_by_key,
    vehicle_model_by_key,
)
from app.plugins.autogrid360.services.seo import slugify
from app.plugins.autogrid360.services.settings import (
    distance_policy,
    public_listing_statuses,
)


SORT_NEWEST = "newest"
SORT_MAKE_ASC = "make_asc"
SORT_MAKE_DESC = "make_desc"
SORT_MODEL_ASC = "model_asc"
SORT_MODEL_DESC = "model_desc"
SORT_YEAR_ASC = "year_asc"
SORT_YEAR_DESC = "year_desc"
SORT_PRICE_ASC = "price_asc"
SORT_PRICE_DESC = "price_desc"
SORT_OPTIONS = {
    SORT_NEWEST,
    SORT_MAKE_ASC,
    SORT_MAKE_DESC,
    SORT_MODEL_ASC,
    SORT_MODEL_DESC,
    SORT_YEAR_ASC,
    SORT_YEAR_DESC,
    SORT_PRICE_ASC,
    SORT_PRICE_DESC,
}
PAGE_SIZE_OPTIONS = (10, 20, 50, 100)


@dataclass(frozen=True)
class InventoryModelChoice:
    """One model option represented by active public inventory."""

    value: str
    label: str
    make_key: str
    make_label: str


@dataclass(frozen=True)
class InventoryZoneChoice:
    """One listing-region option represented by active public inventory."""

    code: str
    label: str
    country_code: str


@dataclass
class InventorySearchFacets:
    """Selectable Advanced Search values derived from active inventory."""

    makes: list[ReferenceValue]
    models: list[InventoryModelChoice]
    years: list[int]
    vehicle_types: list[ReferenceValue]
    drivetrains: list[ReferenceValue]
    features: list[ReferenceValue]
    conditions: list[str]
    transmissions: list[str]
    sellers: list[User]
    countries: list[tuple[str, str]]
    zones: list[InventoryZoneChoice]


@dataclass
class InventorySearchCriteria:
    """Normalized semantic criteria for one public inventory search."""

    make: str = ""
    model: str = ""
    min_year: int | None = None
    max_year: int | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    vehicle_type: str = ""
    drivetrain: str = ""
    feature: list[str] = field(default_factory=list)
    condition: str = ""
    transmission: str = ""
    seller: str = ""
    country_code: str = ""
    zone_code: str = ""
    postal_country: str = ""
    postal_code: str = ""
    radius: int | None = None
    distance_unit: str = ""

    @property
    def has_filters(self) -> bool:
        return any(value not in (None, "", []) for value in self.as_dict().values())

    def as_dict(self) -> dict:
        return {
            "make": self.make,
            "model": self.model,
            "min_year": self.min_year,
            "max_year": self.max_year,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "vehicle_type": self.vehicle_type,
            "drivetrain": self.drivetrain,
            "feature": list(self.feature),
            "condition": self.condition,
            "transmission": self.transmission,
            "seller": self.seller,
            "country_code": self.country_code,
            "zone_code": self.zone_code,
            "postal_country": self.postal_country,
            "postal_code": self.postal_code,
            "radius": self.radius,
            "distance_unit": self.distance_unit,
        }

    def query_args(self) -> dict:
        return {
            name: value
            for name, value in self.as_dict().items()
            if value not in (None, "", [])
        }

    @property
    def exact_year(self) -> int | None:
        if self.min_year is not None and self.min_year == self.max_year:
            return self.min_year
        return None

    @property
    def uses_only_core_seo_filters(self) -> bool:
        """Return whether the criteria form one bounded, indexable facet page."""

        return (
            not any(
                (
                    self.min_price is not None,
                    self.max_price is not None,
                    bool(self.vehicle_type),
                    bool(self.feature),
                    bool(self.condition),
                    bool(self.transmission),
                    bool(self.seller),
                    bool(self.postal_code),
                    self.radius is not None,
                    bool(self.distance_unit),
                )
            )
            and (not self.model or parse_model_choice(self.model) is not None)
            and (
                self.exact_year is not None
                or (self.min_year is None and self.max_year is None)
            )
        )


@dataclass
class InventorySearchQuery:
    """Prepared SQL query plus buyer-facing distance state."""

    query: object
    criteria: InventorySearchCriteria
    distance_by_id: dict[int, float]
    location_error: str | None
    selected_distance_unit: str
    effective_distance_unit: str | None


def _active_reference_choices(category: str, vehicle_column) -> list[ReferenceValue]:
    """Return reference values actually attached to public inventory."""

    return (
        ReferenceValue.query
        .join(Vehicle, vehicle_column == ReferenceValue.id)
        .join(Listing, Listing.vehicle_id == Vehicle.id)
        .filter(
            Listing.status.in_(public_listing_statuses()),
            ReferenceValue.category == category,
        )
        .distinct()
        .order_by(
            ReferenceValue.sort_order.asc(),
            ReferenceValue.label.asc(),
            ReferenceValue.id.asc(),
        )
        .all()
    )


def _active_feature_choices() -> list[ReferenceValue]:
    """Return feature references represented by public inventory."""

    return (
        ReferenceValue.query
        .join(
            vehicle_features,
            vehicle_features.c.reference_value_id == ReferenceValue.id,
        )
        .join(Vehicle, vehicle_features.c.vehicle_id == Vehicle.id)
        .join(Listing, Listing.vehicle_id == Vehicle.id)
        .filter(
            Listing.status.in_(public_listing_statuses()),
            ReferenceValue.category == CATEGORY_FEATURE,
        )
        .distinct()
        .order_by(
            ReferenceValue.sort_order.asc(),
            ReferenceValue.label.asc(),
            ReferenceValue.id.asc(),
        )
        .all()
    )


def _casefold_unique(values) -> list[str]:
    """Return trimmed non-empty strings de-duplicated case-insensitively."""

    by_key: dict[str, str] = {}
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            by_key.setdefault(normalized.casefold(), normalized)
    return sorted(by_key.values(), key=str.casefold)


def active_inventory_search_facets() -> InventorySearchFacets:
    """Build Advanced Search controls from currently public inventory."""

    makes = _active_reference_choices(CATEGORY_MAKE, Vehicle.make_id)
    vehicle_types = _active_reference_choices(
        CATEGORY_VEHICLE_TYPE,
        Vehicle.vehicle_type_id,
    )
    drivetrains = _active_reference_choices(
        CATEGORY_DRIVETRAIN,
        Vehicle.drivetrain_id,
    )

    canonical_models = (
        db.session.query(
            VehicleModel.key,
            VehicleModel.label,
            ReferenceValue.key,
            ReferenceValue.label,
        )
        .join(Vehicle, Vehicle.model_id == VehicleModel.id)
        .join(Listing, Listing.vehicle_id == Vehicle.id)
        .join(ReferenceValue, Vehicle.make_id == ReferenceValue.id)
        .filter(
            Listing.status.in_(public_listing_statuses()),
            ReferenceValue.category == CATEGORY_MAKE,
        )
        .distinct()
        .all()
    )
    text_models = (
        db.session.query(
            Vehicle.model_text,
            ReferenceValue.key,
            ReferenceValue.label,
        )
        .join(Listing, Listing.vehicle_id == Vehicle.id)
        .join(ReferenceValue, Vehicle.make_id == ReferenceValue.id)
        .filter(
            Listing.status.in_(public_listing_statuses()),
            ReferenceValue.category == CATEGORY_MAKE,
            Vehicle.model_id.is_(None),
            Vehicle.model_text.is_not(None),
            func.trim(Vehicle.model_text) != "",
        )
        .distinct()
        .all()
    )

    models_by_label = {
        (make_key, model_label.casefold()): InventoryModelChoice(
            value=f"{make_key}:{model_key}",
            label=model_label,
            make_key=make_key,
            make_label=make_label,
        )
        for model_key, model_label, make_key, make_label in canonical_models
    }
    for model_text, make_key, make_label in text_models:
        label = model_text.strip()
        models_by_label.setdefault(
            (make_key, label.casefold()),
            InventoryModelChoice(
                value=label,
                label=label,
                make_key=make_key,
                make_label=make_label,
            ),
        )
    models = list(models_by_label.values())
    models.sort(
        key=lambda value: (
            value.make_label.casefold(),
            value.label.casefold(),
            value.value.casefold(),
        )
    )

    minimum_year, maximum_year = (
        db.session.query(func.min(Vehicle.year), func.max(Vehicle.year))
        .join(Listing, Listing.vehicle_id == Vehicle.id)
        .filter(
            Listing.status.in_(public_listing_statuses()),
            Vehicle.year.is_not(None),
        )
        .one()
    )
    years: list[int] = []
    if minimum_year is not None:
        latest_boundary = max(
            maximum_year or minimum_year,
            datetime.now(timezone.utc).year,
        )
        years = list(range(latest_boundary, minimum_year - 1, -1))

    conditions = _casefold_unique(
        value
        for value, in (
            db.session.query(Vehicle.condition)
            .join(Listing, Listing.vehicle_id == Vehicle.id)
            .filter(
                Listing.status.in_(public_listing_statuses()),
                Vehicle.condition.is_not(None),
                func.trim(Vehicle.condition) != "",
            )
            .distinct()
            .all()
        )
    )
    transmissions = _casefold_unique(
        value
        for value, in (
            db.session.query(Vehicle.transmission)
            .join(Listing, Listing.vehicle_id == Vehicle.id)
            .filter(
                Listing.status.in_(public_listing_statuses()),
                Vehicle.transmission.is_not(None),
                func.trim(Vehicle.transmission) != "",
            )
            .distinct()
            .all()
        )
    )

    sellers = (
        User.query
        .join(Listing, Listing.seller_id == User.id)
        .filter(Listing.status.in_(public_listing_statuses()))
        .distinct()
        .order_by(User.username.asc())
        .all()
    )

    countries = (
        db.session.query(Country.iso_code_2, Country.name)
        .join(Listing, Listing.country_code == Country.iso_code_2)
        .filter(Listing.status.in_(public_listing_statuses()))
        .distinct()
        .order_by(Country.name.asc(), Country.iso_code_2.asc())
        .all()
    )
    zones = [
        InventoryZoneChoice(
            code=code,
            label=name,
            country_code=country_code,
        )
        for code, name, country_code, _country_name in (
            # PostgreSQL requires DISTINCT ORDER BY expressions in the select list.
            db.session.query(
                Zone.code,
                Zone.name,
                Country.iso_code_2,
                Country.name,
            )
            .join(Country, Zone.country_id == Country.country_id)
            .join(
                Listing,
                and_(
                    Listing.zone_code == Zone.code,
                    Listing.country_code == Country.iso_code_2,
                ),
            )
            .filter(Listing.status.in_(public_listing_statuses()))
            .distinct()
            .order_by(Country.name.asc(), Zone.name.asc(), Zone.code.asc())
            .all()
        )
    ]

    return InventorySearchFacets(
        makes=makes,
        models=models,
        years=years,
        vehicle_types=vehicle_types,
        drivetrains=drivetrains,
        features=_active_feature_choices(),
        conditions=conditions,
        transmissions=transmissions,
        sellers=sellers,
        countries=[(code, label) for code, label in countries],
        zones=zones,
    )


def _text(args, name: str) -> str:
    return (args.get(name) or "").strip()


def _reference(args, name: str) -> str:
    return normalize_reference_key(_text(args, name))


def _references(args, name: str) -> list[str]:
    values: list[str] = []
    raw_values = args.getlist(name) if hasattr(args, "getlist") else []
    for raw_value in raw_values:
        key = normalize_reference_key(raw_value)
        if key and key not in values:
            values.append(key)
    return values


def _year(args, name: str) -> int | None:
    try:
        value = int(args.get(name)) if args.get(name) not in (None, "") else None
    except (TypeError, ValueError):
        return None
    if value is None or not 1886 <= value <= 2100:
        return None
    return value


def _decimal(args, name: str) -> Decimal | None:
    raw_value = _text(args, name)
    if not raw_value:
        return None
    try:
        value = Decimal(raw_value)
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0:
        return None
    return value


def canonicalize_inventory_criteria(
    criteria: InventorySearchCriteria,
) -> InventorySearchCriteria:
    """Normalize stable public identities used by inventory URLs and filters."""

    if criteria.seller:
        seller = (
            User.query
            .filter(func.lower(User.username) == criteria.seller.lower())
            .order_by(User.id.asc())
            .first()
        )
        if seller is not None:
            criteria.seller = seller.username
    return criteria


def parse_inventory_criteria(args) -> InventorySearchCriteria:
    """Parse normalized semantic search criteria from a MultiDict-like object."""

    criteria = InventorySearchCriteria(
        make=_reference(args, "make"),
        model=_text(args, "model"),
        min_year=_year(args, "min_year"),
        max_year=_year(args, "max_year"),
        min_price=_decimal(args, "min_price"),
        max_price=_decimal(args, "max_price"),
        vehicle_type=_reference(args, "vehicle_type"),
        drivetrain=_reference(args, "drivetrain"),
        feature=_references(args, "feature"),
        condition=_text(args, "condition"),
        transmission=_text(args, "transmission"),
        seller=_text(args, "seller"),
        country_code=normalize_country_code(_text(args, "country_code")) or "",
        zone_code=_text(args, "zone_code").upper(),
        postal_country=normalize_country_code(_text(args, "postal_country")) or "",
        postal_code=_text(args, "postal_code"),
    )
    if criteria.model:
        parsed_model = parse_model_choice(criteria.model)
        if parsed_model is not None and not criteria.make:
            criteria.make = parsed_model[0]
    if criteria.postal_code:
        try:
            radius = int(args.get("radius")) if args.get("radius") not in (None, "") else None
        except (TypeError, ValueError):
            radius = None
        criteria.radius = radius if radius in RADIUS_OPTIONS else 50
        criteria.distance_unit = normalize_distance_unit(_text(args, "distance_unit")) or ""
    else:
        criteria.postal_country = ""
    return criteria


def parse_sort(args) -> str:
    sort = _text(args, "sort") or SORT_NEWEST
    return sort if sort in SORT_OPTIONS else SORT_NEWEST


def parse_page(args) -> int:
    try:
        page = int(args.get("page", 1))
    except (TypeError, ValueError):
        return 1
    return max(1, page)


def parse_per_page(args) -> int | None:
    try:
        value = int(args.get("per_page")) if args.get("per_page") not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return value if value in PAGE_SIZE_OPTIONS else None


def _contains(column, value: str):
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def _radius_candidates(
    country_code: str,
    postal_code: str,
    radius_kilometers: float,
) -> tuple[set[int], dict[int, float], str | None]:
    origin = postal_location_by_code(country_code, postal_code)
    if origin is None:
        return (
            set(),
            {},
            "That postal code is not available in the installed AutoGrid360 geography data.",
        )

    min_lat, max_lat, min_lon, max_lon = radius_bounding_box_kilometers(
        origin.latitude,
        origin.longitude,
        radius_kilometers,
    )
    candidates = (
        db.session.query(Listing.id, PostalLocation.latitude, PostalLocation.longitude)
        .join(PostalLocation, Listing.postal_location_id == PostalLocation.id)
        .filter(
            Listing.status.in_(public_listing_statuses()),
            PostalLocation.active.is_(True),
            PostalLocation.latitude.between(min_lat, max_lat),
            PostalLocation.longitude.between(min_lon, max_lon),
        )
        .all()
    )

    distances: dict[int, float] = {}
    for listing_id, latitude, longitude in candidates:
        distance = haversine_kilometers(
            origin.latitude,
            origin.longitude,
            latitude,
            longitude,
        )
        if distance <= radius_kilometers:
            distances[listing_id] = distance
    return set(distances), distances, None


def prepare_inventory_query(
    criteria: InventorySearchCriteria,
    *,
    sort: str = SORT_NEWEST,
) -> InventorySearchQuery:
    """Build one public-inventory query from reusable semantic criteria."""

    location_error = None
    distance_by_id: dict[int, float] = {}
    configured_distance_unit = distance_policy().default_unit
    selected_distance_unit = criteria.distance_unit or configured_distance_unit
    effective_distance_unit = None
    radius_listing_ids: set[int] | None = None

    if criteria.postal_code:
        if not criteria.postal_country:
            location_error = "Choose a country with installed postal data for radius search."
            radius_listing_ids = set()
        else:
            effective_distance_unit = resolve_distance_unit(
                criteria.postal_country,
                selected_distance_unit,
            )
            normalized_postal = normalize_postal_code(
                criteria.postal_country,
                criteria.postal_code,
            )
            if normalized_postal is None:
                location_error = "Enter a valid postal code for the selected country."
                radius_listing_ids = set()
            else:
                criteria.postal_code = normalized_postal
                radius_kilometers = distance_to_kilometers(
                    criteria.radius or 50,
                    effective_distance_unit,
                )
                (
                    radius_listing_ids,
                    distance_kilometers_by_id,
                    location_error,
                ) = _radius_candidates(
                    criteria.postal_country,
                    normalized_postal,
                    radius_kilometers,
                )
                distance_by_id = {
                    listing_id: distance_from_kilometers(
                        distance_kilometers,
                        effective_distance_unit,
                    )
                    for listing_id, distance_kilometers in distance_kilometers_by_id.items()
                }

    make_ref = aliased(ReferenceValue)
    query = (
        Listing.query.join(Vehicle)
        .join(make_ref, Vehicle.make_id == make_ref.id)
        .filter(Listing.status.in_(public_listing_statuses()))
    )
    if radius_listing_ids is not None:
        query = (
            query.filter(Listing.id.in_(radius_listing_ids))
            if radius_listing_ids
            else query.filter(false())
        )

    parsed_model = parse_model_choice(criteria.model) if criteria.model else None
    if criteria.make:
        query = query.filter(make_ref.key == criteria.make)
    if criteria.model:
        if parsed_model is not None:
            model_make_key, model_key = parsed_model
            model_ref = aliased(VehicleModel)
            query = query.join(model_ref, Vehicle.model_id == model_ref.id).filter(
                make_ref.key == model_make_key,
                model_ref.key == model_key,
            )
        else:
            query = query.filter(
                or_(
                    _contains(Vehicle.model_text, criteria.model),
                    Vehicle.model_ref.has(_contains(VehicleModel.label, criteria.model)),
                )
            )
    if criteria.min_year is not None:
        query = query.filter(Vehicle.year >= criteria.min_year)
    if criteria.max_year is not None:
        query = query.filter(Vehicle.year <= criteria.max_year)
    if criteria.min_price is not None:
        query = query.filter(Listing.price >= criteria.min_price)
    if criteria.max_price is not None:
        query = query.filter(Listing.price <= criteria.max_price)
    if criteria.vehicle_type:
        type_ref = aliased(ReferenceValue)
        query = query.join(type_ref, Vehicle.vehicle_type_id == type_ref.id).filter(
            type_ref.key == criteria.vehicle_type
        )
    if criteria.drivetrain:
        drivetrain_ref = aliased(ReferenceValue)
        query = query.join(drivetrain_ref, Vehicle.drivetrain_id == drivetrain_ref.id).filter(
            drivetrain_ref.category == CATEGORY_DRIVETRAIN,
            drivetrain_ref.key == criteria.drivetrain,
        )
    for feature_key in criteria.feature:
        query = query.filter(
            Vehicle.features.any(
                and_(
                    ReferenceValue.category == CATEGORY_FEATURE,
                    ReferenceValue.key == feature_key,
                )
            )
        )
    if criteria.condition:
        query = query.filter(_contains(Vehicle.condition, criteria.condition))
    if criteria.transmission:
        query = query.filter(_contains(Vehicle.transmission, criteria.transmission))
    if criteria.seller:
        query = query.join(User, Listing.seller_id == User.id).filter(
            func.lower(User.username) == criteria.seller.lower()
        )
    if criteria.country_code:
        query = query.filter(Listing.country_code == criteria.country_code)
    if criteria.zone_code:
        query = query.filter(Listing.zone_code == criteria.zone_code)

    availability_rank = case(
        (Listing.status == STATUS_ACTIVE, 0),
        (Listing.status == STATUS_SALE_PENDING, 1),
        else_=2,
    )

    if sort in {SORT_MAKE_ASC, SORT_MAKE_DESC, SORT_MODEL_ASC, SORT_MODEL_DESC}:
        model_sort_ref = aliased(VehicleModel)
        query = query.outerjoin(model_sort_ref, Vehicle.model_id == model_sort_ref.id)
        make_sort_value = func.lower(make_ref.label)
        model_sort_value = func.lower(func.coalesce(model_sort_ref.label, Vehicle.model_text))
        if sort in {SORT_MAKE_ASC, SORT_MAKE_DESC}:
            query = query.order_by(
                availability_rank.asc(),
                make_sort_value.asc() if sort == SORT_MAKE_ASC else make_sort_value.desc(),
                model_sort_value.asc(),
                Vehicle.year.is_(None),
                Vehicle.year.desc(),
                Listing.id.desc(),
            )
        else:
            query = query.order_by(
                availability_rank.asc(),
                model_sort_value.asc() if sort == SORT_MODEL_ASC else model_sort_value.desc(),
                make_sort_value.asc(),
                Vehicle.year.is_(None),
                Vehicle.year.desc(),
                Listing.id.desc(),
            )
    elif sort == SORT_YEAR_ASC:
        query = query.order_by(availability_rank.asc(), Vehicle.year.is_(None), Vehicle.year.asc(), Listing.id.desc())
    elif sort == SORT_YEAR_DESC:
        query = query.order_by(availability_rank.asc(), Vehicle.year.is_(None), Vehicle.year.desc(), Listing.id.desc())
    elif sort == SORT_PRICE_ASC:
        query = query.order_by(availability_rank.asc(), Listing.price.is_(None), Listing.price.asc(), Listing.id.desc())
    elif sort == SORT_PRICE_DESC:
        query = query.order_by(availability_rank.asc(), Listing.price.is_(None), Listing.price.desc(), Listing.id.desc())
    else:
        query = query.order_by(
            availability_rank.asc(),
            Listing.published_at.desc(),
            Listing.created_at.desc(),
            Listing.id.desc(),
        )

    return InventorySearchQuery(
        query=query,
        criteria=criteria,
        distance_by_id=distance_by_id,
        location_error=location_error,
        selected_distance_unit=selected_distance_unit,
        effective_distance_unit=effective_distance_unit,
    )


def _decimal_token(value: Decimal) -> str:
    token = format(value, "f")
    return token.rstrip("0").rstrip(".") if "." in token else token


def _path_text_token(value: str) -> str:
    """Return one path-safe reversible token while keeping ordinary text readable."""

    text = str(value or "").strip()
    if text and "/" not in text and not text.startswith("~"):
        return text
    encoded = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")
    return f"~{encoded}"


def _decode_path_text_token(token: str) -> str | None:
    """Decode a path token produced by :func:`_path_text_token`."""

    if not token.startswith("~"):
        return token
    encoded = token[1:]
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None


def _range_token(minimum, maximum) -> str:
    if minimum is not None and maximum is not None:
        return f"{minimum}-{maximum}"
    if maximum is not None:
        return f"under-{maximum}"
    return f"over-{minimum}"


def _parse_number_range(token: str, *, decimal: bool = False):
    def convert(raw):
        if decimal:
            try:
                value = Decimal(raw)
            except InvalidOperation:
                return None
            return value if value.is_finite() and value >= 0 else None
        try:
            value = int(raw)
        except ValueError:
            return None
        return value

    if token.startswith("under-"):
        return None, convert(token.removeprefix("under-"))
    if token.startswith("over-"):
        return convert(token.removeprefix("over-")), None
    left, separator, right = token.partition("-")
    if not separator:
        value = convert(token)
        return value, value
    return convert(left), convert(right)


def _zone_from_slug(token: str) -> Zone | None:
    normalized = token.strip().lower()
    if not normalized:
        return None
    by_code = Zone.query.filter(func.lower(Zone.code) == normalized).one_or_none()
    if by_code is not None:
        return by_code
    expected_name = normalized.replace("-", " ")
    direct = Zone.query.filter(func.lower(Zone.name) == expected_name).all()
    if len(direct) == 1:
        return direct[0]
    if direct:
        return None
    matches = [
        zone
        for zone in Zone.query.all()
        if slugify(zone.name, fallback=zone.code.lower()) == normalized
    ]
    return matches[0] if len(matches) == 1 else None


def _country_from_slug(token: str) -> Country | None:
    normalized = token.strip().lower()
    if not normalized:
        return None
    by_code = Country.query.filter(func.lower(Country.iso_code_2) == normalized).one_or_none()
    if by_code is not None:
        return by_code
    expected_name = normalized.replace("-", " ")
    direct = Country.query.filter(func.lower(Country.name) == expected_name).all()
    if len(direct) == 1:
        return direct[0]
    if direct:
        return None
    matches = [
        country
        for country in Country.query.all()
        if slugify(country.name, fallback=country.iso_code_2.lower()) == normalized
    ]
    return matches[0] if len(matches) == 1 else None


def _zone_slug(zone_code: str) -> str:
    zone = Zone.query.filter(func.lower(Zone.code) == zone_code.lower()).one_or_none()
    if zone is None:
        return zone_code.lower()
    candidate = slugify(zone.name, fallback=zone.code.lower())
    resolved = _zone_from_slug(candidate)
    return candidate if resolved is not None and resolved.code == zone.code else zone.code.lower()


def _country_slug(country_code: str) -> str:
    country = Country.query.filter(
        func.lower(Country.iso_code_2) == country_code.lower()
    ).one_or_none()
    if country is None:
        return country_code.lower()
    candidate = slugify(country.name, fallback=country.iso_code_2.lower())
    resolved = _country_from_slug(candidate)
    return (
        candidate
        if resolved is not None and resolved.iso_code_2 == country.iso_code_2
        else country.iso_code_2.lower()
    )


def fancy_inventory_path(criteria: InventorySearchCriteria, *, page: int = 1) -> str | None:
    """Build the deterministic SEF path for one semantic inventory search."""

    # Manually crafted invalid query criteria must remain representable as plain
    # query URLs rather than being converted into a misleading or unparseable path.
    if criteria.make and reference_by_key(CATEGORY_MAKE, criteria.make, active_only=False) is None:
        return None
    parsed_model = parse_model_choice(criteria.model) if criteria.model else None
    if parsed_model is not None:
        make = reference_by_key(CATEGORY_MAKE, parsed_model[0], active_only=False)
        if make is None or vehicle_model_by_key(make, parsed_model[1], active_only=False) is None:
            return None
    if criteria.zone_code and Zone.query.filter(
        func.lower(Zone.code) == criteria.zone_code.lower()
    ).one_or_none() is None:
        return None
    if criteria.country_code and not criteria.zone_code and Country.query.filter(
        func.lower(Country.iso_code_2) == criteria.country_code.lower()
    ).one_or_none() is None:
        return None
    if criteria.drivetrain and reference_by_key(
        CATEGORY_DRIVETRAIN, criteria.drivetrain, active_only=False
    ) is None:
        return None
    if criteria.vehicle_type and reference_by_key(
        CATEGORY_VEHICLE_TYPE, criteria.vehicle_type, active_only=False
    ) is None:
        return None
    for feature_key in criteria.feature:
        if reference_by_key(CATEGORY_FEATURE, feature_key, active_only=False) is None:
            return None
    if criteria.postal_code and not criteria.postal_country:
        return None

    segments: list[str] = []
    exact_year = criteria.exact_year
    if exact_year is not None:
        segments.append(str(exact_year))
    elif criteria.min_year is not None or criteria.max_year is not None:
        low = criteria.min_year if criteria.min_year is not None else None
        high = criteria.max_year if criteria.max_year is not None else None
        segments.extend(["years", _range_token(low, high)])

    if criteria.make:
        segments.append(criteria.make)

    if parsed_model is not None and parsed_model[0] == criteria.make:
        segments.append(parsed_model[1])
    elif criteria.model:
        segments.extend(["model", _path_text_token(criteria.model)])

    if criteria.zone_code:
        segments.append(_zone_slug(criteria.zone_code))
    elif criteria.country_code:
        segments.extend(["country", _country_slug(criteria.country_code)])

    if criteria.drivetrain:
        segments.append(criteria.drivetrain)

    if criteria.vehicle_type:
        segments.extend(["type", criteria.vehicle_type])
    if criteria.condition:
        segments.extend(["condition", _path_text_token(criteria.condition)])
    if criteria.transmission:
        segments.extend(["transmission", _path_text_token(criteria.transmission)])
    if criteria.seller:
        segments.extend(["seller", _path_text_token(criteria.seller)])
    if criteria.min_price is not None or criteria.max_price is not None:
        low = _decimal_token(criteria.min_price) if criteria.min_price is not None else None
        high = _decimal_token(criteria.max_price) if criteria.max_price is not None else None
        segments.extend(["price", _range_token(low, high)])
    if criteria.postal_code:
        radius = criteria.radius or 50
        segments.extend(
            [
                "near",
                criteria.postal_country.lower(),
                _path_text_token(criteria.postal_code),
                str(radius),
            ]
        )
        if criteria.distance_unit:
            segments.extend(["unit", criteria.distance_unit])
    for feature in criteria.feature:
        segments.extend(["feature", feature])
    if page > 1:
        segments.extend(["page", str(page)])

    return "/".join(str(segment).strip("/") for segment in segments if str(segment).strip("/"))


def parse_fancy_inventory_path(path: str) -> tuple[InventorySearchCriteria, int] | None:
    """Parse one generated SEF inventory path without guessing arbitrary slugs."""

    segments = [segment for segment in path.split("/") if segment]
    criteria = InventorySearchCriteria()
    page = 1
    index = 0

    while index < len(segments):
        key = segments[index]

        # Explicit labels are reserved and therefore always win over compact facets.
        if key == "page":
            if index + 1 >= len(segments) or not segments[index + 1].isdigit():
                return None
            page = max(1, int(segments[index + 1]))
            index += 2
            if index != len(segments):
                return None
            break
        if key == "years":
            if index + 1 >= len(segments):
                return None
            minimum, maximum = _parse_number_range(segments[index + 1])
            if minimum is None and maximum is None:
                return None
            if minimum is not None and not 1886 <= minimum <= 2100:
                return None
            if maximum is not None and not 1886 <= maximum <= 2100:
                return None
            criteria.min_year = minimum
            criteria.max_year = maximum
            index += 2
            continue
        if key == "model":
            if index + 1 >= len(segments):
                return None
            decoded = _decode_path_text_token(segments[index + 1])
            if decoded is None:
                return None
            criteria.model = decoded
            index += 2
            continue
        if key == "country":
            if index + 1 >= len(segments):
                return None
            country = _country_from_slug(segments[index + 1])
            if country is None:
                return None
            criteria.country_code = country.iso_code_2
            index += 2
            continue
        if key == "type":
            if index + 1 >= len(segments):
                return None
            value = reference_by_key(
                CATEGORY_VEHICLE_TYPE,
                segments[index + 1],
                active_only=False,
            )
            if value is None:
                return None
            criteria.vehicle_type = value.key
            index += 2
            continue
        if key == "condition":
            if index + 1 >= len(segments):
                return None
            decoded = _decode_path_text_token(segments[index + 1])
            if decoded is None:
                return None
            criteria.condition = decoded
            index += 2
            continue
        if key == "transmission":
            if index + 1 >= len(segments):
                return None
            decoded = _decode_path_text_token(segments[index + 1])
            if decoded is None:
                return None
            criteria.transmission = decoded
            index += 2
            continue
        if key == "seller":
            if index + 1 >= len(segments):
                return None
            decoded = _decode_path_text_token(segments[index + 1])
            if decoded is None:
                return None
            criteria.seller = decoded
            index += 2
            continue
        if key == "price":
            if index + 1 >= len(segments):
                return None
            minimum, maximum = _parse_number_range(segments[index + 1], decimal=True)
            if minimum is None and maximum is None:
                return None
            criteria.min_price = minimum
            criteria.max_price = maximum
            index += 2
            continue
        if key == "near":
            if index + 3 >= len(segments):
                return None
            criteria.postal_country = normalize_country_code(segments[index + 1]) or ""
            decoded_postal = _decode_path_text_token(segments[index + 2])
            if decoded_postal is None:
                return None
            criteria.postal_code = decoded_postal
            try:
                radius = int(segments[index + 3])
            except ValueError:
                return None
            criteria.radius = radius if radius in RADIUS_OPTIONS else None
            if not criteria.postal_country or criteria.radius is None:
                return None
            index += 4
            continue
        if key == "unit":
            if index + 1 >= len(segments):
                return None
            unit = normalize_distance_unit(segments[index + 1])
            if not unit:
                return None
            criteria.distance_unit = unit
            index += 2
            continue
        if key == "feature":
            if index + 1 >= len(segments):
                return None
            feature = reference_by_key(
                CATEGORY_FEATURE,
                segments[index + 1],
                active_only=False,
            )
            if feature is None:
                return None
            if feature.key not in criteria.feature:
                criteria.feature.append(feature.key)
            index += 2
            continue

        # Compact common-case facets follow a deterministic semantic order.
        if criteria.min_year is None and criteria.max_year is None and key.isdigit():
            year = int(key)
            if 1886 <= year <= 2100:
                criteria.min_year = year
                criteria.max_year = year
                index += 1
                continue

        if not criteria.make:
            make = reference_by_key(CATEGORY_MAKE, key, active_only=False)
            if make is not None:
                criteria.make = make.key
                index += 1
                continue

        if criteria.make and not criteria.model:
            make = reference_by_key(CATEGORY_MAKE, criteria.make, active_only=False)
            model = vehicle_model_by_key(make, key, active_only=False) if make else None
            if model is not None:
                criteria.model = f"{criteria.make}:{model.key}"
                index += 1
                continue

        if not criteria.zone_code:
            zone = _zone_from_slug(key)
            if zone is not None:
                criteria.zone_code = zone.code
                criteria.country_code = zone.code.partition("-")[0].upper()
                index += 1
                continue

        if not criteria.drivetrain:
            drivetrain = reference_by_key(
                CATEGORY_DRIVETRAIN,
                key,
                active_only=False,
            )
            if drivetrain is not None:
                criteria.drivetrain = drivetrain.key
                index += 1
                continue

        return None

    return criteria, page


def inventory_url(
    criteria: InventorySearchCriteria,
    *,
    fancy: bool,
    sort: str = SORT_NEWEST,
    per_page: int | None = None,
    page: int = 1,
    external: bool = False,
) -> str:
    """Build the selected canonical inventory URL for one search state."""

    state_args = {}
    if sort != SORT_NEWEST:
        state_args["sort"] = sort
    if per_page is not None:
        state_args["per_page"] = per_page

    if fancy:
        path = fancy_inventory_path(criteria, page=page)
        if path:
            return url_for(
                "autogrid360.inventory_fancy",
                inventory_path=path,
                _external=external,
                **state_args,
            )
        if path == "":
            return url_for("autogrid360.index", _external=external, **state_args)
        # Criteria that cannot be represented safely in the SEF grammar retain
        # the ordinary query-string form even while Fancy URLs are enabled.
        args = criteria.query_args()
        args.update(state_args)
        if page > 1:
            args["page"] = page
        return url_for("autogrid360.index", _external=external, **args)

    args = criteria.query_args()
    args.update(state_args)
    if page > 1:
        args["page"] = page
    return url_for("autogrid360.index", _external=external, **args)


def search_form_url(criteria: InventorySearchCriteria) -> str:
    """Build a pre-filled Advanced Search URL for refining current criteria."""

    return url_for("autogrid360.search", **criteria.query_args())


def search_heading(criteria: InventorySearchCriteria) -> str:
    """Build a concise human/SEO heading for one inventory result set."""

    parts: list[str] = []
    if criteria.exact_year is not None:
        parts.append(str(criteria.exact_year))
    if criteria.make:
        make = reference_by_key(CATEGORY_MAKE, criteria.make, active_only=False)
        parts.append(make.label if make is not None else criteria.make.replace("-", " ").title())
    parsed_model = parse_model_choice(criteria.model) if criteria.model else None
    if parsed_model is not None:
        make = reference_by_key(CATEGORY_MAKE, parsed_model[0], active_only=False)
        model = vehicle_model_by_key(make, parsed_model[1], active_only=False) if make else None
        if model is not None:
            parts.append(model.label)
    elif criteria.model:
        parts.append(criteria.model)
    if not parts:
        parts.append("Vehicle")
    heading = " ".join(parts) + " Inventory"
    if criteria.zone_code:
        zone = Zone.query.filter(func.lower(Zone.code) == criteria.zone_code.lower()).one_or_none()
        if zone is not None:
            heading += f" in {zone.name}"
    elif criteria.country_code:
        country = Country.query.filter(
            func.lower(Country.iso_code_2) == criteria.country_code.lower()
        ).one_or_none()
        if country is not None:
            heading += f" in {country.name}"
    return heading
