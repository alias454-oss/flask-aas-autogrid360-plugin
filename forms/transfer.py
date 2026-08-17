# app/plugins/autogrid360/forms/transfer.py
"""Inventory portability and administrator backup/restore forms for AutoGrid360."""

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import BooleanField, SubmitField, TextAreaField
from wtforms.validators import Length, Optional


class InventoryImportForm(FlaskForm):
    """Upload one canonical seller-scoped AutoGrid360 inventory ZIP bundle."""

    bundle = FileField(
        "AutoGrid360 inventory bundle",
        validators=[
            FileRequired(),
            FileAllowed(["zip"], "Upload an AutoGrid360 .zip inventory bundle."),
        ],
    )
    as_draft = BooleanField(
        "Reset restored listings to Draft",
        default=False,
    )
    submit = SubmitField("Restore Inventory")


class AdminInventoryRestoreForm(FlaskForm):
    """Restore a seller or full-site AutoGrid360 backup as an administrator."""

    bundle = FileField(
        "AutoGrid360 backup bundle",
        validators=[
            FileRequired(),
            FileAllowed(["zip"], "Upload an AutoGrid360 .zip backup bundle."),
        ],
    )
    seller_mapping = TextAreaField(
        "Seller mappings",
        validators=[Optional(), Length(max=20000)],
        description="Optional source=destination username mappings, one per line.",
    )
    as_draft = BooleanField(
        "Reset restored listings to Draft",
        default=False,
    )
    submit = SubmitField("Restore Backup")
