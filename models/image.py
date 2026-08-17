# app/plugins/autogrid360/models/image.py
"""Listing-image persistence owned by AutoGrid360."""

from app.core.extensions import db


class ListingImage(db.Model):
    """One normalized display image and thumbnail attached to a listing."""

    __tablename__ = "plugin_autogrid360_listing_images"

    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(
        db.Integer,
        db.ForeignKey("plugin_autogrid360_listings.id"),
        nullable=False,
        index=True,
    )
    storage_key = db.Column(db.String(255), nullable=False, unique=True)
    thumbnail_key = db.Column(db.String(255), nullable=False, unique=True)
    original_filename = db.Column(db.String(255), nullable=True)
    position = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    is_primary = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )
    width = db.Column(db.Integer, nullable=False)
    height = db.Column(db.Integer, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
    )

    listing = db.relationship("Listing", back_populates="images")

    def __repr__(self):
        return (
            f"<ListingImage id={self.id} listing_id={self.listing_id} "
            f"position={self.position} primary={self.is_primary}>"
        )
