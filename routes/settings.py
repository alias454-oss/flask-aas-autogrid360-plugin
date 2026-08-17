# app/plugins/autogrid360/routes/settings.py
"""AutoGrid360 site-policy administration routes."""

from datetime import datetime, timezone
import logging

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from app.core.auth import login_required
from app.core.extensions import db, limiter
from app.core.security import get_client_ip
from app.core.trackers import audit_activity_enabled, log_action
from app.plugins.autogrid360.services.auth import require_autogrid360_admin
from app.plugins.autogrid360.forms.settings import (
    ExpireDueListingsForm,
    AutoGrid360SettingsForm,
)
from app.plugins.autogrid360.services.lifecycle import expire_due_listings
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_SALE_PENDING,
    Listing,
    AutoGrid360Settings,
)
from app.plugins.autogrid360.services.settings import (
    SETTINGS_ROW_ID,
    currency_policy,
    distance_policy,
    listing_images_path,
    listing_policy,
    seller_inventory_import_allowed,
)


logger = logging.getLogger(__name__)

settings_bp = Blueprint(
    "autogrid360_settings",
    __name__,
    url_prefix="/autogrid360/admin",
)


@settings_bp.route("/settings", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"], key_func=get_client_ip)
@login_required
def settings():
    """View or update site-wide AutoGrid360 listing publication settings."""

    require_autogrid360_admin()
    persisted = db.session.get(AutoGrid360Settings, SETTINGS_ROW_ID)
    effective = listing_policy()
    effective_currency = currency_policy()
    effective_distance = distance_policy()
    effective_listing_images_path = listing_images_path()
    effective_seller_import = seller_inventory_import_allowed()
    form = AutoGrid360SettingsForm(obj=persisted)

    if not form.is_submitted() and persisted is None:
        form.require_listing_approval.data = effective.require_approval
        form.require_rereview_on_edit.data = effective.require_rereview
        form.enable_listing_expiration.data = effective.expiration_enabled
        form.listing_expiration_days.data = effective.expiration_days
        form.expiration_warning_days.data = effective.expiration_warning_days
        form.expired_retention_days.data = effective.expired_retention_days
        form.expired_removal_warning_days.data = effective.expired_removal_warning_days
        form.show_sale_pending_listings_publicly.data = effective.show_sale_pending_publicly
        form.show_sold_listings_publicly.data = effective.show_sold_publicly
        form.sold_retention_days.data = effective.sold_retention_days
        form.currency_code.data = effective_currency.code
        form.currency_symbol.data = effective_currency.symbol
        form.currency_decimal_separator.data = (
            effective_currency.decimal_separator
        )
        form.currency_thousands_separator.data = (
            effective_currency.thousands_separator
        )
        form.default_distance_unit.data = effective_distance.default_unit
        form.listing_images_path.data = effective_listing_images_path
        form.allow_seller_inventory_import.data = effective_seller_import

    if form.validate_on_submit():
        previous = {
            "require_listing_approval": effective.require_approval,
            "require_rereview_on_edit": effective.require_rereview,
            "enable_listing_expiration": effective.expiration_enabled,
            "listing_expiration_days": effective.expiration_days,
            "expiration_warning_days": effective.expiration_warning_days,
            "expired_retention_days": effective.expired_retention_days,
            "expired_removal_warning_days": effective.expired_removal_warning_days,
            "show_sale_pending_listings_publicly": effective.show_sale_pending_publicly,
            "show_sold_listings_publicly": effective.show_sold_publicly,
            "sold_retention_days": effective.sold_retention_days,
            "currency_code": effective_currency.code,
            "currency_symbol": effective_currency.symbol,
            "currency_decimal_separator": effective_currency.decimal_separator,
            "currency_thousands_separator": (
                effective_currency.thousands_separator
            ),
            "default_distance_unit": effective_distance.default_unit,
            "listing_images_path": effective_listing_images_path,
            "allow_seller_inventory_import": effective_seller_import,
        }
        if persisted is None:
            persisted = AutoGrid360Settings(id=SETTINGS_ROW_ID)
            db.session.add(persisted)

        persisted.require_listing_approval = bool(form.require_listing_approval.data)
        persisted.require_rereview_on_edit = bool(form.require_rereview_on_edit.data)
        persisted.enable_listing_expiration = bool(form.enable_listing_expiration.data)
        persisted.listing_expiration_days = int(form.listing_expiration_days.data)
        persisted.expiration_warning_days = int(form.expiration_warning_days.data)
        persisted.expired_retention_days = int(form.expired_retention_days.data)
        persisted.expired_removal_warning_days = int(
            form.expired_removal_warning_days.data
        )
        persisted.show_sale_pending_listings_publicly = bool(
            form.show_sale_pending_listings_publicly.data
        )
        persisted.show_sold_listings_publicly = bool(
            form.show_sold_listings_publicly.data
        )
        persisted.sold_retention_days = int(form.sold_retention_days.data)
        persisted.currency_code = form.currency_code.data.strip().upper()
        persisted.currency_symbol = form.currency_symbol.data
        persisted.currency_decimal_separator = (
            form.currency_decimal_separator.data
        )
        persisted.currency_thousands_separator = (
            form.currency_thousands_separator.data
        )
        persisted.default_distance_unit = form.default_distance_unit.data
        persisted.listing_images_path = form.listing_images_path.data
        persisted.allow_seller_inventory_import = bool(
            form.allow_seller_inventory_import.data
        )
        current = {
            "require_listing_approval": persisted.require_listing_approval,
            "require_rereview_on_edit": persisted.require_rereview_on_edit,
            "enable_listing_expiration": persisted.enable_listing_expiration,
            "listing_expiration_days": persisted.listing_expiration_days,
            "expiration_warning_days": persisted.expiration_warning_days,
            "expired_retention_days": persisted.expired_retention_days,
            "expired_removal_warning_days": persisted.expired_removal_warning_days,
            "show_sale_pending_listings_publicly": persisted.show_sale_pending_listings_publicly,
            "show_sold_listings_publicly": persisted.show_sold_listings_publicly,
            "sold_retention_days": persisted.sold_retention_days,
            "currency_code": persisted.currency_code,
            "currency_symbol": persisted.currency_symbol,
            "currency_decimal_separator": persisted.currency_decimal_separator,
            "currency_thousands_separator": persisted.currency_thousands_separator,
            "default_distance_unit": persisted.default_distance_unit,
            "listing_images_path": persisted.listing_images_path,
            "allow_seller_inventory_import": persisted.allow_seller_inventory_import,
        }

        try:
            db.session.flush()
            if audit_activity_enabled() and previous != current:
                log_action(
                    user_id=current_user.id,
                    action="autogrid360_settings_updated",
                    target="autogrid360_settings:1",
                    extra_data={
                        "previous": previous,
                        "current": current,
                    },
                )
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception(
                "AutoGrid360 settings update failed for user_id=%s",
                current_user.id,
            )
            flash("AutoGrid360 settings could not be saved. Please try again.", "danger")
            return render_template(
                "autogrid360/admin/settings.html",
                form=form,
                maintenance_form=ExpireDueListingsForm(),
                title="AutoGrid360 Settings",
            )

        if previous != current:
            logger.info(
                "AutoGrid360 settings updated user_id=%s require_listing_approval=%s require_rereview_on_edit=%s enable_listing_expiration=%s listing_expiration_days=%s expiration_warning_days=%s expired_retention_days=%s expired_removal_warning_days=%s show_sale_pending_publicly=%s show_sold_publicly=%s sold_retention_days=%s allow_seller_inventory_import=%s listing_images_path_changed=%s",
                current_user.id,
                current["require_listing_approval"],
                current["require_rereview_on_edit"],
                current["enable_listing_expiration"],
                current["listing_expiration_days"],
                current["expiration_warning_days"],
                current["expired_retention_days"],
                current["expired_removal_warning_days"],
                current["show_sale_pending_listings_publicly"],
                current["show_sold_listings_publicly"],
                current["sold_retention_days"],
                current["allow_seller_inventory_import"],
                previous["listing_images_path"] != current["listing_images_path"],
            )
        flash("AutoGrid360 settings have been saved.", "success")
        return redirect(url_for("autogrid360_settings.settings"))

    return render_template(
        "autogrid360/admin/settings.html",
        form=form,
        maintenance_form=ExpireDueListingsForm(),
        title="AutoGrid360 Settings",
    )


@settings_bp.post("/maintenance/expire-due")
@limiter.limit("3 per minute", key_func=get_client_ip)
@login_required
def expire_due():
    """Expire every due Active or Sale Pending listing in one admin transaction."""

    require_autogrid360_admin()
    form = ExpireDueListingsForm()
    if not form.validate_on_submit():
        abort(400)

    policy = listing_policy()
    if not policy.expiration_enabled:
        flash(
            "Automatic listing expiration is disabled; no listings were changed.",
            "warning",
        )
        return redirect(url_for("autogrid360_settings.settings"))

    cutoff = datetime.now(timezone.utc)
    due_previous_status = dict(
        Listing.query.with_entities(Listing.id, Listing.status)
        .filter(
            Listing.status.in_((STATUS_ACTIVE, STATUS_SALE_PENDING)),
            Listing.expires_at.is_not(None),
            Listing.expires_at <= cutoff,
        )
        .all()
    )

    try:
        expired = expire_due_listings(now=cutoff)
        if audit_activity_enabled():
            for listing in expired:
                log_action(
                    user_id=current_user.id,
                    action="autogrid360_listing_status_changed",
                    target=f"listing:{listing.id}",
                    extra_data={
                        "listing_id": listing.id,
                        "seller_id": listing.seller_id,
                        "previous_status": due_previous_status.get(
                            listing.id, STATUS_ACTIVE
                        ),
                        "new_status": STATUS_EXPIRED,
                        "source": "expiration_maintenance",
                    },
                )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "AutoGrid360 expiration maintenance failed for admin_id=%s",
            current_user.id,
        )
        flash(
            "Expiration maintenance could not be completed. Please try again.",
            "danger",
        )
        return redirect(url_for("autogrid360_settings.settings"))

    logger.info(
        "AutoGrid360 expiration maintenance completed admin_id=%s expired_count=%s",
        current_user.id,
        len(expired),
    )
    flash(
        f"Expiration maintenance completed: {len(expired)} listing(s) expired.",
        "success",
    )
    return redirect(url_for("autogrid360_settings.settings"))
