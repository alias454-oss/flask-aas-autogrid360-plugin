# app/plugins/autogrid360/forms/inquiries.py
"""Public seller-inquiry forms for AutoGrid360."""

from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length


class ListingInquiryForm(FlaskForm):
    """Collect one public inquiry without exposing the seller's email address."""

    name = StringField(
        "Name",
        validators=[DataRequired(), Length(max=80)],
    )
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=120)],
    )
    subject = StringField(
        "Subject",
        validators=[Length(max=100)],
    )
    message = TextAreaField(
        "Message",
        validators=[DataRequired(), Length(max=2000)],
    )
    nobot_check = StringField("Leave empty", validators=[Length(max=200)])
