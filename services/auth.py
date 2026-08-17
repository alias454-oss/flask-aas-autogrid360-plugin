# app/plugins/autogrid360/services/auth.py
"""AutoGrid360 authorization helpers built on canonical Flask-AAS identity."""

from flask import abort
from flask_login import current_user

from app.core.extensions import db
from app.models import User


def is_autogrid360_admin() -> bool:
    """Return whether the current Flask-AAS user has system-admin authority."""

    return bool(
        current_user.is_authenticated
        and current_user.has_role("admin")
    )


def can_manage_listing(listing) -> bool:
    """Return whether the current user owns a listing or is the system admin."""

    return bool(
        current_user.is_authenticated
        and (
            listing.seller_id == current_user.id
            or is_autogrid360_admin()
        )
    )


def require_autogrid360_admin() -> None:
    """Abort unless the current user has AutoGrid360 administrator authority."""

    if not is_autogrid360_admin():
        abort(403)


def user_by_username(username: str | None) -> User | None:
    """Resolve one host user by exact case-insensitive username."""

    normalized = (username or "").strip()
    if not normalized:
        return None
    return User.query.filter(
        db.func.lower(User.username) == normalized.lower()
    ).one_or_none()
