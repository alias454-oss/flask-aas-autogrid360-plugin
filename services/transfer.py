# app/plugins/autogrid360/services/transfer.py
"""Portable AutoGrid360 inventory bundle import/export."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4
import zipfile

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.core.extensions import db
from app.plugins.autogrid360.services.location import (
    LocationReferenceError,
    validate_location_codes,
)
from app.plugins.autogrid360.services.media import (
    ImageUploadError,
    delete_image_files,
    image_path,
    max_image_bytes,
    max_listing_images,
    store_listing_image,
)
from app.plugins.autogrid360.models import (
    CATEGORY_DRIVETRAIN,
    CATEGORY_FEATURE,
    CATEGORY_MAKE,
    CATEGORY_VEHICLE_TYPE,
    LISTING_STATUSES,
    STATUS_DRAFT,
    Listing,
    ListingImage,
    ReferenceValue,
    SellerProfile,
    Vehicle,
)
from app.plugins.autogrid360.services.geo import synchronize_listing_postal_location
from app.plugins.autogrid360.services.reference import reference_by_key, vehicle_model_by_key


BUNDLE_FORMAT = "autogrid360-inventory"
BUNDLE_VERSION = 1
BUNDLE_SCOPE_SELLER = "seller"
BUNDLE_SCOPE_SITE = "site"
BUNDLE_SCOPES = frozenset({BUNDLE_SCOPE_SELLER, BUNDLE_SCOPE_SITE})
MANIFEST_NAME = "manifest.json"
DEFAULT_MAX_IMPORT_BUNDLE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_IMPORT_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_IMPORT_LISTINGS = 5000
DEFAULT_MAX_IMPORT_SELLERS = 5000
DEFAULT_MAX_IMPORT_ENTRIES = 65000
DEFAULT_MAX_MANIFEST_BYTES = 32 * 1024 * 1024

_PROFILE_FIELDS = (
    "display_name",
    "company_name",
)


class InventoryBundleError(ValueError):
    """Raised when an AutoGrid360 portable inventory bundle is invalid or unsafe."""


@dataclass(frozen=True)
class ExportResult:
    """Summary of one completed inventory export."""

    listings_exported: int
    images_exported: int
    seller_username: str | None = None
    sellers_exported: int = 1
    scope: str = BUNDLE_SCOPE_SELLER


@dataclass(frozen=True)
class RestoreResult:
    """Summary of one completed inventory restore."""

    listings_imported: int
    images_imported: int
    seller_profiles_created: int
    sellers_restored: int
    seller_mappings: tuple[tuple[str, str], ...]
    _stored_keys: tuple[tuple[str, str], ...] = field(default=(), repr=False)


def max_import_bundle_bytes() -> int:
    """Return the compressed upload limit for one inventory bundle."""

    return max(
        int(
            current_app.config.get(
                "AUTOGRID360_MAX_IMPORT_BUNDLE_BYTES",
                DEFAULT_MAX_IMPORT_BUNDLE_BYTES,
            )
        ),
        1,
    )


def max_import_uncompressed_bytes() -> int:
    """Return the maximum aggregate uncompressed size for one bundle."""

    return max(
        int(
            current_app.config.get(
                "AUTOGRID360_MAX_IMPORT_UNCOMPRESSED_BYTES",
                DEFAULT_MAX_IMPORT_UNCOMPRESSED_BYTES,
            )
        ),
        1,
    )


def max_import_listings() -> int:
    """Return the maximum listing count accepted from one bundle."""

    return max(
        int(
            current_app.config.get(
                "AUTOGRID360_MAX_IMPORT_LISTINGS",
                DEFAULT_MAX_IMPORT_LISTINGS,
            )
        ),
        1,
    )


def save_bundle_upload(upload, destination: Path | str) -> int:
    """Save one uploaded bundle without allowing the compressed file to exceed policy."""

    destination = Path(destination)
    limit = max_import_bundle_bytes()
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("wb") as output:
            while True:
                chunk = upload.stream.read(min(1024 * 1024, limit - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise InventoryBundleError("The uploaded inventory bundle is too large.")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if total == 0:
        destination.unlink(missing_ok=True)
        raise InventoryBundleError("The uploaded inventory bundle is empty.")
    return total


def cleanup_restore_files(result: RestoreResult) -> None:
    """Best-effort cleanup when the caller-owned import transaction cannot commit."""

    for storage_key, thumbnail_key in result._stored_keys:
        for key in (storage_key, thumbnail_key):
            try:
                image_path(key).unlink(missing_ok=True)
            except OSError:
                pass


def _iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _seller_payload(seller) -> dict:
    profile = SellerProfile.query.filter_by(user_id=seller.id).one_or_none()
    return {
        "username": seller.username,
        "profile": (
            {field: getattr(profile, field) for field in _PROFILE_FIELDS}
            if profile is not None
            else None
        ),
    }


def _vehicle_payload(vehicle: Vehicle) -> dict:
    model_payload = (
        {"key": vehicle.model_ref.key, "text": None}
        if vehicle.model_ref is not None
        else {"key": None, "text": vehicle.model_text}
    )
    return {
        "year": vehicle.year,
        "make_key": vehicle.make_ref.key,
        "model": model_payload,
        "trim": vehicle.trim,
        "vehicle_type_key": (
            vehicle.vehicle_type_ref.key if vehicle.vehicle_type_ref is not None else None
        ),
        "doors": vehicle.doors,
        "exterior_color": vehicle.exterior_color,
        "mileage": vehicle.mileage,
        "condition": vehicle.condition,
        "engine": vehicle.engine,
        "transmission": vehicle.transmission,
        "drivetrain_key": (
            vehicle.drivetrain_ref.key if vehicle.drivetrain_ref is not None else None
        ),
        "feature_keys": [feature.key for feature in vehicle.features],
        "mpg": vehicle.mpg,
        "fuel_type": vehicle.fuel_type,
        "vin": vehicle.vin,
        "stock_number": vehicle.stock_number,
    }


def _listing_payload(listing: Listing) -> dict:
    return {
        "portable_id": listing.portable_id,
        "source": {
            "status": listing.status,
            "featured": bool(listing.featured),
            "view_count": int(listing.view_count or 0),
            "created_at": _iso_datetime(listing.created_at),
            "updated_at": _iso_datetime(listing.updated_at),
            "first_published_at": _iso_datetime(listing.first_published_at),
            "published_at": _iso_datetime(listing.published_at),
            "expires_at": _iso_datetime(listing.expires_at),
            "expiration_warning_sent_at": _iso_datetime(
                listing.expiration_warning_sent_at
            ),
            "expired_at": _iso_datetime(listing.expired_at),
            "sold_at": _iso_datetime(listing.sold_at),
            "expired_edited_at": _iso_datetime(listing.expired_edited_at),
            "expired_removal_warning_sent_at": _iso_datetime(
                listing.expired_removal_warning_sent_at
            ),
            "aged_out_at": _iso_datetime(listing.aged_out_at),
            "aged_out_notice_sent_at": _iso_datetime(
                listing.aged_out_notice_sent_at
            ),
        },
        "listing": {
            "title": listing.title,
            "price": _decimal_text(listing.price),
            "description": listing.description,
            "country_code": listing.country_code,
            "city": listing.city,
            "zone_code": listing.zone_code,
            "postal_code": listing.postal_code,
        },
        "vehicle": _vehicle_payload(listing.vehicle),
        "images": [],
    }


def _write_inventory_bundle(
    *,
    destination: Path | str,
    manifest: dict,
    listings: list[Listing],
) -> tuple[int, int]:
    """Write one validated export manifest plus listing images atomically."""

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    images_exported = 0

    rows_by_portable_id = {row["portable_id"]: row for row in manifest["listings"]}
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for listing in listings:
                row = rows_by_portable_id[listing.portable_id]
                ordered_images = sorted(
                    listing.images,
                    key=lambda item: (item.position, item.id or 0),
                )
                for index, image in enumerate(ordered_images):
                    source = image_path(image.storage_key)
                    if not source.is_file():
                        raise InventoryBundleError(
                            f"Listing {listing.id} image file is missing: {image.storage_key}"
                        )
                    archive_name = f"images/{listing.portable_id}/{index:03d}.jpg"
                    archive.write(source, archive_name)
                    row["images"].append(
                        {
                            "path": archive_name,
                            "original_filename": image.original_filename,
                            "position": image.position,
                            "is_primary": bool(image.is_primary),
                        }
                    )
                    images_exported += 1

            manifest_bytes = json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            archive.writestr(MANIFEST_NAME, manifest_bytes)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return len(listings), images_exported


def export_inventory_bundle(seller, destination: Path | str) -> ExportResult:
    """Export one seller's complete AutoGrid360 inventory to a canonical ZIP bundle."""

    listings = (
        Listing.query.filter_by(seller_id=seller.id)
        .order_by(Listing.id.asc())
        .all()
    )
    manifest = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "scope": BUNDLE_SCOPE_SELLER,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "seller": _seller_payload(seller),
        "listings": [_listing_payload(listing) for listing in listings],
    }
    listings_exported, images_exported = _write_inventory_bundle(
        destination=destination,
        manifest=manifest,
        listings=listings,
    )
    return ExportResult(
        listings_exported=listings_exported,
        images_exported=images_exported,
        seller_username=seller.username,
        sellers_exported=1,
        scope=BUNDLE_SCOPE_SELLER,
    )


def export_site_inventory_bundle(destination: Path | str) -> ExportResult:
    """Export all AutoGrid360 seller profiles and inventory as one site backup."""

    from app.models import User

    seller_ids = {
        seller_id
        for (seller_id,) in db.session.query(Listing.seller_id).distinct().all()
    }
    seller_ids.update(
        user_id
        for (user_id,) in db.session.query(SellerProfile.user_id).distinct().all()
    )
    sellers = (
        User.query.filter(User.id.in_(seller_ids)).order_by(User.username.asc()).all()
        if seller_ids
        else []
    )
    seller_by_id = {seller.id: seller for seller in sellers}
    listings = (
        Listing.query.filter(Listing.seller_id.in_(seller_ids))
        .order_by(Listing.id.asc())
        .all()
        if seller_ids
        else []
    )
    listing_rows = []
    for listing in listings:
        seller = seller_by_id.get(listing.seller_id)
        if seller is None:
            raise InventoryBundleError(
                f"Listing {listing.id} references a missing Flask-AAS seller."
            )
        row = _listing_payload(listing)
        row["seller_username"] = seller.username
        listing_rows.append(row)

    manifest = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "scope": BUNDLE_SCOPE_SITE,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "sellers": [_seller_payload(seller) for seller in sellers],
        "listings": listing_rows,
    }
    listings_exported, images_exported = _write_inventory_bundle(
        destination=destination,
        manifest=manifest,
        listings=listings,
    )
    return ExportResult(
        listings_exported=listings_exported,
        images_exported=images_exported,
        seller_username=None,
        sellers_exported=len(sellers),
        scope=BUNDLE_SCOPE_SITE,
    )


def _safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def _bounded_zip_read(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    with archive.open(name, "r") as source:
        payload = source.read(limit + 1)
    if len(payload) > limit:
        raise InventoryBundleError(f"Bundle entry is too large: {name}")
    return payload


def _validate_archive(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > DEFAULT_MAX_IMPORT_ENTRIES:
        raise InventoryBundleError("The bundle contains too many files.")

    by_name: dict[str, zipfile.ZipInfo] = {}
    total_uncompressed = 0
    for info in infos:
        if info.is_dir():
            continue
        if not _safe_archive_name(info.filename):
            raise InventoryBundleError("The bundle contains an unsafe file path.")
        if info.filename in by_name:
            raise InventoryBundleError("The bundle contains duplicate file paths.")
        if info.flag_bits & 0x1:
            raise InventoryBundleError("Encrypted inventory bundles are not supported.")
        total_uncompressed += int(info.file_size)
        if total_uncompressed > max_import_uncompressed_bytes():
            raise InventoryBundleError("The uncompressed bundle is too large.")
        by_name[info.filename] = info

    if MANIFEST_NAME not in by_name:
        raise InventoryBundleError("The inventory bundle is missing manifest.json.")
    if by_name[MANIFEST_NAME].file_size > DEFAULT_MAX_MANIFEST_BYTES:
        raise InventoryBundleError("The inventory manifest is too large.")
    return by_name


def _required_dict(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise InventoryBundleError(f"{label} must be an object.")
    return value


def _optional_text(value, label: str, maximum: int | None = None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InventoryBundleError(f"{label} must be text or null.")
    cleaned = value.replace("\x00", "").strip()
    if maximum is not None and len(cleaned) > maximum:
        raise InventoryBundleError(f"{label} is too long.")
    return cleaned or None


def _required_text(value, label: str, maximum: int) -> str:
    cleaned = _optional_text(value, label, maximum)
    if not cleaned:
        raise InventoryBundleError(f"{label} is required.")
    return cleaned


def _optional_int(
    value,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise InventoryBundleError(f"{label} must be an integer or null.")
    if isinstance(value, float) and not value.is_integer():
        raise InventoryBundleError(f"{label} must be an integer or null.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise InventoryBundleError(f"{label} must be an integer or null.") from exc
    if minimum is not None and parsed < minimum:
        raise InventoryBundleError(f"{label} is below the allowed minimum.")
    if maximum is not None and parsed > maximum:
        raise InventoryBundleError(f"{label} is above the allowed maximum.")
    return parsed


def _optional_decimal(value, label: str) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InventoryBundleError(f"{label} must be a decimal value or null.") from exc
    if not parsed.is_finite() or parsed < 0:
        raise InventoryBundleError(f"{label} must be a non-negative finite decimal.")
    if parsed > Decimal("9999999999.99"):
        raise InventoryBundleError(f"{label} exceeds the supported listing price.")
    return parsed.quantize(Decimal("0.01"))


def _optional_datetime(value, label: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InventoryBundleError(f"{label} must be an ISO-8601 timestamp or null.")
    raw = value.strip()
    if not raw or len(raw) > 64:
        raise InventoryBundleError(f"{label} must be an ISO-8601 timestamp or null.")
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise InventoryBundleError(f"{label} is not a valid ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise InventoryBundleError(f"{label} must include a timezone offset.")
    return parsed.astimezone(timezone.utc)


def _validate_source(value, *, listing_index: int) -> dict:
    source = _required_dict(value, f"listings[{listing_index}].source")
    status = _required_text(
        source.get("status"),
        f"listings[{listing_index}].source.status",
        20,
    )
    if status not in LISTING_STATUSES:
        raise InventoryBundleError(
            f"listings[{listing_index}].source.status is not supported."
        )

    featured = source.get("featured", False)
    if not isinstance(featured, bool):
        raise InventoryBundleError(
            f"listings[{listing_index}].source.featured must be true or false."
        )
    view_count = _optional_int(
        source.get("view_count", 0),
        f"listings[{listing_index}].source.view_count",
        minimum=0,
        maximum=9_223_372_036_854_775_807,
    )
    fields = {
        "created_at": _optional_datetime(
            source.get("created_at"), f"listings[{listing_index}].source.created_at"
        ),
        "updated_at": _optional_datetime(
            source.get("updated_at"), f"listings[{listing_index}].source.updated_at"
        ),
        "first_published_at": _optional_datetime(
            source.get("first_published_at"),
            f"listings[{listing_index}].source.first_published_at",
        ),
        "published_at": _optional_datetime(
            source.get("published_at"),
            f"listings[{listing_index}].source.published_at",
        ),
        "expires_at": _optional_datetime(
            source.get("expires_at"), f"listings[{listing_index}].source.expires_at"
        ),
        "expiration_warning_sent_at": _optional_datetime(
            source.get("expiration_warning_sent_at"),
            f"listings[{listing_index}].source.expiration_warning_sent_at",
        ),
        "expired_at": _optional_datetime(
            source.get("expired_at"), f"listings[{listing_index}].source.expired_at"
        ),
        "sold_at": _optional_datetime(
            source.get("sold_at"), f"listings[{listing_index}].source.sold_at"
        ),
        "expired_edited_at": _optional_datetime(
            source.get("expired_edited_at"),
            f"listings[{listing_index}].source.expired_edited_at",
        ),
        "expired_removal_warning_sent_at": _optional_datetime(
            source.get("expired_removal_warning_sent_at"),
            f"listings[{listing_index}].source.expired_removal_warning_sent_at",
        ),
        "aged_out_at": _optional_datetime(
            source.get("aged_out_at"), f"listings[{listing_index}].source.aged_out_at"
        ),
        "aged_out_notice_sent_at": _optional_datetime(
            source.get("aged_out_notice_sent_at"),
            f"listings[{listing_index}].source.aged_out_notice_sent_at",
        ),
    }

    created = fields["created_at"]
    updated = fields["updated_at"]
    first_published = fields["first_published_at"]
    published = fields["published_at"]
    expires = fields["expires_at"]
    expiration_warning = fields["expiration_warning_sent_at"]
    expired = fields["expired_at"]
    sold = fields["sold_at"]
    expired_edited = fields["expired_edited_at"]
    removal_warning = fields["expired_removal_warning_sent_at"]
    aged_out = fields["aged_out_at"]
    aged_out_notice = fields["aged_out_notice_sent_at"]

    if expires is not None and published is None:
        raise InventoryBundleError("A restored expires_at requires published_at.")
    if expiration_warning is not None and expires is None:
        raise InventoryBundleError(
            "A restored expiration warning requires an expiration deadline."
        )
    if sold is not None and status not in {"sold", "removed"}:
        raise InventoryBundleError(
            "A restored sold_at timestamp is valid only for Sold or Removed inventory."
        )
    if expired is not None and status not in {"expired", "removed"}:
        raise InventoryBundleError(
            "A restored expired_at timestamp is valid only for Expired or Removed inventory."
        )
    if aged_out is not None and status != "removed":
        raise InventoryBundleError(
            "A restored aged_out_at timestamp is valid only for Removed inventory."
        )
    if status in {"active", "sale_pending", "sold"} and published is None:
        raise InventoryBundleError(
            f"Restored {status!r} inventory requires published_at."
        )
    if status == "sold" and sold is None:
        raise InventoryBundleError("Restored Sold inventory requires sold_at.")
    if status == "expired" and expired is None:
        raise InventoryBundleError("Restored Expired inventory requires expired_at.")
    if sold is not None and expired is not None:
        raise InventoryBundleError(
            "A restored listing cannot contain both sold_at and expired_at history."
        )
    if expired_edited is not None and expired is None:
        raise InventoryBundleError(
            "A restored expired edit marker requires expired_at."
        )
    if removal_warning is not None and expired is None:
        raise InventoryBundleError(
            "A restored expired-removal warning requires expired_at."
        )
    if aged_out is not None and expired is None:
        raise InventoryBundleError(
            "A restored aged-out timestamp requires expired_at."
        )
    if aged_out_notice is not None and aged_out is None:
        raise InventoryBundleError(
            "A restored aged-out notice requires aged_out_at."
        )

    chronological_pairs = (
        (created, updated, "updated_at cannot be earlier than created_at."),
        (created, first_published, "first_published_at cannot be earlier than created_at."),
        (created, published, "published_at cannot be earlier than created_at."),
        (first_published, published, "first_published_at cannot be later than published_at."),
        (published, expires, "expires_at cannot be earlier than published_at."),
        (published, expiration_warning, "expiration_warning_sent_at cannot be earlier than published_at."),
        (expiration_warning, expires, "expiration_warning_sent_at cannot be later than expires_at."),
        (published, sold, "sold_at cannot be earlier than published_at."),
        (published, expired, "expired_at cannot be earlier than published_at."),
        (expired, expired_edited, "expired_edited_at cannot be earlier than expired_at."),
        (expired, removal_warning, "expired_removal_warning_sent_at cannot be earlier than expired_at."),
        (expired, aged_out, "aged_out_at cannot be earlier than expired_at."),
        (aged_out, aged_out_notice, "aged_out_notice_sent_at cannot be earlier than aged_out_at."),
    )
    for earlier, later, message in chronological_pairs:
        if earlier is not None and later is not None and later < earlier:
            raise InventoryBundleError(message)

    return {
        "status": status,
        "featured": featured,
        "view_count": view_count or 0,
        **fields,
    }


def _validate_profile(value) -> dict | None:
    if value is None:
        return None
    profile = _required_dict(value, "seller.profile")
    limits = {
        "display_name": 120,
        "company_name": 120,
    }
    unknown = set(profile) - set(limits)
    if unknown:
        raise InventoryBundleError("seller.profile contains unsupported fields.")
    validated = {
        field: _optional_text(profile.get(field), f"seller.profile.{field}", limit)
        for field, limit in limits.items()
    }
    return validated


def _reference(category: str, key, label: str) -> ReferenceValue | None:
    if key is None:
        return None
    key = _required_text(key, label, 80)
    value = reference_by_key(category, key, active_only=False)
    if value is None:
        raise InventoryBundleError(f"Unknown destination reference value: {label}={key!r}")
    return value


def _validate_vehicle(value, *, listing_index: int) -> dict:
    vehicle = _required_dict(value, f"listings[{listing_index}].vehicle")
    make = _reference(
        CATEGORY_MAKE,
        vehicle.get("make_key"),
        f"listings[{listing_index}].vehicle.make_key",
    )
    if make is None:
        raise InventoryBundleError("A vehicle make is required.")

    model_payload = _required_dict(
        vehicle.get("model"),
        f"listings[{listing_index}].vehicle.model",
    )
    model_key = model_payload.get("key")
    model_text = model_payload.get("text")
    if model_key is not None and model_text is not None:
        raise InventoryBundleError("A vehicle model cannot contain both key and text.")
    if model_key is not None:
        model_key = _required_text(
            model_key,
            f"listings[{listing_index}].vehicle.model.key",
            80,
        )
        model_ref = vehicle_model_by_key(make, model_key, active_only=False)
        if model_ref is None:
            raise InventoryBundleError(
                f"Unknown destination vehicle model: {make.key}:{model_key}"
            )
        normalized_model_text = None
    else:
        model_ref = None
        normalized_model_text = _required_text(
            model_text,
            f"listings[{listing_index}].vehicle.model.text",
            80,
        )

    feature_keys = vehicle.get("feature_keys", [])
    if not isinstance(feature_keys, list):
        raise InventoryBundleError("vehicle.feature_keys must be a list.")
    features: list[ReferenceValue] = []
    seen_features: set[str] = set()
    for raw_key in feature_keys:
        key = _required_text(raw_key, "vehicle.feature_keys[]", 80)
        if key in seen_features:
            raise InventoryBundleError("vehicle.feature_keys contains duplicates.")
        seen_features.add(key)
        feature = _reference(CATEGORY_FEATURE, key, "vehicle.feature_keys[]")
        if feature is not None:
            features.append(feature)

    return {
        "year": _optional_int(vehicle.get("year"), "vehicle.year", minimum=1886, maximum=2100),
        "make_ref": make,
        "model_ref": model_ref,
        "model_text": normalized_model_text,
        "trim": _optional_text(vehicle.get("trim"), "vehicle.trim", 80),
        "vehicle_type_ref": _reference(
            CATEGORY_VEHICLE_TYPE,
            vehicle.get("vehicle_type_key"),
            "vehicle.vehicle_type_key",
        ),
        "doors": _optional_int(vehicle.get("doors"), "vehicle.doors", minimum=1, maximum=10),
        "exterior_color": _optional_text(vehicle.get("exterior_color"), "vehicle.exterior_color", 50),
        "mileage": _optional_int(vehicle.get("mileage"), "vehicle.mileage", minimum=0),
        "condition": _optional_text(vehicle.get("condition"), "vehicle.condition", 30),
        "engine": _optional_text(vehicle.get("engine"), "vehicle.engine", 80),
        "transmission": _optional_text(vehicle.get("transmission"), "vehicle.transmission", 50),
        "drivetrain_ref": _reference(
            CATEGORY_DRIVETRAIN,
            vehicle.get("drivetrain_key"),
            "vehicle.drivetrain_key",
        ),
        "features": features,
        "mpg": _optional_int(vehicle.get("mpg"), "vehicle.mpg", minimum=0, maximum=255),
        "fuel_type": _optional_text(vehicle.get("fuel_type"), "vehicle.fuel_type", 30),
        "vin": _optional_text(vehicle.get("vin"), "vehicle.vin", 17),
        "stock_number": _optional_text(vehicle.get("stock_number"), "vehicle.stock_number", 32),
    }


def _validate_images(value, *, listing_index: int, entries: dict[str, zipfile.ZipInfo]) -> list[dict]:
    if not isinstance(value, list):
        raise InventoryBundleError(f"listings[{listing_index}].images must be a list.")
    if len(value) > max_listing_images():
        raise InventoryBundleError(
            f"Listing {listing_index + 1} contains more images than this installation permits."
        )

    rows: list[dict] = []
    seen_paths: set[str] = set()
    primary_count = 0
    for image_index, raw in enumerate(value):
        image = _required_dict(raw, f"listings[{listing_index}].images[{image_index}]")
        path = _required_text(image.get("path"), "image.path", 255)
        if not path.startswith("images/") or not _safe_archive_name(path):
            raise InventoryBundleError("An image path is outside the bundle image directory.")
        if path in seen_paths:
            raise InventoryBundleError("A listing references the same image path more than once.")
        seen_paths.add(path)
        info = entries.get(path)
        if info is None:
            raise InventoryBundleError(f"The bundle is missing image data: {path}")
        if info.file_size <= 0 or info.file_size > max_image_bytes():
            raise InventoryBundleError(f"Bundle image exceeds the allowed image size: {path}")
        raw_primary = image.get("is_primary", False)
        if not isinstance(raw_primary, bool):
            raise InventoryBundleError("image.is_primary must be true or false.")
        is_primary = raw_primary
        primary_count += int(is_primary)
        rows.append(
            {
                "path": path,
                "original_filename": _optional_text(
                    image.get("original_filename"),
                    "image.original_filename",
                    255,
                ),
                "position": _optional_int(
                    image.get("position"),
                    "image.position",
                    minimum=0,
                )
                or 0,
                "is_primary": is_primary,
            }
        )
    if primary_count > 1:
        raise InventoryBundleError("A listing may contain only one primary image.")
    rows.sort(key=lambda item: (item["position"], item["path"]))
    return rows


def _validate_manifest(manifest, entries: dict[str, zipfile.ZipInfo]) -> dict:
    manifest = _required_dict(manifest, "manifest")
    if manifest.get("format") != BUNDLE_FORMAT:
        raise InventoryBundleError("This is not an AutoGrid360 inventory bundle.")
    version = manifest.get("version")
    if type(version) is not int or version != BUNDLE_VERSION:
        raise InventoryBundleError("This AutoGrid360 inventory bundle version is unsupported.")

    scope = manifest.get("scope") or BUNDLE_SCOPE_SELLER
    if scope not in BUNDLE_SCOPES:
        raise InventoryBundleError("The inventory bundle scope is unsupported.")

    sellers: list[dict] = []
    seller_keys: set[str] = set()
    if scope == BUNDLE_SCOPE_SELLER:
        seller = _required_dict(manifest.get("seller"), "seller")
        source_username = _required_text(seller.get("username"), "seller.username", 60)
        sellers.append(
            {
                "username": source_username,
                "profile": _validate_profile(seller.get("profile")),
            }
        )
        seller_keys.add(source_username.casefold())
    else:
        raw_sellers = manifest.get("sellers")
        if not isinstance(raw_sellers, list):
            raise InventoryBundleError("manifest.sellers must be a list for a site backup.")
        if len(raw_sellers) > DEFAULT_MAX_IMPORT_SELLERS:
            raise InventoryBundleError("The bundle contains too many sellers.")
        for seller_index, raw_seller in enumerate(raw_sellers):
            seller = _required_dict(raw_seller, f"sellers[{seller_index}]")
            username = _required_text(
                seller.get("username"), f"sellers[{seller_index}].username", 60
            )
            key = username.casefold()
            if key in seller_keys:
                raise InventoryBundleError(
                    "The bundle contains duplicate seller usernames."
                )
            seller_keys.add(key)
            sellers.append(
                {
                    "username": username,
                    "profile": _validate_profile(seller.get("profile")),
                }
            )

    listings = manifest.get("listings")
    if not isinstance(listings, list):
        raise InventoryBundleError("manifest.listings must be a list.")
    if len(listings) > max_import_listings():
        raise InventoryBundleError("The bundle contains too many listings.")

    validated_listings: list[dict] = []
    portable_ids: set[str] = set()
    referenced_image_paths: set[str] = set()
    single_source_username = sellers[0]["username"] if scope == BUNDLE_SCOPE_SELLER else None
    for index, raw in enumerate(listings):
        row = _required_dict(raw, f"listings[{index}]")
        portable_id = _required_text(row.get("portable_id"), "listing.portable_id", 36)
        try:
            portable_id = str(UUID(portable_id))
        except (ValueError, AttributeError) as exc:
            raise InventoryBundleError("listing.portable_id must be a UUID.") from exc
        if portable_id in portable_ids:
            raise InventoryBundleError("The bundle contains duplicate listing portable IDs.")
        portable_ids.add(portable_id)

        if scope == BUNDLE_SCOPE_SELLER:
            source_username = single_source_username
        else:
            source_username = _required_text(
                row.get("seller_username"),
                f"listings[{index}].seller_username",
                60,
            )
            if source_username.casefold() not in seller_keys:
                raise InventoryBundleError(
                    f"listings[{index}] references a seller not declared by the site backup."
                )

        listing_data = _required_dict(row.get("listing"), f"listings[{index}].listing")
        images = _validate_images(
            row.get("images", []),
            listing_index=index,
            entries=entries,
        )
        for image in images:
            if image["path"] in referenced_image_paths:
                raise InventoryBundleError(
                    "The same bundle image cannot belong to multiple listings."
                )
            referenced_image_paths.add(image["path"])
        validated_listings.append(
            {
                "portable_id": portable_id,
                "source_username": source_username,
                "source": _validate_source(row.get("source"), listing_index=index),
                "title": _required_text(listing_data.get("title"), "listing.title", 120),
                "price": _optional_decimal(listing_data.get("price"), "listing.price"),
                "description": _optional_text(
                    listing_data.get("description"),
                    "listing.description",
                    10000,
                ),
                "country_code": _optional_text(
                    listing_data.get("country_code"),
                    "listing.country_code",
                    2,
                ),
                "city": _optional_text(
                    listing_data.get("city"),
                    "listing.city",
                    100,
                ),
                "zone_code": _optional_text(
                    listing_data.get("zone_code"),
                    "listing.zone_code",
                    16,
                ),
                "postal_code": _optional_text(
                    listing_data.get("postal_code"),
                    "listing.postal_code",
                    20,
                ),
                "vehicle": _validate_vehicle(row.get("vehicle"), listing_index=index),
                "images": images,
            }
        )
        try:
            country_code, zone_code = validate_location_codes(
                validated_listings[-1]["country_code"],
                validated_listings[-1]["zone_code"],
                active_only=False,
            )
        except LocationReferenceError as exc:
            raise InventoryBundleError(
                f"Invalid listings[{index}] location: {exc}"
            ) from exc
        validated_listings[-1]["country_code"] = country_code
        validated_listings[-1]["zone_code"] = zone_code

    existing_ids = (
        {
            value
            for (value,) in db.session.query(Listing.portable_id)
            .filter(Listing.portable_id.in_(portable_ids))
            .all()
        }
        if portable_ids
        else set()
    )
    if existing_ids:
        raise InventoryBundleError(
            "The bundle contains listing(s) that already exist in this installation."
        )

    extra_files = set(entries) - {MANIFEST_NAME} - referenced_image_paths
    if extra_files:
        raise InventoryBundleError("The bundle contains unreferenced files.")

    return {
        "scope": scope,
        "sellers": sellers,
        "listings": validated_listings,
    }


def inspect_inventory_bundle(source: Path | str | BytesIO) -> dict:
    """Validate a bundle without changing the database or filesystem."""

    try:
        with zipfile.ZipFile(source, "r") as archive:
            entries = _validate_archive(archive)
            manifest_bytes = _bounded_zip_read(
                archive,
                MANIFEST_NAME,
                DEFAULT_MAX_MANIFEST_BYTES,
            )
            try:
                manifest = json.loads(manifest_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise InventoryBundleError("manifest.json is not valid UTF-8 JSON.") from exc
            return _validate_manifest(manifest, entries)
    except zipfile.BadZipFile as exc:
        raise InventoryBundleError("The uploaded file is not a valid ZIP bundle.") from exc


def parse_seller_mapping_entries(entries) -> dict[str, str]:
    """Parse ``source=destination`` seller mappings with case-insensitive source keys."""

    mapping: dict[str, str] = {}
    for raw_entry in entries:
        entry = str(raw_entry or "").strip()
        if not entry or entry.startswith("#"):
            continue
        if "=" not in entry:
            raise InventoryBundleError(
                f"Invalid seller mapping {entry!r}; use source=destination."
            )
        source_username, destination_username = (
            part.strip() for part in entry.split("=", 1)
        )
        if not source_username or not destination_username:
            raise InventoryBundleError(
                f"Invalid seller mapping {entry!r}; use source=destination."
            )
        key = source_username.casefold()
        if key in mapping:
            raise InventoryBundleError(
                f"Seller mapping for {source_username!r} was provided more than once."
            )
        mapping[key] = destination_username
    return mapping


def resolve_restore_seller_mapping(
    validated: dict,
    overrides: dict[str, str] | None = None,
) -> dict[str, object]:
    """Resolve source seller usernames to destination Flask-AAS users.

    Source usernames map to the same destination username by default. Explicit
    ``overrides`` are useful when restoring onto a site with renamed accounts.
    """

    from app.models import User

    override_map = {str(key).casefold(): value for key, value in (overrides or {}).items()}
    source_keys = {seller["username"].casefold() for seller in validated["sellers"]}
    unknown = sorted(set(override_map) - source_keys)
    if unknown:
        raise InventoryBundleError(
            "Seller mapping references source username(s) not present in the bundle: "
            + ", ".join(unknown)
        )

    resolved: dict[str, object] = {}
    destination_ids: set[int] = set()
    missing: list[str] = []
    for seller_row in validated["sellers"]:
        source_username = seller_row["username"]
        destination_username = override_map.get(
            source_username.casefold(), source_username
        )
        user = User.query.filter(
            db.func.lower(User.username) == destination_username.strip().lower()
        ).one_or_none()
        if user is None:
            missing.append(f"{source_username}->{destination_username}")
            continue
        if user.id in destination_ids:
            raise InventoryBundleError(
                "Each source seller must map to a different destination Flask-AAS user."
            )
        destination_ids.add(user.id)
        resolved[source_username] = user

    if missing:
        raise InventoryBundleError(
            "No destination Flask-AAS user exists for seller mapping(s): "
            + ", ".join(missing)
        )
    return resolved


def _normalized_seller_mapping(validated: dict, seller_mapping: dict[str, object]) -> dict[str, object]:
    normalized = {str(key).casefold(): value for key, value in seller_mapping.items()}
    resolved: dict[str, object] = {}
    destination_ids: set[int] = set()
    missing: list[str] = []
    for seller_row in validated["sellers"]:
        source_username = seller_row["username"]
        seller = normalized.get(source_username.casefold())
        if seller is None:
            missing.append(source_username)
            continue
        seller_id = getattr(seller, "id", None)
        if seller_id is None:
            raise InventoryBundleError(
                f"Destination mapping for {source_username!r} is not a Flask-AAS user."
            )
        if seller_id in destination_ids:
            raise InventoryBundleError(
                "Each source seller must map to a different destination Flask-AAS user."
            )
        destination_ids.add(seller_id)
        resolved[source_username.casefold()] = seller
    if missing:
        raise InventoryBundleError(
            "No destination Flask-AAS user mapping was provided for: "
            + ", ".join(sorted(missing, key=str.casefold))
        )
    return resolved


def restore_inventory_bundle(
    source: Path | str | BytesIO,
    seller_mapping: dict[str, object],
    *,
    as_draft: bool = False,
) -> RestoreResult:
    """Restore one seller or full-site bundle using explicit destination user mappings.

    Normal restore preserves lifecycle state, publication/history timestamps, featured state,
    view counts, and portable identities. ``as_draft=True`` provides an explicit content-
    ingestion mode that discards source publication/lifecycle state. The caller owns the
    database transaction; this helper cleans up image files it created when it raises.
    """

    stored_images: list[ListingImage] = []
    try:
        with zipfile.ZipFile(source, "r") as archive:
            entries = _validate_archive(archive)
            manifest_bytes = _bounded_zip_read(
                archive,
                MANIFEST_NAME,
                DEFAULT_MAX_MANIFEST_BYTES,
            )
            try:
                manifest = json.loads(manifest_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise InventoryBundleError("manifest.json is not valid UTF-8 JSON.") from exc
            validated = _validate_manifest(manifest, entries)
            resolved_sellers = _normalized_seller_mapping(validated, seller_mapping)

            profiles_created = 0
            for seller_row in validated["sellers"]:
                if seller_row["profile"] is None:
                    continue
                seller = resolved_sellers[seller_row["username"].casefold()]
                existing_profile = SellerProfile.query.filter_by(user_id=seller.id).one_or_none()
                if existing_profile is None:
                    db.session.add(
                        SellerProfile(user_id=seller.id, **seller_row["profile"])
                    )
                    profiles_created += 1

            listings_imported = 0
            images_imported = 0
            for row in validated["listings"]:
                seller = resolved_sellers[row["source_username"].casefold()]
                vehicle_data = dict(row["vehicle"])
                features = vehicle_data.pop("features")
                vehicle = Vehicle(**vehicle_data)
                vehicle.features = features
                listing_kwargs = {
                    "portable_id": row["portable_id"],
                    "seller_id": seller.id,
                    "vehicle": vehicle,
                    "title": row["title"],
                    "price": row["price"],
                    "description": row["description"],
                    "country_code": row["country_code"],
                    "city": row["city"],
                    "zone_code": row["zone_code"],
                    "postal_code": row["postal_code"],
                }
                if as_draft:
                    listing_kwargs.update(status=STATUS_DRAFT, featured=False, view_count=0)
                else:
                    listing_kwargs.update(
                        {
                            key: value
                            for key, value in row["source"].items()
                            if value is not None or key not in {"created_at", "updated_at"}
                        }
                    )
                listing = Listing(**listing_kwargs)
                synchronize_listing_postal_location(listing)
                db.session.add(listing)
                db.session.flush()

                has_primary_image = any(image["is_primary"] for image in row["images"])
                for position, image_row in enumerate(row["images"]):
                    payload = _bounded_zip_read(
                        archive,
                        image_row["path"],
                        max_image_bytes(),
                    )
                    filename = (
                        secure_filename(image_row["original_filename"] or "")[:255]
                        or f"import-{position + 1}.jpg"
                    )
                    upload = FileStorage(
                        stream=BytesIO(payload),
                        filename=filename,
                        content_type="image/jpeg",
                    )
                    stored = store_listing_image(listing.id, upload)
                    image = ListingImage(
                        listing=listing,
                        original_filename=filename,
                        position=position,
                        is_primary=(
                            image_row["is_primary"] or (position == 0 and not has_primary_image)
                        ),
                        **stored,
                    )
                    db.session.add(image)
                    stored_images.append(image)
                    images_imported += 1

                listings_imported += 1

            mappings = tuple(
                (seller_row["username"], resolved_sellers[seller_row["username"].casefold()].username)
                for seller_row in validated["sellers"]
            )
            return RestoreResult(
                listings_imported=listings_imported,
                images_imported=images_imported,
                seller_profiles_created=profiles_created,
                sellers_restored=len(validated["sellers"]),
                seller_mappings=mappings,
                _stored_keys=tuple(
                    (image.storage_key, image.thumbnail_key) for image in stored_images
                ),
            )
    except Exception as exc:
        for image in stored_images:
            delete_image_files(image)
        if isinstance(exc, ImageUploadError):
            raise InventoryBundleError(str(exc)) from exc
        if isinstance(exc, zipfile.BadZipFile):
            raise InventoryBundleError("The uploaded file is not a valid ZIP bundle.") from exc
        raise


def import_inventory_bundle(
    source: Path | str | BytesIO,
    seller,
    *,
    as_draft: bool = False,
) -> RestoreResult:
    """Restore one seller-scoped bundle to ``seller`` with lifecycle fidelity by default."""

    validated = inspect_inventory_bundle(source)
    if validated["scope"] != BUNDLE_SCOPE_SELLER:
        raise InventoryBundleError(
            "A full-site backup must be restored through the administrator restore workflow."
        )
    source_username = validated["sellers"][0]["username"]
    return restore_inventory_bundle(
        source,
        {source_username: seller},
        as_draft=as_draft,
    )
