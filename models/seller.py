# app/plugins/autogrid360/models/seller.py
"""AutoGrid360-owned seller profile data linked to application users."""

from app.core.extensions import db


class SellerProfile(db.Model):
    """Marketplace presentation data that is specific to AutoGrid360."""

    __tablename__ = "plugin_autogrid360_seller_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    display_name = db.Column(db.String(120), nullable=True)
    company_name = db.Column(db.String(120), nullable=True)
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

    user = db.relationship("User")

    @property
    def public_label(self) -> str | None:
        """Return the preferred public marketplace label for this seller."""

        return self.company_name or self.display_name

    def __repr__(self):
        return f"<SellerProfile id={self.id} user_id={self.user_id}>"
