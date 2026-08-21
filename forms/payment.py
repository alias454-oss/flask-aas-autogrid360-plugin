# app/plugins/autogrid360/forms/payment.py
"""Buyer payment-calculator form."""

from flask_wtf import FlaskForm
from wtforms import BooleanField, DecimalField, HiddenField, SelectField, SubmitField
from wtforms.validators import InputRequired, NumberRange, Optional, ValidationError

from app.plugins.autogrid360.forms.currency import CurrencyDecimalField
from app.plugins.autogrid360.services.paycalc import PAYMENT_FREQUENCIES


class PaymentCalculatorForm(FlaskForm):
    """Collect deterministic vehicle-loan calculation inputs."""

    listing_id = HiddenField(validators=[Optional()])
    amount = CurrencyDecimalField(
        "Vehicle Price / Amount",
        validators=[InputRequired(), NumberRange(min=0)],
        places=2,
        invalid_message="Enter a valid vehicle price / amount.",
    )
    down_payment = CurrencyDecimalField(
        "Down Payment",
        validators=[InputRequired(), NumberRange(min=0)],
        places=2,
        default=0,
        invalid_message="Enter a valid down payment.",
    )
    annual_interest_rate = DecimalField(
        "Annual Interest Rate (%)",
        validators=[InputRequired(), NumberRange(min=0, max=100)],
        places=3,
        default="6.500",
    )
    loan_years = SelectField(
        "Loan Length",
        choices=[(years, f"{years} year{'s' if years != 1 else ''}") for years in range(1, 11)],
        coerce=int,
        default=5,
        validators=[InputRequired()],
    )
    frequency = SelectField(
        "Payment Terms",
        choices=[
            (key, label)
            for key, (label, _payments_per_year) in PAYMENT_FREQUENCIES.items()
        ],
        default="monthly",
        validators=[InputRequired()],
    )
    show_schedule = BooleanField("Show amortization schedule")
    submit = SubmitField("Calculate Payment")

    def validate_down_payment(self, field):
        """Prevent a down payment larger than the entered vehicle amount."""

        if self.amount.data is not None and field.data is not None:
            if field.data > self.amount.data:
                raise ValidationError(
                    "Down payment cannot exceed the vehicle price / amount."
                )
