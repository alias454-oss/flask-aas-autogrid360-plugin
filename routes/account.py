# app/plugins/autogrid360/routes/account.py
"""AutoGrid360-specific account routes."""

import logging
from pathlib import Path
import tempfile

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.core.auth import login_required
from app.core.extensions import db, limiter
from app.core.security import get_client_ip
from app.core.trackers import (
    audit_activity_enabled,
    log_action,
    log_action_isolated,
)
from app.plugins.autogrid360.forms.seller import SellerProfileForm
from app.plugins.autogrid360.forms.transfer import InventoryImportForm
from app.plugins.autogrid360.models import SellerProfile
from app.plugins.autogrid360.services.auth import is_autogrid360_admin
from app.plugins.autogrid360.services.settings import seller_inventory_import_allowed
from app.plugins.autogrid360.services.transfer import (
    InventoryBundleError,
    cleanup_restore_files,
    export_inventory_bundle,
    import_inventory_bundle,
    max_import_bundle_bytes,
    save_bundle_upload,
)


logger = logging.getLogger(__name__)

account_bp = Blueprint(
    "autogrid360_account",
    __name__,
    url_prefix="/autogrid360/account",
)

_PROFILE_FIELDS = (
    "display_name",
    "company_name",
)


@account_bp.route("/profile", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], key_func=get_client_ip)
@login_required
def profile():
    """Create or edit AutoGrid360-owned seller profile data for the current user."""

    seller_profile = SellerProfile.query.filter_by(user_id=current_user.id).one_or_none()
    form = SellerProfileForm(obj=seller_profile)

    if form.validate_on_submit():
        is_new = seller_profile is None
        if is_new:
            seller_profile = SellerProfile(user_id=current_user.id)
            db.session.add(seller_profile)

        previous = {
            field: getattr(seller_profile, field)
            for field in _PROFILE_FIELDS
        }
        for field in _PROFILE_FIELDS:
            setattr(seller_profile, field, getattr(form, field).data)

        changed_fields = sorted(
            field
            for field in _PROFILE_FIELDS
            if previous[field] != getattr(seller_profile, field)
        )

        try:
            db.session.flush()
            if audit_activity_enabled() and (is_new or changed_fields):
                log_action(
                    user_id=current_user.id,
                    action="autogrid360_seller_profile_updated",
                    target=f"seller_profile:{seller_profile.id}",
                    extra_data={
                        "seller_profile_id": seller_profile.id,
                        "created": is_new,
                        "changed_fields": changed_fields,
                    },
                )
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception(
                "AutoGrid360 seller profile update failed for user_id=%s",
                current_user.id,
            )
            flash("The seller profile could not be saved. Please try again.", "danger")
            return render_template(
                "autogrid360/account/profile.html",
                form=form,
                seller_profile=seller_profile,
                title="Seller Profile",
            )

        if is_new or changed_fields:
            logger.info(
                "AutoGrid360 seller profile updated user_id=%s seller_profile_id=%s created=%s changed_fields=%s",
                current_user.id,
                seller_profile.id,
                is_new,
                ",".join(changed_fields),
            )
        flash("Your AutoGrid360 seller profile has been saved.", "success")
        return redirect(url_for("autogrid360_account.profile"))

    return render_template(
        "autogrid360/account/profile.html",
        form=form,
        seller_profile=seller_profile,
        title="Seller Profile",
    )


@account_bp.get("/inventory-transfer")
@login_required
def inventory_transfer():
    """Show seller-owned canonical inventory backup/restore tools."""

    return render_template(
        "autogrid360/account/transfer.html",
        form=InventoryImportForm(),
        seller_import_allowed=(
            is_autogrid360_admin() or seller_inventory_import_allowed()
        ),
        title="Inventory Backup / Restore",
    )


@account_bp.get("/inventory-export")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def inventory_export():
    """Download all AutoGrid360 inventory owned by the current seller."""

    temporary = tempfile.NamedTemporaryFile(
        prefix="autogrid360-inventory-",
        suffix=".zip",
        delete=False,
    )
    temporary_path = Path(temporary.name)
    temporary.close()

    try:
        result = export_inventory_bundle(current_user, temporary_path)
    except (InventoryBundleError, OSError):
        temporary_path.unlink(missing_ok=True)
        logger.exception(
            "AutoGrid360 inventory export failed for user_id=%s",
            current_user.id,
        )
        flash("Your inventory could not be exported. Please try again.", "danger")
        return redirect(url_for("autogrid360_account.inventory_transfer"))

    if audit_activity_enabled():
        log_action_isolated(
            user_id=current_user.id,
            action="autogrid360_inventory_exported",
            target=f"seller:{current_user.id}",
            extra_data={
                "seller_id": current_user.id,
                "listing_count": result.listings_exported,
                "image_count": result.images_exported,
            },
        )

    safe_username = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in current_user.username
    ).strip("-") or "seller"
    response = send_file(
        temporary_path,
        as_attachment=True,
        download_name=f"autogrid360-inventory-{safe_username}.zip",
        mimetype="application/zip",
        conditional=False,
    )
    response.call_on_close(lambda: temporary_path.unlink(missing_ok=True))
    return response


@account_bp.post("/inventory-import")
@limiter.limit("3 per minute", key_func=get_client_ip)
@login_required
def inventory_import():
    """Restore one seller-scoped canonical bundle for the current seller."""

    if not (is_autogrid360_admin() or seller_inventory_import_allowed()):
        abort(403)

    request_limit = max_import_bundle_bytes() + (1024 * 1024)
    if request.content_length is not None and request.content_length > request_limit:
        flash("The uploaded inventory bundle is too large.", "danger")
        return redirect(url_for("autogrid360_account.inventory_transfer"))

    form = InventoryImportForm()
    if not form.validate_on_submit():
        for error in form.bundle.errors:
            flash(error, "danger")
        return redirect(url_for("autogrid360_account.inventory_transfer"))

    temporary = tempfile.NamedTemporaryFile(
        prefix="autogrid360-import-",
        suffix=".zip",
        delete=False,
    )
    temporary_path = Path(temporary.name)
    temporary.close()
    result = None

    try:
        save_bundle_upload(form.bundle.data, temporary_path)
        result = import_inventory_bundle(
            temporary_path,
            current_user,
            as_draft=bool(form.as_draft.data),
        )
        if audit_activity_enabled():
            log_action(
                user_id=current_user.id,
                action="autogrid360_inventory_imported",
                target=f"seller:{current_user.id}",
                extra_data={
                    "seller_id": current_user.id,
                    "source_seller_username": result.seller_mappings[0][0],
                    "listing_count": result.listings_imported,
                    "image_count": result.images_imported,
                    "seller_profile_created": bool(result.seller_profiles_created),
                    "as_draft": bool(form.as_draft.data),
                },
            )
        db.session.commit()
    except InventoryBundleError as exc:
        db.session.rollback()
        if result is not None:
            cleanup_restore_files(result)
        flash(str(exc), "danger")
        return redirect(url_for("autogrid360_account.inventory_transfer"))
    except (OSError, SQLAlchemyError):
        db.session.rollback()
        if result is not None:
            cleanup_restore_files(result)
        logger.exception(
            "AutoGrid360 inventory import failed for user_id=%s",
            current_user.id,
        )
        flash("The inventory bundle could not be imported. Please try again.", "danger")
        return redirect(url_for("autogrid360_account.inventory_transfer"))
    finally:
        temporary_path.unlink(missing_ok=True)

    logger.info(
        "AutoGrid360 inventory imported user_id=%s source_seller=%s listings=%s images=%s profile_created=%s",
        current_user.id,
        result.seller_mappings[0][0],
        result.listings_imported,
        result.images_imported,
        bool(result.seller_profiles_created),
    )
    flash(
        f"Restored {result.listings_imported} listing"
        f"{'s' if result.listings_imported != 1 else ''}"
        f"{' as Draft' if form.as_draft.data else ''}"
        f" with {result.images_imported} image"
        f"{'s' if result.images_imported != 1 else ''}.",
        "success",
    )
    return redirect(url_for("autogrid360_listings.mine"))
