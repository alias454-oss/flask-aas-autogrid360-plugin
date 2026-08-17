# app/plugins/autogrid360/routes/reference.py
"""AutoGrid360 administrator management of controlled reference data."""

import logging

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.core.auth import login_required
from app.core.extensions import db, limiter
from app.core.security import get_client_ip
from app.core.trackers import audit_activity_enabled, log_action
from app.plugins.autogrid360.services.auth import require_autogrid360_admin
from app.plugins.autogrid360.forms.reference import (
    ReferenceToggleForm,
    ReferenceValueForm,
    VehicleModelForm,
    VehicleModelToggleForm,
)
from app.plugins.autogrid360.models import ReferenceValue, VehicleModel
from app.plugins.autogrid360.services.reference import (
    REFERENCE_FILES,
    REFERENCE_LABELS,
    normalize_reference_key,
    reference_values,
)


logger = logging.getLogger(__name__)

reference_bp = Blueprint(
    "autogrid360_reference",
    __name__,
    url_prefix="/autogrid360/admin/reference",
)

_CATEGORY_SLUGS = {
    "makes": "make",
    "vehicle-types": "vehicle_type",
    "drivetrains": "drivetrain",
    "features": "feature",
}
_CATEGORY_TO_SLUG = {category: slug for slug, category in _CATEGORY_SLUGS.items()}


def _category(slug: str) -> str:
    category = _CATEGORY_SLUGS.get(slug)
    if category is None:
        abort(404)
    return category


def _duplicate_label(category: str, label: str, *, exclude_id: int | None = None) -> bool:
    query = ReferenceValue.query.filter(
        ReferenceValue.category == category,
        db.func.lower(ReferenceValue.label) == label.lower(),
    )
    if exclude_id is not None:
        query = query.filter(ReferenceValue.id != exclude_id)
    return query.first() is not None


def _make(make_id: int) -> ReferenceValue:
    """Return one make reference row or abort when the ID is not a make."""

    return ReferenceValue.query.filter_by(
        id=make_id,
        category="make",
    ).first_or_404()


def _duplicate_model_label(
    make_id: int,
    label: str,
    *,
    exclude_id: int | None = None,
) -> bool:
    query = VehicleModel.query.filter(
        VehicleModel.make_id == make_id,
        db.func.lower(VehicleModel.label) == label.lower(),
    )
    if exclude_id is not None:
        query = query.filter(VehicleModel.id != exclude_id)
    return query.first() is not None


def _audit_reference(value: ReferenceValue, *, action: str, extra_data: dict | None = None) -> None:
    if not audit_activity_enabled():
        return

    payload = {
        "reference_value_id": value.id,
        "category": value.category,
        "key": value.key,
    }
    if extra_data:
        payload.update(extra_data)

    log_action(
        user_id=current_user.id,
        action=action,
        target=f"autogrid360-reference:{value.id}",
        extra_data=payload,
    )


def _audit_model(
    model: VehicleModel,
    *,
    action: str,
    extra_data: dict | None = None,
) -> None:
    if not audit_activity_enabled():
        return

    payload = {
        "vehicle_model_id": model.id,
        "make_id": model.make_id,
        "key": model.key,
    }
    if extra_data:
        payload.update(extra_data)

    log_action(
        user_id=current_user.id,
        action=action,
        target=f"autogrid360-vehicle-model:{model.id}",
        extra_data=payload,
    )


@reference_bp.get("/")
@login_required
def index():
    """Show the four automotive reference-data groups used by Alpha."""

    require_autogrid360_admin()
    counts = {}
    for slug, category in _CATEGORY_SLUGS.items():
        total = ReferenceValue.query.filter_by(category=category).count()
        active = ReferenceValue.query.filter_by(category=category, active=True).count()
        counts[slug] = {"total": total, "active": active}

    return render_template(
        "autogrid360/admin/reference/index.html",
        categories=[
            {
                "slug": slug,
                "category": category,
                "label": REFERENCE_LABELS[category],
                "filename": REFERENCE_FILES[category],
            }
            for slug, category in _CATEGORY_SLUGS.items()
        ],
        counts=counts,
        title="AutoGrid360 Reference Data",
    )


@reference_bp.route("/<string:slug>", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], key_func=get_client_ip)
@login_required
def values(slug):
    """List one reference category and add new stable values."""

    require_autogrid360_admin()
    category = _category(slug)
    form = ReferenceValueForm()
    form.submit.label.text = "Add Value"

    if form.validate_on_submit():
        key = normalize_reference_key(form.label.data)
        existing = (
            ReferenceValue.query.filter_by(category=category, key=key).one_or_none()
            if key
            else None
        )
        if not key:
            form.label.errors.append("That label does not produce a usable stable key.")
        elif existing is not None:
            form.label.errors.append(
                "A value with the same stable key already exists in this category."
            )
        elif _duplicate_label(category, form.label.data):
            form.label.errors.append("That label already exists in this category.")
        else:
            value = ReferenceValue(
                category=category,
                key=key,
                label=form.label.data,
                active=bool(form.active.data),
                sort_order=form.sort_order.data,
                default_selected=(
                    bool(form.default_selected.data) if category == "feature" else False
                ),
            )
            try:
                db.session.add(value)
                db.session.flush()
                _audit_reference(value, action="autogrid360_reference_value_created")
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                logger.exception(
                    "AutoGrid360 reference create failed admin_id=%s category=%s key=%s",
                    current_user.id,
                    category,
                    key,
                )
                flash("The reference value could not be created.", "danger")
            else:
                logger.info(
                    "AutoGrid360 reference created admin_id=%s reference_value_id=%s category=%s key=%s",
                    current_user.id,
                    value.id,
                    category,
                    key,
                )
                flash("The reference value has been added.", "success")
                return redirect(url_for("autogrid360_reference.values", slug=slug))

    model_counts = {}
    if category == "make":
        model_counts = dict(
            db.session.query(VehicleModel.make_id, db.func.count(VehicleModel.id))
            .group_by(VehicleModel.make_id)
            .all()
        )

    return render_template(
        "autogrid360/admin/reference/values.html",
        category=category,
        category_label=REFERENCE_LABELS[category],
        slug=slug,
        values=reference_values(category, active_only=False),
        model_counts=model_counts,
        form=form,
        toggle_form=ReferenceToggleForm(),
        title=f"AutoGrid360 {REFERENCE_LABELS[category]}",
    )


@reference_bp.route("/<string:slug>/<int:value_id>/edit", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], key_func=get_client_ip)
@login_required
def edit(slug, value_id):
    """Edit presentation/order for one stable reference identity."""

    require_autogrid360_admin()
    category = _category(slug)
    value = ReferenceValue.query.filter_by(id=value_id, category=category).first_or_404()
    form = ReferenceValueForm(obj=value)
    form.submit.label.text = "Save Changes"

    if form.validate_on_submit():
        if _duplicate_label(category, form.label.data, exclude_id=value.id):
            form.label.errors.append("That label already exists in this category.")
        else:
            previous = {
                "label": value.label,
                "sort_order": value.sort_order,
                "active": value.active,
                "default_selected": value.default_selected,
            }
            value.label = form.label.data
            value.sort_order = form.sort_order.data
            value.active = bool(form.active.data)
            value.default_selected = (
                bool(form.default_selected.data) if category == "feature" else False
            )
            changed_fields = sorted(
                field for field, old in previous.items() if old != getattr(value, field)
            )
            if changed_fields:
                try:
                    _audit_reference(
                        value,
                        action="autogrid360_reference_value_updated",
                        extra_data={"changed_fields": changed_fields},
                    )
                    db.session.commit()
                except SQLAlchemyError:
                    db.session.rollback()
                    logger.exception(
                        "AutoGrid360 reference edit failed admin_id=%s reference_value_id=%s",
                        current_user.id,
                        value.id,
                    )
                    flash("The reference value could not be updated.", "danger")
                else:
                    logger.info(
                        "AutoGrid360 reference updated admin_id=%s reference_value_id=%s changed_fields=%s",
                        current_user.id,
                        value.id,
                        ",".join(changed_fields),
                    )
                    flash("The reference value has been updated.", "success")
                    return redirect(url_for("autogrid360_reference.values", slug=slug))
            else:
                flash("No reference-data changes were made.", "success")
                return redirect(url_for("autogrid360_reference.values", slug=slug))

    return render_template(
        "autogrid360/admin/reference/edit.html",
        category=category,
        category_label=REFERENCE_LABELS[category],
        slug=slug,
        value=value,
        form=form,
        title=f"Edit {REFERENCE_LABELS[category]}",
    )


@reference_bp.post("/<string:slug>/<int:value_id>/toggle")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def toggle(slug, value_id):
    """Enable or disable a value while preserving every existing foreign key."""

    require_autogrid360_admin()
    category = _category(slug)
    value = ReferenceValue.query.filter_by(id=value_id, category=category).first_or_404()
    form = ReferenceToggleForm()
    if not form.validate_on_submit():
        abort(400)

    value.active = not value.active
    try:
        _audit_reference(
            value,
            action="autogrid360_reference_value_availability_changed",
            extra_data={"active": value.active},
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "AutoGrid360 reference toggle failed admin_id=%s reference_value_id=%s",
            current_user.id,
            value.id,
        )
        flash("The reference value availability could not be changed.", "danger")
    else:
        logger.info(
            "AutoGrid360 reference availability changed admin_id=%s reference_value_id=%s active=%s",
            current_user.id,
            value.id,
            value.active,
        )
        flash(
            "The reference value is now available."
            if value.active
            else "The reference value is now disabled for future selection.",
            "success",
        )

    return redirect(url_for("autogrid360_reference.values", slug=_CATEGORY_TO_SLUG[category]))


@reference_bp.route("/makes/<int:make_id>/models", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], key_func=get_client_ip)
@login_required
def models(make_id):
    """List and create canonical vehicle models for one make."""

    require_autogrid360_admin()
    make = _make(make_id)
    form = VehicleModelForm()
    form.submit.label.text = "Add Model"

    if form.validate_on_submit():
        key = normalize_reference_key(form.label.data)
        existing = (
            VehicleModel.query.filter_by(make_id=make.id, key=key).one_or_none()
            if key
            else None
        )
        if not key:
            form.label.errors.append("That label does not produce a usable stable key.")
        elif existing is not None:
            form.label.errors.append(
                "A model with the same stable key already exists for this make."
            )
        elif _duplicate_model_label(make.id, form.label.data):
            form.label.errors.append("That model label already exists for this make.")
        else:
            model = VehicleModel(
                make_id=make.id,
                key=key,
                label=form.label.data,
                active=bool(form.active.data),
                sort_order=form.sort_order.data,
            )
            try:
                db.session.add(model)
                db.session.flush()
                _audit_model(model, action="autogrid360_vehicle_model_created")
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                logger.exception(
                    "AutoGrid360 vehicle model create failed admin_id=%s make_id=%s key=%s",
                    current_user.id,
                    make.id,
                    key,
                )
                flash("The vehicle model could not be created.", "danger")
            else:
                logger.info(
                    "AutoGrid360 vehicle model created admin_id=%s vehicle_model_id=%s make_id=%s key=%s",
                    current_user.id,
                    model.id,
                    make.id,
                    key,
                )
                flash("The vehicle model has been added.", "success")
                return redirect(
                    url_for("autogrid360_reference.models", make_id=make.id)
                )

    models = (
        VehicleModel.query.filter_by(make_id=make.id)
        .order_by(
            VehicleModel.sort_order.asc(),
            VehicleModel.label.asc(),
            VehicleModel.id.asc(),
        )
        .all()
    )
    return render_template(
        "autogrid360/admin/reference/models.html",
        make=make,
        models=models,
        form=form,
        toggle_form=VehicleModelToggleForm(),
        title=f"AutoGrid360 {make.label} Models",
    )


@reference_bp.route(
    "/makes/<int:make_id>/models/<int:model_id>/edit",
    methods=["GET", "POST"],
)
@limiter.limit("10 per minute", methods=["POST"], key_func=get_client_ip)
@login_required
def edit_model(make_id, model_id):
    """Edit presentation/order for one stable make-scoped model identity."""

    require_autogrid360_admin()
    make = _make(make_id)
    model = VehicleModel.query.filter_by(id=model_id, make_id=make.id).first_or_404()
    form = VehicleModelForm(obj=model)
    form.submit.label.text = "Save Changes"

    if form.validate_on_submit():
        if _duplicate_model_label(make.id, form.label.data, exclude_id=model.id):
            form.label.errors.append("That model label already exists for this make.")
        else:
            previous = {
                "label": model.label,
                "sort_order": model.sort_order,
                "active": model.active,
            }
            model.label = form.label.data
            model.sort_order = form.sort_order.data
            model.active = bool(form.active.data)
            changed_fields = sorted(
                field for field, old in previous.items() if old != getattr(model, field)
            )
            if changed_fields:
                try:
                    _audit_model(
                        model,
                        action="autogrid360_vehicle_model_updated",
                        extra_data={"changed_fields": changed_fields},
                    )
                    db.session.commit()
                except SQLAlchemyError:
                    db.session.rollback()
                    logger.exception(
                        "AutoGrid360 vehicle model edit failed admin_id=%s vehicle_model_id=%s",
                        current_user.id,
                        model.id,
                    )
                    flash("The vehicle model could not be updated.", "danger")
                else:
                    logger.info(
                        "AutoGrid360 vehicle model updated admin_id=%s vehicle_model_id=%s changed_fields=%s",
                        current_user.id,
                        model.id,
                        ",".join(changed_fields),
                    )
                    flash("The vehicle model has been updated.", "success")
                    return redirect(
                        url_for("autogrid360_reference.models", make_id=make.id)
                    )
            else:
                flash("No vehicle-model changes were made.", "success")
                return redirect(url_for("autogrid360_reference.models", make_id=make.id))

    return render_template(
        "autogrid360/admin/reference/model_edit.html",
        make=make,
        model=model,
        form=form,
        title=f"Edit {make.label} Model",
    )


@reference_bp.post("/makes/<int:make_id>/models/<int:model_id>/toggle")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def toggle_model(make_id, model_id):
    """Enable or disable one model while preserving existing vehicle references."""

    require_autogrid360_admin()
    make = _make(make_id)
    model = VehicleModel.query.filter_by(id=model_id, make_id=make.id).first_or_404()
    form = VehicleModelToggleForm()
    if not form.validate_on_submit():
        abort(400)

    model.active = not model.active
    try:
        _audit_model(
            model,
            action="autogrid360_vehicle_model_availability_changed",
            extra_data={"active": model.active},
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "AutoGrid360 vehicle model toggle failed admin_id=%s vehicle_model_id=%s",
            current_user.id,
            model.id,
        )
        flash("The vehicle model availability could not be changed.", "danger")
    else:
        logger.info(
            "AutoGrid360 vehicle model availability changed admin_id=%s vehicle_model_id=%s active=%s",
            current_user.id,
            model.id,
            model.active,
        )
        flash(
            "The vehicle model is now available."
            if model.active
            else "The vehicle model is now disabled for future selection.",
            "success",
        )

    return redirect(url_for("autogrid360_reference.models", make_id=make.id))
