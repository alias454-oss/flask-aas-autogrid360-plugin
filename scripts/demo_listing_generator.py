# scripts/demo_listing_generator.py
"""Build deterministic AutoGrid360 inventory backup bundles for QA/demo shakedowns.

This script never writes AutoGrid360 database rows. It produces the same portable
ZIP contract consumed by the real inventory restore path so demo loading exercises
normal validation, seller mapping, lifecycle restoration, and image processing.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import random
import sys
from uuid import NAMESPACE_URL, uuid5
import zipfile

from PIL import Image, ImageDraw, ImageFont, ImageOps


BUNDLE_FORMAT = "autogrid360-inventory"
BUNDLE_VERSION = 1
BUNDLE_SCOPE_SELLER = "seller"
BUNDLE_SCOPE_SITE = "site"
MANIFEST_NAME = "manifest.json"

ARCHETYPES = (
    {
        "make_key": "ford",
        "make": "Ford",
        "model_key": "f-150",
        "model": "F-150",
        "vehicle_type_key": "pickup",
        "drivetrain_key": "4wd",
        "engine": "3.5L V6",
        "mpg": 20,
    },
    {
        "make_key": "ford",
        "make": "Ford",
        "model_key": "escape",
        "model": "Escape",
        "vehicle_type_key": "suv",
        "drivetrain_key": "awd",
        "engine": "2.0L I4",
        "mpg": 27,
    },
    {
        "make_key": "ford",
        "make": "Ford",
        "model_key": "explorer",
        "model": "Explorer",
        "vehicle_type_key": "suv",
        "drivetrain_key": "4wd",
        "engine": "3.0L V6",
        "mpg": 23,
    },
    {
        "make_key": "honda",
        "make": "Honda",
        "model_key": "civic",
        "model": "Civic",
        "vehicle_type_key": "sedan",
        "drivetrain_key": "fwd",
        "engine": "2.0L I4",
        "mpg": 35,
    },
    {
        "make_key": "honda",
        "make": "Honda",
        "model_key": "accord",
        "model": "Accord",
        "vehicle_type_key": "sedan",
        "drivetrain_key": "fwd",
        "engine": "1.5L I4",
        "mpg": 33,
    },
    {
        "make_key": "honda",
        "make": "Honda",
        "model_key": "cr-v",
        "model": "CR-V",
        "vehicle_type_key": "suv",
        "drivetrain_key": "awd",
        "engine": "1.5L I4",
        "mpg": 30,
    },
    {
        "make_key": "toyota",
        "make": "Toyota",
        "model_key": "camry",
        "model": "Camry",
        "vehicle_type_key": "sedan",
        "drivetrain_key": "fwd",
        "engine": "2.5L I4",
        "mpg": 32,
    },
    {
        "make_key": "toyota",
        "make": "Toyota",
        "model_key": "corolla",
        "model": "Corolla",
        "vehicle_type_key": "sedan",
        "drivetrain_key": "fwd",
        "engine": "2.0L I4",
        "mpg": 35,
    },
    {
        "make_key": "toyota",
        "make": "Toyota",
        "model_key": "rav4",
        "model": "RAV4",
        "vehicle_type_key": "suv",
        "drivetrain_key": "awd",
        "engine": "2.5L I4",
        "mpg": 30,
    },
    {
        "make_key": "jeep",
        "make": "Jeep",
        "model_key": "wrangler",
        "model": "Wrangler",
        "vehicle_type_key": "suv",
        "drivetrain_key": "4wd",
        "engine": "3.6L V6",
        "mpg": 21,
    },
    {
        "make_key": "jeep",
        "make": "Jeep",
        "model_key": "grand-cherokee",
        "model": "Grand Cherokee",
        "vehicle_type_key": "suv",
        "drivetrain_key": "4wd",
        "engine": "3.6L V6",
        "mpg": 22,
    },
    {
        "make_key": "jeep",
        "make": "Jeep",
        "model_key": "compass",
        "model": "Compass",
        "vehicle_type_key": "suv",
        "drivetrain_key": "awd",
        "engine": "2.4L I4",
        "mpg": 25,
    },
    {
        "make_key": "chevrolet",
        "make": "Chevrolet",
        "model_key": "silverado",
        "model": "Silverado",
        "vehicle_type_key": "pickup",
        "drivetrain_key": "4wd",
        "engine": "5.3L V8",
        "mpg": 19,
    },
    {
        "make_key": "chevrolet",
        "make": "Chevrolet",
        "model_key": "equinox",
        "model": "Equinox",
        "vehicle_type_key": "suv",
        "drivetrain_key": "awd",
        "engine": "1.5L I4",
        "mpg": 28,
    },
    {
        "make_key": "chevrolet",
        "make": "Chevrolet",
        "model_key": "tahoe",
        "model": "Tahoe",
        "vehicle_type_key": "suv",
        "drivetrain_key": "4wd",
        "engine": "5.3L V8",
        "mpg": 18,
    },
    {
        "make_key": "subaru",
        "make": "Subaru",
        "model_key": "outback",
        "model": "Outback",
        "vehicle_type_key": "station-wagon",
        "drivetrain_key": "awd",
        "engine": "2.5L H4",
        "mpg": 28,
    },
    {
        "make_key": "subaru",
        "make": "Subaru",
        "model_key": "forester",
        "model": "Forester",
        "vehicle_type_key": "suv",
        "drivetrain_key": "awd",
        "engine": "2.5L H4",
        "mpg": 29,
    },
    {
        "make_key": "subaru",
        "make": "Subaru",
        "model_key": "impreza",
        "model": "Impreza",
        "vehicle_type_key": "sedan",
        "drivetrain_key": "awd",
        "engine": "2.0L H4",
        "mpg": 30,
    },
)

FEATURE_KEYS = (
    "air-conditioning",
    "cruise-control",
    "power-locks",
    "power-windows",
    "remote-keyless-entry",
    "abs-anti-lock-brakes",
    "heated-seats",
    "satellite-radio",
    "leather-interior",
    "fog-lights",
)

COLORS = (
    "Black",
    "White",
    "Silver",
    "Blue",
    "Red",
    "Gray",
    "Green",
    "Burgundy",
)

TRIMS = ("Base", "Sport", "SE", "EX", "Limited", "Touring", "Lariat", "XLT")
LOCATIONS = (
    ("Freeport", "US-IL", "61032"),
    ("Rockford", "US-IL", "61101"),
    ("Chicago", "US-IL", "60601"),
    ("Madison", "US-WI", "53703"),
)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _status_for(index: int) -> str:
    """Return the deterministic lifecycle mix used by 100-row QA blocks."""

    bucket = index % 100
    if bucket < 70:
        return "active"
    if bucket < 80:
        return "sale_pending"
    if bucket < 90:
        return "sold"
    if bucket < 94:
        return "pending"
    if bucket < 96:
        return "draft"
    if bucket < 98:
        return "expired"
    return "removed"


def _published_cycle(
    *,
    anchor: datetime,
    rng: random.Random,
    published_days_ago: tuple[int, int],
) -> tuple[datetime, datetime, datetime]:
    published = anchor - timedelta(
        days=rng.randint(*published_days_ago),
        hours=rng.randint(0, 11),
    )
    first_published = published - timedelta(days=rng.randint(0, 45))
    created = first_published - timedelta(days=rng.randint(1, 30), hours=1)
    return created, first_published, published


def _lifecycle(status: str, *, anchor: datetime, rng: random.Random, index: int) -> dict:
    """Build internally consistent source lifecycle/history metadata."""

    created = anchor - timedelta(days=rng.randint(15, 180), hours=rng.randint(0, 11))
    first_published = None
    published = None
    expires = None
    warning = None
    expired = None
    sold = None
    expired_edited = None
    removal_warning = None
    aged_out = None
    aged_out_notice = None
    updated = created

    if status in {"active", "sale_pending"}:
        created, first_published, published = _published_cycle(
            anchor=anchor,
            rng=rng,
            published_days_ago=(1, 55),
        )
        expires = published + timedelta(days=60)
        if expires <= anchor + timedelta(days=7) and index % 2 == 0:
            warning = max(published, expires - timedelta(days=7))
        updated = max(value for value in (created, first_published, published, warning) if value)
        if status == "sale_pending":
            updated = max(updated, anchor - timedelta(days=rng.randint(0, 5)))

    elif status == "sold":
        created, first_published, published = _published_cycle(
            anchor=anchor,
            rng=rng,
            published_days_ago=(15, 90),
        )
        expires = published + timedelta(days=60)
        available_days = max(1, min(20, (anchor - published).days))
        sold = published + timedelta(days=rng.randint(1, available_days))
        updated = sold

    elif status == "expired":
        expired = anchor - timedelta(days=rng.randint(1, 20), hours=rng.randint(0, 11))
        expires = expired - timedelta(hours=rng.randint(0, 12))
        published = expires - timedelta(days=rng.randint(30, 55))
        first_published = published - timedelta(days=rng.randint(0, 30))
        created = first_published - timedelta(days=rng.randint(1, 30), hours=1)
        warning = max(published, expires - timedelta(days=7)) if index % 2 == 0 else None
        if index % 2 == 0:
            expired_edited = expired + timedelta(days=1)
        if index % 3 == 0:
            removal_warning = expired + timedelta(days=15)
        updated = max(
            value
            for value in (created, first_published, published, warning, expired, expired_edited, removal_warning)
            if value
        )

    elif status == "removed":
        if index % 2 == 0:
            # Removed after expired-retention aging.
            expired = anchor - timedelta(days=rng.randint(35, 60), hours=rng.randint(0, 11))
            expires = expired - timedelta(hours=rng.randint(0, 12))
            published = expires - timedelta(days=rng.randint(30, 55))
            first_published = published - timedelta(days=rng.randint(0, 30))
            created = first_published - timedelta(days=rng.randint(1, 30), hours=1)
            warning = max(published, expires - timedelta(days=7))
            removal_warning = expired + timedelta(days=23)
            aged_out = expired + timedelta(days=30)
            aged_out_notice = aged_out
            updated = aged_out_notice
        else:
            # Removed after a completed sale; the current model has no removed_at field.
            created, first_published, published = _published_cycle(
                anchor=anchor,
                rng=rng,
                published_days_ago=(60, 120),
            )
            expires = published + timedelta(days=60)
            sold = published + timedelta(days=rng.randint(5, 25))
            updated = max(sold, anchor - timedelta(days=rng.randint(0, 10)))

    elif status == "pending":
        if index % 2 == 0:
            # Re-review inventory retains immutable first-publication history.
            first_published = anchor - timedelta(days=90 + rng.randint(0, 30))
            created = first_published - timedelta(days=rng.randint(1, 30), hours=1)
            updated = anchor - timedelta(days=rng.randint(0, 5))
        else:
            updated = min(anchor, created + timedelta(days=rng.randint(0, 10)))

    elif status == "draft":
        updated = min(anchor, created + timedelta(days=rng.randint(0, 10)))

    return {
        "status": status,
        "featured": index % 11 == 0,
        "view_count": rng.randint(0, 5000),
        "created_at": _iso(created),
        "updated_at": _iso(updated),
        "first_published_at": _iso(first_published),
        "published_at": _iso(published),
        "expires_at": _iso(expires),
        "expiration_warning_sent_at": _iso(warning),
        "expired_at": _iso(expired),
        "sold_at": _iso(sold),
        "expired_edited_at": _iso(expired_edited),
        "expired_removal_warning_sent_at": _iso(removal_warning),
        "aged_out_at": _iso(aged_out),
        "aged_out_notice_sent_at": _iso(aged_out_notice),
    }


def _synthetic_image(label: str, *, seed: int, slot: int) -> bytes:
    """Return one deterministic project-generated JPEG used only for QA/demo media."""

    rng = random.Random((seed * 1009) + slot)
    width, height = ((1280, 960), (1200, 900), (1024, 768))[slot % 3]
    image = Image.new(
        "RGB",
        (width, height),
        color=(rng.randint(35, 110), rng.randint(45, 120), rng.randint(55, 135)),
    )
    draw = ImageDraw.Draw(image)

    # Simple neutral vehicle-like silhouette; this is QA media, not a vehicle claim.
    body_y = int(height * 0.55)
    body_left = int(width * 0.18)
    body_right = int(width * 0.82)
    body_top = int(height * 0.42)
    draw.rounded_rectangle(
        (body_left, body_top, body_right, body_y + int(height * 0.13)),
        radius=max(12, width // 50),
        fill=(220, 220, 220),
    )
    draw.polygon(
        [
            (int(width * 0.34), body_top),
            (int(width * 0.44), int(height * 0.31)),
            (int(width * 0.64), int(height * 0.31)),
            (int(width * 0.72), body_top),
        ],
        fill=(195, 205, 215),
    )
    wheel_radius = max(24, width // 28)
    for wheel_x in (int(width * 0.32), int(width * 0.69)):
        draw.ellipse(
            (
                wheel_x - wheel_radius,
                body_y + int(height * 0.07) - wheel_radius,
                wheel_x + wheel_radius,
                body_y + int(height * 0.07) + wheel_radius,
            ),
            fill=(35, 35, 35),
        )
    text = f"AUTOGRID360 DEMO  {label}  #{slot + 1}"
    draw.rectangle((0, height - 72, width, height), fill=(20, 20, 20))
    draw.text((24, height - 52), text, fill=(245, 245, 245), font=ImageFont.load_default())

    output = BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True)
    return output.getvalue()


def _external_image_pool(directory: Path) -> list[bytes]:
    if not directory.is_dir():
        raise ValueError(f"Demo image directory does not exist: {directory}")

    payloads: list[bytes] = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        with Image.open(path) as source:
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            normalized.thumbnail((1600, 1200), Image.Resampling.LANCZOS)
            output = BytesIO()
            normalized.save(output, format="JPEG", quality=90, optimize=True)
            payloads.append(output.getvalue())
    if not payloads:
        raise ValueError(f"No JPEG/PNG/WebP demo images found in {directory}.")
    return payloads


def _seller_payload(username: str, index: int) -> dict:
    return {
        "username": username,
        "profile": {
            "display_name": f"Demo Seller {index + 1}",
            "company_name": f"Demo Motors {index + 1}",
        },
    }


def _listing_row(
    index: int,
    *,
    seller_username: str,
    seed: int,
    anchor: datetime,
    rng: random.Random,
) -> dict:
    archetype = ARCHETYPES[index % len(ARCHETYPES)]
    status = _status_for(index)
    year = rng.randint(2008, 2026)
    trim = rng.choice(TRIMS)
    color = rng.choice(COLORS)
    mileage = 0 if year >= 2026 and index % 10 == 0 else rng.randint(5_000, 190_000)
    base_price = max(2500, 39000 - ((2026 - year) * 1700) - int(mileage * 0.055))
    price = max(1500, base_price + rng.randint(-2500, 4500))
    city, zone_code, postal_code = LOCATIONS[index % len(LOCATIONS)]
    feature_count = rng.randint(2, 6)
    features = sorted(rng.sample(FEATURE_KEYS, feature_count))
    portable_id = str(uuid5(NAMESPACE_URL, f"autogrid360-demo:{seed}:{index}"))

    return {
        "portable_id": portable_id,
        "source": _lifecycle(status, anchor=anchor, rng=rng, index=index),
        "listing": {
            "title": f"{year} {archetype['make']} {archetype['model']} {trim}",
            "price": f"{price:.2f}",
            "description": (
                "Deterministic AutoGrid360 QA inventory generated for backup/restore, "
                "search, pagination, lifecycle, image, and presentation shakedowns."
            ),
            "country_code": "US",
            "city": city,
            "zone_code": zone_code,
            "postal_code": postal_code,
        },
        "vehicle": {
            "year": year,
            "make_key": archetype["make_key"],
            "model": {"key": archetype["model_key"], "text": None},
            "trim": trim,
            "vehicle_type_key": archetype["vehicle_type_key"],
            "doors": 4,
            "exterior_color": color,
            "mileage": mileage,
            "condition": "New" if mileage == 0 else "Used",
            "engine": archetype["engine"],
            "transmission": "Automatic",
            "drivetrain_key": archetype["drivetrain_key"],
            "feature_keys": features,
            "mpg": archetype["mpg"],
            "fuel_type": "Gasoline",
            "vin": None,
            "stock_number": f"DEMO-{seed}-{index + 1:06d}",
        },
        "images": [],
        "seller_username": seller_username,
    }


def _normalized_sellers(values: list[str]) -> list[str]:
    sellers: list[str] = []
    seen: set[str] = set()
    for raw in values:
        username = raw.replace("\x00", "").strip()
        if not username:
            continue
        if len(username) > 60:
            raise ValueError("--seller usernames must be 60 characters or fewer.")
        key = username.casefold()
        if key in seen:
            continue
        seen.add(key)
        sellers.append(username)
    if not sellers:
        raise ValueError("Provide at least one --seller username.")
    return sellers


def _zip_info(name: str, *, anchor: datetime) -> zipfile.ZipInfo:
    """Return deterministic metadata so fixed seed/anchor reruns are byte-stable."""

    info = zipfile.ZipInfo(
        name,
        date_time=(anchor.year, anchor.month, anchor.day, anchor.hour, 0, 0),
    )
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def _zip_write(archive: zipfile.ZipFile, name: str, payload: bytes, *, anchor: datetime) -> None:
    archive.writestr(_zip_info(name, anchor=anchor), payload, compresslevel=6)


def build_bundle(args: argparse.Namespace) -> None:
    if args.num < 1 or args.num > 100_000:
        raise ValueError("--num must be between 1 and 100000.")
    if args.images < 0 or args.images > 12:
        raise ValueError("--images must be between 0 and 12.")
    sellers = _normalized_sellers(args.seller)

    output = args.output.resolve()
    if output.exists() and not args.force:
        raise ValueError(f"Output already exists: {output}; use --force to replace it.")
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        anchor_day = date.fromisoformat(args.anchor_date)
    except ValueError as exc:
        raise ValueError("--anchor-date must use YYYY-MM-DD.") from exc
    anchor = datetime.combine(anchor_day, time(12, 0), tzinfo=timezone.utc)
    rng = random.Random(args.seed)

    external_pool = None
    if args.image_dir is not None:
        external_pool = _external_image_pool(args.image_dir.resolve())
    synthetic_pool: dict[int, bytes] = {}

    seller_payloads = [_seller_payload(username, index) for index, username in enumerate(sellers)]
    manifest = {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "scope": BUNDLE_SCOPE_SELLER if len(sellers) == 1 else BUNDLE_SCOPE_SITE,
        "exported_at": anchor.isoformat(),
        "listings": [],
    }
    if len(sellers) == 1:
        manifest["seller"] = seller_payloads[0]
    else:
        manifest["sellers"] = seller_payloads

    temporary = output.with_name(f".{output.name}.tmp")
    state_counts: dict[str, int] = {}
    image_count = 0
    try:
        with zipfile.ZipFile(temporary, mode="w") as archive:
            for index in range(args.num):
                seller_username = sellers[index % len(sellers)]
                row = _listing_row(
                    index,
                    seller_username=seller_username,
                    seed=args.seed,
                    anchor=anchor,
                    rng=rng,
                )
                archetype = ARCHETYPES[index % len(ARCHETYPES)]
                for image_index in range(args.images):
                    archive_path = f"images/{row['portable_id']}/{image_index:03d}.jpg"
                    if external_pool is not None:
                        payload = external_pool[(index + image_index) % len(external_pool)]
                    else:
                        pool_slot = (
                            (index % len(ARCHETYPES)) * max(args.images, 1)
                        ) + image_index
                        payload = synthetic_pool.get(pool_slot)
                        if payload is None:
                            payload = _synthetic_image(
                                f"{archetype['make']} {archetype['model']}",
                                seed=args.seed,
                                slot=pool_slot,
                            )
                            synthetic_pool[pool_slot] = payload
                    _zip_write(archive, archive_path, payload, anchor=anchor)
                    row["images"].append(
                        {
                            "path": archive_path,
                            "original_filename": f"autogrid360-demo-{image_index + 1}.jpg",
                            "position": image_index,
                            "is_primary": image_index == 0,
                        }
                    )
                    image_count += 1

                state = row["source"]["status"]
                state_counts[state] = state_counts.get(state, 0) + 1
                if len(sellers) == 1:
                    row.pop("seller_username", None)
                manifest["listings"].append(row)

            manifest_bytes = json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            _zip_write(archive, MANIFEST_NAME, manifest_bytes, anchor=anchor)
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    print(
        "AutoGrid360 demo backup generated: "
        f"scope={manifest['scope']} sellers={len(sellers)} listings={args.num} "
        f"images={image_count} seed={args.seed} output={output}"
    )
    print(
        "Lifecycle mix: "
        + " ".join(f"{key}={value}" for key, value in sorted(state_counts.items()))
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Generate a valid AutoGrid360 inventory backup for QA/demo restore testing."
    )
    result.add_argument("--num", type=int, default=100, help="Listings to generate (1-100000).")
    result.add_argument(
        "--seller",
        action="append",
        required=True,
        help="Source seller username; repeat to generate a multi-seller site backup.",
    )
    result.add_argument("--images", type=int, default=0, help="Images per listing (0-12).")
    result.add_argument("--seed", type=int, default=454, help="Deterministic data seed.")
    result.add_argument(
        "--anchor-date",
        default=date.today().isoformat(),
        help="Lifecycle anchor date in YYYY-MM-DD; specify explicitly for byte-stable reruns.",
    )
    result.add_argument(
        "--image-dir",
        type=Path,
        help=(
            "Optional directory of project-owned JPEG/PNG/WebP images; otherwise "
            "synthetic QA images are generated locally."
        ),
    )
    result.add_argument("--force", action="store_true", help="Replace an existing output file.")
    result.add_argument("output", type=Path, help="Destination .zip backup bundle.")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        build_bundle(args)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
