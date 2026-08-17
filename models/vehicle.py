# app/plugins/autogrid360/models/vehicle.py
"""Vehicle persistence owned by AutoGrid360."""

from app.core.extensions import db
from app.plugins.autogrid360.models.reference import ReferenceValue, vehicle_features


class Vehicle(db.Model):
    """Physical vehicle described by one or more marketplace listings."""

    __tablename__ = "plugin_autogrid360_vehicles"
    __table_args__ = (
        db.CheckConstraint(
            "(model_id IS NOT NULL AND model_text IS NULL) OR "
            "(model_id IS NULL AND model_text IS NOT NULL)",
            name="ck_plugin_autogrid360_vehicle_model_source",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.SmallInteger, nullable=True, index=True)
    make_id = db.Column(
        db.Integer,
        db.ForeignKey("plugin_autogrid360_reference_values.id"),
        nullable=False,
        index=True,
    )
    model_id = db.Column(
        db.Integer,
        db.ForeignKey("plugin_autogrid360_vehicle_models.id"),
        nullable=True,
        index=True,
    )
    model_text = db.Column(db.String(80), nullable=True, index=True)
    trim = db.Column(db.String(80), nullable=True)
    vehicle_type_id = db.Column(
        db.Integer,
        db.ForeignKey("plugin_autogrid360_reference_values.id"),
        nullable=True,
        index=True,
    )
    doors = db.Column(db.SmallInteger, nullable=True)
    exterior_color = db.Column(db.String(50), nullable=True)
    mileage = db.Column(db.Integer, nullable=True)
    condition = db.Column(db.String(30), nullable=True)
    engine = db.Column(db.String(80), nullable=True)
    transmission = db.Column(db.String(50), nullable=True)
    drivetrain_id = db.Column(
        db.Integer,
        db.ForeignKey("plugin_autogrid360_reference_values.id"),
        nullable=True,
        index=True,
    )
    mpg = db.Column(db.SmallInteger, nullable=True)
    fuel_type = db.Column(db.String(30), nullable=True)
    vin = db.Column(db.String(17), nullable=True, index=True)
    stock_number = db.Column(db.String(32), nullable=True)
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

    make_ref = db.relationship(
        "ReferenceValue",
        foreign_keys=[make_id],
        lazy="joined",
    )
    model_ref = db.relationship(
        "VehicleModel",
        foreign_keys=[model_id],
        lazy="joined",
    )
    vehicle_type_ref = db.relationship(
        "ReferenceValue",
        foreign_keys=[vehicle_type_id],
        lazy="joined",
    )
    drivetrain_ref = db.relationship(
        "ReferenceValue",
        foreign_keys=[drivetrain_id],
        lazy="joined",
    )
    features = db.relationship(
        "ReferenceValue",
        secondary=vehicle_features,
        lazy="selectin",
        order_by=(ReferenceValue.sort_order, ReferenceValue.label, ReferenceValue.id),
    )

    listings = db.relationship(
        "Listing",
        back_populates="vehicle",
        lazy="selectin",
    )

    @property
    def make(self) -> str:
        """Return the display label for the referenced make."""

        return self.make_ref.label if self.make_ref else ""

    @property
    def model(self) -> str:
        """Return the canonical model label or the unlisted-model fallback text."""

        if self.model_ref is not None:
            return self.model_ref.label
        return self.model_text or ""

    @property
    def vehicle_type(self) -> str | None:
        """Return the display label for the referenced vehicle type."""

        return self.vehicle_type_ref.label if self.vehicle_type_ref else None

    @property
    def drivetrain(self) -> str | None:
        """Return the display label for the referenced drivetrain."""

        return self.drivetrain_ref.label if self.drivetrain_ref else None

    def __repr__(self):
        return (
            f"<Vehicle id={self.id} year={self.year!r} "
            f"make={self.make!r} model={self.model!r}>"
        )
