# app/plugins/autogrid360/forms/seller.py
"""Seller-profile forms for AutoGrid360."""

from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import Length, Optional

from app.plugins.autogrid360.forms.listings import normalize_text


class SellerProfileForm(FlaskForm):
    """Edit marketplace presentation data that is specific to AutoGrid360."""

    display_name = StringField(
        "Display Name",
        validators=[Optional(), Length(max=120)],
        filters=[normalize_text],
    )
    company_name = StringField(
        "Company Name",
        validators=[Optional(), Length(max=120)],
        filters=[normalize_text],
    )
    submit = SubmitField("Save Seller Profile")
