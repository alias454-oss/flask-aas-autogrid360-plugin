# app/plugins/autogrid360/forms/images.py
"""Listing-image forms for AutoGrid360."""

from flask_wtf import FlaskForm
from flask_wtf.file import FileRequired, MultipleFileField
from wtforms import HiddenField, SubmitField
from wtforms.validators import AnyOf, DataRequired


class ImageUploadForm(FlaskForm):
    """Upload one or more images for an owned listing."""

    images = MultipleFileField("Listing Images", validators=[FileRequired()])
    submit = SubmitField("Upload Images")


class ImageActionForm(FlaskForm):
    """CSRF-protected action against one existing listing image."""

    submit = SubmitField("Apply")


class ImageMoveForm(FlaskForm):
    """Move one existing listing image one position in either direction."""

    direction = HiddenField(validators=[DataRequired(), AnyOf(["up", "down"])])
    submit = SubmitField("Move")
