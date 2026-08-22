# app/plugins/autogrid360/routes/images.py
"""Listing-image management routes for AutoGrid360."""

import logging
from pathlib import Path

from flask import Blueprint, abort, flash, redirect, request, send_file, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import lazyload, load_only
from werkzeug.utils import secure_filename

from app.core.auth import login_required
from app.core.extensions import db, limiter
from app.core.security import get_client_ip
from app.core.sessions import session_activity_exempt
from app.plugins.autogrid360.services.audit import (
    audit_listing_action,
    audit_listing_image_read,
)
from app.plugins.autogrid360.services.auth import can_manage_listing, is_autogrid360_admin
from app.plugins.autogrid360.forms.images import (
    ImageActionForm,
    ImageMoveForm,
    ImageUploadForm,
)
from app.plugins.autogrid360.services.lifecycle import (
    mark_expired_listing_edited,
    return_public_listing_to_pending,
)
from app.plugins.autogrid360.services.media import (
    ImageUploadError,
    delete_image_files,
    image_path,
    max_listing_images,
    max_upload_request_bytes,
    store_listing_image,
)
from app.plugins.autogrid360.services.notifications import notify_admin_listing_pending
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_DRAFT,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_SALE_PENDING,
    Listing,
    ListingImage,
)
from app.plugins.autogrid360.services.settings import (
    listing_is_publicly_visible,
    listing_policy,
)


logger = logging.getLogger(__name__)

images_bp = Blueprint(
    "autogrid360_images",
    __name__,
    url_prefix="/autogrid360/listings",
)

IMAGE_EDITABLE_STATUSES = frozenset(
    {STATUS_DRAFT, STATUS_PENDING, STATUS_ACTIVE, STATUS_SALE_PENDING, STATUS_EXPIRED}
)


def _manageable_listing(listing_id: int) -> Listing:
    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    if not can_manage_listing(listing):
        abort(404)
    return listing


def _manageable_image(listing_id: int, image_id: int) -> tuple[Listing, ListingImage]:
    listing = _manageable_listing(listing_id)
    image = ListingImage.query.filter_by(
        id=image_id,
        listing_id=listing.id,
    ).first_or_404()
    return listing, image


def _ensure_images_editable(listing: Listing) -> None:
    if listing.status not in IMAGE_EDITABLE_STATUSES:
        abort(409)


def _apply_rereview_policy(listing: Listing) -> bool:
    """Apply lifecycle effects for one meaningful listing-image mutation."""

    previous_status = listing.status
    if listing.status == STATUS_EXPIRED:
        if not is_autogrid360_admin():
            mark_expired_listing_edited(listing)
        return False
    transitioned = return_public_listing_to_pending(
        listing,
        require_rereview=listing_policy().rereview_active_edits and not is_autogrid360_admin(),
    )
    if transitioned:
        audit_listing_action(
            listing,
            action="autogrid360_listing_status_changed",
            extra_data={
                "previous_status": previous_status,
                "new_status": STATUS_PENDING,
            },
        )
    return transitioned


def _notify_pending_after_image_change(listing: Listing, transitioned: bool) -> None:
    """Notify the administrator only after a committed image re-review transition."""

    if transitioned:
        notify_admin_listing_pending(
            listing,
            reason="listing image change requires re-review",
        )


def _renumber_images(listing: Listing) -> None:
    for position, image in enumerate(
        sorted(listing.images, key=lambda item: (item.position, item.id or 0))
    ):
        image.position = position


def _edit_redirect(listing: Listing):
    """Return image-management actions to the listing-content editor."""

    return redirect(url_for("autogrid360_listings.edit", listing_id=listing.id))


@images_bp.post("/<int:listing_id>/images")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def upload(listing_id):
    """Upload normalized images for one listing owned by the current user or managed by the system administrator."""

    listing = _manageable_listing(listing_id)
    _ensure_images_editable(listing)
    if (
        request.content_length is not None
        and request.content_length > max_upload_request_bytes()
    ):
        abort(413)
    form = ImageUploadForm()
    if not form.validate_on_submit():
        abort(400)

    uploads = [upload for upload in form.images.data if upload and upload.filename]
    if not uploads:
        abort(400)

    current_count = len(listing.images)
    maximum = max_listing_images()
    if current_count + len(uploads) > maximum:
        flash(f"A listing may have at most {maximum} images.", "danger")
        return _edit_redirect(listing)

    created_images = []
    stored_images = []
    try:
        for offset, upload_file in enumerate(uploads):
            stored = store_listing_image(listing.id, upload_file)
            image = ListingImage(
                listing=listing,
                original_filename=secure_filename(Path(upload_file.filename).name)[:255] or None,
                position=current_count + offset,
                is_primary=current_count == 0 and offset == 0,
                **stored,
            )
            stored_images.append(image)
            created_images.append(image)
            db.session.add(image)

        returned_to_review = _apply_rereview_policy(listing)
        db.session.flush()
        audit_listing_action(
            listing,
            action="autogrid360_listing_images_uploaded",
            extra_data={
                "image_ids": [image.id for image in created_images],
                "image_count": len(created_images),
                "returned_to_review": returned_to_review,
            },
        )
        db.session.commit()
    except ImageUploadError as exc:
        db.session.rollback()
        for image in stored_images:
            delete_image_files(image)
        flash(str(exc), "danger")
        return _edit_redirect(listing)
    except (OSError, SQLAlchemyError):
        db.session.rollback()
        for image in stored_images:
            delete_image_files(image)
        logger.exception(
            "AutoGrid360 listing image upload failed for listing_id=%s user_id=%s",
            listing.id,
            current_user.id,
        )
        flash("The images could not be saved. Please try again.", "danger")
        return _edit_redirect(listing)

    _notify_pending_after_image_change(listing, returned_to_review)
    logger.info(
        "AutoGrid360 listing images uploaded listing_id=%s seller_id=%s image_ids=%s returned_to_review=%s",
        listing.id,
        listing.seller_id,
        ",".join(str(image.id) for image in created_images),
        returned_to_review,
    )
    flash(
        f"Uploaded {len(created_images)} listing image"
        f"{'s' if len(created_images) != 1 else ''}.",
        "success",
    )
    return _edit_redirect(listing)


@images_bp.post("/<int:listing_id>/images/<int:image_id>/primary")
@limiter.limit("30 per minute", key_func=get_client_ip)
@login_required
def primary(listing_id, image_id):
    """Select one owned listing image as the primary image."""

    listing, image = _manageable_image(listing_id, image_id)
    _ensure_images_editable(listing)
    form = ImageActionForm()
    if not form.validate_on_submit():
        abort(400)

    if image.is_primary:
        flash("That image is already the primary image.", "success")
        return _edit_redirect(listing)

    previous_primary = next(
        (existing for existing in listing.images if existing.is_primary),
        None,
    )
    for existing in listing.images:
        existing.is_primary = existing.id == image.id
    returned_to_review = _apply_rereview_policy(listing)
    audit_listing_action(
        listing,
        action="autogrid360_listing_primary_image_changed",
        extra_data={
            "image_id": image.id,
            "previous_primary_image_id": (
                previous_primary.id if previous_primary is not None else None
            ),
            "returned_to_review": returned_to_review,
        },
    )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "AutoGrid360 primary image update failed for listing_id=%s image_id=%s",
            listing.id,
            image.id,
        )
        flash("The primary image could not be changed.", "danger")
        return _edit_redirect(listing)

    _notify_pending_after_image_change(listing, returned_to_review)
    logger.info(
        "AutoGrid360 primary image changed listing_id=%s seller_id=%s image_id=%s previous_primary_image_id=%s returned_to_review=%s",
        listing.id,
        listing.seller_id,
        image.id,
        previous_primary.id if previous_primary is not None else None,
        returned_to_review,
    )
    flash("Primary image updated.", "success")
    return _edit_redirect(listing)


@images_bp.post("/<int:listing_id>/images/<int:image_id>/move")
@limiter.limit("30 per minute", key_func=get_client_ip)
@login_required
def move(listing_id, image_id):
    """Move one owned listing image one position up or down."""

    listing, image = _manageable_image(listing_id, image_id)
    _ensure_images_editable(listing)
    form = ImageMoveForm()
    if not form.validate_on_submit():
        abort(400)

    ordered = sorted(listing.images, key=lambda item: (item.position, item.id or 0))
    current_index = next(index for index, item in enumerate(ordered) if item.id == image.id)
    target_index = current_index - 1 if form.direction.data == "up" else current_index + 1
    if target_index < 0 or target_index >= len(ordered):
        return _edit_redirect(listing)

    previous_position = image.position
    ordered[current_index], ordered[target_index] = ordered[target_index], ordered[current_index]
    for position, item in enumerate(ordered):
        item.position = position
    returned_to_review = _apply_rereview_policy(listing)
    audit_listing_action(
        listing,
        action="autogrid360_listing_image_reordered",
        extra_data={
            "image_id": image.id,
            "previous_position": previous_position,
            "new_position": target_index,
            "returned_to_review": returned_to_review,
        },
    )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "AutoGrid360 listing image reorder failed for listing_id=%s image_id=%s",
            listing.id,
            image.id,
        )
        flash("The image order could not be changed.", "danger")
        return _edit_redirect(listing)

    _notify_pending_after_image_change(listing, returned_to_review)
    logger.info(
        "AutoGrid360 listing image reordered listing_id=%s seller_id=%s image_id=%s previous_position=%s new_position=%s returned_to_review=%s",
        listing.id,
        listing.seller_id,
        image.id,
        previous_position,
        target_index,
        returned_to_review,
    )
    flash("Image order updated.", "success")
    return _edit_redirect(listing)


@images_bp.post("/<int:listing_id>/images/<int:image_id>/delete")
@limiter.limit("20 per minute", key_func=get_client_ip)
@login_required
def delete(listing_id, image_id):
    """Delete one owned listing image and its stored files."""

    listing, image = _manageable_image(listing_id, image_id)
    _ensure_images_editable(listing)
    form = ImageActionForm()
    if not form.validate_on_submit():
        abort(400)

    image_id_value = image.id
    deleted_was_primary = image.is_primary
    listing.images.remove(image)
    remaining = list(listing.images)
    new_primary = None
    if deleted_was_primary and remaining:
        new_primary = min(remaining, key=lambda item: (item.position, item.id or 0))
        new_primary.is_primary = True
    _renumber_images(listing)
    returned_to_review = _apply_rereview_policy(listing)
    audit_listing_action(
        listing,
        action="autogrid360_listing_image_deleted",
        extra_data={
            "image_id": image_id_value,
            "was_primary": deleted_was_primary,
            "new_primary_image_id": new_primary.id if new_primary is not None else None,
            "returned_to_review": returned_to_review,
        },
    )

    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "AutoGrid360 listing image deletion failed for listing_id=%s image_id=%s",
            listing.id,
            image.id,
        )
        flash("The image could not be deleted.", "danger")
        return _edit_redirect(listing)

    delete_image_files(image)
    _notify_pending_after_image_change(listing, returned_to_review)
    logger.info(
        "AutoGrid360 listing image deleted listing_id=%s seller_id=%s image_id=%s was_primary=%s new_primary_image_id=%s returned_to_review=%s",
        listing.id,
        listing.seller_id,
        image_id_value,
        deleted_was_primary,
        new_primary.id if new_primary is not None else None,
        returned_to_review,
    )
    flash("Listing image deleted.", "success")
    return _edit_redirect(listing)


@images_bp.get("/<int:listing_id>/images/<int:image_id>/<variant>")
@session_activity_exempt
def file(listing_id, image_id, variant):
    """Serve a normalized listing image to an authorized viewer."""

    record = (
        db.session.query(Listing, ListingImage)
        .join(ListingImage, ListingImage.listing_id == Listing.id)
        .options(
            load_only(Listing.id, Listing.seller_id, Listing.status),
            lazyload(Listing.vehicle),
        )
        .filter(
            Listing.id == listing_id,
            ListingImage.id == image_id,
        )
        .first()
    )
    if record is None:
        abort(404)
    listing, image = record

    if not listing_is_publicly_visible(listing):
        if not current_user.is_authenticated:
            abort(404)
        if not can_manage_listing(listing):
            abort(404)

    if variant == "display":
        storage_key = image.storage_key
    elif variant == "thumb":
        storage_key = image.thumbnail_key
    else:
        abort(404)

    path = image_path(storage_key)
    if not path.is_file():
        abort(404)

    audit_listing_image_read(listing, image, variant=variant)

    return send_file(
        path,
        mimetype="image/jpeg",
        conditional=True,
        etag=True,
    )
