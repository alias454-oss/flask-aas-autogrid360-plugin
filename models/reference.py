# app/plugins/autogrid360/models/reference.py
"""AutoGrid360 controlled reference values."""

from app.core.extensions import db


CATEGORY_MAKE = "make"
CATEGORY_VEHICLE_TYPE = "vehicle_type"
CATEGORY_DRIVETRAIN = "drivetrain"
CATEGORY_FEATURE = "feature"

REFERENCE_CATEGORIES = (
    CATEGORY_MAKE,
    CATEGORY_VEHICLE_TYPE,
    CATEGORY_DRIVETRAIN,
    CATEGORY_FEATURE,
)


class ReferenceValue(db.Model):
    """One stable AutoGrid360 lookup value within a controlled category."""

    __tablename__ = "plugin_autogrid360_reference_values"
    __table_args__ = (
        db.UniqueConstraint(
            "category",
            "key",
            name="uq_plugin_autogrid360_reference_value_category_key",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(32), nullable=False, index=True)
    key = db.Column(db.String(80), nullable=False)
    label = db.Column(db.String(80), nullable=False)
    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
        index=True,
    )
    sort_order = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    default_selected = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
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
            f"<ReferenceValue id={self.id} category={self.category!r} "
            f"key={self.key!r} label={self.label!r} active={self.active!r}>"
        )


vehicle_features = db.Table(
    "plugin_autogrid360_vehicle_features",
    db.Column(
        "vehicle_id",
        db.Integer,
        db.ForeignKey("plugin_autogrid360_vehicles.id"),
        primary_key=True,
    ),
    db.Column(
        "reference_value_id",
        db.Integer,
        db.ForeignKey("plugin_autogrid360_reference_values.id"),
        primary_key=True,
    ),
)
