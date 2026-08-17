# app/plugins/autogrid360/tests/test_transfer.py
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from click.testing import CliRunner
from flask import Flask
from PIL import Image

from app.core.extensions import db
from app.models import Country, User, Zone
from app.plugins.autogrid360.cli import cli
from app.plugins.autogrid360.models import (
    CATEGORY_DRIVETRAIN,
    CATEGORY_FEATURE,
    CATEGORY_MAKE,
    CATEGORY_VEHICLE_TYPE,
    STATUS_ACTIVE,
    STATUS_DRAFT,
    STATUS_SOLD,
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
from app.plugins.autogrid360.services.transfer import (
    BUNDLE_FORMAT,
    BUNDLE_SCOPE_SITE,
    BUNDLE_VERSION,
    InventoryBundleError,
    export_inventory_bundle,
    export_site_inventory_bundle,
    import_inventory_bundle,
    inspect_inventory_bundle,
    parse_seller_mapping_entries,
    resolve_restore_seller_mapping,
    restore_inventory_bundle,
)
from app.plugins.autogrid360.services.media import image_root as listing_image_root
from app.plugins.autogrid360.services.reference import (
    reference_by_key,
    seed_reference_data,
    vehicle_model_by_key,
)
from app.plugins.autogrid360.tests.support import seed_location_references


class AutoGrid360PortableInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        database_path = root / "portable.db"

        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="autogrid360-portable-test",
            SQLALCHEMY_DATABASE_URI=f"sqlite:///{database_path}",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            AUTOGRID360_IMAGE_ROOT=str(root / "images"),
            AUTOGRID360_MAX_LISTING_IMAGES=12,
            AUTOGRID360_MAX_IMAGE_BYTES=1024 * 1024,
            AUTOGRID360_MAX_IMAGE_PIXELS=24_000_000,
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

        self.source = User(
            username="source-seller",
            email="source@example.test",
            hashed_password="secret-hash-must-not-export",
            activated=True,
            approved=True,
        )
        self.target = User(
            username="target-seller",
            email="target@example.test",
            hashed_password="target-secret-hash",
            activated=True,
            approved=True,
        )
        self.second_source = User(
            username="second-source",
            email="second-source@example.test",
            hashed_password="second-source-secret",
            activated=True,
            approved=True,
        )
        self.second_target = User(
            username="second-target",
            email="second-target@example.test",
            hashed_password="second-target-secret",
            activated=True,
            approved=True,
        )
        db.session.add_all(
            [self.source, self.target, self.second_source, self.second_target]
        )
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

    def _reference(self, category, key):
        value = reference_by_key(category, key, active_only=False)
        self.assertIsNotNone(value)
        return value

    def _create_source_inventory(
        self,
        *,
        seller=None,
        with_profile=True,
        with_image=True,
        title="Portable Civic",
    ):
        seller = seller or self.source
        make = self._reference(CATEGORY_MAKE, "honda")
        model = vehicle_model_by_key(make, "civic", active_only=False)
        self.assertIsNotNone(model)
        vehicle = Vehicle(
            year=2018,
            make_ref=make,
            model_ref=model,
            trim="EX",
            vehicle_type_ref=self._reference(CATEGORY_VEHICLE_TYPE, "sedan"),
            doors=4,
            exterior_color="Blue",
            mileage=45000,
            condition="Used",
            engine="2.0L",
            transmission="Automatic",
            drivetrain_ref=self._reference(CATEGORY_DRIVETRAIN, "fwd"),
            mpg=34,
            fuel_type="Gasoline",
            vin="2HGFC2F70JH000001",
            stock_number="OA-PORTABLE",
        )
        vehicle.features = [
            self._reference(CATEGORY_FEATURE, "air-conditioning"),
            self._reference(CATEGORY_FEATURE, "cruise-control"),
        ]
        now = datetime.now(timezone.utc)
        listing = Listing(
            seller=seller,
            vehicle=vehicle,
            title=title,
            price=Decimal("18450.00"),
            description="Portable inventory description",
            status=STATUS_ACTIVE,
            featured=True,
            country_code="US",
            city="Freeport",
            zone_code="US-IL",
            postal_code="61032",
            view_count=87,
            created_at=now - timedelta(days=30),
            first_published_at=now - timedelta(days=20),
            published_at=now - timedelta(days=10),
            expires_at=now + timedelta(days=50),
            expiration_warning_sent_at=now,
        )
        db.session.add(listing)
        if with_profile:
            db.session.add(
                SellerProfile(
                    user_id=seller.id,
                    display_name=f"{seller.username} display",
                    company_name=f"{seller.username} Motors",
                )
            )
        db.session.commit()

        if with_image:
            storage_key = f"listings/{listing.id}/portable.jpg"
            thumbnail_key = f"listings/{listing.id}/portable_thumb.jpg"
            root = listing_image_root()
            for key, size in ((storage_key, (160, 120)), (thumbnail_key, (80, 60))):
                path = root / key
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", size, color=(20, 80, 140)).save(path, format="JPEG")
            db.session.add(
                ListingImage(
                    listing=listing,
                    storage_key=storage_key,
                    thumbnail_key=thumbnail_key,
                    original_filename="seller-photo.png",
                    position=0,
                    is_primary=True,
                    width=160,
                    height=120,
                )
            )
            db.session.commit()
        return listing

    def _export_path(self):
        return Path(self.temp_dir.name) / "inventory.zip"

    def _rewrite_manifest(self, source_path, mutate, *, image_bytes=None):
        destination = Path(self.temp_dir.name) / "rewritten.zip"
        with zipfile.ZipFile(source_path, "r") as source:
            manifest = json.loads(source.read("manifest.json"))
            mutate(manifest)
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for info in source.infolist():
                    if info.is_dir() or info.filename == "manifest.json":
                        continue
                    payload = source.read(info.filename)
                    if image_bytes is not None and info.filename.startswith("images/"):
                        payload = image_bytes
                    output.writestr(info.filename, payload)
                output.writestr(
                    "manifest.json",
                    json.dumps(manifest, sort_keys=True).encode("utf-8"),
                )
        return destination

    def _remove_source_listing(self, listing):
        vehicle = listing.vehicle
        db.session.delete(listing)
        db.session.flush()
        db.session.delete(vehicle)
        db.session.commit()

    @staticmethod
    def _utc(value):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def test_export_bundle_uses_stable_reference_keys_and_excludes_host_secrets(self):
        listing = self._create_source_inventory()
        destination = self._export_path()

        result = export_inventory_bundle(self.source, destination)

        self.assertEqual(result.listings_exported, 1)
        self.assertEqual(result.images_exported, 1)
        with zipfile.ZipFile(destination, "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["format"], BUNDLE_FORMAT)
            self.assertEqual(manifest["version"], BUNDLE_VERSION)
            self.assertEqual(manifest["seller"]["username"], "source-seller")
            self.assertNotIn("email", manifest["seller"])
            self.assertNotIn("hashed_password", json.dumps(manifest))
            row = manifest["listings"][0]
            self.assertEqual(row["portable_id"], listing.portable_id)
            self.assertEqual(row["vehicle"]["make_key"], "honda")
            self.assertEqual(row["listing"]["country_code"], "US")
            self.assertEqual(row["listing"]["zone_code"], "US-IL")
            self.assertEqual(row["listing"]["city"], "Freeport")
            self.assertIn("sold_at", row["source"])
            self.assertEqual(
                manifest["seller"]["profile"],
                {"display_name": "source-seller display", "company_name": "source-seller Motors"},
            )
            self.assertEqual(row["vehicle"]["model"]["key"], "civic")
            self.assertEqual(row["vehicle"]["drivetrain_key"], "fwd")
            self.assertEqual(
                row["vehicle"]["feature_keys"],
                ["air-conditioning", "cruise-control"],
            )
            self.assertIn(row["images"][0]["path"], archive.namelist())


    def test_failed_export_does_not_destroy_existing_destination_bundle(self):
        listing = self._create_source_inventory()
        image = listing.images[0]
        (listing_image_root() / image.storage_key).unlink()
        destination = self._export_path()
        destination.write_bytes(b"existing-bundle")

        with self.assertRaises(InventoryBundleError):
            export_inventory_bundle(self.source, destination)

        self.assertEqual(destination.read_bytes(), b"existing-bundle")

    def test_round_trip_import_maps_to_target_and_preserves_inventory_state(self):
        source_listing = self._create_source_inventory()
        portable_id = source_listing.portable_id
        expected = {
            "status": source_listing.status,
            "featured": source_listing.featured,
            "view_count": source_listing.view_count,
            "first_published_at": self._utc(source_listing.first_published_at),
            "published_at": self._utc(source_listing.published_at),
            "expires_at": self._utc(source_listing.expires_at),
            "expiration_warning_sent_at": self._utc(
                source_listing.expiration_warning_sent_at
            ),
        }
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(source_listing)

        result = import_inventory_bundle(destination, self.target)
        db.session.commit()

        self.assertEqual(result.listings_imported, 1)
        self.assertEqual(result.images_imported, 1)
        self.assertEqual(result.seller_profiles_created, 1)
        imported = Listing.query.filter_by(portable_id=portable_id).one()
        self.assertEqual(imported.seller_id, self.target.id)
        self.assertEqual(imported.status, expected["status"])
        self.assertEqual(imported.featured, expected["featured"])
        self.assertEqual(imported.view_count, expected["view_count"])
        self.assertEqual(imported.country_code, "US")
        self.assertEqual(imported.zone_code, "US-IL")
        self.assertEqual(imported.city, "Freeport")
        self.assertEqual(
            self._utc(imported.first_published_at), expected["first_published_at"]
        )
        self.assertEqual(self._utc(imported.published_at), expected["published_at"])
        self.assertEqual(self._utc(imported.expires_at), expected["expires_at"])
        self.assertEqual(
            self._utc(imported.expiration_warning_sent_at),
            expected["expiration_warning_sent_at"],
        )
        self.assertEqual(imported.vehicle.make_ref.key, "honda")
        self.assertEqual(imported.vehicle.model_ref.key, "civic")
        self.assertEqual(imported.vehicle.drivetrain_ref.key, "fwd")
        self.assertEqual(
            [feature.key for feature in imported.vehicle.features],
            ["air-conditioning", "cruise-control"],
        )
        self.assertEqual(len(imported.images), 1)
        self.assertTrue(imported.images[0].is_primary)
        image_path = listing_image_root() / imported.images[0].storage_key
        self.assertTrue(image_path.is_file())
        profile = SellerProfile.query.filter_by(user_id=self.target.id).one()
        self.assertEqual(profile.company_name, "source-seller Motors")

    def test_import_as_draft_explicitly_resets_publication_state(self):
        source_listing = self._create_source_inventory(with_image=False)
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(source_listing)

        result = import_inventory_bundle(destination, self.target, as_draft=True)
        db.session.commit()

        imported = Listing.query.filter_by(seller_id=self.target.id).one()
        self.assertEqual(result.listings_imported, 1)
        self.assertEqual(imported.status, STATUS_DRAFT)
        self.assertFalse(imported.featured)
        self.assertEqual(imported.view_count, 0)
        self.assertIsNone(imported.first_published_at)
        self.assertIsNone(imported.published_at)
        self.assertIsNone(imported.expires_at)
        self.assertIsNone(imported.expiration_warning_sent_at)
        self.assertIsNone(imported.expired_at)
        self.assertIsNone(imported.sold_at)
        self.assertIsNone(imported.expired_edited_at)
        self.assertIsNone(imported.expired_removal_warning_sent_at)
        self.assertIsNone(imported.aged_out_at)
        self.assertIsNone(imported.aged_out_notice_sent_at)


    def test_import_does_not_overwrite_existing_destination_seller_profile(self):
        source_listing = self._create_source_inventory()
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(source_listing)
        db.session.add(
            SellerProfile(
                user_id=self.target.id,
                display_name="Destination Seller",
                company_name="Destination Motors",
            )
        )
        db.session.commit()

        result = import_inventory_bundle(destination, self.target)
        db.session.commit()

        self.assertEqual(result.seller_profiles_created, 0)
        profile = SellerProfile.query.filter_by(user_id=self.target.id).one()
        self.assertEqual(profile.company_name, "Destination Motors")

    def test_import_rejects_duplicate_portable_listing_identity_atomically(self):
        self._create_source_inventory()
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)

        with self.assertRaisesRegex(InventoryBundleError, "already exist"):
            import_inventory_bundle(destination, self.target)
        db.session.rollback()

        self.assertEqual(Listing.query.filter_by(seller_id=self.target.id).count(), 0)

    def test_import_rejects_missing_destination_reference_before_writes(self):
        source_listing = self._create_source_inventory(with_image=False)
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(source_listing)
        broken = self._rewrite_manifest(
            destination,
            lambda manifest: manifest["listings"][0]["vehicle"].__setitem__(
                "make_key", "missing-make"
            ),
        )

        with self.assertRaisesRegex(InventoryBundleError, "Unknown destination reference"):
            import_inventory_bundle(broken, self.target)
        db.session.rollback()

        self.assertEqual(Listing.query.filter_by(seller_id=self.target.id).count(), 0)

    def test_import_rejects_zone_that_does_not_belong_to_country(self):
        source_listing = self._create_source_inventory(with_image=False)
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(source_listing)
        broken = self._rewrite_manifest(
            destination,
            lambda manifest: manifest["listings"][0]["listing"].update(
                {"country_code": "CA", "zone_code": "US-IL"}
            ),
        )

        with self.assertRaisesRegex(InventoryBundleError, r"Invalid listings\[0\] location"):
            import_inventory_bundle(broken, self.target)
        db.session.rollback()

        self.assertEqual(Listing.query.filter_by(seller_id=self.target.id).count(), 0)

    def test_import_rejects_unsafe_or_unreferenced_archive_paths(self):
        source_listing = self._create_source_inventory(with_image=False)
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(source_listing)
        malicious = Path(self.temp_dir.name) / "malicious.zip"
        with zipfile.ZipFile(destination, "r") as source, zipfile.ZipFile(
            malicious, "w"
        ) as output:
            output.writestr("manifest.json", source.read("manifest.json"))
            output.writestr("../escape.txt", b"nope")

        with self.assertRaisesRegex(InventoryBundleError, "unsafe file path"):
            inspect_inventory_bundle(malicious)

    def test_import_reprocesses_images_and_rolls_back_invalid_image_data(self):
        source_listing = self._create_source_inventory()
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(source_listing)
        broken = self._rewrite_manifest(
            destination,
            lambda manifest: None,
            image_bytes=b"not-an-image",
        )

        with self.assertRaises(InventoryBundleError):
            import_inventory_bundle(broken, self.target)
        db.session.rollback()

        self.assertEqual(Listing.query.filter_by(seller_id=self.target.id).count(), 0)

    def test_inspection_rejects_inconsistent_lifecycle_timestamps(self):
        source_listing = self._create_source_inventory(with_image=False)
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(source_listing)

        def break_sold_history(manifest):
            source = manifest["listings"][0]["source"]
            source["status"] = STATUS_SOLD
            source["sold_at"] = source["created_at"]

        broken = self._rewrite_manifest(destination, break_sold_history)

        with self.assertRaisesRegex(
            InventoryBundleError,
            "sold_at cannot be earlier than published_at",
        ):
            inspect_inventory_bundle(broken)

    def test_inspection_rejects_unreferenced_extra_files(self):
        source_listing = self._create_source_inventory(with_image=False)
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(source_listing)
        extra = Path(self.temp_dir.name) / "extra.zip"
        with zipfile.ZipFile(destination, "r") as source, zipfile.ZipFile(
            extra, "w"
        ) as output:
            output.writestr("manifest.json", source.read("manifest.json"))
            output.writestr("extra.txt", b"unexpected")

        with self.assertRaisesRegex(InventoryBundleError, "unreferenced files"):
            inspect_inventory_bundle(extra)


    def test_import_rejects_non_zip_bundle(self):
        invalid = Path(self.temp_dir.name) / "not-a-bundle.zip"
        invalid.write_bytes(b"not a zip archive")

        with self.assertRaisesRegex(InventoryBundleError, "not a valid ZIP"):
            import_inventory_bundle(invalid, self.target)
        db.session.rollback()

        self.assertEqual(Listing.query.filter_by(seller_id=self.target.id).count(), 0)

    def test_full_site_backup_restores_multiple_sellers_with_lifecycle_fidelity(self):
        first = self._create_source_inventory(with_image=False)
        second = self._create_source_inventory(
            seller=self.second_source,
            with_image=False,
            title="Second Portable Civic",
        )
        second.status = STATUS_SOLD
        second.sold_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.session.commit()
        first_id = first.portable_id
        second_id = second.portable_id
        second_sold_at = self._utc(second.sold_at)

        destination = Path(self.temp_dir.name) / "site-backup.zip"
        result = export_site_inventory_bundle(destination)
        self.assertEqual(result.scope, BUNDLE_SCOPE_SITE)
        self.assertEqual(result.sellers_exported, 2)
        self.assertEqual(result.listings_exported, 2)

        with zipfile.ZipFile(destination, "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["scope"], BUNDLE_SCOPE_SITE)
        self.assertEqual(
            {seller["username"] for seller in manifest["sellers"]},
            {"source-seller", "second-source"},
        )
        self.assertEqual(
            {row["seller_username"] for row in manifest["listings"]},
            {"source-seller", "second-source"},
        )

        self._remove_source_listing(first)
        self._remove_source_listing(second)
        validated = inspect_inventory_bundle(destination)
        overrides = parse_seller_mapping_entries(
            [
                "source-seller=target-seller",
                "second-source=second-target",
            ]
        )
        mapping = resolve_restore_seller_mapping(validated, overrides)
        restored = restore_inventory_bundle(destination, mapping)
        db.session.commit()

        self.assertEqual(restored.sellers_restored, 2)
        self.assertEqual(restored.listings_imported, 2)
        first_restored = Listing.query.filter_by(portable_id=first_id).one()
        second_restored = Listing.query.filter_by(portable_id=second_id).one()
        self.assertEqual(first_restored.seller_id, self.target.id)
        self.assertEqual(first_restored.status, STATUS_ACTIVE)
        self.assertEqual(second_restored.seller_id, self.second_target.id)
        self.assertEqual(second_restored.status, STATUS_SOLD)
        self.assertEqual(self._utc(second_restored.sold_at), second_sold_at)

    def test_full_site_backup_includes_profile_only_seller(self):
        self._create_source_inventory(with_image=False)
        db.session.add(
            SellerProfile(
                user_id=self.second_source.id,
                display_name="Profile Only Seller",
                company_name="Profile Only Motors",
            )
        )
        db.session.commit()
        destination = Path(self.temp_dir.name) / "profile-only-site-backup.zip"

        result = export_site_inventory_bundle(destination)

        self.assertEqual(result.sellers_exported, 2)
        self.assertEqual(result.listings_exported, 1)
        with zipfile.ZipFile(destination, "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(
            {seller["username"] for seller in manifest["sellers"]},
            {"source-seller", "second-source"},
        )
        self.assertEqual(
            {row["seller_username"] for row in manifest["listings"]},
            {"source-seller"},
        )

    def test_seller_import_rejects_full_site_backup(self):
        first = self._create_source_inventory(with_image=False)
        second = self._create_source_inventory(
            seller=self.second_source,
            with_image=False,
            title="Second Portable Civic",
        )
        destination = Path(self.temp_dir.name) / "site-backup.zip"
        export_site_inventory_bundle(destination)
        self._remove_source_listing(first)
        self._remove_source_listing(second)

        with self.assertRaisesRegex(InventoryBundleError, "full-site backup"):
            import_inventory_bundle(destination, self.target)

    def test_restore_mapping_defaults_to_matching_destination_username(self):
        listing = self._create_source_inventory(with_image=False)
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(listing)

        validated = inspect_inventory_bundle(destination)
        mapping = resolve_restore_seller_mapping(validated)

        self.assertIs(mapping["source-seller"], self.source)

    def test_restore_mapping_rejects_unknown_source_override(self):
        listing = self._create_source_inventory(with_image=False)
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(listing)
        validated = inspect_inventory_bundle(destination)

        with self.assertRaisesRegex(InventoryBundleError, "not present"):
            resolve_restore_seller_mapping(
                validated,
                parse_seller_mapping_entries(["missing=target-seller"]),
            )

    def test_operator_cli_exports_selected_seller_inventory(self):
        self._create_source_inventory(with_image=False)
        destination = Path(self.temp_dir.name) / "cli-export.zip"

        with patch("app.plugins.autogrid360.cli.audit_activity_enabled", return_value=False):
            result = CliRunner().invoke(
                cli,
                [
                    "inventory",
                    "export",
                    "--seller",
                    self.source.username,
                    str(destination),
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(destination.is_file())
        self.assertIn("listings=1", result.output)
        self.assertIn("images=0", result.output)

    def test_operator_cli_import_maps_source_bundle_to_destination_seller(self):
        source_listing = self._create_source_inventory(with_image=False)
        destination = self._export_path()
        export_inventory_bundle(self.source, destination)
        self._remove_source_listing(source_listing)

        with patch("app.plugins.autogrid360.cli.audit_activity_enabled", return_value=False):
            result = CliRunner().invoke(
                cli,
                [
                    "inventory",
                    "import",
                    "--seller",
                    self.target.username,
                    str(destination),
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        imported = Listing.query.filter_by(seller_id=self.target.id).one()
        self.assertEqual(imported.status, STATUS_ACTIVE)
        self.assertIn("source_seller=source-seller", result.output)
        self.assertIn("as_draft=false", result.output)
        self.assertIn("destination_seller=target-seller", result.output)


    def test_operator_cli_exports_full_site_backup(self):
        self._create_source_inventory(with_image=False)
        self._create_source_inventory(
            seller=self.second_source,
            with_image=False,
            title="Second Portable Civic",
        )
        destination = Path(self.temp_dir.name) / "cli-site-export.zip"

        with patch("app.plugins.autogrid360.cli.audit_activity_enabled", return_value=False):
            result = CliRunner().invoke(
                cli,
                ["inventory", "export-all", str(destination)],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(destination.is_file())
        self.assertIn("sellers=2", result.output)
        self.assertIn("listings=2", result.output)
        with zipfile.ZipFile(destination, "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["scope"], BUNDLE_SCOPE_SITE)

    def test_operator_cli_restores_full_site_backup_with_seller_mappings(self):
        first = self._create_source_inventory(with_image=False)
        second = self._create_source_inventory(
            seller=self.second_source,
            with_image=False,
            title="Second Portable Civic",
        )
        second.status = STATUS_SOLD
        second.sold_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.session.commit()
        first_portable_id = first.portable_id
        second_portable_id = second.portable_id
        destination = Path(self.temp_dir.name) / "cli-site-restore.zip"
        export_site_inventory_bundle(destination)
        self._remove_source_listing(first)
        self._remove_source_listing(second)

        with patch("app.plugins.autogrid360.cli.audit_activity_enabled", return_value=False):
            result = CliRunner().invoke(
                cli,
                [
                    "inventory",
                    "restore",
                    "--map",
                    "source-seller=target-seller",
                    "--map",
                    "second-source=second-target",
                    str(destination),
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("sellers=2", result.output)
        self.assertIn("listings=2", result.output)
        self.assertIn("as_draft=false", result.output)
        first_restored = Listing.query.filter_by(portable_id=first_portable_id).one()
        second_restored = Listing.query.filter_by(portable_id=second_portable_id).one()
        self.assertEqual(first_restored.seller_id, self.target.id)
        self.assertEqual(first_restored.status, STATUS_ACTIVE)
        self.assertEqual(second_restored.seller_id, self.second_target.id)
        self.assertEqual(second_restored.status, STATUS_SOLD)
