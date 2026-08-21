# app/plugins/autogrid360/forms/currency.py
"""Currency-aware WTForms fields for AutoGrid360."""

from wtforms import DecimalField
from wtforms.widgets import TextInput

from app.plugins.autogrid360.services.currency import parse_currency_input


class CurrencyDecimalField(DecimalField):
    """Decimal field that accepts configured human-readable currency input."""

    widget = TextInput()

    def __init__(
        self,
        *args,
        invalid_message: str = "Enter a valid amount.",
        **kwargs,
    ):
        self.invalid_message = invalid_message
        super().__init__(*args, **kwargs)

    def process_formdata(self, valuelist):
        if not valuelist:
            return
        try:
            self.data = parse_currency_input(valuelist[0])
        except (ArithmeticError, ValueError):
            self.data = None
            raise ValueError(self.gettext(self.invalid_message))
