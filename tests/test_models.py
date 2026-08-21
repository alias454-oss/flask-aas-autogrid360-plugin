# app/plugins/autogrid360/tests/test_models.py
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import tempfile
import unittest
from pathlib import Path

from flask import Flask
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.core.extensions import db
from app.models import Country, User, Zone
from app.plugins.autogrid360.services.lifecycle import (
    admin_set_listing_status,
    approve_listing,
    expire_due_listings,
    expire_listing,
    make_listing_available,
    mark_expired_listing_edited,
    mark_sale_pending_listing,
    mark_sold_listing,
    relist_listing,
    return_public_listing_to_pending,
    submit_listing,
)
from app.plugins.autogrid360.models import (
    STATUS_ACTIVE,
    STATUS_DRAFT,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REMOVED,
    STATUS_SALE_PENDING,
    STATUS_SOLD,
    CATEGORY_DRIVETRAIN,
    CATEGORY_FEATURE,
    CATEGORY_MAKE,
    CATEGORY_VEHICLE_TYPE,
    Listing,
    ListingImage,
    AutoGrid360Settings,
    PostalLocation,
    ReferenceValue,
    SellerProfile,
    Vehicle,
    VehicleModel,
    vehicle_features,
)
from app.plugins.autogrid360.services.reference import (
    reference_by_key,
    seed_reference_data,
    vehicle_model_by_key,
)
from app.plugins.autogrid360.services.currency import parse_currency_input
from app.plugins.autogrid360.services.formatting import format_currency
from app.plugins.autogrid360.services.media import image_root, image_path
from app.plugins.autogrid360.services.settings import (
    currency_policy,
    distance_policy,
    listing_images_path,
    listing_policy,
)
from app.plugins.autogrid360.tests.support import seed_location_references


class AutoGrid360ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "autogrid360-models.db"

        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="autogrid360-model-test",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)

        self.app_context = self.app.app_context()
        self.app_context.push()

        db.metadata.create_all(
            bind=db.engine,
            tables=[
                User.__table__,
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

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
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
                User.__table__,
            ],
        )
        db.engine.dispose()
        self.app_context.pop()
        self.temp_dir.cleanup()

    def _seller(self) -> User:
        seller = User(
            username="autogrid360-seller",
            email="autogrid360-seller@example.test",
            hashed_password="not-used",
            activated=True,
            approved=True,
        )
        db.session.add(seller)
        db.session.flush()
        return seller

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

    def test_clean_database_creates_autogrid360_model_tables(self):
        inspector = inspect(db.engine)

        self.assertTrue(inspector.has_table("users"))
        self.assertTrue(inspector.has_table("plugin_autogrid360_reference_values"))
        self.assertTrue(inspector.has_table("plugin_autogrid360_postal_locations"))
        self.assertTrue(inspector.has_table("plugin_autogrid360_vehicle_models"))
        self.assertTrue(inspector.has_table("plugin_autogrid360_vehicle_features"))
        self.assertTrue(inspector.has_table("plugin_autogrid360_vehicles"))
        self.assertTrue(inspector.has_table("plugin_autogrid360_listings"))
        self.assertTrue(inspector.has_table("plugin_autogrid360_listing_images"))
        self.assertTrue(inspector.has_table("plugin_autogrid360_seller_profiles"))
        self.assertTrue(inspector.has_table("plugin_autogrid360_settings"))
        listing_columns = {
            column["name"]
            for column in inspector.get_columns("plugin_autogrid360_listings")
        }
        self.assertIn("portable_id", listing_columns)
        self.assertIn("expiration_warning_sent_at", listing_columns)
        self.assertIn("first_published_at", listing_columns)
        self.assertIn("expired_at", listing_columns)
        self.assertIn("sold_at", listing_columns)
        self.assertIn("expired_edited_at", listing_columns)
        self.assertIn("expired_removal_warning_sent_at", listing_columns)
        self.assertIn("aged_out_at", listing_columns)
        self.assertIn("aged_out_notice_sent_at", listing_columns)
        self.assertIn("postal_location_id", listing_columns)
        self.assertIn("country_code", listing_columns)
        self.assertIn("zone_code", listing_columns)
        seller_columns = {
            column["name"]
            for column in inspector.get_columns("plugin_autogrid360_seller_profiles")
        }
        self.assertEqual(
            seller_columns,
            {
                "id",
                "user_id",
                "display_name",
                "company_name",
                "created_at",
                "updated_at",
            },
        )
        self.assertNotIn("country", listing_columns)
        self.assertNotIn("state", listing_columns)
        settings_columns = {
            column["name"]
            for column in inspector.get_columns("plugin_autogrid360_settings")
        }
        self.assertIn("currency_code", settings_columns)
        self.assertIn("currency_symbol", settings_columns)
        self.assertIn("currency_decimal_separator", settings_columns)
        self.assertIn("currency_thousands_separator", settings_columns)
        self.assertIn("default_distance_unit", settings_columns)
        self.assertIn("listing_images_path", settings_columns)
        self.assertIn("allow_seller_inventory_import", settings_columns)
        self.assertIn("expired_retention_days", settings_columns)
        self.assertIn("expired_removal_warning_days", settings_columns)
        self.assertIn("show_sale_pending_listings_publicly", settings_columns)
        self.assertIn("show_sold_listings_publicly", settings_columns)
        self.assertIn("sold_retention_days", settings_columns)

        seller_fk = next(iter(Listing.__table__.c.seller_id.foreign_keys))
        self.assertEqual(seller_fk.target_fullname, "users.id")
        make_fk = next(iter(Vehicle.__table__.c.make_id.foreign_keys))
        self.assertEqual(
            make_fk.target_fullname,
            "plugin_autogrid360_reference_values.id",
        )
        model_fk = next(iter(Vehicle.__table__.c.model_id.foreign_keys))
        self.assertEqual(
            model_fk.target_fullname,
            "plugin_autogrid360_vehicle_models.id",
        )
        postal_fk = next(iter(Listing.__table__.c.postal_location_id.foreign_keys))
        self.assertEqual(
            postal_fk.target_fullname,
            "plugin_autogrid360_postal_locations.id",
        )

    def test_listing_portable_identity_is_generated_and_unique(self):
        seller = self._seller()
        first = Listing(
            seller=seller,
            vehicle=self._vehicle(make="Honda", model="Civic"),
            title="First portable listing",
        )
        second = Listing(
            seller=seller,
            vehicle=self._vehicle(make="Ford", model="Focus"),
            title="Second portable listing",
        )
        db.session.add_all([first, second])
        db.session.commit()

        self.assertEqual(len(first.portable_id), 36)
        self.assertEqual(len(second.portable_id), 36)
        self.assertNotEqual(first.portable_id, second.portable_id)

    def test_listing_image_storage_defaults_to_project_upload_directory(self):
        self.assertEqual(listing_images_path(), "uploads/listings")
        self.assertEqual(
            image_root(),
            (Path(self.app.root_path).parent / "uploads/listings").resolve(),
        )

    def test_listing_image_storage_deployment_seed_is_used_before_settings_exist(self):
        configured = Path(self.temp_dir.name) / "seed-images"
        self.app.config["AUTOGRID360_IMAGE_ROOT"] = str(configured)

        self.assertEqual(listing_images_path(), str(configured))
        self.assertEqual(image_root(), configured.resolve())

    def test_persisted_listing_image_storage_overrides_deployment_seed(self):
        self.app.config["AUTOGRID360_IMAGE_ROOT"] = str(
            Path(self.temp_dir.name) / "seed-images"
        )
        db.session.add(
            AutoGrid360Settings(
                id=1,
                listing_images_path="uploads/persisted-listings",
            )
        )
        db.session.commit()

        self.assertEqual(
            listing_images_path(),
            "uploads/persisted-listings",
        )
        self.assertEqual(
            image_root(),
            (Path(self.app.root_path).parent / "uploads/persisted-listings").resolve(),
        )

    def test_absolute_listing_image_storage_path_is_used_as_configured(self):
        configured = Path(self.temp_dir.name) / "absolute-listings"
        db.session.add(
            AutoGrid360Settings(
                id=1,
                listing_images_path=str(configured),
            )
        )
        db.session.commit()

        self.assertEqual(image_root(), configured.resolve())

    def test_changing_listing_image_storage_path_does_not_move_existing_files(self):
        old_root = Path(self.temp_dir.name) / "old-listings"
        new_root = Path(self.temp_dir.name) / "new-listings"
        settings = AutoGrid360Settings(
            id=1,
            listing_images_path=str(old_root),
        )
        db.session.add(settings)
        db.session.commit()

        old_path = image_path("1/example.jpg")
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_bytes(b"example")

        settings.listing_images_path = str(new_root)
        db.session.commit()

        self.assertTrue(old_path.is_file())
        self.assertEqual(image_path("1/example.jpg"), new_root / "1/example.jpg")
        self.assertFalse(image_path("1/example.jpg").exists())

    def test_listing_image_storage_key_cannot_escape_configured_root(self):
        configured = Path(self.temp_dir.name) / "listing-images"
        db.session.add(
            AutoGrid360Settings(
                id=1,
                listing_images_path=str(configured),
            )
        )
        db.session.commit()

        with self.assertRaises(ValueError):
            image_path("../escape.jpg")

    def test_listing_policy_defaults_to_secure_moderation_settings(self):
        policy = listing_policy()

        self.assertTrue(policy.require_approval)
        self.assertTrue(policy.require_rereview)
        self.assertTrue(policy.rereview_active_edits)
        self.assertFalse(policy.expiration_enabled)
        self.assertEqual(policy.expiration_days, 60)
        self.assertEqual(policy.expiration_warning_days, 7)
        self.assertEqual(policy.expired_retention_days, 30)
        self.assertEqual(policy.expired_removal_warning_days, 7)
        self.assertTrue(policy.show_sale_pending_publicly)
        self.assertTrue(policy.show_sold_publicly)
        self.assertEqual(policy.sold_retention_days, 90)
        self.assertEqual(
            policy.public_statuses,
            (STATUS_ACTIVE, STATUS_SALE_PENDING, STATUS_SOLD),
        )
        self.assertIsNone(policy.active_expiration_days)

    def test_currency_policy_defaults_and_formatter_use_us_style(self):
        policy = currency_policy()

        self.assertEqual(policy.code, "USD")
        self.assertEqual(policy.symbol, "$")
        self.assertEqual(policy.decimal_separator, ".")
        self.assertEqual(policy.thousands_separator, ",")
        self.assertEqual(format_currency(Decimal("12345.6")), "$12,345.60")

    def test_price_parser_accepts_common_human_us_currency_input(self):
        for raw in ("8950", "8,950", "$8,950", "$ 8,950", "  8,950  "):
            with self.subTest(raw=raw):
                self.assertEqual(parse_currency_input(raw), Decimal("8950"))

    def test_distance_policy_defaults_to_auto_and_reads_persisted_override(self):
        self.assertEqual(distance_policy().default_unit, "auto")

        db.session.add(AutoGrid360Settings(id=1, default_distance_unit="kilometers"))
        db.session.commit()

        self.assertEqual(distance_policy().default_unit, "kilometers")

    def test_currency_policy_reads_persisted_formatting_settings(self):
        db.session.add(
            AutoGrid360Settings(
                id=1,
                currency_code="EUR",
                currency_symbol="€",
                currency_decimal_separator=",",
                currency_thousands_separator=".",
            )
        )
        db.session.commit()

        policy = currency_policy()

        self.assertEqual(policy.code, "EUR")
        self.assertEqual(policy.symbol, "€")
        self.assertEqual(policy.decimal_separator, ",")
        self.assertEqual(policy.thousands_separator, ".")
        self.assertEqual(format_currency(Decimal("12345.6")), "€12.345,60")

    def test_listing_policy_reads_persisted_expiration_settings(self):
        db.session.add(
            AutoGrid360Settings(
                id=1,
                require_listing_approval=False,
                require_rereview_on_edit=False,
                enable_listing_expiration=True,
                listing_expiration_days=90,
                expiration_warning_days=10,
                expired_retention_days=45,
                expired_removal_warning_days=5,
                show_sale_pending_listings_publicly=False,
                show_sold_listings_publicly=False,
                sold_retention_days=120,
            )
        )
        db.session.commit()

        policy = listing_policy()

        self.assertFalse(policy.require_approval)
        self.assertFalse(policy.require_rereview)
        self.assertTrue(policy.expiration_enabled)
        self.assertEqual(policy.expiration_days, 90)
        self.assertEqual(policy.expiration_warning_days, 10)
        self.assertEqual(policy.expired_retention_days, 45)
        self.assertEqual(policy.expired_removal_warning_days, 5)
        self.assertFalse(policy.show_sale_pending_publicly)
        self.assertFalse(policy.show_sold_publicly)
        self.assertEqual(policy.sold_retention_days, 120)
        self.assertEqual(policy.public_statuses, (STATUS_ACTIVE,))
        self.assertEqual(policy.active_expiration_days, 90)

    def test_submit_listing_supports_approval_or_direct_publish_without_committing(self):
        seller = self._seller()
        pending = Listing(
            seller=seller,
            vehicle=self._vehicle(make="Honda", model="Civic"),
            title="Needs approval",
        )
        direct = Listing(
            seller=seller,
            vehicle=self._vehicle(make="Ford", model="Focus"),
            title="Direct publish",
        )
        db.session.add_all([pending, direct])
        db.session.commit()
        now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)

        submit_listing(pending, require_approval=True, expiration_days=60, now=now)
        submit_listing(direct, require_approval=False, expiration_days=60, now=now)

        self.assertEqual(pending.status, STATUS_PENDING)
        self.assertIsNone(pending.published_at)
        self.assertIsNone(pending.expires_at)
        self.assertEqual(direct.status, STATUS_ACTIVE)
        self.assertEqual(direct.published_at, now)
        self.assertEqual(direct.expires_at, now + timedelta(days=60))

        db.session.rollback()
        db.session.expire_all()
        self.assertEqual(db.session.get(Listing, pending.id).status, STATUS_DRAFT)
        self.assertEqual(db.session.get(Listing, direct.id).status, STATUS_DRAFT)

    def test_approve_listing_assigns_a_fresh_expiration_deadline(self):
        listing = Listing(
            seller=self._seller(),
            vehicle=self._vehicle(make="Honda", model="Accord"),
            title="Pending Accord",
            status=STATUS_PENDING,
            expiration_warning_sent_at=datetime(
                2026, 8, 1, 12, 0, tzinfo=timezone.utc
            ),
        )
        db.session.add(listing)
        db.session.commit()
        now = datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)

        approve_listing(listing, now=now, expiration_days=45)

        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertEqual(listing.published_at, now)
        self.assertEqual(listing.expires_at, now + timedelta(days=45))
        self.assertIsNone(listing.expiration_warning_sent_at)

    def test_rereview_transition_is_policy_controlled_and_caller_owned(self):
        listing = Listing(
            seller=self._seller(),
            vehicle=self._vehicle(make="Honda", model="Accord"),
            title="Published Accord",
            status=STATUS_ACTIVE,
            published_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            expiration_warning_sent_at=datetime.now(timezone.utc),
        )
        db.session.add(listing)
        db.session.commit()
        listing_id = listing.id

        self.assertFalse(
            return_public_listing_to_pending(listing, require_rereview=False)
        )
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertTrue(
            return_public_listing_to_pending(listing, require_rereview=True)
        )
        self.assertEqual(listing.status, STATUS_PENDING)
        self.assertIsNone(listing.published_at)
        self.assertIsNone(listing.expires_at)
        self.assertIsNone(listing.expiration_warning_sent_at)

        db.session.rollback()
        db.session.expire_all()
        self.assertEqual(db.session.get(Listing, listing_id).status, STATUS_ACTIVE)

    def test_sale_pending_listing_follows_published_rereview_policy(self):
        listing = Listing(
            seller=self._seller(),
            vehicle=self._vehicle(make="Honda", model="Accord"),
            title="Sale Pending Accord",
            status=STATUS_SALE_PENDING,
            published_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db.session.add(listing)
        db.session.commit()

        self.assertTrue(
            return_public_listing_to_pending(listing, require_rereview=True)
        )
        self.assertEqual(listing.status, STATUS_PENDING)
        self.assertIsNone(listing.published_at)
        self.assertIsNone(listing.expires_at)

    def test_sale_pending_sold_and_available_transitions_preserve_publication_cycle(self):
        now = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)
        listing = Listing(
            seller=self._seller(),
            vehicle=self._vehicle(make="Honda", model="Civic"),
            title="Lifecycle Civic",
            status=STATUS_ACTIVE,
            first_published_at=now - timedelta(days=10),
            published_at=now - timedelta(days=10),
            expires_at=now + timedelta(days=50),
        )
        db.session.add(listing)
        db.session.commit()

        mark_sale_pending_listing(listing, now=now, expiration_days=60)
        self.assertEqual(listing.status, STATUS_SALE_PENDING)
        self.assertIsNone(listing.sold_at)

        mark_sold_listing(listing, now=now)
        self.assertEqual(listing.status, STATUS_SOLD)
        self.assertEqual(listing.sold_at, now)

        make_listing_available(listing, now=now + timedelta(days=1), expiration_days=60)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertIsNone(listing.sold_at)
        first_published_at = listing.first_published_at
        published_at = listing.published_at
        expires_at = listing.expires_at
        if first_published_at.tzinfo is None:
            first_published_at = first_published_at.replace(tzinfo=timezone.utc)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        self.assertEqual(first_published_at, now - timedelta(days=10))
        self.assertEqual(published_at, now - timedelta(days=10))
        self.assertEqual(expires_at, now + timedelta(days=50))

    def test_admin_status_override_uses_lifecycle_bookkeeping(self):
        now = datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)
        listing = Listing(
            seller=self._seller(),
            vehicle=self._vehicle(make="Honda", model="Civic"),
            title="Admin Lifecycle Civic",
            status=STATUS_SOLD,
            first_published_at=now - timedelta(days=5),
            published_at=now - timedelta(days=5),
            expires_at=now + timedelta(days=55),
            sold_at=now - timedelta(days=1),
        )
        db.session.add(listing)
        db.session.commit()

        admin_set_listing_status(listing, STATUS_PENDING, now=now, expiration_days=60)
        self.assertEqual(listing.status, STATUS_PENDING)
        self.assertIsNone(listing.published_at)
        self.assertIsNone(listing.expires_at)
        self.assertIsNone(listing.sold_at)

        admin_set_listing_status(listing, STATUS_ACTIVE, now=now, expiration_days=60)
        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertEqual(listing.published_at, now)
        self.assertEqual(listing.expires_at, now + timedelta(days=60))

        admin_set_listing_status(listing, STATUS_SALE_PENDING, now=now, expiration_days=60)
        self.assertEqual(listing.status, STATUS_SALE_PENDING)

        admin_set_listing_status(listing, STATUS_SOLD, now=now, expiration_days=60)
        self.assertEqual(listing.status, STATUS_SOLD)
        self.assertEqual(listing.sold_at, now)

    def test_seller_profile_persists_only_autogrid360_presentation_data(self):
        seller = self._seller()
        profile = SellerProfile(
            user_id=seller.id,
            display_name="Jane Seller",
            company_name="Jane Motors",
        )
        db.session.add(profile)
        db.session.commit()

        loaded = db.session.get(SellerProfile, profile.id)
        self.assertEqual(loaded.user_id, seller.id)
        self.assertEqual(loaded.user.username, "autogrid360-seller")
        self.assertEqual(loaded.public_label, "Jane Motors")
        for field_name in (
            "phone",
            "alternate_phone",
            "fax",
            "address",
            "city",
            "zone_code",
            "postal_code",
            "country_code",
        ):
            self.assertFalse(hasattr(loaded, field_name))

    def test_seller_profile_user_id_is_unique(self):
        seller = self._seller()
        db.session.add(SellerProfile(user_id=seller.id, display_name="First"))
        db.session.commit()

        db.session.add(SellerProfile(user_id=seller.id, display_name="Second"))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()


    def test_listing_persists_vehicle_and_flask_aas_owner(self):
        seller = self._seller()
        vehicle = self._vehicle(
            year=2005,
            make="Ford",
            model="KA Sport",
            vehicle_type="Convertible",
            mileage=89989,
            vin="1FTCR10T7KUB59290",
        )
        listing = Listing(
            seller=seller,
            vehicle=vehicle,
            title="Nice little Ford",
            price=Decimal("32565.00"),
            description="Initial AutoGrid360 persistence test listing.",
            zone_code="US-IL",
            postal_code="61032",
        )
        db.session.add(listing)
        db.session.commit()

        stored = db.session.get(Listing, listing.id)
        self.assertEqual(stored.seller_id, seller.id)
        self.assertEqual(stored.seller.username, "autogrid360-seller")
        self.assertEqual(stored.vehicle.make, "Ford")
        self.assertEqual(stored.vehicle.model, "KA Sport")
        self.assertEqual(stored.price, Decimal("32565.00"))
        self.assertEqual(stored.status, STATUS_DRAFT)
        self.assertIn(stored, stored.vehicle.listings)

    def test_listing_status_labels_follow_lifecycle(self):
        seller = self._seller()
        cases = (
            (STATUS_ACTIVE, "Active"),
            (STATUS_SALE_PENDING, "Sale Pending"),
            (STATUS_SOLD, "Sold"),
            (STATUS_EXPIRED, "Expired"),
            (STATUS_REMOVED, "Removed"),
        )

        for status, label in cases:
            with self.subTest(status=status):
                listing = Listing(
                    seller=seller,
                    vehicle=self._vehicle(make="Honda", model=f"Civic {status}"),
                    title=f"{label} Honda Civic",
                    status=status,
                )
                db.session.add(listing)
                db.session.flush()
                self.assertEqual(listing.status_label, label)


    def test_expire_due_listings_transitions_active_and_sale_pending_rows(self):
        seller = self._seller()
        now = datetime.now(timezone.utc)
        due = Listing(
            seller=seller,
            vehicle=self._vehicle(make="Honda", model="Due Civic"),
            title="Due listing",
            status=STATUS_ACTIVE,
            expires_at=now - timedelta(minutes=2),
        )
        sale_pending = Listing(
            seller=seller,
            vehicle=self._vehicle(make="Honda", model="Pending Civic"),
            title="Due Sale Pending listing",
            status=STATUS_SALE_PENDING,
            expires_at=now - timedelta(minutes=1),
        )
        future = Listing(
            seller=seller,
            vehicle=self._vehicle(make="Honda", model="Future Civic"),
            title="Future listing",
            status=STATUS_ACTIVE,
            expires_at=now + timedelta(days=1),
        )
        sold = Listing(
            seller=seller,
            vehicle=self._vehicle(make="Honda", model="Sold Civic"),
            title="Sold due listing",
            status=STATUS_SOLD,
            expires_at=now - timedelta(days=1),
        )
        db.session.add_all([due, sale_pending, future, sold])
        db.session.commit()

        expired = expire_due_listings(now=now)

        self.assertEqual(
            [listing.id for listing in expired],
            [due.id, sale_pending.id],
        )
        self.assertEqual(due.status, STATUS_EXPIRED)
        self.assertEqual(sale_pending.status, STATUS_EXPIRED)
        self.assertEqual(future.status, STATUS_ACTIVE)
        self.assertEqual(sold.status, STATUS_SOLD)

    def test_expire_due_listings_leaves_transaction_control_to_caller(self):
        seller = self._seller()
        now = datetime.now(timezone.utc)
        listing = Listing(
            seller=seller,
            vehicle=self._vehicle(make="Honda", model="Rollback Civic"),
            title="Rollback expiration",
            status=STATUS_ACTIVE,
            expires_at=now - timedelta(minutes=1),
        )
        db.session.add(listing)
        db.session.commit()
        listing_id = listing.id

        expire_due_listings(now=now)
        self.assertEqual(listing.status, STATUS_EXPIRED)

        db.session.rollback()
        db.session.expire_all()
        self.assertEqual(db.session.get(Listing, listing_id).status, STATUS_ACTIVE)

    def test_vehicle_can_be_relisted_without_duplication(self):
        seller = self._seller()
        vehicle = self._vehicle(make="Chevrolet", model="Camaro", year=1969)
        first = Listing(seller=seller, vehicle=vehicle, title="1969 Camaro")
        second = Listing(seller=seller, vehicle=vehicle, title="1969 Camaro relisted")
        db.session.add_all([first, second])
        db.session.commit()

        self.assertEqual(Vehicle.query.count(), 1)
        self.assertEqual(Listing.query.count(), 2)
        self.assertEqual({item.vehicle_id for item in Listing.query.all()}, {vehicle.id})


    def test_listing_owns_ordered_images_and_deletes_image_records(self):
        seller = self._seller()
        vehicle = self._vehicle(make="Ford", model="Mustang")
        listing = Listing(seller=seller, vehicle=vehicle, title="Mustang")
        listing.images.extend(
            [
                ListingImage(
                    storage_key="listings/1/second.jpg",
                    thumbnail_key="listings/1/second_thumb.jpg",
                    position=1,
                    is_primary=False,
                    width=800,
                    height=600,
                ),
                ListingImage(
                    storage_key="listings/1/first.jpg",
                    thumbnail_key="listings/1/first_thumb.jpg",
                    position=0,
                    is_primary=True,
                    width=800,
                    height=600,
                ),
            ]
        )
        db.session.add(listing)
        db.session.commit()

        self.assertEqual([image.position for image in listing.images], [0, 1])
        self.assertTrue(listing.images[0].is_primary)
        vehicle_id = vehicle.id

        db.session.delete(listing)
        db.session.commit()

        self.assertEqual(ListingImage.query.count(), 0)
        self.assertIsNotNone(db.session.get(Vehicle, vehicle_id))

    def test_legacy_automotive_reference_defaults_are_seeded(self):
        counts = {
            category: ReferenceValue.query.filter_by(category=category).count()
            for category in (
                CATEGORY_MAKE,
                CATEGORY_VEHICLE_TYPE,
                CATEGORY_DRIVETRAIN,
                CATEGORY_FEATURE,
            )
        }

        self.assertEqual(counts[CATEGORY_MAKE], 58)
        self.assertEqual(counts[CATEGORY_VEHICLE_TYPE], 14)
        self.assertEqual(counts[CATEGORY_DRIVETRAIN], 4)
        self.assertEqual(counts[CATEGORY_FEATURE], 25)
        self.assertEqual(self._reference(CATEGORY_MAKE, "Ford").label, "Ford")
        self.assertEqual(
            self._reference(CATEGORY_DRIVETRAIN, "FWD").label,
            "Front Wheel Drive",
        )
        self.assertEqual(
            self._reference(CATEGORY_DRIVETRAIN, "4WD").label,
            "Four Wheel Drive",
        )
        honda = self._reference(CATEGORY_MAKE, "Honda")
        civic = vehicle_model_by_key(honda, "Civic", active_only=False)
        self.assertIsNotNone(civic)
        self.assertEqual(civic.label, "Civic")
        self.assertEqual(civic.make_id, honda.id)
        self.assertGreater(VehicleModel.query.count(), 800)

    def test_reference_reseed_preserves_runtime_identity_and_customization(self):
        ford = self._reference(CATEGORY_MAKE, "Ford")
        original_id = ford.id
        ford.label = "Ford Motor Company"
        ford.active = False
        ford.sort_order = 999
        ford.default_selected = True
        db.session.commit()

        inserted = seed_reference_data()
        db.session.commit()
        loaded = db.session.get(ReferenceValue, original_id)

        self.assertEqual(inserted, 0)
        self.assertEqual(loaded.id, original_id)
        self.assertEqual(loaded.key, "ford")
        self.assertEqual(loaded.label, "Ford Motor Company")
        self.assertFalse(loaded.active)
        self.assertEqual(loaded.sort_order, 999)
        self.assertTrue(loaded.default_selected)

    def test_adding_seed_value_never_repurposes_existing_database_id(self):
        chevrolet = self._reference(CATEGORY_MAKE, "Chevrolet")
        original_id = chevrolet.id
        root = Path(self.temp_dir.name) / "reference-seed"
        root.mkdir()
        payloads = {
            "makes.json": [
                {"key": "chevrolet", "label": "Do Not Overwrite"},
                {"key": "future-make", "label": "Future Make"},
            ],
            "types.json": [],
            "drivetrains.json": [],
            "features.json": [],
        }
        for filename, payload in payloads.items():
            (root / filename).write_text(json.dumps(payload), encoding="utf-8")

        inserted = seed_reference_data(data_root=root)
        db.session.commit()

        self.assertEqual(inserted, 1)
        self.assertEqual(db.session.get(ReferenceValue, original_id).key, "chevrolet")
        self.assertEqual(db.session.get(ReferenceValue, original_id).label, "Chevrolet")
        future = ReferenceValue.query.filter_by(
            category=CATEGORY_MAKE,
            key="future-make",
        ).one()
        self.assertNotEqual(future.id, original_id)

    def test_vehicle_uses_reference_ids_and_structured_features(self):
        vehicle = self._vehicle(
            make="Ford",
            vehicle_type="Convertible",
            drivetrain="FWD",
            features=["Air Conditioning", "Cruise Control"],
            model="Mustang",
        )
        db.session.add(vehicle)
        db.session.commit()

        self.assertEqual(vehicle.make_id, self._reference(CATEGORY_MAKE, "Ford").id)
        self.assertEqual(vehicle.make, "Ford")
        self.assertIsNotNone(vehicle.model_id)
        self.assertIsNone(vehicle.model_text)
        self.assertEqual(vehicle.model, "Mustang")
        self.assertEqual(vehicle.vehicle_type, "Convertible")
        self.assertEqual(vehicle.drivetrain, "Front Wheel Drive")
        self.assertEqual(
            [feature.label for feature in vehicle.features],
            ["Air Conditioning", "Cruise Control"],
        )

    def test_vehicle_preserves_unlisted_model_text_without_creating_reference_rows(self):
        before = VehicleModel.query.count()
        vehicle = self._vehicle(make="Ford", model="One-Off Coachbuilt Special")
        db.session.add(vehicle)
        db.session.commit()

        self.assertIsNone(vehicle.model_id)
        self.assertEqual(vehicle.model_text, "One-Off Coachbuilt Special")
        self.assertEqual(vehicle.model, "One-Off Coachbuilt Special")
        self.assertEqual(VehicleModel.query.count(), before)

    def test_relist_unchanged_expired_listing_preserves_first_listed_date(self):
        seller = self._seller()
        listing = Listing(
            seller=seller,
            vehicle=self._vehicle(make="Honda", model="Civic"),
            title="Relist Civic",
        )
        db.session.add(listing)
        db.session.commit()
        first_publication = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        expired_at = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
        relisted_at = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)

        submit_listing(
            listing,
            require_approval=False,
            expiration_days=60,
            now=first_publication,
        )
        expire_listing(listing, now=expired_at)
        relist_listing(
            listing,
            require_approval=True,
            expiration_days=60,
            now=relisted_at,
        )

        self.assertEqual(listing.status, STATUS_ACTIVE)
        self.assertEqual(listing.first_published_at, first_publication)
        self.assertEqual(listing.published_at, relisted_at)
        self.assertEqual(listing.expires_at, relisted_at + timedelta(days=60))
        self.assertIsNone(listing.expired_at)
        self.assertIsNone(listing.expired_edited_at)

    def test_changed_expired_listing_follows_current_approval_policy(self):
        listing = Listing(
            seller=self._seller(),
            vehicle=self._vehicle(make="Honda", model="Accord"),
            title="Changed Accord",
            status=STATUS_ACTIVE,
            first_published_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
            published_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )
        db.session.add(listing)
        db.session.commit()

        expire_listing(
            listing,
            now=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
        )
        mark_expired_listing_edited(
            listing,
            now=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        )
        relist_listing(
            listing,
            require_approval=True,
            expiration_days=60,
            now=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(listing.status, STATUS_PENDING)
        first_published_at = listing.first_published_at
        if first_published_at.tzinfo is None:
            first_published_at = first_published_at.replace(tzinfo=timezone.utc)
        self.assertEqual(
            first_published_at,
            datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )
        self.assertIsNone(listing.published_at)
        self.assertIsNone(listing.expires_at)
        self.assertIsNone(listing.expired_at)
        self.assertIsNone(listing.expired_edited_at)


if __name__ == "__main__":
    unittest.main()
