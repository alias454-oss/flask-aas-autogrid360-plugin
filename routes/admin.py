# app/plugins/autogrid360/routes/admin.py
"""AutoGrid360 administration and cross-seller inventory management."""

import logging
from pathlib import Path
import tempfile

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import or_
from sqlalchemy.orm import aliased
from sqlalchemy.exc import SQLAlchemyError

from app.core.auth import login_required
from app.core.extensions import db, limiter
from app.core.security import get_client_ip
from app.core.trackers import audit_activity_enabled, log_action, log_action_isolated
from app.models import User
from app.plugins.autogrid360.services.audit import audit_listing_action
from app.plugins.autogrid360.services.auth import (
    require_autogrid360_admin,
    user_by_username,
)
from app.plugins.autogrid360.forms.admin import AdminListingForm, AssignSellerForm
from app.plugins.autogrid360.forms.listings import ApproveListingForm
from app.plugins.autogrid360.forms.seller import SellerProfileForm
from app.plugins.autogrid360.forms.transfer import AdminInventoryRestoreForm
from app.plugins.autogrid360.services.geo import apply_listing_form_location
from app.plugins.autogrid360.services.location import configure_location_form
from app.plugins.autogrid360.services.transfer import (
    InventoryBundleError,
    cleanup_restore_files,
    export_site_inventory_bundle,
    inspect_inventory_bundle,
    max_import_bundle_bytes,
    parse_seller_mapping_entries,
    resolve_restore_seller_mapping,
    restore_inventory_bundle,
    save_bundle_upload,
)
from app.plugins.autogrid360.services.reference import (
    ReferenceDataError,
    apply_listing_references,
    configure_listing_form,
)
from app.plugins.autogrid360.models import (
    LISTING_STATUSES,
    STATUS_DRAFT,
    STATUS_PENDING,
    Listing,
    ReferenceValue,
    SellerProfile,
    Vehicle,
    VehicleModel,
)


logger = logging.getLogger(__name__)

admin_bp = Blueprint(
    "autogrid360_admin",
    __name__,
    url_prefix="/autogrid360/admin",
)

_PROFILE_FIELDS = (
    "display_name",
    "company_name",
)


def _contains(column, value: str):
    """Build a case-insensitive literal substring predicate."""

    escaped = (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return column.ilike(f"%{escaped}%", escape="\\")


def _status_counts() -> dict[str, int]:
    """Return listing counts keyed by lifecycle state."""

    counts = {status: 0 for status in LISTING_STATUSES}
    for status, count in (
        db.session.query(Listing.status, db.func.count(Listing.id))
        .group_by(Listing.status)
        .all()
    ):
        counts[status] = count
    return counts


def _seller_count() -> int:
    """Count users with either AutoGrid360 profile data or listing ownership."""

    seller_ids = (
        db.session.query(Listing.seller_id.label("user_id"))
        .union(db.session.query(SellerProfile.user_id.label("user_id")))
        .subquery()
    )
    return (
        db.session.query(db.func.count())
        .select_from(seller_ids)
        .scalar()
        or 0
    )


def _admin_per_page() -> int:
    """Return a bounded admin inventory pagination size."""

    return max(
        1,
        min(int(current_app.config.get("AUTOGRID360_ADMIN_LISTINGS_PER_PAGE", 25)), 100),
    )


def _inventory_query(
    *,
    forced_status: str | None = None,
    sort_order: str = "newest",
):
    """Build the admin inventory query and normalized filter state."""

    status = forced_status or (request.args.get("status") or "").strip().lower()
    if status not in LISTING_STATUSES:
        status = ""

    search = (request.args.get("q") or "").strip()[:120]
    seller = (request.args.get("seller") or "").strip()[:120]

    make_ref = aliased(ReferenceValue)
    query = (
        Listing.query
        .join(User, Listing.seller_id == User.id)
        .join(Vehicle, Listing.vehicle_id == Vehicle.id)
        .join(make_ref, Vehicle.make_id == make_ref.id)
    )
    if status:
        query = query.filter(Listing.status == status)
    if seller:
        query = query.filter(
            db.func.lower(User.username) == seller.lower()
        )
    if search:
        query = query.filter(
            or_(
                _contains(Listing.title, search),
                _contains(User.username, search),
                _contains(make_ref.label, search),
                _contains(Vehicle.model_text, search),
                Vehicle.model_ref.has(_contains(VehicleModel.label, search)),
            )
        )

    if sort_order == "oldest":
        query = query.order_by(Listing.created_at.asc(), Listing.id.asc())
    else:
        query = query.order_by(Listing.created_at.desc(), Listing.id.desc())

    return (
        query,
        {"status": status, "q": search, "seller": seller},
    )


def _inventory_url(
    filters: dict[str, str],
    *,
    sort_order: str,
    page: int | None = None,
) -> str:
    """Build an inventory URL while preserving active filters and sort order."""

    args = {key: value for key, value in filters.items() if value}
    if sort_order != "newest":
        args["sort"] = sort_order
    if page is not None:
        args["page"] = page
    return url_for("autogrid360_admin.inventory", **args)


def _inventory_sort_url(filters: dict[str, str], sort_order: str) -> str:
    """Build one inventory sort-toggle URL while preserving active filters."""

    args = {key: value for key, value in filters.items() if value}
    args["sort"] = sort_order
    return url_for("autogrid360_admin.inventory", **args)


def _pending_page_url(page: int, sort_order: str) -> str:
    return url_for(
        "autogrid360_admin.pending",
        page=page,
        sort=sort_order,
    )


def _render_inventory(
    *,
    forced_status: str | None = None,
    title: str = "Inventory",
    sort_order: str = "newest",
):
    page = request.args.get("page", default=1, type=int)
    if page is None or page < 1:
        page = 1

    query, filters = _inventory_query(
        forced_status=forced_status,
        sort_order=sort_order,
    )
    pagination = query.paginate(
        page=page,
        per_page=_admin_per_page(),
        error_out=False,
    )

    return render_template(
        "autogrid360/admin/listings.html",
        listings=pagination.items,
        pagination=pagination,
        filters=filters,
        status_counts=_status_counts(),
        previous_url=(
            (
                _pending_page_url(pagination.prev_num, sort_order)
                if forced_status == STATUS_PENDING
                else _inventory_url(
                    filters,
                    sort_order=sort_order,
                    page=pagination.prev_num,
                )
            )
            if pagination.has_prev
            else None
        ),
        next_url=(
            (
                _pending_page_url(pagination.next_num, sort_order)
                if forced_status == STATUS_PENDING
                else _inventory_url(
                    filters,
                    sort_order=sort_order,
                    page=pagination.next_num,
                )
            )
            if pagination.has_next
            else None
        ),
        submitted_sort_url=(
            url_for(
                "autogrid360_admin.pending",
                sort="newest" if sort_order == "oldest" else "oldest",
            )
            if forced_status == STATUS_PENDING
            else _inventory_sort_url(
                filters,
                "newest" if sort_order == "oldest" else "oldest",
            )
        ),
        forced_status=forced_status,
        sort_order=sort_order,
        approve_form=ApproveListingForm(),
        title=title,
    )


@admin_bp.get("/")
@login_required
def index():
    """Show the AutoGrid360 administration dashboard."""

    require_autogrid360_admin()
    counts = _status_counts()
    recent_pending = (
        Listing.query.filter_by(status=STATUS_PENDING)
        .order_by(Listing.created_at.asc(), Listing.id.asc())
        .limit(10)
        .all()
    )
    return render_template(
        "autogrid360/admin/index.html",
        counts=counts,
        people_count=User.query.count(),
        seller_count=_seller_count(),
        total_managed=sum(counts.values()),
        recent_pending=recent_pending,
        title="AutoGrid360 Administration",
    )


@admin_bp.get("/listings")
@login_required
def inventory():
    """Browse all AutoGrid360 inventory across sellers."""

    require_autogrid360_admin()
    sort_order = (request.args.get("sort") or "newest").strip().lower()
    if sort_order not in {"oldest", "newest"}:
        sort_order = "newest"

    return _render_inventory(sort_order=sort_order)


@admin_bp.get("/pending")
@login_required
def pending():
    """Show the administrator's pending-listing review queue."""

    require_autogrid360_admin()
    sort_order = (request.args.get("sort") or "oldest").strip().lower()
    if sort_order not in {"oldest", "newest"}:
        sort_order = "oldest"

    return _render_inventory(
        forced_status=STATUS_PENDING,
        title="Pending Review",
        sort_order=sort_order,
    )


@admin_bp.route("/listings/create", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], key_func=get_client_ip)
@login_required
def create_listing():
    """Create one AutoGrid360 draft on behalf of a selected Flask-AAS seller."""

    require_autogrid360_admin()
    form = AdminListingForm()
    configure_listing_form(form)
    configure_location_form(form)

    if form.validate_on_submit():
        seller = user_by_username(form.seller_username.data)
        if seller is None:
            form.seller_username.errors.append("No user has that username.")
        else:
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
                    "autogrid360/admin/listing_create.html",
                    form=form,
                    title="Create Seller Listing",
                )
            listing = Listing(
                seller_id=seller.id,
                vehicle=vehicle,
                title=form.title.data,
                price=form.price.data,
                description=form.description.data,
                status=STATUS_DRAFT,
            )
            if not apply_listing_form_location(listing, form):
                return render_template(
                    "autogrid360/admin/listing_create.html",
                    form=form,
                    title="Create Seller Listing",
                )
            try:
                db.session.add(listing)
                db.session.flush()
                audit_listing_action(
                    listing,
                    action="autogrid360_listing_created",
                    extra_data={
                        "vehicle_id": listing.vehicle_id,
                        "created_by_admin": True,
                    },
                )
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                logger.exception(
                    "AutoGrid360 admin listing creation failed admin_id=%s seller_id=%s",
                    current_user.id,
                    seller.id,
                )
                flash("The listing could not be created. Please try again.", "danger")
            else:
                logger.info(
                    "AutoGrid360 admin created listing listing_id=%s admin_id=%s seller_id=%s vehicle_id=%s",
                    listing.id,
                    current_user.id,
                    seller.id,
                    listing.vehicle_id,
                )
                flash("The seller listing draft has been created.", "success")
                return redirect(
                    url_for("autogrid360_listings.detail", listing_id=listing.id)
                )

    return render_template(
        "autogrid360/admin/listing_create.html",
        form=form,
        title="Create Seller Listing",
    )


@admin_bp.post("/listings/<int:listing_id>/seller")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def assign_seller(listing_id):
    """Assign one existing listing to another canonical Flask-AAS user."""

    require_autogrid360_admin()
    listing = Listing.query.filter_by(id=listing_id).first_or_404()
    form = AssignSellerForm()
    if not form.validate_on_submit():
        abort(400)

    seller = user_by_username(form.seller_username.data)
    if seller is None:
        flash("No user has that username.", "danger")
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    previous_seller_id = listing.seller_id
    if previous_seller_id == seller.id:
        flash("That user already owns this listing.", "success")
        return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))

    listing.seller_id = seller.id
    try:
        audit_listing_action(
            listing,
            action="autogrid360_listing_seller_changed",
            extra_data={
                "previous_seller_id": previous_seller_id,
                "new_seller_id": seller.id,
            },
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "AutoGrid360 seller reassignment failed listing_id=%s admin_id=%s previous_seller_id=%s new_seller_id=%s",
            listing.id,
            current_user.id,
            previous_seller_id,
            seller.id,
        )
        flash("The listing seller could not be changed.", "danger")
    else:
        logger.info(
            "AutoGrid360 listing seller changed listing_id=%s admin_id=%s previous_seller_id=%s new_seller_id=%s",
            listing.id,
            current_user.id,
            previous_seller_id,
            seller.id,
        )
        flash("The listing seller has been changed.", "success")

    return redirect(url_for("autogrid360_listings.detail", listing_id=listing.id))


@admin_bp.get("/sellers")
@login_required
def sellers():
    """Browse users who own AutoGrid360 profile data or inventory."""

    require_autogrid360_admin()
    page = request.args.get("page", default=1, type=int)
    if page is None or page < 1:
        page = 1
    search = (request.args.get("q") or "").strip()[:120]

    seller_ids = (
        db.session.query(Listing.seller_id.label("user_id"))
        .union(db.session.query(SellerProfile.user_id.label("user_id")))
        .subquery()
    )
    query = User.query.join(seller_ids, seller_ids.c.user_id == User.id)
    if search:
        profile_match = (
            db.session.query(SellerProfile.user_id)
            .filter(
                or_(
                    _contains(SellerProfile.display_name, search),
                    _contains(SellerProfile.company_name, search),
                )
            )
        )
        query = query.filter(
            or_(
                _contains(User.username, search),
                User.id.in_(profile_match),
            )
        )

    pagination = (
        query.order_by(User.username.asc(), User.id.asc())
        .paginate(page=page, per_page=_admin_per_page(), error_out=False)
    )
    user_ids = [user.id for user in pagination.items]
    profiles = {
        profile.user_id: profile
        for profile in SellerProfile.query.filter(SellerProfile.user_id.in_(user_ids)).all()
    } if user_ids else {}
    listing_counts = {
        seller_id: count
        for seller_id, count in (
            db.session.query(Listing.seller_id, db.func.count(Listing.id))
            .filter(Listing.seller_id.in_(user_ids))
            .group_by(Listing.seller_id)
            .all()
        )
    } if user_ids else {}

    return render_template(
        "autogrid360/admin/sellers.html",
        sellers=pagination.items,
        profiles=profiles,
        listing_counts=listing_counts,
        pagination=pagination,
        search=search,
        title="AutoGrid360 Sellers",
    )


@admin_bp.route("/sellers/<int:user_id>/profile", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], key_func=get_client_ip)
@login_required
def seller_profile(user_id):
    """Manage AutoGrid360-owned profile data for one Flask-AAS user."""

    require_autogrid360_admin()
    seller = User.query.filter_by(id=user_id).first_or_404()
    profile = SellerProfile.query.filter_by(user_id=seller.id).one_or_none()
    form = SellerProfileForm(obj=profile)
    form.submit.label.text = "Save Seller Profile"

    if form.validate_on_submit():
        is_new = profile is None
        if is_new:
            profile = SellerProfile(user_id=seller.id)
            db.session.add(profile)

        previous = {field: getattr(profile, field) for field in _PROFILE_FIELDS}
        for field in _PROFILE_FIELDS:
            setattr(profile, field, getattr(form, field).data)
        changed_fields = sorted(
            field
            for field in _PROFILE_FIELDS
            if previous[field] != getattr(profile, field)
        )

        try:
            db.session.flush()
            if audit_activity_enabled() and (is_new or changed_fields):
                log_action(
                    user_id=current_user.id,
                    action="autogrid360_seller_profile_updated",
                    target=f"seller_profile:{profile.id}",
                    extra_data={
                        "seller_profile_id": profile.id,
                        "seller_user_id": seller.id,
                        "created": is_new,
                        "changed_fields": changed_fields,
                        "updated_by_admin": True,
                    },
                )
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception(
                "AutoGrid360 admin seller profile update failed admin_id=%s seller_id=%s",
                current_user.id,
                seller.id,
            )
            flash("The seller profile could not be saved. Please try again.", "danger")
        else:
            if is_new or changed_fields:
                logger.info(
                    "AutoGrid360 admin seller profile updated admin_id=%s seller_id=%s profile_id=%s created=%s changed_fields=%s",
                    current_user.id,
                    seller.id,
                    profile.id,
                    is_new,
                    ",".join(changed_fields),
                )
            flash("The AutoGrid360 seller profile has been saved.", "success")
            return redirect(
                url_for("autogrid360_admin.seller_profile", user_id=seller.id)
            )

    return render_template(
        "autogrid360/admin/seller_profile.html",
        seller=seller,
        seller_profile=profile,
        form=form,
        title=f"Seller: {seller.username}",
    )


@admin_bp.get("/backup-restore")
@login_required
def backup_restore():
    """Show full AutoGrid360 administrator backup/restore tools."""

    require_autogrid360_admin()
    return render_template(
        "autogrid360/admin/transfer.html",
        form=AdminInventoryRestoreForm(),
        title="AutoGrid360 Backup / Restore",
    )


@admin_bp.get("/inventory-export-all")
@limiter.limit("10 per minute", key_func=get_client_ip)
@login_required
def inventory_export_all():
    """Download one full AutoGrid360 seller/profile/inventory backup."""

    require_autogrid360_admin()
    temporary = tempfile.NamedTemporaryFile(
        prefix="autogrid360-site-backup-",
        suffix=".zip",
        delete=False,
    )
    temporary_path = Path(temporary.name)
    temporary.close()

    try:
        result = export_site_inventory_bundle(temporary_path)
    except (InventoryBundleError, OSError):
        temporary_path.unlink(missing_ok=True)
        logger.exception(
            "AutoGrid360 full inventory backup failed admin_id=%s",
            current_user.id,
        )
        flash("The AutoGrid360 backup could not be created.", "danger")
        return redirect(url_for("autogrid360_admin.backup_restore"))

    if audit_activity_enabled():
        log_action_isolated(
            user_id=current_user.id,
            action="autogrid360_inventory_full_exported",
            target="autogrid360_inventory:all",
            extra_data={
                "seller_count": result.sellers_exported,
                "listing_count": result.listings_exported,
                "image_count": result.images_exported,
            },
        )

    response = send_file(
        temporary_path,
        as_attachment=True,
        download_name="autogrid360-site-backup.zip",
        mimetype="application/zip",
        conditional=False,
    )
    response.call_on_close(lambda: temporary_path.unlink(missing_ok=True))
    return response


@admin_bp.post("/inventory-restore")
@limiter.limit("3 per minute", key_func=get_client_ip)
@login_required
def inventory_restore():
    """Restore one seller or full-site AutoGrid360 backup as administrator."""

    require_autogrid360_admin()
    request_limit = max_import_bundle_bytes() + (1024 * 1024)
    if request.content_length is not None and request.content_length > request_limit:
        flash("The uploaded AutoGrid360 backup is too large.", "danger")
        return redirect(url_for("autogrid360_admin.backup_restore"))

    form = AdminInventoryRestoreForm()
    if not form.validate_on_submit():
        for field in (form.bundle, form.seller_mapping):
            for error in field.errors:
                flash(error, "danger")
        return redirect(url_for("autogrid360_admin.backup_restore"))

    temporary = tempfile.NamedTemporaryFile(
        prefix="autogrid360-site-restore-",
        suffix=".zip",
        delete=False,
    )
    temporary_path = Path(temporary.name)
    temporary.close()
    result = None

    try:
        save_bundle_upload(form.bundle.data, temporary_path)
        validated = inspect_inventory_bundle(temporary_path)
        overrides = parse_seller_mapping_entries(
            (form.seller_mapping.data or "").splitlines()
        )
        seller_mapping = resolve_restore_seller_mapping(validated, overrides)
        result = restore_inventory_bundle(
            temporary_path,
            seller_mapping,
            as_draft=bool(form.as_draft.data),
        )
        if audit_activity_enabled():
            log_action(
                user_id=current_user.id,
                action="autogrid360_inventory_restored_admin",
                target="autogrid360_inventory:restore",
                extra_data={
                    "seller_count": result.sellers_restored,
                    "listing_count": result.listings_imported,
                    "image_count": result.images_imported,
                    "seller_profiles_created": result.seller_profiles_created,
                    "as_draft": bool(form.as_draft.data),
                    "seller_mappings": [
                        {"source": source, "destination": destination}
                        for source, destination in result.seller_mappings
                    ],
                },
            )
        db.session.commit()
    except InventoryBundleError as exc:
        db.session.rollback()
        if result is not None:
            cleanup_restore_files(result)
        flash(str(exc), "danger")
        return redirect(url_for("autogrid360_admin.backup_restore"))
    except (OSError, SQLAlchemyError):
        db.session.rollback()
        if result is not None:
            cleanup_restore_files(result)
        logger.exception(
            "AutoGrid360 full inventory restore failed admin_id=%s",
            current_user.id,
        )
        flash("The AutoGrid360 backup could not be restored.", "danger")
        return redirect(url_for("autogrid360_admin.backup_restore"))
    finally:
        temporary_path.unlink(missing_ok=True)

    logger.info(
        "AutoGrid360 inventory restore completed admin_id=%s sellers=%s listings=%s images=%s as_draft=%s",
        current_user.id,
        result.sellers_restored,
        result.listings_imported,
        result.images_imported,
        bool(form.as_draft.data),
    )
    flash(
        f"Restored {result.listings_imported} listing"
        f"{'s' if result.listings_imported != 1 else ''} across "
        f"{result.sellers_restored} seller"
        f"{'s' if result.sellers_restored != 1 else ''} with "
        f"{result.images_imported} image"
        f"{'s' if result.images_imported != 1 else ''}.",
        "success",
    )
    return redirect(url_for("autogrid360_admin.backup_restore"))
