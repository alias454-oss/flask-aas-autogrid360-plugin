# app/plugins/autogrid360/routes/__init__.py
"""AutoGrid360 web routes."""

from app.plugins.autogrid360.routes.account import account_bp
from app.plugins.autogrid360.routes.admin import admin_bp
from app.plugins.autogrid360.routes.images import images_bp
from app.plugins.autogrid360.routes.listings import listings_bp
from app.plugins.autogrid360.routes.public import public_bp
from app.plugins.autogrid360.routes.reference import reference_bp
from app.plugins.autogrid360.routes.settings import settings_bp
from app.plugins.autogrid360.routes.tools import tools_bp


BLUEPRINTS = (
    public_bp,
    admin_bp,
    reference_bp,
    listings_bp,
    images_bp,
    account_bp,
    settings_bp,
    tools_bp,
)

__all__ = ["BLUEPRINTS"]
