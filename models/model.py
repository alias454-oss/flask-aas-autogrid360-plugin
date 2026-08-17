# app/plugins/autogrid360/models/model.py
"""Vehicle-model persistence for AutoGrid360."""

from app.core.extensions import db


class VehicleModel(db.Model):
    """One stable vehicle model scoped to one automotive make."""

    __tablename__ = "plugin_autogrid360_vehicle_models"
    __table_args__ = (
        db.UniqueConstraint(
            "make_id",
            "key",
            name="uq_plugin_autogrid360_vehicle_model_make_key",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    make_id = db.Column(
        db.Integer,
        db.ForeignKey("plugin_autogrid360_reference_values.id"),
        nullable=False,
        index=True,
    )
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

    def __repr__(self):
        return (
            f"<VehicleModel id={self.id} make_id={self.make_id} "
            f"key={self.key!r} label={self.label!r} active={self.active!r}>"
        )
