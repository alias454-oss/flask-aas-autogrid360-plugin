# app/plugins/autogrid360/services/currency.py
"""Human-entered currency normalization for AutoGrid360."""

from decimal import Decimal

from app.plugins.autogrid360.services.settings import currency_policy


def parse_currency_input(value) -> Decimal | None:
    """Parse human-entered money using the configured currency presentation."""

    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    policy = currency_policy()
    symbol = policy.symbol or ""
    if symbol:
        if text.startswith(symbol):
            text = text[len(symbol):].lstrip()
        elif symbol in text:
            raise ValueError("invalid currency symbol placement")

    sign = ""
    if text[:1] in {"+", "-"}:
        sign, text = text[0], text[1:]

    decimal_separator = policy.decimal_separator
    if not text or text.count(decimal_separator) > 1:
        raise ValueError("invalid decimal value")

    if decimal_separator in text:
        integer_text, fraction_text = text.split(decimal_separator, 1)
        if not fraction_text or not fraction_text.isdigit():
            raise ValueError("invalid fractional value")
    else:
        integer_text, fraction_text = text, None

    thousands_separator = policy.thousands_separator
    if thousands_separator and thousands_separator in integer_text:
        groups = integer_text.split(thousands_separator)
        if (
            not groups
            or not groups[0]
            or len(groups[0]) > 3
            or not groups[0].isdigit()
            or any(len(group) != 3 or not group.isdigit() for group in groups[1:])
        ):
            raise ValueError("invalid thousands grouping")
        integer_text = "".join(groups)

    if not integer_text:
        integer_text = "0"
    if not integer_text.isdigit():
        raise ValueError("invalid integer value")

    normalized = f"{sign}{integer_text}"
    if fraction_text is not None:
        normalized = f"{normalized}.{fraction_text}"
    return Decimal(normalized)
