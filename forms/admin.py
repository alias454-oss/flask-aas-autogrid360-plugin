# app/plugins/autogrid360/forms/admin.py
"""AutoGrid360 administration forms."""

from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length

from app.plugins.autogrid360.forms.listings import ListingForm, normalize_text


class AdminListingForm(ListingForm):
    """Create a listing draft for a selected Flask-AAS seller."""

    seller_username = StringField(
        "Seller Username",
        validators=[DataRequired(), Length(max=120)],
        filters=[normalize_text],
    )
    submit = SubmitField("Create Draft")


class AssignSellerForm(FlaskForm):
    """Assign an existing AutoGrid360 listing to one Flask-AAS user."""

    seller_username = StringField(
        "Seller Username",
        validators=[DataRequired(), Length(max=120)],
        filters=[normalize_text],
    )
    submit = SubmitField("Assign Seller")
