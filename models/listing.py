# app/plugins/autogrid360/models/listing.py
"""Marketplace listing persistence owned by AutoGrid360."""

from uuid import uuid4

from app.core.extensions import db


STATUS_DRAFT = "draft"
STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_SALE_PENDING = "sale_pending"
STATUS_SOLD = "sold"
STATUS_EXPIRED = "expired"
STATUS_REMOVED = "removed"
LISTING_STATUS_LABELS = {
    STATUS_DRAFT: "Draft",
    STATUS_PENDING: "Pending Review",
    STATUS_ACTIVE: "Active",
    STATUS_SALE_PENDING: "Sale Pending",
    STATUS_SOLD: "Sold",
    STATUS_EXPIRED: "Expired",
    STATUS_REMOVED: "Removed",
}
LISTING_STATUSES = frozenset(LISTING_STATUS_LABELS)


class Listing(db.Model):
    """Marketplace state for advertising one vehicle by one Flask-AAS user."""

    __tablename__ = "plugin_autogrid360_listings"

    id = db.Column(db.Integer, primary_key=True)
    portable_id = db.Column(
        db.String(36),
        nullable=False,
        unique=True,
        default=lambda: str(uuid4()),
        index=True,
    )
    seller_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    vehicle_id = db.Column(
        db.Integer,
        db.ForeignKey("plugin_autogrid360_vehicles.id"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(120), nullable=False)
    price = db.Column(db.Numeric(12, 2), nullable=True)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(
        db.String(20),
        nullable=False,
        default=STATUS_DRAFT,
        server_default=STATUS_DRAFT,
        index=True,
    )
    featured = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false(),
    )
    country_code = db.Column(db.String(2), nullable=True, index=True)
    city = db.Column(db.String(100), nullable=True, index=True)
    zone_code = db.Column(db.String(16), nullable=True, index=True)
    postal_code = db.Column(db.String(20), nullable=True, index=True)
    postal_location_id = db.Column(
        db.Integer,
        db.ForeignKey("plugin_autogrid360_postal_locations.id"),
        nullable=True,
        index=True,
    )
    view_count = db.Column(
        db.BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    first_published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expiration_warning_sent_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )
    expired_at = db.Column(db.DateTime(timezone=True), nullable=True)
    sold_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expired_edited_at = db.Column(db.DateTime(timezone=True), nullable=True)
    expired_removal_warning_sent_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )
    aged_out_at = db.Column(db.DateTime(timezone=True), nullable=True)
    aged_out_notice_sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
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

    seller = db.relationship("User")
    postal_location = db.relationship("PostalLocation")
    vehicle = db.relationship(
        "Vehicle",
        back_populates="listings",
        lazy="joined",
    )
    images = db.relationship(
        "ListingImage",
        back_populates="listing",
        cascade="all, delete-orphan",
        order_by="ListingImage.position",
    )

    @property
    def public_location(self) -> str:
        """Return approximate buyer-facing locality without exposing street address."""

        from app.plugins.autogrid360.services.location import format_public_location

        return format_public_location(
            country_code=self.country_code,
            zone_code=self.zone_code,
            city=self.city,
            postal_code=self.postal_code,
        )

    @property
    def status_label(self) -> str:
        """Return the buyer/seller-facing label for the current lifecycle state."""

        return LISTING_STATUS_LABELS.get(
            self.status,
            str(self.status or "").replace("_", " ").title(),
        )


    @property
    def listed_at(self):
        """Return the original publication date used for buyer-facing listing age."""

        return self.first_published_at or self.published_at or self.created_at

    @property
    def changed_since_expiration(self) -> bool:
        """Return whether meaningful listing content changed in this expired cycle."""

        return self.expired_edited_at is not None

    def __repr__(self):
        return (
            f"<Listing id={self.id} seller_id={self.seller_id} "
            f"vehicle_id={self.vehicle_id} status={self.status!r}>"
        )
