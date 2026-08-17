# app/plugins/autogrid360/forms/settings.py
"""AutoGrid360 site-policy forms."""

from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, StringField, SubmitField
from app.plugins.autogrid360.models.settings import (
    DEFAULT_LISTING_IMAGES_PATH,
    DEFAULT_ALLOW_SELLER_INVENTORY_IMPORT,
    DEFAULT_SOLD_RETENTION_DAYS,
)

from wtforms.validators import (
    AnyOf,
    DataRequired,
    InputRequired,
    Length,
    NumberRange,
    ValidationError,
)


class AutoGrid360SettingsForm(FlaskForm):
    """Edit site-wide AutoGrid360 listing moderation and expiration policy."""

    require_listing_approval = BooleanField(
        "Require approval before listings are published"
    )
    require_rereview_on_edit = BooleanField(
        "Require re-review when a published listing is edited"
    )
    enable_listing_expiration = BooleanField(
        "Automatically expire listings after publication"
    )
    listing_expiration_days = IntegerField(
        "Listing lifetime (days)",
        validators=[InputRequired(), NumberRange(min=1, max=3650)],
    )
    expiration_warning_days = IntegerField(
        "Expiration warning lead time (days)",
        validators=[InputRequired(), NumberRange(min=0, max=3649)],
    )
    expired_retention_days = IntegerField(
        "Expired listing retention (days)",
        validators=[InputRequired(), NumberRange(min=1, max=3650)],
        default=30,
    )
    expired_removal_warning_days = IntegerField(
        "Expired removal warning lead time (days)",
        validators=[InputRequired(), NumberRange(min=0, max=3649)],
        default=7,
    )
    show_sale_pending_listings_publicly = BooleanField(
        "Show Sale Pending listings publicly"
    )
    show_sold_listings_publicly = BooleanField(
        "Show Sold listings publicly"
    )
    sold_retention_days = IntegerField(
        "Sold listing retention (days)",
        validators=[InputRequired(), NumberRange(min=0, max=3650)],
        default=DEFAULT_SOLD_RETENTION_DAYS,
    )
    currency_code = StringField(
        "Currency Code",
        validators=[DataRequired(), Length(min=3, max=3)],
        default="USD",
    )
    currency_symbol = StringField(
        "Currency Symbol",
        validators=[DataRequired(), Length(max=8)],
        default="$",
    )
    currency_decimal_separator = SelectField(
        "Decimal Separator",
        choices=[(".", "Period (.)"), (",", "Comma (,)")],
        validators=[AnyOf([".", ","])],
        default=".",
    )
    listing_images_path = StringField(
        "Listing Images Path",
        validators=[DataRequired(), Length(max=255)],
        default=DEFAULT_LISTING_IMAGES_PATH,
    )
    allow_seller_inventory_import = BooleanField(
        "Allow sellers to restore inventory bundles",
        default=DEFAULT_ALLOW_SELLER_INVENTORY_IMPORT,
    )
    default_distance_unit = SelectField(
        "Default Distance Unit",
        choices=[
            ("auto", "Auto (based on search country)"),
            ("miles", "Miles"),
            ("kilometers", "Kilometers"),
        ],
        validators=[AnyOf(["auto", "miles", "kilometers"])],
        default="auto",
    )
    currency_thousands_separator = SelectField(
        "Thousands Separator",
        choices=[
            (",", "Comma (,)"),
            (".", "Period (.)"),
            (" ", "Space"),
            ("", "None"),
        ],
        validators=[AnyOf([",", ".", " ", ""])],
        default=",",
    )
    submit = SubmitField("Save AutoGrid360 Settings")

    def validate_listing_images_path(self, field):
        """Require a non-empty path without embedded null bytes."""

        value = str(field.data or "").strip()
        if not value:
            raise ValidationError("Listing Images Path is required.")
        if "\x00" in value:
            raise ValidationError("Listing Images Path contains an invalid null byte.")
        field.data = value

    def validate_expiration_warning_days(self, field):
        """Keep the warning window strictly inside the configured lifetime."""

        lifetime = self.listing_expiration_days.data
        if lifetime is not None and field.data is not None and field.data >= lifetime:
            raise ValidationError(
                "Expiration warning lead time must be less than the listing lifetime."
            )

    def validate_expired_removal_warning_days(self, field):
        """Keep the removal warning window inside expired retention."""

        retention = self.expired_retention_days.data
        if retention is not None and field.data is not None and field.data >= retention:
            raise ValidationError(
                "Expired removal warning lead time must be less than the retention period."
            )

    def validate_currency_code(self, field):
        """Require a normalized ISO-style three-letter currency code."""

        value = (field.data or "").strip().upper()
        if len(value) != 3 or not value.isascii() or not value.isalpha():
            raise ValidationError("Currency code must contain exactly three letters.")
        field.data = value

    def validate_currency_thousands_separator(self, field):
        """Keep decimal and grouping separators unambiguous."""

        if field.data and field.data == self.currency_decimal_separator.data:
            raise ValidationError(
                "Thousands separator must differ from the decimal separator."
            )


class ExpireDueListingsForm(FlaskForm):
    """Run deterministic expiration maintenance as the system administrator."""

    submit = SubmitField("Expire Due Listings Now")
