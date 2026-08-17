# app/plugins/autogrid360/services/formatting.py
"""Presentation formatting helpers owned by AutoGrid360."""

from decimal import Decimal, InvalidOperation

from app.plugins.autogrid360.services.settings import currency_policy


def format_currency(value) -> str:
    """Format one amount with the current AutoGrid360 currency presentation policy."""

    if value is None:
        return ""

    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return ""

    policy = currency_policy()
    rendered = f"{amount:,.2f}"
    integer, decimals = rendered.rsplit(".", 1)
    integer = integer.replace(",", policy.thousands_separator)
    return f"{policy.symbol}{integer}{policy.decimal_separator}{decimals}"
