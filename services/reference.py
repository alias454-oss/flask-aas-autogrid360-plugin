# app/plugins/autogrid360/services/reference.py
"""Reference-data loading and lookup helpers for AutoGrid360."""

from __future__ import annotations

import json
from pathlib import Path
import re
import unicodedata

from app.core.extensions import db
from app.plugins.autogrid360.models.model import VehicleModel
from app.plugins.autogrid360.models.reference import (
    CATEGORY_DRIVETRAIN,
    CATEGORY_FEATURE,
    CATEGORY_MAKE,
    CATEGORY_VEHICLE_TYPE,
    REFERENCE_CATEGORIES,
    ReferenceValue,
)


DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "automotive"
REFERENCE_FILES = {
    CATEGORY_MAKE: "makes.json",
    CATEGORY_VEHICLE_TYPE: "types.json",
    CATEGORY_DRIVETRAIN: "drivetrains.json",
    CATEGORY_FEATURE: "features.json",
}
REFERENCE_LABELS = {
    CATEGORY_MAKE: "Makes",
    CATEGORY_VEHICLE_TYPE: "Vehicle Types",
    CATEGORY_DRIVETRAIN: "Drivetrains",
    CATEGORY_FEATURE: "Features",
}
MODEL_OTHER_VALUE = "__other__"


class ReferenceDataError(ValueError):
    """Raised when shipped or administrator reference data is invalid."""


def normalize_reference_key(value: object) -> str:
    """Return a stable lowercase ASCII key suitable for forms and seed matching."""

    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value).strip())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


def _load_payload(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceDataError(f"Unable to load AutoGrid360 reference data: {path}") from exc

    if not isinstance(payload, list):
        raise ReferenceDataError(f"AutoGrid360 reference data must be a list: {path}")
    return payload


def _load_file(path: Path, category: str) -> list[dict]:
    payload = _load_payload(path)
    seen_keys: set[str] = set()
    rows: list[dict] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ReferenceDataError(f"Reference row {index} in {path} must be an object.")

        label = " ".join(str(item.get("label") or "").split())
        key = normalize_reference_key(item.get("key") or label)
        if not label or not key:
            raise ReferenceDataError(f"Reference row {index} in {path} needs key and label.")
        if len(key) > 80 or len(label) > 80:
            raise ReferenceDataError(f"Reference row {index} in {path} is too long.")
        if key in seen_keys:
            raise ReferenceDataError(f"Duplicate reference key {key!r} in {path}.")
        seen_keys.add(key)

        try:
            sort_order = int(item.get("sort_order", index * 10))
        except (TypeError, ValueError) as exc:
            raise ReferenceDataError(
                f"Reference row {index} in {path} has invalid sort_order."
            ) from exc

        rows.append(
            {
                "category": category,
                "key": key,
                "label": label,
                "sort_order": sort_order,
                "default_selected": bool(item.get("default_selected", False)),
            }
        )

    return rows


def _load_model_defaults(path: Path) -> list[dict]:
    """Load models nested below each make in the automotive makes file."""

    payload = _load_payload(path)
    rows: list[dict] = []
    seen_make_keys: set[str] = set()

    for make_index, make_item in enumerate(payload, start=1):
        if not isinstance(make_item, dict):
            raise ReferenceDataError(
                f"Reference row {make_index} in {path} must be an object."
            )

        make_label = " ".join(str(make_item.get("label") or "").split())
        make_key = normalize_reference_key(make_item.get("key") or make_label)
        if not make_label or not make_key:
            raise ReferenceDataError(
                f"Reference row {make_index} in {path} needs key and label."
            )
        if make_key in seen_make_keys:
            raise ReferenceDataError(f"Duplicate reference key {make_key!r} in {path}.")
        seen_make_keys.add(make_key)

        models = make_item.get("models", [])
        if models is None:
            models = []
        if not isinstance(models, list):
            raise ReferenceDataError(
                f"Models for make {make_key!r} in {path} must be a list."
            )

        seen_model_keys: set[str] = set()
        for model_index, model_item in enumerate(models, start=1):
            if not isinstance(model_item, dict):
                raise ReferenceDataError(
                    f"Model row {model_index} for make {make_key!r} in {path} must be an object."
                )

            label = " ".join(str(model_item.get("label") or "").split())
            key = normalize_reference_key(model_item.get("key") or label)
            if not label or not key:
                raise ReferenceDataError(
                    f"Model row {model_index} for make {make_key!r} in {path} needs key and label."
                )
            if len(key) > 80 or len(label) > 80:
                raise ReferenceDataError(
                    f"Model row {model_index} for make {make_key!r} in {path} is too long."
                )
            if key in seen_model_keys:
                raise ReferenceDataError(
                    f"Duplicate model key {key!r} for make {make_key!r} in {path}."
                )
            seen_model_keys.add(key)

            try:
                sort_order = int(model_item.get("sort_order", model_index * 10))
            except (TypeError, ValueError) as exc:
                raise ReferenceDataError(
                    f"Model row {model_index} for make {make_key!r} in {path} has invalid sort_order."
                ) from exc

            rows.append(
                {
                    "make_key": make_key,
                    "key": key,
                    "label": label,
                    "sort_order": sort_order,
                }
            )

    return rows


def load_reference_defaults(data_root: Path | str | None = None) -> dict[str, list[dict]]:
    """Load the editable automotive default lists from disk without touching the DB."""

    root = Path(data_root) if data_root is not None else DATA_ROOT
    return {
        category: _load_file(root / filename, category)
        for category, filename in REFERENCE_FILES.items()
    }


def load_model_defaults(data_root: Path | str | None = None) -> list[dict]:
    """Load nested make/model defaults without touching the database."""

    root = Path(data_root) if data_root is not None else DATA_ROOT
    return _load_model_defaults(root / REFERENCE_FILES[CATEGORY_MAKE])


def seed_reference_data(*, data_root: Path | str | None = None) -> int:
    """Add missing automotive defaults without overwriting runtime-owned rows.

    Flat reference rows are matched by ``category + key``. Vehicle models are
    matched by ``make + model key``. Database IDs, labels, active state, and sort
    order remain runtime-owned and are never replaced by an ordinary re-seed.
    The caller owns transaction commit/rollback.
    """

    inserted = 0
    for category, defaults in load_reference_defaults(data_root).items():
        existing_keys = {
            key
            for (key,) in db.session.query(ReferenceValue.key)
            .filter(ReferenceValue.category == category)
            .all()
        }
        for item in defaults:
            if item["key"] in existing_keys:
                continue
            db.session.add(ReferenceValue(**item, active=True))
            existing_keys.add(item["key"])
            inserted += 1

    # Model defaults reference make rows by stable key, never by file position or ID.
    db.session.flush()
    makes_by_key = {
        value.key: value
        for value in ReferenceValue.query.filter_by(category=CATEGORY_MAKE).all()
    }
    existing_models = {
        (model.make_id, model.key)
        for model in VehicleModel.query.all()
    }
    for item in load_model_defaults(data_root):
        make = makes_by_key.get(item["make_key"])
        if make is None:
            raise ReferenceDataError(
                f"Vehicle model {item['key']!r} references missing make {item['make_key']!r}."
            )
        identity = (make.id, item["key"])
        if identity in existing_models:
            continue
        db.session.add(
            VehicleModel(
                make_id=make.id,
                key=item["key"],
                label=item["label"],
                sort_order=item["sort_order"],
                active=True,
            )
        )
        existing_models.add(identity)
        inserted += 1

    return inserted


def reference_values(category: str, *, active_only: bool = True) -> list[ReferenceValue]:
    """Return ordered values for one supported reference category."""

    if category not in REFERENCE_CATEGORIES:
        raise ReferenceDataError(f"Unsupported AutoGrid360 reference category: {category}")

    query = ReferenceValue.query.filter_by(category=category)
    if active_only:
        query = query.filter_by(active=True)
    return query.order_by(
        ReferenceValue.sort_order.asc(),
        ReferenceValue.label.asc(),
        ReferenceValue.id.asc(),
    ).all()


def reference_by_key(
    category: str,
    value: object,
    *,
    active_only: bool = True,
) -> ReferenceValue | None:
    """Resolve a value by its stable key, accepting label-like form input."""

    key = normalize_reference_key(value)
    if not key:
        return None
    query = ReferenceValue.query.filter_by(category=category, key=key)
    if active_only:
        query = query.filter_by(active=True)
    return query.one_or_none()


def reference_choices(
    category: str,
    *,
    current: ReferenceValue | None = None,
    include_blank: bool = False,
) -> list[tuple[str, str]]:
    """Build form choices, retaining one currently selected disabled value on edit."""

    values = reference_values(category)
    if current is not None and not current.active and all(item.id != current.id for item in values):
        values.append(current)
        values.sort(key=lambda item: (item.sort_order, item.label.lower(), item.id))

    choices = [(item.key, item.label + (" (disabled)" if not item.active else "")) for item in values]
    if include_blank:
        choices.insert(0, ("", "Not specified"))
    return choices


def vehicle_models(*, active_only: bool = True) -> list[VehicleModel]:
    """Return ordered vehicle models with their make identity available."""

    query = VehicleModel.query.join(
        ReferenceValue,
        VehicleModel.make_id == ReferenceValue.id,
    ).filter(ReferenceValue.category == CATEGORY_MAKE)
    if active_only:
        query = query.filter(
            VehicleModel.active.is_(True),
            ReferenceValue.active.is_(True),
        )
    return query.order_by(
        ReferenceValue.sort_order.asc(),
        ReferenceValue.label.asc(),
        VehicleModel.sort_order.asc(),
        VehicleModel.label.asc(),
        VehicleModel.id.asc(),
    ).all()


def vehicle_model_by_key(
    make: ReferenceValue,
    value: object,
    *,
    active_only: bool = True,
) -> VehicleModel | None:
    """Resolve one model key within exactly one make."""

    key = normalize_reference_key(value)
    if not key:
        return None
    query = VehicleModel.query.filter_by(make_id=make.id, key=key)
    if active_only:
        query = query.filter_by(active=True)
    return query.one_or_none()


def model_choice_value(make_key: object, model_key: object) -> str:
    """Return the stable form/query token for one make-scoped model."""

    normalized_make = normalize_reference_key(make_key)
    normalized_model = normalize_reference_key(model_key)
    if not normalized_make or not normalized_model:
        return ""
    return f"{normalized_make}:{normalized_model}"


def parse_model_choice(value: object) -> tuple[str, str] | None:
    """Parse one ``make:model`` token without accepting ambiguous bare model keys."""

    raw_value = str(value or "").strip()
    make_value, separator, model_value = raw_value.partition(":")
    if not separator:
        return None
    make_key = normalize_reference_key(make_value)
    model_key = normalize_reference_key(model_value)
    if not make_key or not model_key:
        return None
    return make_key, model_key


def model_choices(
    *,
    current: VehicleModel | None = None,
    include_blank: bool = True,
    include_other: bool = True,
) -> list[tuple]:
    """Build make-scoped model choices with data attributes for progressive UX."""

    models = vehicle_models()
    if current is not None and all(item.id != current.id for item in models):
        models.append(current)
        models.sort(
            key=lambda item: (
                item.make_ref.sort_order,
                item.make_ref.label.lower(),
                item.sort_order,
                item.label.lower(),
                item.id,
            )
        )

    choices: list[tuple] = []
    if include_blank:
        choices.append(("", "Select a model"))
    for item in models:
        choices.append(
            (
                model_choice_value(item.make_ref.key, item.key),
                f"{item.make_ref.label} — {item.label}"
                + (
                    " (disabled)"
                    if not item.active or not item.make_ref.active
                    else ""
                ),
                {"data-make": item.make_ref.key},
            )
        )
    if include_other:
        choices.append((MODEL_OTHER_VALUE, "Other / Unlisted"))
    return choices


def configure_listing_form(form, *, vehicle=None) -> None:
    """Populate controlled listing choices before WTForms validates submitted data."""

    form.make.choices = reference_choices(
        CATEGORY_MAKE,
        current=getattr(vehicle, "make_ref", None),
    )
    form.model.choices = model_choices(
        current=getattr(vehicle, "model_ref", None),
    )
    form.vehicle_type.choices = reference_choices(
        CATEGORY_VEHICLE_TYPE,
        current=getattr(vehicle, "vehicle_type_ref", None),
        include_blank=True,
    )
    form.drivetrain.choices = reference_choices(
        CATEGORY_DRIVETRAIN,
        current=getattr(vehicle, "drivetrain_ref", None),
        include_blank=True,
    )

    current_features = list(getattr(vehicle, "features", ()) or ())
    active_features = reference_values(CATEGORY_FEATURE)
    known_ids = {item.id for item in active_features}
    active_features.extend(item for item in current_features if item.id not in known_ids)
    active_features.sort(key=lambda item: (item.sort_order, item.label.lower(), item.id))
    form.features.choices = [
        (item.key, item.label + (" (disabled)" if not item.active else ""))
        for item in active_features
    ]

    if not form.is_submitted():
        if vehicle is not None:
            form.make.data = vehicle.make_ref.key
            if vehicle.model_ref is not None:
                form.model.data = model_choice_value(
                    vehicle.model_ref.make_ref.key,
                    vehicle.model_ref.key,
                )
                form.model_other.data = None
            else:
                form.model.data = MODEL_OTHER_VALUE
                form.model_other.data = vehicle.model_text
            form.vehicle_type.data = vehicle.vehicle_type_ref.key if vehicle.vehicle_type_ref else ""
            form.drivetrain.data = vehicle.drivetrain_ref.key if vehicle.drivetrain_ref else ""
            form.features.data = [item.key for item in vehicle.features]
        else:
            form.features.data = [
                item.key for item in active_features if item.active and item.default_selected
            ]


def apply_listing_references(vehicle, form) -> None:
    """Assign already-validated reference choices from a listing form to a vehicle."""

    current_make = getattr(vehicle, "make_ref", None)
    make = reference_by_key(CATEGORY_MAKE, form.make.data)
    if make is None and current_make is not None and current_make.key == form.make.data:
        make = current_make
    if make is None:
        raise ReferenceDataError("The selected make is unavailable.")

    current_model = getattr(vehicle, "model_ref", None)
    if form.model.data == MODEL_OTHER_VALUE:
        if not form.model_other.data:
            raise ReferenceDataError("Enter the unlisted vehicle model.")
        model = None
        model_text = form.model_other.data
    else:
        parsed_model = parse_model_choice(form.model.data)
        if parsed_model is None:
            raise ReferenceDataError("The selected model is unavailable.")
        model_make_key, model_key = parsed_model
        if model_make_key != make.key:
            raise ReferenceDataError("The selected model does not belong to the selected make.")
        model = vehicle_model_by_key(make, model_key)
        if (
            model is None
            and current_model is not None
            and current_model.make_id == make.id
            and current_model.key == model_key
        ):
            model = current_model
        if model is None:
            raise ReferenceDataError("The selected model is unavailable.")
        model_text = None

    vehicle_type = None
    if form.vehicle_type.data:
        vehicle_type = reference_by_key(CATEGORY_VEHICLE_TYPE, form.vehicle_type.data)
        current = getattr(vehicle, "vehicle_type_ref", None)
        if vehicle_type is None and current is not None and current.key == form.vehicle_type.data:
            vehicle_type = current
        if vehicle_type is None:
            raise ReferenceDataError("The selected vehicle type is unavailable.")

    drivetrain = None
    if form.drivetrain.data:
        drivetrain = reference_by_key(CATEGORY_DRIVETRAIN, form.drivetrain.data)
        current = getattr(vehicle, "drivetrain_ref", None)
        if drivetrain is None and current is not None and current.key == form.drivetrain.data:
            drivetrain = current
        if drivetrain is None:
            raise ReferenceDataError("The selected drivetrain is unavailable.")

    current_features = {item.key: item for item in getattr(vehicle, "features", ())}
    features: list[ReferenceValue] = []
    for key in form.features.data:
        feature = reference_by_key(CATEGORY_FEATURE, key)
        if feature is None:
            feature = current_features.get(key)
        if feature is None:
            raise ReferenceDataError("One selected vehicle feature is unavailable.")
        features.append(feature)

    vehicle.make_ref = make
    vehicle.model_ref = model
    vehicle.model_text = model_text
    vehicle.vehicle_type_ref = vehicle_type
    vehicle.drivetrain_ref = drivetrain
    vehicle.features = features
