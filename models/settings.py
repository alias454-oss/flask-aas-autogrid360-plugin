# app/plugins/autogrid360/models/settings.py
"""AutoGrid360-owned site policy settings."""

from app.core.extensions import db


DEFAULT_LISTING_EXPIRATION_DAYS = 60
DEFAULT_EXPIRATION_WARNING_DAYS = 7
DEFAULT_EXPIRED_RETENTION_DAYS = 30
DEFAULT_EXPIRED_REMOVAL_WARNING_DAYS = 7
DEFAULT_SOLD_RETENTION_DAYS = 90
DEFAULT_CURRENCY_SYMBOL = "$"
DEFAULT_CURRENCY_CODE = "USD"
DEFAULT_CURRENCY_DECIMAL_SEPARATOR = "."
DEFAULT_CURRENCY_THOUSANDS_SEPARATOR = ","
DISTANCE_UNIT_AUTO = "auto"
DISTANCE_UNIT_MILES = "miles"
DISTANCE_UNIT_KILOMETERS = "kilometers"
DEFAULT_DISTANCE_UNIT = DISTANCE_UNIT_AUTO
DEFAULT_LISTING_IMAGES_PATH = "uploads/listings"
DEFAULT_ALLOW_SELLER_INVENTORY_IMPORT = False


class AutoGrid360Settings(db.Model):
    """Singleton AutoGrid360 policy settings for one Flask-AAS installation."""

    __tablename__ = "plugin_autogrid360_settings"
    __table_args__ = (
        db.CheckConstraint(
            "listing_expiration_days >= 1",
            name="ck_autogrid360_settings_expiration_days_positive",
        ),
        db.CheckConstraint(
            "expiration_warning_days >= 0",
            name="ck_autogrid360_settings_warning_days_nonnegative",
        ),
        db.CheckConstraint(
            "expiration_warning_days < listing_expiration_days",
            name="ck_autogrid360_settings_warning_before_expiration",
        ),
        db.CheckConstraint(
            "expired_retention_days >= 1",
            name="ck_autogrid360_settings_expired_retention_days_positive",
        ),
        db.CheckConstraint(
            "expired_removal_warning_days >= 0",
            name="ck_autogrid360_settings_removal_warning_days_nonnegative",
        ),
        db.CheckConstraint(
            "expired_removal_warning_days < expired_retention_days",
            name="ck_autogrid360_settings_removal_warning_before_archive",
        ),
        db.CheckConstraint(
            "sold_retention_days >= 0",
            name="ck_autogrid360_settings_sold_retention_days_nonnegative",
        ),
        db.CheckConstraint(
            "length(currency_code) = 3",
            name="ck_autogrid360_settings_currency_code_length",
        ),
        db.CheckConstraint(
            "currency_decimal_separator IN ('.', ',')",
            name="ck_autogrid360_settings_currency_decimal_separator",
        ),
        db.CheckConstraint(
            "currency_thousands_separator IN (',', '.', ' ', '')",
            name="ck_autogrid360_settings_currency_thousands_separator",
        ),
        db.CheckConstraint(
            "currency_thousands_separator = '' OR "
            "currency_thousands_separator <> currency_decimal_separator",
            name="ck_autogrid360_settings_currency_separators_distinct",
        ),
        db.CheckConstraint(
            "default_distance_unit IN ('auto', 'miles', 'kilometers')",
            name="ck_autogrid360_settings_default_distance_unit",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    require_listing_approval = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
    )
    require_rereview_on_edit = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
    )
    enable_listing_expiration = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )
    listing_expiration_days = db.Column(
        db.Integer,
        nullable=False,
        default=DEFAULT_LISTING_EXPIRATION_DAYS,
        server_default=str(DEFAULT_LISTING_EXPIRATION_DAYS),
    )
    expiration_warning_days = db.Column(
        db.Integer,
        nullable=False,
        default=DEFAULT_EXPIRATION_WARNING_DAYS,
        server_default=str(DEFAULT_EXPIRATION_WARNING_DAYS),
    )
    expired_retention_days = db.Column(
        db.Integer,
        nullable=False,
        default=DEFAULT_EXPIRED_RETENTION_DAYS,
        server_default=str(DEFAULT_EXPIRED_RETENTION_DAYS),
    )
    expired_removal_warning_days = db.Column(
        db.Integer,
        nullable=False,
        default=DEFAULT_EXPIRED_REMOVAL_WARNING_DAYS,
        server_default=str(DEFAULT_EXPIRED_REMOVAL_WARNING_DAYS),
    )
    show_sale_pending_listings_publicly = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
    )
    show_sold_listings_publicly = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
    )
    sold_retention_days = db.Column(
        db.Integer,
        nullable=False,
        default=DEFAULT_SOLD_RETENTION_DAYS,
        server_default=str(DEFAULT_SOLD_RETENTION_DAYS),
    )
    currency_code = db.Column(
        db.String(3),
        nullable=False,
        default=DEFAULT_CURRENCY_CODE,
        server_default=DEFAULT_CURRENCY_CODE,
    )
    currency_symbol = db.Column(
        db.String(8),
        nullable=False,
        default=DEFAULT_CURRENCY_SYMBOL,
        server_default=DEFAULT_CURRENCY_SYMBOL,
    )
    currency_decimal_separator = db.Column(
        db.String(1),
        nullable=False,
        default=DEFAULT_CURRENCY_DECIMAL_SEPARATOR,
        server_default=DEFAULT_CURRENCY_DECIMAL_SEPARATOR,
    )
    currency_thousands_separator = db.Column(
        db.String(1),
        nullable=False,
        default=DEFAULT_CURRENCY_THOUSANDS_SEPARATOR,
        server_default=DEFAULT_CURRENCY_THOUSANDS_SEPARATOR,
    )
    default_distance_unit = db.Column(
        db.String(16),
        nullable=False,
        default=DEFAULT_DISTANCE_UNIT,
        server_default=DEFAULT_DISTANCE_UNIT,
    )
    listing_images_path = db.Column(
        db.String(255),
        nullable=False,
        default=DEFAULT_LISTING_IMAGES_PATH,
        server_default=DEFAULT_LISTING_IMAGES_PATH,
    )
    allow_seller_inventory_import = db.Column(
        db.Boolean,
        nullable=False,
        default=DEFAULT_ALLOW_SELLER_INVENTORY_IMPORT,
        server_default=db.false(),
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
        onupdate=db.func.now(),
    )

    def __repr__(self):
        return (
            f"<AutoGrid360Settings id={self.id} "
            f"approval={self.require_listing_approval} "
            f"rereview={self.require_rereview_on_edit} "
            f"expiration={self.enable_listing_expiration} "
            f"expiration_days={self.listing_expiration_days}>"
        )
