# app/plugins/autogrid360/models/postal.py
"""Country-aware postal centroids used by AutoGrid360 radius search."""

from app.core.extensions import db


class PostalLocation(db.Model):
    """One searchable postal identifier mapped to a representative coordinate."""

    __tablename__ = "plugin_autogrid360_postal_locations"
    __table_args__ = (
        db.UniqueConstraint(
            "country_code",
            "postal_code",
            name="uq_plugin_autogrid360_postal_country_code",
        ),
        db.CheckConstraint(
            "latitude >= -90 AND latitude <= 90",
            name="ck_plugin_autogrid360_postal_latitude",
        ),
        db.CheckConstraint(
            "longitude >= -180 AND longitude <= 180",
            name="ck_plugin_autogrid360_postal_longitude",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    country_code = db.Column(db.String(2), nullable=False, index=True)
    postal_code = db.Column(db.String(20), nullable=False, index=True)
    locality = db.Column(db.String(180), nullable=True)
    region = db.Column(db.String(100), nullable=True)
    region_code = db.Column(db.String(20), nullable=True)
    county = db.Column(db.String(100), nullable=True)
    latitude = db.Column(db.Float, nullable=False, index=True)
    longitude = db.Column(db.Float, nullable=False, index=True)
    accuracy = db.Column(db.SmallInteger, nullable=True)
    source = db.Column(db.String(40), nullable=False, default="geonames")
    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.true(),
        index=True,
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
            f"<PostalLocation id={self.id} country={self.country_code!r} "
            f"postal_code={self.postal_code!r}>"
        )
