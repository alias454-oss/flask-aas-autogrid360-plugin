# app/plugins/autogrid360/tests/listing_support.py
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from flask import Blueprint, Flask, g
from flask_login import LoginManager
from PIL import Image

from app.core.extensions import db, limiter
from app.models import Country, EnvSettings, Role, User, UserRole, Zone
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    CATEGORY_DRIVETRAIN,
    CATEGORY_FEATURE,
    CATEGORY_MAKE,
    CATEGORY_VEHICLE_TYPE,
    AutoGrid360Settings,
    Listing,
    ListingImage,
    PostalLocation,
    ReferenceValue,
    SellerProfile,
    Vehicle,
    VehicleModel,
    vehicle_features,
)
from app.plugins.autogrid360.routes.account import account_bp
from app.plugins.autogrid360.routes.admin import admin_bp
from app.plugins.autogrid360.routes.images import images_bp
from app.plugins.autogrid360.routes.listings import listings_bp
from app.plugins.autogrid360.routes.public import public_bp
from app.plugins.autogrid360.routes.reference import reference_bp
from app.plugins.autogrid360.routes.settings import settings_bp
from app.plugins.autogrid360.routes.tools import tools_bp
from app.plugins.autogrid360.services.media import image_root as listing_image_root
from app.plugins.autogrid360.services.seo import listing_slug
from app.plugins.autogrid360.services.reference import (
    reference_by_key,
    seed_reference_data,
    vehicle_model_by_key,
)
from app.plugins.autogrid360.tests.support import seed_location_references
from app.routes.locations import locations_bp


class AutoGrid360ListingRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "autogrid360-listings.db"

        host_templates = Path(__file__).resolve().parents[4] / "app" / "templates"
        self.app = Flask(__name__, template_folder=str(host_templates))
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="autogrid360-listing-route-test",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            AUTOGRID360_IMAGE_ROOT=str(Path(self.temp_dir.name) / "images"),
            AUTOGRID360_MAX_LISTING_IMAGES=3,
            AUTOGRID360_MAX_IMAGE_BYTES=1024 * 1024,
            AUTOGRID360_MAX_IMAGE_PIXELS=24_000_000,
            AUTOGRID360_MAX_UPLOAD_REQUEST_BYTES=4 * 1024 * 1024,
            AUTOGRID360_LISTINGS_PER_PAGE=2,
            RATELIMIT_ENABLED=False,
            RATELIMIT_STORAGE_URI="memory://",
        )
        db.init_app(self.app)
        limiter.init_app(self.app)

        self.login_manager = LoginManager()
        self.login_manager.login_view = "login.login"
        self.login_manager.init_app(self.app)

        @self.login_manager.user_loader
        def load_user(session_id):
            return User.load_from_session_id(
                session_id,
                require_session_record=False,
            )

        for blueprint_name, route_path in (
            ("index", "/host-index"),
            ("about", "/host-about"),
            ("login", "/host-login"),
            ("dashboard", "/host-dashboard"),
            ("logout", "/host-logout"),
        ):
            blueprint = Blueprint(blueprint_name, __name__)
            blueprint.add_url_rule(
                route_path,
                endpoint=blueprint_name,
                view_func=lambda: "",
            )
            self.app.register_blueprint(blueprint)

        admin_blueprint = Blueprint("admin", __name__)
        admin_blueprint.add_url_rule(
            "/host-admin",
            endpoint="admin_home",
            view_func=lambda: "",
        )
        self.app.register_blueprint(admin_blueprint)

        self.app.register_blueprint(locations_bp)
        self.app.register_blueprint(public_bp)
        self.app.register_blueprint(admin_bp)
        self.app.register_blueprint(reference_bp)
        self.app.register_blueprint(account_bp)
        self.app.register_blueprint(listings_bp)
        self.app.register_blueprint(images_bp)
        self.app.register_blueprint(settings_bp)
        self.app.register_blueprint(tools_bp)

        @self.app.context_processor
        def inject_host_template_context():
            return {
                "tpl_path": "themes/default",
                "env": SimpleNamespace(
                    site_name="AutoGrid360 Test",
                    description="",
                    keywords="",
                    contact_enabled=False,
                    allow_registration=False,
                ),
                "nonce": "",
                "sidebar_position": "none",
                "current_year": 2026,
                "page_gen_time": 0,
                "autogrid360_navigation_label": "AutoGrid360",
            }

        self.app_context = self.app.app_context()
        self.app_context.push()
        db.metadata.create_all(
            bind=db.engine,
            tables=[
                User.__table__,
                Role.__table__,
                UserRole.__table__,
                EnvSettings.__table__,
                Country.__table__,
                Zone.__table__,
                AutoGrid360Settings.__table__,
                SellerProfile.__table__,
                PostalLocation.__table__,
                ReferenceValue.__table__,
                VehicleModel.__table__,
                Vehicle.__table__,
                vehicle_features,
                Listing.__table__,
                ListingImage.__table__,
            ],
        )

        seed_location_references()
        seed_reference_data()
        db.session.commit()

        self.seller = User(
            username="listing-seller",
            email="listing-seller@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        self.other_user = User(
            username="other-seller",
            email="other-seller@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        self.moderator = User(
            username="listing-moderator",
            email="listing-moderator@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        self.admin = User(
            username="listing-admin",
            email="listing-admin@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        self.moderator.roles.append(Role(name="moderator"))
        self.admin.roles.append(Role(name="admin"))
        db.session.add_all(
            [self.seller, self.other_user, self.moderator, self.admin]
        )
        db.session.commit()

        self.env_settings = EnvSettings(
            user_id=self.admin.id,
            site_name="AutoGrid360 Test",
            site_lang="en",
            site_timezone="UTC",
            description="",
            keywords="",
            users_per_page=20,
            users_stored_path=str(Path(self.temp_dir.name) / "users"),
            enable_logging=False,
            use_user_location=True,
            use_fancy_urls=False,
        )
        db.session.add(self.env_settings)
        db.session.commit()
        EnvSettings._cached_instance = None
        g.pop("_env_settings", None)


    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        g.pop("_env_settings", None)
        EnvSettings._cached_instance = None
        db.metadata.drop_all(
            bind=db.engine,
            tables=[
                ListingImage.__table__,
                Listing.__table__,
                vehicle_features,
                Vehicle.__table__,
                VehicleModel.__table__,
                ReferenceValue.__table__,
                PostalLocation.__table__,
                SellerProfile.__table__,
                AutoGrid360Settings.__table__,
                Zone.__table__,
                Country.__table__,
                EnvSettings.__table__,
                UserRole.__table__,
                Role.__table__,
                User.__table__,
            ],
        )
        db.engine.dispose()
        self.app_context.pop()
        self.temp_dir.cleanup()


    @staticmethod
    def public_listing_path(listing) -> str:
        return f"/autogrid360/listings/{listing.id}/{listing_slug(listing)}"

    def _login(self, client, user):
        with client.session_transaction() as flask_session:
            flask_session["_user_id"] = user.get_id()
            flask_session["_fresh"] = True

        g.pop("_login_user", None)


    def _set_fancy_urls(self, enabled: bool):
        self.env_settings.use_fancy_urls = enabled
        db.session.commit()
        EnvSettings._cached_instance = None
        g.pop("_env_settings", None)


    def _settings_form_data(self, **overrides):
        """Return complete required AutoGrid360 settings POST data for route tests."""

        data = {
            "listing_expiration_days": "60",
            "expiration_warning_days": "7",
            "expired_retention_days": "30",
            "expired_removal_warning_days": "7",
            "show_sale_pending_listings_publicly": "y",
            "show_sold_listings_publicly": "y",
            "sold_retention_days": "90",
            "listing_images_path": self.app.config["AUTOGRID360_IMAGE_ROOT"],
        }
        data.update(overrides)
        return data


    def _set_listing_policy(
        self,
        *,
        approval=True,
        rereview=True,
        expiration=False,
        expiration_days=60,
        warning_days=7,
        expired_retention_days=30,
        expired_removal_warning_days=7,
        show_sale_pending=True,
        show_sold=True,
        sold_retention_days=90,
    ):
        settings = db.session.get(AutoGrid360Settings, 1)
        if settings is None:
            settings = AutoGrid360Settings(
                id=1,
                listing_images_path=self.app.config["AUTOGRID360_IMAGE_ROOT"],
            )
            db.session.add(settings)
        settings.require_listing_approval = approval
        settings.require_rereview_on_edit = rereview
        settings.enable_listing_expiration = expiration
        settings.listing_expiration_days = expiration_days
        settings.expiration_warning_days = warning_days
        settings.expired_retention_days = expired_retention_days
        settings.expired_removal_warning_days = expired_removal_warning_days
        settings.show_sale_pending_listings_publicly = show_sale_pending
        settings.show_sold_listings_publicly = show_sold
        settings.sold_retention_days = sold_retention_days
        db.session.commit()
        return settings


    def _reference(self, category, value):
        reference = reference_by_key(category, value, active_only=False)
        self.assertIsNotNone(reference, f"Missing test reference {category}:{value}")
        return reference


    def _vehicle(
        self,
        *,
        make,
        vehicle_type=None,
        drivetrain=None,
        features=None,
        **kwargs,
    ):
        model_name = kwargs.pop("model", None)
        make_ref = self._reference(CATEGORY_MAKE, make)
        vehicle = Vehicle(
            make_ref=make_ref,
            vehicle_type_ref=(
                self._reference(CATEGORY_VEHICLE_TYPE, vehicle_type)
                if vehicle_type
                else None
            ),
            drivetrain_ref=(
                self._reference(CATEGORY_DRIVETRAIN, drivetrain)
                if drivetrain
                else None
            ),
            **kwargs,
        )
        if model_name:
            model_ref = vehicle_model_by_key(
                make_ref,
                model_name,
                active_only=False,
            )
            if model_ref is not None:
                vehicle.model_ref = model_ref
            else:
                vehicle.model_text = model_name
        if features:
            vehicle.features = [
                self._reference(CATEGORY_FEATURE, feature) for feature in features
            ]
        return vehicle


    def _image_file(self, filename="listing.jpg", color=(32, 64, 96)):
        payload = BytesIO()
        Image.new("RGB", (80, 60), color=color).save(payload, format="JPEG")
        payload.seek(0)
        return payload, filename


    def _create_image(self, listing, position=0, is_primary=False, token=None):
        token = token or f"image-{position}"
        image = ListingImage(
            listing=listing,
            storage_key=f"listings/{listing.id}/{token}.jpg",
            thumbnail_key=f"listings/{listing.id}/{token}_thumb.jpg",
            original_filename=f"{token}.jpg",
            position=position,
            is_primary=is_primary,
            width=80,
            height=60,
        )
        image_root = listing_image_root()
        for relative in (image.storage_key, image.thumbnail_key):
            path = image_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake-image")
        db.session.add(image)
        db.session.commit()
        return image


    def _create_listing(self):
        listing = Listing(
            seller=self.seller,
            vehicle=self._vehicle(
                year=2012,
                make="Honda",
                model="Civic",
                mileage=82000,
            ),
            title="Seller-owned Civic",
            price=Decimal("9250.00"),
            description="Original description",
            country_code="US",
            city="Freeport",
            zone_code="US-IL",
            postal_code="61032",
        )
        db.session.add(listing)
        db.session.commit()
        return listing


    def _create_active_inventory_listing(
        self,
        *,
        title,
        year,
        make,
        model,
        price=None,
        vehicle_type=None,
        drivetrain=None,
        features=None,
        condition=None,
        transmission=None,
        seller=None,
        zone_code=None,
    ):
        listing = Listing(
            seller=seller or self.seller,
            vehicle=self._vehicle(
                year=year,
                make=make,
                model=model,
                vehicle_type=vehicle_type,
                drivetrain=drivetrain,
                features=features,
                condition=condition,
                transmission=transmission,
            ),
            title=title,
            price=Decimal(price) if price is not None else None,
            status=STATUS_ACTIVE,
            country_code="US" if zone_code else None,
            zone_code=zone_code,
        )
        db.session.add(listing)
        db.session.commit()
        return listing


    def _postal_location(
        self,
        *,
        country_code,
        postal_code,
        latitude,
        longitude,
        locality=None,
        region=None,
    ):
        location = PostalLocation(
            country_code=country_code,
            postal_code=postal_code,
            locality=locality,
            region=region,
            latitude=latitude,
            longitude=longitude,
            source="test",
            active=True,
        )
        db.session.add(location)
        db.session.flush()
        return location


    @staticmethod
    def _inquiry_form_data(**overrides):
        data = {
            "name": "Buyer Person",
            "email": "Buyer@Example.com",
            "subject": "Availability",
            "message": "Is this vehicle still available?",
            "nobot_check": "",
        }
        data.update(overrides)
        return data


    @staticmethod
    def _seller_profile_form_data(**overrides):
        data = {
            "display_name": "Jane Seller",
            "company_name": "Jane Motors",
        }
        data.update(overrides)
        return data


    def _set_user_location_enabled(self, enabled):
        self.env_settings.use_user_location = enabled
        db.session.commit()
        EnvSettings._cached_instance = None
        g.pop("_env_settings", None)


    @staticmethod
    def _listing_form_data(**overrides):
        data = {
            "title": "Updated Civic",
            "price": "8750.00",
            "description": "Updated description",
            "year": "2013",
            "make": "Honda",
            "model": "honda:civic",
            "model_other": "",
            "trim": "EX",
            "vehicle_type": "Sedan",
            "doors": "4",
            "exterior_color": "Blue",
            "mileage": "84500",
            "condition": "Used",
            "engine": "1.8L",
            "transmission": "Automatic",
            "drivetrain": "FWD",
            "mpg": "31",
            "fuel_type": "Gasoline",
            "vin": "2HGFB2F50DH000001",
            "stock_number": "OA-1001",
            "country_code": "US",
            "city": "Freeport",
            "zone_code": "US-IL",
            "postal_code": "61032",
        }
        data.update(overrides)
        return data


    @staticmethod
    def _flash_messages(client):
        with client.session_transaction() as flask_session:
            return list(flask_session.get("_flashes", []))
