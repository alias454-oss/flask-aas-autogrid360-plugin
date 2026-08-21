# app/plugins/autogrid360/routes/public.py
"""Public AutoGrid360 routes."""

import logging
from urllib.parse import unquote, urlencode, urlparse

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy import case, func
from sqlalchemy.exc import SQLAlchemyError

from app.core.avatar import profile_image_data_uri
from app.core.cache import get_cached_env_settings
from app.core.extensions import db, limiter
from app.core.mailer import get_mail_configuration_state, send_email
from app.core.security import get_client_ip, normalize_email, redact_email
from app.core.spam import check_spam
from app.core.trackers import audit_activity_enabled, log_action_isolated
from app.models import User
from app.plugins.autogrid360.services.auth import can_manage_listing
from app.plugins.autogrid360.forms.inquiries import ListingInquiryForm
from app.plugins.autogrid360.services.formatting import format_currency
from app.plugins.autogrid360.services.settings import (
    currency_policy,
    distance_policy,
    listing_is_publicly_visible,
    listing_policy,
    public_listing_statuses,
)
from app.plugins.autogrid360.services.search import (
    PAGE_SIZE_OPTIONS,
    SORT_MAKE_ASC,
    SORT_MAKE_DESC,
    SORT_MODEL_ASC,
    SORT_MODEL_DESC,
    SORT_NEWEST,
    SORT_PRICE_ASC,
    SORT_PRICE_DESC,
    SORT_YEAR_ASC,
    SORT_YEAR_DESC,
    canonicalize_inventory_criteria,
    inventory_url,
    parse_fancy_inventory_path,
    parse_inventory_criteria,
    parse_page,
    parse_per_page,
    parse_sort,
    prepare_inventory_query,
    search_form_url,
    search_heading,
    active_inventory_search_facets,
)
from app.plugins.autogrid360.services.seo import (
    listing_meta_description,
    listing_meta_title,
    listing_robots_meta,
    listing_slug,
    listing_structured_data,
    listing_url,
    listing_vehicle_name,
    rss_datetime,
    sitemap_lastmod,
)
from app.plugins.autogrid360.services.geo import (
    RADIUS_OPTIONS,
    postal_country_choices,
    postal_location_by_code,
    resolve_distance_unit,
)
from app.plugins.autogrid360.services.location import (
    postal_zone_code,
    user_public_location,
)
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_SALE_PENDING,
    STATUS_SOLD,
    Listing,
    SellerProfile,
)


logger = logging.getLogger(__name__)

public_bp = Blueprint(
    "autogrid360",
    __name__,
    url_prefix="/autogrid360",
    template_folder="../templates",
    static_folder="../static",
)


@public_bp.app_template_filter("autogrid360_currency")
def _autogrid360_currency(value):
    """Expose the configured AutoGrid360 currency formatter to plugin templates."""

    return format_currency(value)


@public_bp.app_template_global("autogrid360_currency_policy")
def _autogrid360_currency_policy():
    """Expose current currency presentation settings to plugin templates."""

    return currency_policy()


@public_bp.app_template_global("autogrid360_listing_url")
def _autogrid360_listing_url(listing, external=False):
    """Expose canonical listing URL generation to plugin templates."""

    return listing_url(listing, external=external)


VEHICLE_HISTORY_LINKS = (
    (
        "CARFAX Vehicle History Reports",
        "https://www.carfax.com/vehicle-history-reports/",
    ),
    ("NICB VINCheck", "https://www.nicb.org/vincheck"),
    ("NHTSA Recall Lookup", "https://www.nhtsa.gov/recalls"),
)


def _primary_image(listing):
    """Return the selected primary image, falling back to the first image."""

    primary = next((image for image in listing.images if image.is_primary), None)
    return primary or next(iter(listing.images), None)


def _seller_label(seller: User, profile: SellerProfile | None) -> str:
    """Return one public marketplace label without exposing account email."""

    if profile and profile.public_label:
        return profile.public_label
    return seller.username


def _public_listing_or_404(listing_id: int) -> Listing:
    """Return one listing exposed on the anonymous public surface by site policy."""

    return Listing.query.filter(
        Listing.id == listing_id,
        Listing.status.in_(public_listing_statuses()),
    ).first_or_404()


def _share_mailto(listing: Listing, listing_url: str) -> str:
    """Build a local-mail-client share link without operating a server mail relay."""

    lines = [listing.title, listing_vehicle_name(listing)]
    if listing.price is not None:
        lines.append(f"Price: {format_currency(listing.price)}")
    lines.extend(["", listing_url])
    query = urlencode(
        {
            "subject": f"AutoGrid360 listing: {listing.title}",
            "body": "\n".join(line for line in lines if line is not None),
        }
    )
    return f"mailto:?{query}"


def _increment_view_count(listing: Listing) -> None:
    """Best-effort atomic increment of the public listing view counter."""

    try:
        Listing.query.filter_by(id=listing.id).update(
            {
                Listing.view_count: Listing.view_count + 1,
                Listing.updated_at: Listing.updated_at,
            },
            synchronize_session=False,
        )
        db.session.commit()
        db.session.expire(listing, ["view_count"])
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception(
            "AutoGrid360 public listing view count update failed for listing_id=%s",
            listing.id,
        )


def _normalized_header_text(value: str | None, *, limit: int) -> str:
    """Collapse user-controlled header text to one bounded line."""

    return " ".join((value or "").split())[:limit]


def _inquiry_mail_subject(listing: Listing, submitted_subject: str | None) -> str:
    """Build a bounded, single-line seller-inquiry subject."""

    subject = _normalized_header_text(submitted_subject, limit=100)
    if not subject:
        subject = (
            _normalized_header_text(listing.title, limit=100)
            or f"Listing {listing.id}"
        )
    return f"AutoGrid360 inquiry: {subject}"[:150]


def _inquiry_mail_body(
    listing: Listing,
    *,
    name: str,
    email: str,
    message: str,
) -> str:
    """Build the plain-text seller inquiry delivered by the host mailer."""

    vehicle_name = listing_vehicle_name(listing)
    account = (
        current_user.username
        if current_user.is_authenticated
        else "Anonymous visitor"
    )

    return "\n".join(
        [
            "AutoGrid360 listing inquiry",
            "",
            f"Listing: {listing.title}",
            f"Listing ID: {listing.id}",
            f"Vehicle: {vehicle_name or 'Not specified'}",
            f"From: {name}",
            f"Contact email: {email}",
            f"AutoGrid360 account: {account}",
            "",
            "Message:",
            message.strip(),
        ]
    )


def _audit_inquiry(
    listing: Listing,
    *,
    sender_email: str,
    status: str,
    ip: str,
    user_agent: str,
) -> None:
    """Record inquiry dispatch metadata without storing message content or raw email."""

    if not audit_activity_enabled():
        return

    log_action_isolated(
        user_id=getattr(current_user, "id", None),
        action="autogrid360_listing_inquiry",
        target=f"listing:{listing.id}",
        extra_data={
            "listing_id": listing.id,
            "seller_id": listing.seller_id,
            "sender_email": redact_email(sender_email),
            "ip": ip,
            "user_agent": user_agent,
            "status": status,
        },
    )


def _fancy_inventory_enabled() -> bool:
    """Return whether the host currently prefers search-engine-friendly URLs."""

    return bool(get_cached_env_settings().use_fancy_urls)


def _inventory_state_needs_cleanup(sort: str, requested_per_page: int | None) -> bool:
    """Return whether explicit/default presentation query state should be canonicalized."""

    if "sort" in request.args:
        raw_sort = (request.args.get("sort") or "").strip()
        if sort == SORT_NEWEST or raw_sort != sort:
            return True
    if "per_page" in request.args:
        raw_per_page = (request.args.get("per_page") or "").strip()
        if requested_per_page is None or raw_per_page != str(requested_per_page):
            return True
    return False


def _inventory_sort_controls(criteria, *, fancy: bool, sort: str, per_page: int | None):
    """Return direct sortable-column controls for the public inventory table."""

    specs = (
        ("make", "Make", SORT_MAKE_ASC, SORT_MAKE_DESC),
        ("model", "Model", SORT_MODEL_ASC, SORT_MODEL_DESC),
        ("year", "Year", SORT_YEAR_DESC, SORT_YEAR_ASC),
        ("price", "Price", SORT_PRICE_ASC, SORT_PRICE_DESC),
    )
    controls = {}
    for key, label, first_sort, reverse_sort in specs:
        if sort == first_sort:
            target_sort = reverse_sort
            arrow = "↑" if first_sort.endswith("_asc") else "↓"
            aria_sort = "ascending" if first_sort.endswith("_asc") else "descending"
        elif sort == reverse_sort:
            target_sort = first_sort
            arrow = "↑" if reverse_sort.endswith("_asc") else "↓"
            aria_sort = "ascending" if reverse_sort.endswith("_asc") else "descending"
        else:
            target_sort = first_sort
            arrow = ""
            aria_sort = "none"
        controls[key] = {
            "label": label,
            "arrow": arrow,
            "aria_sort": aria_sort,
            "url": inventory_url(
                criteria,
                fancy=fancy,
                sort=target_sort,
                per_page=per_page,
            ),
        }
    return controls


def _inventory_page_size_controls(
    criteria,
    *,
    fancy: bool,
    sort: str,
    default_per_page: int,
    requested_per_page: int | None,
):
    """Return link-based page-size controls without adding another public form."""

    controls = [
        {
            "label": f"Default ({default_per_page})",
            "active": requested_per_page is None or requested_per_page == default_per_page,
            "url": inventory_url(criteria, fancy=fancy, sort=sort),
        }
    ]
    for size in PAGE_SIZE_OPTIONS:
        if size == default_per_page:
            continue
        controls.append(
            {
                "label": str(size),
                "active": requested_per_page == size,
                "url": inventory_url(
                    criteria,
                    fancy=fancy,
                    sort=sort,
                    per_page=size,
                ),
            }
        )
    return controls


def _render_inventory(criteria, *, page: int, sort: str, requested_per_page: int | None):
    """Render the one canonical public inventory/results surface."""

    prepared = prepare_inventory_query(criteria, sort=sort)
    criteria = prepared.criteria
    default_per_page = max(
        1,
        min(int(current_app.config.get("AUTOGRID360_LISTINGS_PER_PAGE", 20)), 100),
    )
    per_page = requested_per_page or default_per_page
    pagination = prepared.query.paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )
    fancy = _fancy_inventory_enabled()
    heading = search_heading(criteria)
    canonical_url = inventory_url(
        criteria,
        fancy=fancy,
        page=page,
        external=True,
    )
    should_index = (
        page == 1
        and sort == SORT_NEWEST
        and requested_per_page is None
        and (not criteria.has_filters or (fancy and criteria.uses_only_core_seo_filters))
    )

    return render_template(
        "autogrid360/index.html",
        listings=pagination.items,
        pagination=pagination,
        filters=criteria.as_dict(),
        has_filters=criteria.has_filters,
        sort=sort,
        requested_per_page=requested_per_page,
        default_per_page=default_per_page,
        sort_controls=_inventory_sort_controls(
            criteria,
            fancy=fancy,
            sort=sort,
            per_page=requested_per_page,
        ),
        newest_url=inventory_url(
            criteria,
            fancy=fancy,
            per_page=requested_per_page,
        ),
        page_size_controls=_inventory_page_size_controls(
            criteria,
            fancy=fancy,
            sort=sort,
            default_per_page=default_per_page,
            requested_per_page=requested_per_page,
        ),
        refine_search_url=search_form_url(criteria),
        clear_inventory_url=url_for("autogrid360.index"),
        previous_url=(
            inventory_url(
                criteria,
                fancy=fancy,
                sort=sort,
                per_page=requested_per_page,
                page=pagination.prev_num,
            )
            if pagination.has_prev
            else None
        ),
        next_url=(
            inventory_url(
                criteria,
                fancy=fancy,
                sort=sort,
                per_page=requested_per_page,
                page=pagination.next_num,
            )
            if pagination.has_next
            else None
        ),
        primary_images={
            listing.id: _primary_image(listing)
            for listing in pagination.items
        },
        distance_by_id={
            listing.id: prepared.distance_by_id.get(listing.id)
            for listing in pagination.items
            if listing.id in prepared.distance_by_id
        },
        effective_distance_unit=prepared.effective_distance_unit,
        location_error=prepared.location_error,
        title=heading,
        seo_title=heading,
        description=f"Browse {heading.lower()} on AutoGrid360.",
        canonical_url=canonical_url,
        robots_meta="index,follow" if should_index else "noindex,follow",
        seo_type="website",
    )


@public_bp.get("/geo/lookup")
@limiter.limit("120 per minute", key_func=get_client_ip)
def geo_lookup():
    """Return one installed postal locality for progressive listing-form assistance."""

    location = postal_location_by_code(
        request.args.get("country"),
        request.args.get("postal_code"),
    )
    if location is None:
        return jsonify({"found": False}), 404

    return jsonify(
        {
            "found": True,
            "country_code": location.country_code,
            "postal_code": location.postal_code,
            "city": location.locality or "",
            "zone_code": postal_zone_code(
                location.country_code,
                location.region_code,
                location.region,
            ) or "",
        }
    )


@public_bp.get("/")
@limiter.limit("120 per minute", key_func=get_client_ip)
def index():
    """Render all active inventory or query-string search results."""

    criteria = canonicalize_inventory_criteria(
        parse_inventory_criteria(request.args)
    )
    page = parse_page(request.args)
    sort = parse_sort(request.args)
    requested_per_page = parse_per_page(request.args)
    fancy = _fancy_inventory_enabled()
    should_canonicalize = fancy and (
        criteria.has_filters
        or "page" in request.args
        or _inventory_state_needs_cleanup(sort, requested_per_page)
    )
    if should_canonicalize:
        target = inventory_url(
            criteria,
            fancy=fancy,
            sort=sort,
            per_page=requested_per_page,
            page=page,
        )
        if request.full_path.rstrip("?") != target:
            return redirect(target, code=302)

    return _render_inventory(
        criteria,
        page=page,
        sort=sort,
        requested_per_page=requested_per_page,
    )


@public_bp.get("/listings/search")
@limiter.limit("120 per minute", key_func=get_client_ip)
def search():
    """Render the Advanced Search query builder or apply it to inventory."""

    criteria = canonicalize_inventory_criteria(
        parse_inventory_criteria(request.args)
    )
    if request.args.get("apply") == "1":
        return redirect(
            inventory_url(
                criteria,
                fancy=_fancy_inventory_enabled(),
            )
        )

    configured_distance_unit = distance_policy().default_unit
    selected_distance_unit = criteria.distance_unit or configured_distance_unit
    effective_distance_unit = (
        resolve_distance_unit(criteria.postal_country, selected_distance_unit)
        if criteria.postal_country
        else None
    )

    facets = active_inventory_search_facets()

    return render_template(
        "autogrid360/listings/search.html",
        filters=criteria.as_dict(),
        make_choices=facets.makes,
        model_choices=facets.models,
        year_choices=facets.years,
        vehicle_type_choices=facets.vehicle_types,
        drivetrain_choices=facets.drivetrains,
        feature_choices=facets.features,
        condition_choices=facets.conditions,
        transmission_choices=facets.transmissions,
        seller_choices=facets.sellers,
        location_country_choices=facets.countries,
        location_zone_choices=facets.zones,
        postal_country_choices=postal_country_choices(),
        radius_options=RADIUS_OPTIONS,
        selected_distance_unit=selected_distance_unit,
        effective_distance_unit=effective_distance_unit,
        title="Advanced Search",
        seo_title="Advanced Search",
        description="Build a detailed AutoGrid360 vehicle inventory search.",
        canonical_url=url_for("autogrid360.search", _external=True),
        robots_meta="noindex,follow",
        seo_type="website",
    )


@public_bp.get("/<path:inventory_path>")
@limiter.limit("120 per minute", key_func=get_client_ip)
def inventory_fancy(inventory_path):
    """Render one SEF inventory path using the same reusable search service."""

    parsed = parse_fancy_inventory_path(inventory_path)
    if parsed is None:
        abort(404)
    criteria, page = parsed
    canonicalize_inventory_criteria(criteria)
    if "page" in request.args and page == 1:
        page = parse_page(request.args)
    sort = parse_sort(request.args)
    requested_per_page = parse_per_page(request.args)

    if not _fancy_inventory_enabled():
        return redirect(
            inventory_url(
                criteria,
                fancy=False,
                sort=sort,
                per_page=requested_per_page,
                page=page,
            ),
            code=302,
        )

    canonical_path = inventory_url(
        criteria,
        fancy=True,
        sort=sort,
        per_page=requested_per_page,
        page=page,
    )
    if (
        request.path.rstrip("/") != unquote(urlparse(canonical_path).path).rstrip("/")
        or _inventory_state_needs_cleanup(sort, requested_per_page)
        or "page" in request.args
    ):
        return redirect(canonical_path, code=301)

    return _render_inventory(
        criteria,
        page=page,
        sort=sort,
        requested_per_page=requested_per_page,
    )


@public_bp.get("/sitemap.xml")
def sitemap():
    """Publish indexable AutoGrid360 URLs, including dynamic active inventory."""

    items = [
        {"loc": url_for("autogrid360.index", _external=True), "lastmod": None},
        {
            "loc": url_for("autogrid360_tools.payment_calculator", _external=True),
            "lastmod": None,
        },
    ]
    active_listings = (
        Listing.query
        .filter_by(status=STATUS_ACTIVE)
        .order_by(Listing.id.asc())
        .all()
    )
    items.extend(
        {
            "loc": listing_url(listing, external=True),
            "lastmod": sitemap_lastmod(listing),
        }
        for listing in active_listings
    )

    seller_ids = sorted({listing.seller_id for listing in active_listings})
    if seller_ids:
        sellers = User.query.filter(User.id.in_(seller_ids)).order_by(User.username).all()
        items.extend(
            {
                "loc": url_for(
                    "autogrid360.seller_detail",
                    username=seller.username,
                    _external=True,
                ),
                "lastmod": None,
            }
            for seller in sellers
        )

    return Response(
        render_template("autogrid360/sitemap.xml", items=items).lstrip(),
        mimetype="application/xml",
    )


@public_bp.get("/feed.xml")
def inventory_feed():
    """Publish available/Sale Pending inventory as RSS 2.0, optionally by seller."""

    feed_statuses = [STATUS_ACTIVE]
    if listing_policy().show_sale_pending_publicly:
        feed_statuses.append(STATUS_SALE_PENDING)
    query = Listing.query.filter(Listing.status.in_(tuple(feed_statuses)))
    seller_name = (request.args.get("seller") or "").strip()
    seller = None
    if seller_name:
        seller = User.query.filter(func.lower(User.username) == seller_name.lower()).one_or_none()
        if seller is None:
            abort(404)
        query = query.filter(Listing.seller_id == seller.id)

    limit = max(1, min(int(current_app.config.get("AUTOGRID360_FEED_LIMIT", 100)), 500))
    listings = query.order_by(
        Listing.published_at.desc(),
        Listing.created_at.desc(),
        Listing.id.desc(),
    ).limit(limit).all()

    site_name = get_cached_env_settings().site_name or "AutoGrid360"
    seller_profile = (
        SellerProfile.query.filter_by(user_id=seller.id).one_or_none()
        if seller is not None
        else None
    )
    seller_label = _seller_label(seller, seller_profile) if seller is not None else None
    channel_title = (
        f"{seller_label} - AutoGrid360 Inventory"
        if seller_label
        else f"{site_name} - AutoGrid360 Inventory"
    )
    channel_link = (
        url_for("autogrid360.seller_detail", username=seller.username, _external=True)
        if seller is not None
        else url_for("autogrid360.index", _external=True)
    )
    items = [
        {
            "title": listing_meta_title(listing),
            "link": listing_url(listing, external=True),
            "guid": listing.portable_id,
            "pub_date": rss_datetime(listing.published_at or listing.created_at),
            "description": listing_meta_description(listing),
        }
        for listing in listings
    ]
    return Response(
        render_template(
            "autogrid360/feed.xml",
            channel_title=channel_title,
            channel_link=channel_link,
            channel_description=(
                f"Available vehicle listings from {seller_label}."
                if seller_label
                else "Available vehicle listings published through AutoGrid360."
            ),
            self_url=request.url,
            items=items,
        ).lstrip(),
        mimetype="application/rss+xml",
    )


@public_bp.get("/sellers/<string:username>")
@limiter.limit("120 per minute", key_func=get_client_ip)
def seller_detail(username):
    """Show one seller profile and that seller's public inventory."""

    seller = User.query.filter_by(username=username).one_or_none()
    if seller is None:
        abort(404)

    profile = SellerProfile.query.filter_by(user_id=seller.id).one_or_none()
    availability_rank = case(
        (Listing.status == STATUS_ACTIVE, 0),
        (Listing.status == STATUS_SALE_PENDING, 1),
        else_=2,
    )
    active_query = Listing.query.filter(
        Listing.seller_id == seller.id,
        Listing.status.in_(public_listing_statuses()),
    ).order_by(
        availability_rank.asc(),
        Listing.published_at.desc(),
        Listing.created_at.desc(),
        Listing.id.desc(),
    )

    if profile is None and not active_query.first():
        abort(404)

    page = request.args.get("page", default=1, type=int)
    if page is None or page < 1:
        page = 1
    per_page = max(
        1,
        min(int(current_app.config.get("AUTOGRID360_LISTINGS_PER_PAGE", 20)), 100),
    )
    pagination = active_query.paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )

    return render_template(
        "autogrid360/sellers/public.html",
        seller=seller,
        profile=profile,
        seller_label=_seller_label(seller, profile),
        seller_profile_image=profile_image_data_uri(seller.image),
        seller_location=user_public_location(seller),
        listings=pagination.items,
        pagination=pagination,
        primary_images={
            listing.id: _primary_image(listing)
            for listing in pagination.items
        },
        title=_seller_label(seller, profile),
        seo_title=f"{_seller_label(seller, profile)} Vehicle Listings",
        description=f"Browse public vehicle listings from {_seller_label(seller, profile)} on AutoGrid360.",
        canonical_url=url_for(
            "autogrid360.seller_detail",
            username=seller.username,
            _external=True,
        ),
        robots_meta="noindex,follow" if page > 1 else "index,follow",
        seo_type="profile",
        rss_feed_url=url_for(
            "autogrid360.inventory_feed",
            seller=seller.username,
            _external=True,
        ),
    )


def _seller_inquiry_available() -> bool:
    """Return whether the host can currently deliver seller inquiries."""

    try:
        state = get_mail_configuration_state(get_cached_env_settings())
    except Exception:
        logger.exception("Unable to determine AutoGrid360 seller inquiry availability")
        return False
    return state.enabled and state.available


def _render_public_listing(listing: Listing):
    """Render one policy-visible listing with canonical metadata."""

    seller_profile = SellerProfile.query.filter_by(
        user_id=listing.seller_id
    ).one_or_none()
    primary_image = _primary_image(listing)
    canonical_url = listing_url(listing, external=True)
    image_url = (
        url_for(
            "autogrid360_images.file",
            listing_id=listing.id,
            image_id=primary_image.id,
            variant="display",
            _external=True,
        )
        if primary_image is not None
        else None
    )
    _increment_view_count(listing)
    seo_title = listing_meta_title(listing)
    description = listing_meta_description(listing)

    return render_template(
        "autogrid360/listings/public.html",
        listing=listing,
        primary_image=primary_image,
        seller_label=_seller_label(listing.seller, seller_profile),
        is_sale_pending=listing.status == STATUS_SALE_PENDING,
        is_sold=listing.status == STATUS_SOLD,
        seller_contact_available=_seller_inquiry_available(),
        can_manage_current_listing=can_manage_listing(listing),
        listing_url=canonical_url,
        share_mailto=_share_mailto(listing, canonical_url),
        vehicle_history_links=(
            VEHICLE_HISTORY_LINKS
            if listing.vehicle.vin and len(listing.vehicle.vin.strip()) == 17
            else ()
        ),
        title=seo_title,
        seo_title=seo_title,
        description=description,
        canonical_url=canonical_url,
        robots_meta=listing_robots_meta(listing),
        seo_type="product",
        seo_image_url=image_url,
        structured_data=listing_structured_data(
            listing,
            canonical_url=canonical_url,
            image_url=image_url,
        ),
    )


@public_bp.get("/listings/<int:listing_id>/<string:slug>")
@limiter.limit("120 per minute", key_func=get_client_ip)
def listing_public(listing_id, slug):
    """Show one listing at its search-engine-friendly canonical URL."""

    listing = _public_listing_or_404(listing_id)
    canonical_slug = listing_slug(listing)
    if slug != canonical_slug:
        return redirect(listing_url(listing), code=301)
    return _render_public_listing(listing)


@public_bp.get("/listings/<int:listing_id>/print")
@limiter.limit("120 per minute", key_func=get_client_ip)
def printable_listing(listing_id):
    """Render a print-oriented public listing without incrementing view count."""

    listing = _public_listing_or_404(listing_id)
    seller_profile = SellerProfile.query.filter_by(
        user_id=listing.seller_id
    ).one_or_none()
    canonical_url = listing_url(listing, external=True)
    return render_template(
        "autogrid360/listings/print.html",
        listing=listing,
        primary_image=_primary_image(listing),
        seller_label=_seller_label(listing.seller, seller_profile),
        is_sale_pending=listing.status == STATUS_SALE_PENDING,
        is_sold=listing.status == STATUS_SOLD,
        listing_url=canonical_url,
        title=f"Printable Listing - {listing.title}",
        description=listing_meta_description(listing),
        canonical_url=canonical_url,
        robots_meta="noindex,follow",
        seo_title=f"Printable Listing - {listing.title}",
        seo_type="article",
    )


@public_bp.route("/listings/<int:listing_id>/contact", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"], key_func=get_client_ip)
def contact_seller(listing_id):
    """Accept a public inquiry for one Active/Sale Pending listing and email its seller."""

    listing = Listing.query.filter(
        Listing.id == listing_id,
        Listing.status.in_((STATUS_ACTIVE, STATUS_SALE_PENDING)),
    ).first_or_404()
    if not listing_is_publicly_visible(listing):
        abort(404)
    if not _seller_inquiry_available():
        flash("Seller contact is currently unavailable.", "danger")
        return redirect(listing_url(listing))

    form = ListingInquiryForm()

    if current_user.is_authenticated:
        form.name.data = current_user.username
        form.email.data = current_user.email

    if form.validate_on_submit():
        ip = get_client_ip()
        user_agent = (
            request.headers.get("User-Agent", "Unknown")
            .replace("\r", " ")
            .replace("\n", " ")[:255]
        )
        sender_name = _normalized_header_text(form.name.data, limit=80)
        sender_email = normalize_email(form.email.data)

        if form.nobot_check.data:
            logger.warning(
                "AutoGrid360 inquiry honeypot triggered listing_id=%s from IP %s",
                listing.id,
                ip,
            )
            _audit_inquiry(
                listing,
                sender_email=sender_email,
                status="honeypot",
                ip=ip,
                user_agent=user_agent,
            )
            return redirect(
                url_for("autogrid360.contact_seller", listing_id=listing.id)
            )

        env = get_cached_env_settings()
        if getattr(env, "spam_check_enabled", True):
            provider = str(getattr(env, "spam_check_provider", "local"))
            spam_result = check_spam(form.message.data, provider)
            if not spam_result.passed:
                logger.warning(
                    "AutoGrid360 inquiry blocked by spam provider=%s listing_id=%s from IP %s",
                    provider,
                    listing.id,
                    ip,
                )
                _audit_inquiry(
                    listing,
                    sender_email=sender_email,
                    status="spam_blocked",
                    ip=ip,
                    user_agent=user_agent,
                )
                flash(
                    spam_result.message or "Your message appears to be spam.",
                    "danger",
                )
                return redirect(
                    url_for("autogrid360.contact_seller", listing_id=listing.id)
                )

        recipient = normalize_email(listing.seller.email)
        subject = _inquiry_mail_subject(listing, form.subject.data)
        body = _inquiry_mail_body(
            listing,
            name=sender_name,
            email=sender_email,
            message=form.message.data,
        )

        try:
            mail_status = send_email(subject, recipient, body)
        except Exception:
            logger.exception(
                "Unexpected AutoGrid360 inquiry email failure listing_id=%s",
                listing.id,
            )
            mail_status = "failed"

        _audit_inquiry(
            listing,
            sender_email=sender_email,
            status=mail_status,
            ip=ip,
            user_agent=user_agent,
        )

        if mail_status == "queued":
            logger.info(
                "AutoGrid360 inquiry accepted listing_id=%s seller_id=%s from IP %s",
                listing.id,
                listing.seller_id,
                ip,
            )
            flash("Your inquiry was accepted for delivery.", "success")
        else:
            logger.warning(
                "AutoGrid360 inquiry not queued listing_id=%s seller_id=%s status=%s",
                listing.id,
                listing.seller_id,
                mail_status,
            )
            flash(
                "Seller contact is currently unavailable. Please try again later.",
                "danger",
            )

        return redirect(listing_url(listing))

    canonical_url = listing_url(listing, external=True)
    return render_template(
        "autogrid360/listings/contact.html",
        listing=listing,
        form=form,
        title=f"Contact Seller - {listing.title}",
        description=f"Contact the seller of {listing_vehicle_name(listing) or listing.title} through AutoGrid360.",
        canonical_url=canonical_url,
        robots_meta="noindex,follow",
        seo_title=f"Contact Seller - {listing.title}",
        seo_type="website",
    )
