# app/plugins/autogrid360/forms/reference.py
"""Administrator forms for AutoGrid360 controlled reference data."""

from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange

from app.plugins.autogrid360.forms.listings import normalize_text


class ReferenceValueForm(FlaskForm):
    """Create or edit one AutoGrid360 reference value."""

    label = StringField(
        "Label",
        validators=[DataRequired(), Length(max=80)],
        filters=[normalize_text],
    )
    sort_order = IntegerField(
        "Sort Order",
        validators=[DataRequired(), NumberRange(min=-100000, max=100000)],
        default=0,
    )
    active = BooleanField("Available for selection", default=True)
    default_selected = BooleanField("Selected by default on new listings")
    submit = SubmitField("Save Reference Value")


class ReferenceToggleForm(FlaskForm):
    """Enable or disable one reference value without deleting its identity."""

    submit = SubmitField("Change Availability")


class VehicleModelForm(FlaskForm):
    """Create or edit one make-scoped vehicle model."""

    label = StringField(
        "Label",
        validators=[DataRequired(), Length(max=80)],
        filters=[normalize_text],
    )
    sort_order = IntegerField(
        "Sort Order",
        validators=[DataRequired(), NumberRange(min=-100000, max=100000)],
        default=0,
    )
    active = BooleanField("Available for selection", default=True)
    submit = SubmitField("Save Vehicle Model")


class VehicleModelToggleForm(FlaskForm):
    """Enable or disable one make-scoped vehicle model."""

    submit = SubmitField("Change Availability")
