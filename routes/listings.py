# app/plugins/autogrid360/routes/listings.py
"""Listing-management routes for AutoGrid360."""

import logging

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.core.auth import login_required
from app.core.extensions import db, limiter
from app.core.security import get_client_ip
from app.plugins.autogrid360.services.audit import audit_listing_action
from app.plugins.autogrid360.services.auth import (
    can_manage_listing,
    is_autogrid360_admin,
    require_autogrid360_admin,
)
from app.plugins.autogrid360.forms.admin import AssignSellerForm
from app.plugins.autogrid360.forms.images import ImageActionForm, ImageMoveForm, ImageUploadForm
from app.plugins.autogrid360.forms.listings import (
    AdminListingStatusForm,
    ApproveListingForm,
    DeleteListingForm,
    ExpireListingForm,
    ListingForm,
    MakeAvailableListingForm,
    MarkSalePendingListingForm,
    MarkSoldListingForm,
    RelistListingForm,
    RemoveListingForm,
    SubmitListingForm,
)
from app.plugins.autogrid360.services.location import (
    configure_location_form,
    listing_profile_location,
)
from app.plugins.autogrid360.services.lifecycle import (
    ListingTransitionError,
    admin_set_listing_status,
    approve_listing,
    expire_listing,
    make_listing_available,
    mark_expired_listing_edited,
    mark_sale_pending_listing,
    mark_sold_listing,
    relist_listing,
    remove_listing,
    return_public_listing_to_pending,
    submit_listing,
)
from app.plugins.autogrid360.services.media import delete_image_files
from app.plugins.autogrid360.services.notifications import notify_admin_listing_pending
from app.plugins.autogrid360.services.geo import apply_listing_form_location
from app.plugins.autogrid360.services.reference import (
    ReferenceDataError,
    apply_listing_references,
    configure_listing_form,
)
from app.plugins.autogrid360.services.settings import (
    listing_is_publicly_visible,
    listing_policy,
)
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_DRAFT,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_SALE_PENDING,
    STATUS_SOLD,
    Listing,
    Vehicle,
)


logger = logging.getLogger(__name__)

listings_bp = Blueprint(
    "autogrid360_listings",
    __name__,
    url_prefix="/autogrid360/listings",
)


_LISTING_EDIT_FIELDS = (
    "title",
    "price",
    "description",
    "country_code",
    "city",
    "zone_code",
    "postal_code",
)

_VEHICLE_EDIT_FIELDS = (
    "year",
    "make_id",
    "model_id",
    "model_text",
    "trim",
    "vehicle_type_id",
    "doors",
    "exterior_color",
    "mileage",
    "condition",
    "engine",
    "transmission",
    "drivetrain_id",
    "mpg",
    "fuel_type",
    "vin",
    "stock_number",
)

_VEHICLE_AUDIT_FIELD_NAMES = {
    "make_id": "make",
    "model_id": "model",
    "model_text": "model",
    "vehicle_type_id": "vehicle_type",
    "drivetrain_id": "drivetrain",
}



def _audit_status_change(listing: Listing, *, previous_status: str, new_status: str) -> None:
    """Queue one listing lifecycle audit event in the caller-owned transaction."""

    audit_listing_action(
        listing,
        action="autogrid360_listing_status_changed",
        extra_data={
            "previous_status": previous_status,
            "new_status": new_status,
        },
    )


def _commit_status_transition(
    listing: Listing,
    *,
    previous_status: str,
    operation: str,
    failure_message: str,
) -> bool:
    """Audit and commit one completed lifecycle transition."""

    _audit_status_change(
        listing,
        previous_status=previous_status,
        new_status=listing.status,
    )
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "AutoGrid360 lifecycle transaction failed operation=%s listing_id=%s actor_id=%s previous_status=%s new_status=%s",
            operation,
            listing.id,
            current_user.id,
            previous_status,
            listing.status,
        )
        flash(failure_message, "danger")
        return False

    return True


def _render_edit_listing(listing: Listing, form: ListingForm):
    """Render listing-content editing with its image-management controls."""

    return render_template(
        "autogrid360/listings/edit.html",
        form=form,
        listing=listing,
        can_manage_images=listing.status in {
            STATUS_DRAFT,
            STATUS_PENDING,
            STATUS_ACTIVE,
            STATUS_SALE_PENDING,
            STATUS_EXPIRED,
        },
        image_upload_form=ImageUploadForm(),
        image_action_form=ImageActionForm(),
        image_move_form=ImageMoveForm(),
        can_view_public=listing_is_publicly_visible(listing),
        title="Edit Listing",
    )



@listings_bp.get("/")
@login_required
def mine():
    """Show listings owned by the current Flask-AAS user."""

    listings = (
        Listing.query.filter_by(seller_id=current_user.id)
        .order_by(Listing.created_at.desc(), Listing.id.desc())
        .all()
    )
    return render_template(
        "autogrid360/listings/index.html",
        listings=listings,
        title="My Listings",
    )


@listings_bp.route("/create", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], key_func=get_client_ip)
@login_required
def create():
    """Create one draft listing and its vehicle record for the current user."""

    form = ListingForm()
    configure_listing_form(form)
    configure_location_form(form)
    profile_location = listing_profile_location(current_user)

    if form.validate_on_submit():
        vehicle = Vehicle(
            year=form.resolved_year,
            trim=form.trim.data,
            doors=form.resolved_doors,
            exterior_color=form.exterior_color.data,
            mileage=form.mileage.data,
            condition=form.condition.data,
            engine=form.engine.data,
            transmission=form.transmission.data,
            mpg=form.mpg.data,
            fuel_type=form.fuel_type.data,
            vin=form.vin.data,
            stock_number=form.stock_number.data,
        )
        try:
            apply_listing_references(vehicle, form)
        except ReferenceDataError as exc:
            flash(str(exc), "danger")
            return render_template(
                "autogrid360/listings/create.html",
                form=form,
                title="Create Listing",
                profile_location=profile_location,
            )
        listing = Listing(
            seller_id=current_user.id,
            vehicle=vehicle,
            title=form.title.data,
            price=form.price.data,
            description=form.description.data,
        )
        if not apply_listing_form_location(listing, form):
            return render_template(
                "autogrid360/listings/create.html",
                form=form,
                title="Create Listing",
                profile_location=profile_location,
            )

        try:
            db.session.add(listing)
            db.session.flush()
            audit_listing_action(
                listing,
                action="autogrid360_listing_created",
                extra_data={"vehicle_id": listing.vehicle_id},
            )
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception(
                "AutoGrid360 listing creation failed for user_id=%s",
                current_user.id,
            )
            flash("The listing could not be saved. Please try again.", "danger")
            return render_template(
                "autogrid360/listings/create.html",
                form=form,
                title="Create Listing",
                profile_location=profile_location,
            )

        logger.info(
            "AutoGrid360 listing created listing_id=%s seller_id=%s vehicle_id=%s",
            listing.id,
            listing.seller_id,
            listing.vehicle_id,
        )
        flash("Your listing draft has been saved.", "success")
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    return render_template(
        "autogrid360/listings/create.html",
        form=form,
        title="Create Listing",
        profile_location=profile_location,
    )


@listings_bp.route("/<int:listing_id>/edit", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], key_func=get_client_ip)
@login_required
def edit(listing_id):
    """Edit one manageable listing and its vehicle record."""

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    if not can_manage_listing(listing):
        abort(404)

    is_admin = is_autogrid360_admin()
    vehicle = listing.vehicle
    previous_status = listing.status

    form = ListingForm(obj=listing)
    configure_listing_form(form, vehicle=vehicle)
    configure_location_form(form)
    if not form.is_submitted():
        form.set_vehicle_year(vehicle.year)
        form.trim.data = vehicle.trim
        form.set_vehicle_doors(vehicle.doors)
        form.exterior_color.data = vehicle.exterior_color
        form.mileage.data = vehicle.mileage
        form.condition.data = vehicle.condition
        form.engine.data = vehicle.engine
        form.transmission.data = vehicle.transmission
        form.mpg.data = vehicle.mpg
        form.fuel_type.data = vehicle.fuel_type
        form.vin.data = vehicle.vin
        form.stock_number.data = vehicle.stock_number

    if form.validate_on_submit():
        listing_before = {
            field: getattr(listing, field)
            for field in _LISTING_EDIT_FIELDS
        }
        vehicle_before = {
            field: getattr(vehicle, field)
            for field in _VEHICLE_EDIT_FIELDS
        }
        feature_ids_before = tuple(sorted(item.id for item in vehicle.features))

        listing.title = form.title.data
        listing.price = form.price.data
        listing.description = form.description.data
        if not apply_listing_form_location(listing, form):
            db.session.rollback()
            return _render_edit_listing(listing, form)

        vehicle.year = form.resolved_year
        vehicle.trim = form.trim.data
        vehicle.doors = form.resolved_doors
        vehicle.exterior_color = form.exterior_color.data
        vehicle.mileage = form.mileage.data
        vehicle.condition = form.condition.data
        vehicle.engine = form.engine.data
        vehicle.transmission = form.transmission.data
        vehicle.mpg = form.mpg.data
        vehicle.fuel_type = form.fuel_type.data
        vehicle.vin = form.vin.data
        vehicle.stock_number = form.stock_number.data
        try:
            apply_listing_references(vehicle, form)
        except ReferenceDataError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return _render_edit_listing(listing, form)

        changed_fields = sorted(set(
            [
                f"listing.{field}"
                for field in _LISTING_EDIT_FIELDS
                if listing_before[field] != getattr(listing, field)
            ]
            + [
                f"vehicle.{_VEHICLE_AUDIT_FIELD_NAMES.get(field, field)}"
                for field in _VEHICLE_EDIT_FIELDS
                if vehicle_before[field] != getattr(vehicle, field)
            ]
            + (
                ["vehicle.features"]
                if feature_ids_before != tuple(sorted(item.id for item in vehicle.features))
                else []
            )
        ))
        policy = listing_policy()
        returned_to_review = False
        if changed_fields:
            if previous_status == STATUS_EXPIRED and not is_admin:
                mark_expired_listing_edited(listing)
            returned_to_review = return_public_listing_to_pending(
                listing,
                require_rereview=policy.rereview_active_edits and not is_admin,
            )
            audit_listing_action(
                listing,
                action="autogrid360_listing_edited",
                extra_data={
                    "changed_fields": changed_fields,
                    "returned_to_review": returned_to_review,
                },
            )
            if returned_to_review:
                _audit_status_change(
                    listing,
                    previous_status=previous_status,
                    new_status=STATUS_PENDING,
                )

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception(
                "AutoGrid360 listing edit failed for listing_id=%s user_id=%s",
                listing.id,
                current_user.id,
            )
            flash("The listing could not be updated. Please try again.", "danger")
            return _render_edit_listing(listing, form)

        if returned_to_review:
            notify_admin_listing_pending(
                listing,
                reason="seller edit requires re-review",
            )

        if changed_fields:
            logger.info(
                "AutoGrid360 listing edited listing_id=%s seller_id=%s changed_fields=%s returned_to_review=%s",
                listing.id,
                listing.seller_id,
                ",".join(changed_fields),
                returned_to_review,
            )

        if returned_to_review:
            flash(
                "The listing changes have been saved and returned for review.",
                "success",
            )
        else:
            flash("The listing changes have been saved.", "success")
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    return _render_edit_listing(listing, form)


@listings_bp.post("/<int:listing_id>/submit")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def submit(listing_id):
    """Submit one owned draft through the configured publication policy."""

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    if not can_manage_listing(listing):
        abort(404)
    form = SubmitListingForm()

    if not form.validate_on_submit():
        abort(400)

    policy = listing_policy()
    previous_status = listing.status
    try:
        submit_listing(
            listing,
            require_approval=policy.require_approval,
            expiration_days=policy.active_expiration_days,
        )
    except ListingTransitionError:
        abort(409)

    if not _commit_status_transition(
        listing,
        previous_status=previous_status,
        operation="submit",
        failure_message="The listing could not be submitted. Please try again.",
    ):
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    if listing.status == STATUS_PENDING and not is_autogrid360_admin():
        notify_admin_listing_pending(
            listing,
            reason="new listing submitted for approval",
        )

    logger.info(
        "AutoGrid360 listing submitted listing_id=%s seller_id=%s new_status=%s",
        listing.id,
        listing.seller_id,
        listing.status,
    )
    if listing.status == STATUS_PENDING:
        flash("Your listing has been submitted for approval.", "success")
    else:
        flash("Your listing has been published.", "success")
    return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))


@listings_bp.post("/<int:listing_id>/approve")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def approve(listing_id):
    """Approve one pending listing as the Flask-AAS system administrator."""

    require_autogrid360_admin()

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    form = ApproveListingForm()

    if not form.validate_on_submit():
        abort(400)

    policy = listing_policy()
    previous_status = listing.status
    try:
        approve_listing(
            listing,
            expiration_days=policy.active_expiration_days,
        )
    except ListingTransitionError:
        abort(409)

    if not _commit_status_transition(
        listing,
        previous_status=previous_status,
        operation="approve",
        failure_message="The listing could not be approved. Please try again.",
    ):
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    logger.info(
        "AutoGrid360 listing approved listing_id=%s seller_id=%s admin_id=%s",
        listing.id,
        listing.seller_id,
        current_user.id,
    )
    flash("The listing has been approved and activated.", "success")
    return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))


@listings_bp.post("/<int:listing_id>/relist")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def relist(listing_id):
    """Relist one owned expired listing through the current publication policy."""

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    if not can_manage_listing(listing):
        abort(404)

    form = RelistListingForm()
    if not form.validate_on_submit():
        abort(400)

    policy = listing_policy()
    previous_status = listing.status
    changed_since_expiration = listing.changed_since_expiration
    require_approval = policy.require_approval and not is_autogrid360_admin()
    try:
        relist_listing(
            listing,
            require_approval=require_approval,
            expiration_days=policy.active_expiration_days,
        )
    except ListingTransitionError:
        abort(409)

    if not _commit_status_transition(
        listing,
        previous_status=previous_status,
        operation="relist",
        failure_message="The listing could not be relisted. Please try again.",
    ):
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    if listing.status == STATUS_PENDING:
        notify_admin_listing_pending(
            listing,
            reason="changed expired listing submitted for re-approval",
        )

    logger.info(
        "AutoGrid360 listing relisted listing_id=%s seller_id=%s actor_id=%s changed_since_expiration=%s new_status=%s",
        listing.id,
        listing.seller_id,
        current_user.id,
        changed_since_expiration,
        listing.status,
    )
    if listing.status == STATUS_PENDING:
        flash(
            "The changed listing has been submitted for approval before relisting.",
            "success",
        )
    else:
        flash("The listing has been relisted.", "success")
    return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))


@listings_bp.post("/<int:listing_id>/sale-pending")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def mark_sale_pending(listing_id):
    """Mark one active/sold listing Sale Pending as owner or administrator."""

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    if not can_manage_listing(listing):
        abort(404)

    form = MarkSalePendingListingForm()
    if not form.validate_on_submit():
        abort(400)

    policy = listing_policy()
    previous_status = listing.status
    try:
        mark_sale_pending_listing(
            listing,
            expiration_days=policy.active_expiration_days,
        )
    except ListingTransitionError:
        abort(409)

    if not _commit_status_transition(
        listing,
        previous_status=previous_status,
        operation="mark_sale_pending",
        failure_message="The listing could not be marked Sale Pending. Please try again.",
    ):
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    flash("The listing has been marked Sale Pending.", "success")
    return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))


@listings_bp.post("/<int:listing_id>/available")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def make_available(listing_id):
    """Return one Sale Pending/sold listing to available Active status."""

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    if not can_manage_listing(listing):
        abort(404)

    form = MakeAvailableListingForm()
    if not form.validate_on_submit():
        abort(400)

    policy = listing_policy()
    previous_status = listing.status
    try:
        make_listing_available(
            listing,
            expiration_days=policy.active_expiration_days,
        )
    except ListingTransitionError:
        abort(409)

    if not _commit_status_transition(
        listing,
        previous_status=previous_status,
        operation="make_available",
        failure_message="The listing could not be made available. Please try again.",
    ):
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    flash("The listing is available again.", "success")
    return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))


@listings_bp.post("/<int:listing_id>/sold")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def mark_sold(listing_id):
    """Mark one available/Sale Pending listing sold as owner or administrator."""

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    if not can_manage_listing(listing):
        abort(404)

    form = MarkSoldListingForm()
    if not form.validate_on_submit():
        abort(400)

    previous_status = listing.status
    try:
        mark_sold_listing(listing)
    except ListingTransitionError:
        abort(409)
    if not _commit_status_transition(
        listing,
        previous_status=previous_status,
        operation="mark_sold",
        failure_message="The listing could not be marked sold. Please try again.",
    ):
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    logger.info(
        "AutoGrid360 listing marked sold listing_id=%s seller_id=%s actor_id=%s",
        listing.id,
        listing.seller_id,
        current_user.id,
    )
    flash("The listing has been marked sold.", "success")
    return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))


@listings_bp.post("/<int:listing_id>/expire")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def expire(listing_id):
    """Expire one Active or Sale Pending listing as the system administrator."""

    require_autogrid360_admin()

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    form = ExpireListingForm()
    if not form.validate_on_submit():
        abort(400)

    previous_status = listing.status
    try:
        expire_listing(listing)
    except ListingTransitionError:
        abort(409)

    if not _commit_status_transition(
        listing,
        previous_status=previous_status,
        operation="expire",
        failure_message="The listing could not be expired. Please try again.",
    ):
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    logger.info(
        "AutoGrid360 listing expired listing_id=%s seller_id=%s admin_id=%s",
        listing.id,
        listing.seller_id,
        current_user.id,
    )
    flash("The listing has been expired.", "success")
    return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))


@listings_bp.post("/<int:listing_id>/remove")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def remove(listing_id):
    """Remove one owned public/expired listing or an admin-managed listing."""

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    if not can_manage_listing(listing):
        abort(404)

    form = RemoveListingForm()
    if not form.validate_on_submit():
        abort(400)

    previous_status = listing.status
    try:
        remove_listing(listing)
    except ListingTransitionError:
        abort(409)

    if not _commit_status_transition(
        listing,
        previous_status=previous_status,
        operation="remove",
        failure_message="The listing could not be removed. Please try again.",
    ):
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    logger.info(
        "AutoGrid360 listing removed listing_id=%s seller_id=%s actor_id=%s previous_status=%s",
        listing.id,
        listing.seller_id,
        current_user.id,
        previous_status,
    )
    flash("The listing has been removed.", "success")
    return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))


@listings_bp.post("/<int:listing_id>/status")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def admin_status(listing_id):
    """Apply one explicit listing lifecycle state as the system administrator."""

    require_autogrid360_admin()

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    form = AdminListingStatusForm()
    if not form.validate_on_submit():
        abort(400)

    policy = listing_policy()
    previous_status = listing.status
    try:
        admin_set_listing_status(
            listing,
            form.status.data,
            expiration_days=policy.active_expiration_days,
        )
    except ListingTransitionError:
        abort(409)

    if listing.status == previous_status:
        flash("The listing is already in that status.", "success")
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    if not _commit_status_transition(
        listing,
        previous_status=previous_status,
        operation="admin_status",
        failure_message="The listing status could not be changed. Please try again.",
    ):
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    logger.info(
        "AutoGrid360 admin changed listing status listing_id=%s seller_id=%s admin_id=%s previous_status=%s new_status=%s",
        listing.id,
        listing.seller_id,
        current_user.id,
        previous_status,
        listing.status,
    )
    flash(f"Listing status changed to {listing.status.replace('_', ' ').title()}.", "success")
    return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))


@listings_bp.post("/<int:listing_id>/delete")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def delete(listing_id):
    """Delete one manageable draft/pending listing while preserving its vehicle."""

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    if not can_manage_listing(listing):
        abort(404)
    if listing.status not in {STATUS_DRAFT, STATUS_PENDING}:
        abort(409)

    form = DeleteListingForm()

    if not form.validate_on_submit():
        abort(400)

    listing_images = list(listing.images)
    listing_id_value = listing.id
    seller_id = listing.seller_id
    vehicle_id = listing.vehicle_id
    previous_status = listing.status

    try:
        audit_listing_action(
            listing,
            action="autogrid360_listing_deleted",
            extra_data={
                "vehicle_id": vehicle_id,
                "previous_status": previous_status,
                "image_count": len(listing_images),
            },
        )
        db.session.delete(listing)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "AutoGrid360 listing deletion failed for listing_id=%s user_id=%s",
            listing.id,
            current_user.id,
        )
        flash("The listing could not be deleted. Please try again.", "danger")
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    for image in listing_images:
        delete_image_files(image)

    logger.info(
        "AutoGrid360 listing deleted listing_id=%s seller_id=%s vehicle_id=%s previous_status=%s image_count=%s",
        listing_id_value,
        seller_id,
        vehicle_id,
        previous_status,
        len(listing_images),
    )
    flash("The listing has been deleted.", "success")
    if is_autogrid360_admin():
        return redirect(url_for("autogrid360_admin.inventory"))
    return redirect(url_for("autogrid360_listings.mine"))


@listings_bp.get("/<int:listing_id>")
@login_required
def detail(listing_id):
    """Show one listing to its owner or the Flask-AAS system administrator."""

    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    is_owner = listing.seller_id == current_user.id
    is_admin = is_autogrid360_admin()

    if not is_owner and not is_admin:
        abort(404)

    policy = listing_policy()
    submit_form = SubmitListingForm()
    submit_form.submit.label.text = (
        "Submit for Approval" if policy.require_approval else "Publish Listing"
    )

    return render_template(
        "autogrid360/listing.html",
        listing=listing,
        is_owner=is_owner,
        is_admin=is_admin,
        can_view_public=listing_is_publicly_visible(listing),
        can_submit=(is_owner or is_admin) and listing.status == STATUS_DRAFT,
        can_approve=is_admin and listing.status == STATUS_PENDING,
        can_mark_sale_pending=(is_owner or is_admin) and listing.status in {STATUS_ACTIVE, STATUS_SOLD},
        can_make_available=(is_owner or is_admin) and listing.status in {STATUS_SALE_PENDING, STATUS_SOLD},
        can_mark_sold=(is_owner or is_admin) and listing.status in {STATUS_ACTIVE, STATUS_SALE_PENDING},
        can_relist=(is_owner or is_admin) and listing.status == STATUS_EXPIRED,
        can_expire=is_admin and listing.status in {STATUS_ACTIVE, STATUS_SALE_PENDING},
        can_remove=(is_owner or is_admin) and listing.status in {
            STATUS_ACTIVE,
            STATUS_SALE_PENDING,
            STATUS_SOLD,
            STATUS_EXPIRED,
        },
        can_delete=(is_owner or is_admin) and listing.status in {STATUS_DRAFT, STATUS_PENDING},
        delete_form=DeleteListingForm(),
        submit_form=submit_form,
        approve_form=ApproveListingForm(),
        relist_form=RelistListingForm(),
        mark_sale_pending_form=MarkSalePendingListingForm(),
        make_available_form=MakeAvailableListingForm(),
        mark_sold_form=MarkSoldListingForm(),
        expire_form=ExpireListingForm(),
        remove_form=RemoveListingForm(),
        admin_status_form=AdminListingStatusForm(status=listing.status),
        assign_seller_form=AssignSellerForm(seller_username=listing.seller.username),
        primary_image=next((image for image in listing.images if image.is_primary), None),
        title=listing.title,
    )
