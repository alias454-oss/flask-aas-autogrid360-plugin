# app/plugins/autogrid360/routes/tools.py
"""Public AutoGrid360 buyer utility routes."""

from decimal import Decimal

from flask import Blueprint, abort, render_template, request, url_for

from app.plugins.autogrid360.forms.payment import PaymentCalculatorForm
from app.plugins.autogrid360.models import Listing
from app.plugins.autogrid360.services.settings import public_listing_statuses
from app.plugins.autogrid360.services.paycalc import calculate_payment


tools_bp = Blueprint(
    "autogrid360_tools",
    __name__,
    url_prefix="/autogrid360/tools",
)


def _public_listing(listing_id: int | None) -> Listing | None:
    """Resolve an optional policy-visible listing used to seed a buyer tool."""

    if listing_id is None:
        return None

    listing = Listing.query.filter(
        Listing.id == listing_id,
        Listing.status.in_(public_listing_statuses()),
    ).one_or_none()
    if listing is None:
        abort(404)
    return listing


@tools_bp.route("/payment-calculator", methods=["GET", "POST"])
def payment_calculator():
    """Calculate vehicle-loan payments without persisting buyer financial inputs."""

    form = PaymentCalculatorForm()
    result = None

    raw_listing_id = (
        form.listing_id.data
        if form.is_submitted()
        else request.args.get("listing_id")
    )
    try:
        listing_id = int(raw_listing_id) if raw_listing_id else None
    except (TypeError, ValueError):
        abort(404)

    listing = _public_listing(listing_id)

    if not form.is_submitted():
        form.listing_id.data = str(listing.id) if listing is not None else ""
        if listing is not None and listing.price is not None:
            form.amount.data = Decimal(listing.price)
        form.down_payment.data = Decimal("0.00")
        form.annual_interest_rate.data = Decimal("6.500")
        form.loan_years.data = 5
        form.frequency.data = "monthly"

    if form.validate_on_submit():
        result = calculate_payment(
            amount=Decimal(form.amount.data),
            down_payment=Decimal(form.down_payment.data),
            annual_interest_rate=Decimal(form.annual_interest_rate.data),
            loan_years=int(form.loan_years.data),
            frequency=form.frequency.data,
            include_schedule=bool(form.show_schedule.data),
        )

    return render_template(
        "autogrid360/tools/payment.html",
        form=form,
        result=result,
        listing=listing,
        title="Vehicle Payment Calculator",
        seo_title="Vehicle Payment Calculator",
        description="Estimate vehicle loan payments by price, down payment, APR, term, and payment frequency.",
        canonical_url=url_for("autogrid360_tools.payment_calculator", _external=True),
        robots_meta="index,follow",
        seo_type="website",
    )
